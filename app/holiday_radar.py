import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .market import macro_quote
from .market_calendar import market_status
from .telegram import send_message

RIYADH = ZoneInfo("Asia/Riyadh")

_last_snapshot_at = None
_btc_previous_snapshot_price = None

# أثناء إغلاق السوق نعرض المؤشرات الثلاثة + بيتكوين + الذهب.
MACRO = [
    ("IXIC", "📊 Nasdaq"),
    ("SPX", "📊 S&P 500"),
    ("DJI", "📊 Dow Jones Industrial"),
    ("BTC/USD", "₿ بيتكوين"),
    ("XAU/USD", "🥇 الذهب"),
]

HOLIDAY_INTERVAL_MINUTES = 240  # 4 ساعات


def _fmt_price(value):
    if value is None:
        return "—"
    try:
        n = float(value)
        return f"{n:,.4f}" if n < 1000 else f"{n:,.2f}"
    except Exception:
        return str(value)


def _fmt_pct(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return str(value)


def _valid_price(value):
    try:
        return value is not None and float(value) > 0
    except Exception:
        return False


async def _public_holiday_fallback(symbol: str):
    """مصادر مستقلة عن Twelve Data للرادار أثناء العطلة/نهاية الأسبوع.

    الهدف هنا أن لا تصبح رسالة Holiday Radar فارغة بسبب نفاد حصة
    Twelve Data أو فشل مزود واحد.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"}

        # الذهب: XAUS مصدر عام مخصص للـ XAU/USD، بلا مفتاح API.
        if symbol == "XAU/USD":
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as client:
                r = await client.get("https://xaus.com/api/v1/spot?compact=1")
                if r.status_code < 400:
                    d = r.json()
                    price = d.get("spot_usd_oz")
                    if _valid_price(price):
                        return {
                            "symbol": symbol,
                            "price": float(price),
                            "change_pct": None,
                            "source": "XAUS Public",
                            "is_extended_hours": False,
                            "datetime": d.get("price_as_of") or d.get("updated_at"),
                        }

        # مؤشرات الأسهم: Stooq مستقل عن حصص مزودي API المدفوعة.
        mapped = {
            "SPX": "^spx",
            "IXIC": "^ndq",
            "DJI": "^dji",
        }.get(symbol)
        if mapped:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
                r = await client.get(
                    "https://stooq.com/q/d/l/",
                    params={"s": mapped, "i": "d"},
                )
                if r.status_code < 400:
                    import csv
                    import io

                    rows = list(csv.DictReader(io.StringIO(r.text)))
                    valid = []
                    for row in rows:
                        try:
                            close = float(row.get("Close"))
                            if close > 0:
                                valid.append((row.get("Date"), close))
                        except Exception:
                            continue

                    if valid:
                        latest_date, latest = valid[-1]
                        previous = valid[-2][1] if len(valid) > 1 else None
                        change_pct = (
                            ((latest - previous) / previous) * 100
                            if previous
                            else None
                        )
                        return {
                            "symbol": symbol,
                            "price": latest,
                            "change_pct": change_pct,
                            "source": "Stooq Last Close",
                            "is_extended_hours": False,
                            "datetime": latest_date,
                        }
    except Exception:
        return None

    return None


def _btc_move_line(current_price, previous_price):
    if current_price is None or previous_price in (None, 0):
        return "⏱️ <b>حركة 4 ساعات:</b> قيد المقارنة"

    move = ((current_price - previous_price) / previous_price) * 100
    if move > 0:
        arrow = "⚡📈" if move >= 3 else "↗️"
        label = "صعود قوي" if move >= 3 else "صعود"
    elif move < 0:
        arrow = "⚡📉" if abs(move) >= 3 else "↘️"
        label = "هبوط قوي" if abs(move) >= 3 else "هبوط"
    else:
        arrow = "➡️"
        label = "مستقر"

    return f"⏱️ <b>4 ساعات:</b> {arrow} <b>{move:+.2f}%</b> — {label}"


async def holiday_snapshot():
    rows = []
    for symbol, label in MACRO:
        try:
            # للمؤشرات والذهب نجرب المصدر المستقل أولاً، ثم طبقة السوق
            # الحالية التي تحتوي Finnhub/FMP/Twelve Data وغيرها.
            q = await _public_holiday_fallback(symbol)
            if not q or not _valid_price(q.get("price")):
                q = await macro_quote(symbol)

            if not q:
                q = {"price": None, "change_pct": None, "source": "unavailable"}

            rows.append((label, q))
        except Exception:
            rows.append(
                (
                    label,
                    {
                        "price": None,
                        "change_pct": None,
                        "source": "unavailable",
                    },
                )
            )
    return rows


async def publish_market_update(reason: str = "نفاد رصيد Twelve Data"):
    """Send a fallback market/news update without consuming Twelve Data when quota is exhausted."""
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram not configured"}

    rows = await holiday_snapshot()
    news = []
    if settings.finnhub_api_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://finnhub.io/api/v1/news",
                    params={"category": "general", "token": settings.finnhub_api_key},
                )
                if r.status_code < 400:
                    news = r.json()[:5]
        except Exception:
            news = []

    lines = [
        "📰 <b>SAS PRO — تحديث السوق</b>",
        "",
        f"ℹ️ <b>{reason}</b>",
        "",
        "📊 <b>مؤشرات السوق</b>",
    ]
    for label, q in rows:
        lines.append(
            f"{label}: <b>{_fmt_price(q.get('price'))}</b> "
            f"({_fmt_pct(q.get('change_pct'))})"
        )

    lines += ["", "📰 <b>آخر مستجدات السوق</b>"]
    if news:
        for item in news:
            headline = str(item.get("headline") or "").strip()
            source = str(item.get("source") or "").strip()
            if headline:
                lines.append(f"• {headline}" + (f" — {source}" if source else ""))
    else:
        lines.append("• لا تتوفر أخبار من مصدر الأخبار الاحتياطي حاليًا.")

    lines += [
        "",
        f"🕐 {datetime.now(RIYADH).strftime('%Y-%m-%d %H:%M')} بتوقيت السعودية",
        "",
        "⚠️ تحديث معلوماتي للسوق، وليس توصية شراء أو بيع.",
    ]
    await send_message(settings.telegram_channel_id, "\n".join(lines))
    return {
        "sent": True,
        "assets": ["NASDAQ", "SP500", "DOW", "BTC", "GOLD"],
        "news": len(news),
    }


async def publish_holiday_radar():
    """تحديث العطلة كل 4 ساعات مع مقارنة حركة بيتكوين بين التحديثين."""
    global _last_snapshot_at, _btc_previous_snapshot_price

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram channel/bot is not configured"}

    status = market_status()
    if not status["holiday"] and status["session"] != "weekend":
        return {"sent": False, "reason": "Stock radar session is available"}

    now = datetime.now(timezone.utc)
    interval = max(
        HOLIDAY_INTERVAL_MINUTES,
        settings.weekend_radar_interval_minutes,
        settings.holiday_radar_interval_minutes,
    ) * 60

    if _last_snapshot_at is not None:
        elapsed = (now - _last_snapshot_at).total_seconds()
        if elapsed < interval:
            return {"sent": False, "reason": "holiday snapshot interval not reached"}

    try:
        rows = await holiday_snapshot()
        btc = next((q for label, q in rows if label == "₿ بيتكوين"), None)
        btc_price = None
        if btc and btc.get("price") is not None:
            try:
                btc_price = float(btc["price"])
            except Exception:
                btc_price = None

        period = "عطلة السوق" if status["holiday"] else "نهاية الأسبوع"
        lines = [
            "🌙 <b>SAS PRO HOLIDAY RADAR</b>",
            "",
            f"🇺🇸 <b>الوضع:</b> {period}",
            "",
            "📡 <b>الأسعار الحالية / آخر إغلاق</b> ✅",
            "",
        ]

        for label, q in rows:
            price = _fmt_price(q.get("price"))
            change = _fmt_pct(q.get("change_pct"))
            source = str(q.get("source") or "")
            suffix = " • إغلاق أخير" if "Last Close" in source else ""

            if label == "₿ بيتكوين":
                display_label = "🔹 <b>BTC</b>"
                price_text = "$" + price if price != "—" else price
            elif label == "🥇 الذهب":
                display_label = "🔸 <b>Gold</b>"
                price_text = "$" + price if price != "—" else price
            elif label == "📊 Dow Jones Industrial":
                display_label = "📊 <b>Dow Jones</b>"
                price_text = price
            else:
                display_label = label
                price_text = price

            lines.append(f"{display_label}: {price_text} ({change}){suffix}")

        lines += [
            "",
            _btc_move_line(btc_price, _btc_previous_snapshot_price),
            "⚡ <b>حركة قوية:</b> تعني أن حركة بيتكوين خلال 4 ساعات بلغت 3% أو أكثر.",
            "",
            "🔄 <b>التحديث التالي بعد 4 ساعات</b>",
            f"🕐 {datetime.now(RIYADH).strftime('%H:%M')} بتوقيت السعودية",
            "",
            "📡 رصد معلوماتي للأسعار أثناء عطلة السوق ⚠️",
        ]

        await send_message(settings.telegram_channel_id, "\n".join(lines))
        _last_snapshot_at = now
        if btc_price is not None:
            _btc_previous_snapshot_price = btc_price

        return {
            "sent": True,
            "assets": ["NASDAQ", "SP500", "DOW", "BTC", "GOLD"],
            "interval_hours": 4,
        }

    except Exception as exc:
        return {"sent": False, "reason": f"holiday radar failed: {exc}"}


async def holiday_radar_scheduler():
    interval = max(
        HOLIDAY_INTERVAL_MINUTES,
        settings.weekend_radar_interval_minutes,
        settings.holiday_radar_interval_minutes,
    ) * 60
    while True:
        try:
            await publish_holiday_radar()
        except Exception:
            pass
        await asyncio.sleep(interval)


def stock_radar_enabled() -> bool:
    """Stock radar works across weekday sessions; holidays/weekends use holiday radar."""
    status = market_status()
    return not status["holiday"] and status["session"] != "weekend"
