import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .market import quote
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
        exists = (await db.execute(
            __import__("sqlalchemy").select(ScheduledReport).where(ScheduledReport.report_key == key)
        )).scalars().first()
        if exists:
            return {"sent": False, "reason": "already_sent"}

        symbols = [("IXIC", "Nasdaq"), ("SPX", "S&P 500"), ("DJI", "Dow Jones")]
        quotes = []
        for symbol, label in symbols:
            try:
                quotes.append((label, await quote(symbol)))
            except Exception:
                quotes.append((label, {"price": None, "change_pct": None}))

        async with httpx.AsyncClient(timeout=12) as client:
            news = await _general_news(client)
            events = await _economic_events(client, datetime.now(ET).date())

        news = sorted(news, key=_importance, reverse=True)
        lines = [INTRO, "", "━━━━━━━━━━━━━━━━━━", "", "📊 <b>حركة المؤشرات</b>"]
        for label, q in quotes:
            lines.append(f"• {label}: <b>{_fmt_price(q.get('price'))}</b> ({_fmt_pct(q.get('change_pct'))})")

        lines += ["", "📰 <b>الأخبار المؤثرة</b>"]
        selected = []
        for item in news:
            headline = str(item.get("headline") or "").strip()
            if not headline:
                continue
            selected.append(item)
            if len(selected) >= 5:
                break
        if selected:
            for item in selected:
                source = html.escape(str(item.get("source") or "المصدر"))
                headline = html.escape(str(item.get("headline") or ""))
                link = _news_link(item)
                lines.append(f"• <b>{headline}</b> — {source} {link}")
        else:
            lines.append("• لا يوجد خبر موثوق متاح من المصدر الاحتياطي حاليًا.")

        lines += ["", "📅 <b>البيانات الاقتصادية عالية التأثير</b>"]
        if events:
            for item in events[:5]:
                event = html.escape(str(item.get("event") or item.get("indicator") or "حدث اقتصادي"))
                actual_time = item.get("time") or item.get("date") or ""
                lines.append(f"• {event} — {actual_time} ET")
        else:
            lines.append("• لا تتوفر بيانات اقتصادية عالية التأثير من المصدر الحالي.")

        lines += [
            "",
            "📌 <b>قراءة السوق:</b> تعتمد النشرة على آخر بيانات متاحة قبل الافتتاح؛ لا تُعرض مستويات دعم/مقاومة غير محسوبة من بيانات تاريخية.",
            f"🕐 الافتتاح المنتظم: 09:30 ET | {datetime.now(RIYADH).strftime('%H:%M')} السعودية",
            "",
            "لا تعد هذه النشرة توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️",
        ]

        await send_message(settings.telegram_channel_id, "\n".join(lines))
        db.add(ScheduledReport(report_key=key))
        await db.commit()
        return {"sent": True, "key": key}
