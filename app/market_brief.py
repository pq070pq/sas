import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from .config import settings
from .market import macro_quote, quote, _fred_index_quote, _stooq_quote
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

        symbols = [("IXIC", "Nasdaq"), ("SPX", "S&P 500"), ("DJI", "Dow Jones Industrial"), ("BTC/USD", "Bitcoin"), ("XAU/USD", "Gold")]
        quotes = []
        for symbol, label in symbols:
            try:
                # استخدم نفس مسار المؤشرات المستقل المستخدم في شريط التطبيق والرادار,
                # حتى لا تتوقف الأسعار في التقرير عند تعطل/نفاد مصدر Twelve Data.
                result = await macro_quote(symbol)
                # Last-resort independent index sources for the opening brief.
                if symbol in {"IXIC", "SPX", "DJI"} and not result.get("price"):
                    for fallback in (_fred_index_quote, _stooq_quote):
                        try:
                            candidate = await fallback(symbol)
                            if candidate and candidate.get("price"):
                                result = dict(candidate)
                                result["stale"] = True
                                result["source"] = str(result.get("source") or "آخر إغلاق موثق")
                                break
                        except Exception:
                            continue
                quotes.append((label, result))
            except Exception:
                quotes.append((label, {"price": None, "change_pct": None, "source": "غير متاح"}))

        async with httpx.AsyncClient(timeout=12) as client:
            news = await _general_news(client)
            events = await _economic_events(client, datetime.now(ET).date())
        news = sorted(news, key=_importance, reverse=True)[:5]

        try:
            from .scrapling_source import enrich_news_items
            news = list(await enrich_news_items(news, limit=3))
        except Exception:
            pass

        # Translate verified headlines and available summaries; keep original URLs.
        translations = {}
        try:
            from .news_translation import translate_stock_news
            translated = await translate_stock_news("MARKET", news[:5])
            translations = {str(item.get("url") or ""): item for item in translated}
        except Exception:
            translations = {}

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

        # اعرض فقط البيانات الحقيقية المتاحة، ولا تطبع حقولًا فارغة أو "غير متوفر".
        available_quotes = [(label, q) for label, q in quotes if q and q.get("price") is not None]
        lines = [INTRO, "", "━━━━━━━━━━━━━━━━━━"]
        if available_quotes:
            lines += ["", "📊 <b>مؤشرات السوق قبل الافتتاح</b>"]
            for label, q in available_quotes:
                price_text = _fmt_price(q.get("price"))
                change = q.get("change_pct")
                change_text = f" ({_fmt_pct(change)})" if change is not None else ""
                lines.append(f"• <b>{html.escape(label)}</b>: {price_text}{change_text}")

        lines += ["", "📰 <b>زبدة الأخبار المؤثرة</b>"]
        if ai.get("enabled") and ai.get("status") == "ok":
            lines += [
                f"🔹 <b>الخلاصة:</b> {html.escape(str(ai.get('headline_summary') or 'غير واضح'))}",
                f"🔹 <b>تأثير الخبر:</b> {html.escape(str(ai.get('why_rising') or 'غير واضح'))}",
                f"🔹 <b>التقييم:</b> {html.escape(str(ai.get('news_assessment') or 'غير واضح'))}",
            ]
            # حتى عند نجاح الترجمة/التحليل، يبقى رابط الخبر الأصلي ظاهرًا لكل خبر.
            for item in news[:3]:
                original = str(item.get("headline") or "").strip()
                translation = translations.get(str(item.get("url") or ""), {})
                headline = html.escape(str(translation.get("translated_headline") or original).strip())
                source = html.escape(str(item.get("source") or "المصدر"))
                summary = html.escape(str(translation.get("summary") or item.get("ai_summary") or item.get("summary") or "").strip())
                link = _news_link(item)
                if headline:
                    lines.append(f"📰 <b>{headline}</b> — <i>{source}</i> {link}")
                    if summary:
                        lines.append(f"   {summary}")
        elif news:
            for item in news[:3]:
                original = str(item.get("headline") or "").strip()
                translation = translations.get(str(item.get("url") or ""), {})
                headline = html.escape(str(translation.get("translated_headline") or original).strip())
                source = html.escape(str(item.get("source") or "المصدر"))
                summary = html.escape(str(translation.get("summary") or item.get("ai_summary") or item.get("summary") or "").strip())
                link = _news_link(item)
                if headline:
                    lines.append(f"• <b>{headline}</b> — <i>{source}</i> {link}")
                    if summary:
                        lines.append(f"   {summary}")
        else:
            lines.append("• لا يوجد خبر موثوق متاح حاليًا.")

        if events:
            lines += ["", "📅 <b>أحداث اليوم المؤثرة</b>"]
            for item in events[:5]:
                event = html.escape(str(item.get("event") or item.get("indicator") or "حدث اقتصادي"))
                actual_time = item.get("time") or item.get("date") or ""
                time_text = f" — {actual_time} ET" if actual_time else ""
                lines.append(f"• {event}{time_text}")

        lines += [
            "",
            "🧭 <b>قراءة الافتتاح</b>",
            f"⏱️ الافتتاح المنتظم: <b>09:30 ET</b> | قبل الافتتاح بـ <b>{minutes} دقيقة</b>",
            "📌 تعتمد النشرة على آخر البيانات الموثقة قبل الافتتاح، ولا تستبدل الرصد الفني للأسهم.",
            "",
            "📡 <b>SAS PRO</b>",
        ]
        await send_message(settings.telegram_channel_id, "\n".join(lines))
        db.add(ScheduledReport(report_key=key))
        await db.commit()
        return {"sent": True, "key": key}
