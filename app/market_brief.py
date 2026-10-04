import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from .config import settings
from .market import macro_quote, quote
from .market_calendar import market_status
from .telegram import send_message
from .db import SessionLocal, ScheduledReport

RIYADH = ZoneInfo("Asia/Riyadh")
ET = ZoneInfo("America/New_York")

INTRO = """🕌 <b>اللهم صلِّ وسلم على نبينا محمد ﷺ</b>
<b>بسم الله نبدأ</b> 🤲
📡 <b>نشرة افتتاح السوق الأمريكي</b> 🇺🇸
أهم الأخبار المؤثرة + نقاط حركة السوق قبل الافتتاح."""

def _fmt_price(value):
    if value is None:
        return "غير متوفر"
    try:
        n = float(value)
        return f"{n:,.4f}" if n < 1000 else f"{n:,.2f}"
    except Exception:
        return "غير متوفر"

def _fmt_pct(value):
    if value is None:
        return "غير متوفر"
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "غير متوفر"

def _news_link(item):
    url = html.escape(str(item.get("url") or ""), quote=True)
    return f'<a href="{url}">فتح الخبر</a>' if url else ""

async def _general_news(client):
    if not settings.finnhub_api_key:
        return []
    try:
        r = await client.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": settings.finnhub_api_key},
        )
        r.raise_for_status()
        rows = r.json()
        return rows if isinstance(rows, list) else []
    except Exception:
        return []

async def _economic_events(client, today):
    if not settings.finnhub_api_key:
        return []
    try:
        r = await client.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={
                "from": today.isoformat(),
                "to": today.isoformat(),
                "token": settings.finnhub_api_key,
            },
        )
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("economicCalendar") or payload.get("calendar") or []
        return [x for x in rows if str(x.get("impact") or "").lower() in {"high", "3"}][:8]
    except Exception:
        return []

def _importance(item):
    text = " ".join(str(item.get(k) or "") for k in ("headline", "summary", "category")).lower()
    keywords = (
        "fed", "federal reserve", "cpi", "pce", "inflation", "jobs", "employment",
        "unemployment", "payroll", "gdp", "yield", "treasury", "oil", "iran",
        "israel", "china", "tariff", "chip", "semiconductor", "nvidia", "ai",
        "apple", "microsoft", "amazon", "tesla", "meta", "alphabet",
    )
    return sum(1 for word in keywords if word in text)

async def publish_market_brief():
    if not settings.market_brief_enabled:
        return {"sent": False, "reason": "disabled"}
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "telegram_not_configured"}
    status = market_status()
    if status.get("holiday") or status.get("session") != "premarket":
        return {"sent": False, "reason": "not_premarket"}
    minutes = int(status.get("minutes_to_open") or 9999)
    if minutes > max(5, settings.market_brief_window_minutes) or minutes < 50:
        return {"sent": False, "reason": "outside_window"}

    key = f"market-brief:{status.get('date')}"
    async with SessionLocal() as db:
        exists = (await db.execute(select(ScheduledReport).where(ScheduledReport.report_key == key))).scalars().first()
        if exists:
            return {"sent": False, "reason": "already_sent"}

        symbols = [("IXIC", "Nasdaq"), ("SPX", "S&P 500"), ("DJI", "Dow Jones Industrial")]
        quotes = []
        for symbol, label in symbols:
            try:
                # استخدم نفس مسار المؤشرات المستقل المستخدم في شريط التطبيق والرادار,
                # حتى لا تتوقف الأسعار في التقرير عند تعطل/نفاد مصدر Twelve Data.
                quotes.append((label, await macro_quote(symbol)))
            except Exception:
                quotes.append((label, {"price": None, "change_pct": None}))

        async with httpx.AsyncClient(timeout=12) as client:
            news = await _general_news(client)
            events = await _economic_events(client, datetime.now(ET).date())
        news = sorted(news, key=_importance, reverse=True)[:5]

        try:
            from .scrapling_source import enrich_news_items
            news = list(await enrich_news_items(news, limit=3))
        except Exception:
            pass

        ai = {"enabled": False, "status": "unavailable"}
        try:
            from .ai_radar import analyze_stock
            ai_news = [{
                "headline": item.get("headline"),
                "source": item.get("source"),
                "url": item.get("url"),
                "datetime": item.get("datetime"),
                "summary": item.get("summary") or "",
            } for item in news if item.get("headline") and item.get("url") and item.get("source")]
            if ai_news:
                ai = await analyze_stock("MARKET", news=ai_news, fundamentals={}, market={
                    "session": "premarket",
                    "minutes_to_open": minutes,
                    "indices": [{"name": label, "price": q.get("price"), "change_pct": q.get("change_pct")} for label, q in quotes],
                })
        except Exception:
            pass

        lines = [INTRO, "", "━━━━━━━━━━━━━━━━━━", "", "📊 <b>مؤشرات السوق قبل الافتتاح</b>"]
        for label, q in quotes:
            lines.append(f"• <b>{html.escape(label)}</b>: {_fmt_price(q.get('price'))} ({_fmt_pct(q.get('change_pct'))})")

        lines += ["", "📰 <b>زبدة الأخبار المؤثرة</b>"]
        if ai.get("enabled") and ai.get("status") == "ok":
            lines += [
                f"🔹 <b>الخلاصة:</b> {html.escape(str(ai.get('headline_summary') or 'غير واضح'))}",
                f"🔹 <b>تأثير الخبر:</b> {html.escape(str(ai.get('why_rising') or 'غير واضح'))}",
                f"🔹 <b>التقييم:</b> {html.escape(str(ai.get('news_assessment') or 'غير واضح'))}",
            ]
        elif news:
            for item in news[:3]:
                headline = html.escape(str(item.get("headline") or "").strip())
                source = html.escape(str(item.get("source") or "المصدر"))
                link = _news_link(item)
                if headline:
                    lines.append(f"• <b>{headline}</b> — {source} {link}")
        else:
            lines.append("• لا يوجد خبر موثوق متاح حاليًا.")

        lines += ["", "📚 <b>المصادر</b>"]
        for item in news[:3]:
            headline = html.escape(str(item.get("headline") or "").strip())
            source = html.escape(str(item.get("source") or "المصدر"))
            link = _news_link(item)
            if headline:
                lines.append(f"• {source}: {headline} {link}")

        lines += ["", "📅 <b>أحداث اليوم المؤثرة</b>"]
        if events:
            for item in events[:5]:
                event = html.escape(str(item.get("event") or item.get("indicator") or "حدث اقتصادي"))
                actual_time = item.get("time") or item.get("date") or ""
                lines.append(f"• {event} — {actual_time} ET")
        else:
            lines.append("• لا تتوفر بيانات اقتصادية عالية التأثير من المصدر الحالي.")

        lines += [
            "",
            "🧭 <b>قراءة الافتتاح</b>",
            f"⏱️ الافتتاح المنتظم: <b>09:30 ET</b> | قبل الافتتاح بـ <b>{minutes} دقيقة</b>",
            "📌 تعتمد النشرة على آخر البيانات الموثقة قبل الافتتاح، ولا تستبدل الرصد الفني للأسهم.",
            "",
            "⚠️ لا تعد هذه النشرة توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول.",
            "📡 <b>SAS PRO</b>",
        ]
        await send_message(settings.telegram_channel_id, "\n".join(lines))
        db.add(ScheduledReport(report_key=key))
        await db.commit()
        return {"sent": True, "key": key}
