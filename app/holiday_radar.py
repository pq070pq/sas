import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .market import quote
from .market_calendar import market_status
from .telegram import send_message

RIYADH = ZoneInfo("Asia/Riyadh")

_last_snapshot_at = None
_last_btc_alert_at = None
_btc_alert_reference = None

# أثناء عطلات سوق الأسهم ونهاية الأسبوع نعرض فقط الأصول التي طلبها SAS PRO.
MACRO = [
    ("BTC/USD", "₿ بيتكوين"),
    ("XAU/USD", "🥇 الذهب"),
    ("WTI/USD", "🛢 النفط"),
]


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


async def holiday_snapshot():
    rows = []
    for symbol, label in MACRO:
        try:
            q = await quote(symbol)
            rows.append((label, q))
        except Exception:
            rows.append((label, {"price": None, "change_pct": None}))
    return rows


async def publish_holiday_radar():
    """في عطلة السوق/نهاية الأسبوع: عرض دوري لبيتكوين والذهب والنفط."""
    global _last_snapshot_at, _last_btc_alert_at, _btc_alert_reference

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram channel/bot is not configured"}

    status = market_status()
    if not status["holiday"] and status["session"] != "weekend":
        return {"sent": False, "reason": "Stock radar session is available"}

    now = datetime.now(timezone.utc)
    interval = max(10, settings.holiday_radar_interval_minutes) * 60

    # هذه الدالة تُستدعى من أكثر من دورة؛ لا ترسل رسالة كل 5 دقائق.
    if _last_snapshot_at is not None:
        elapsed = (now - _last_snapshot_at).total_seconds()
        if elapsed < interval:
            return {"sent": False, "reason": "holiday snapshot interval not reached"}

    try:
        rows = await holiday_snapshot()
        btc = next((q for label, q in rows if label == "₿ بيتكوين"), None)
        btc_price = float(btc.get("price")) if btc and btc.get("price") is not None else None

        period = "عطلة السوق" if status["holiday"] else "نهاية الأسبوع"
        lines = [
            "🌙 <b>SAS PRO HOLIDAY RADAR</b>",
            "",
            f"🇺🇸 الوضع: <b>{period}</b>",
            "",
        ]

        for label, q in rows:
            lines.append(
                f"{label}: <b>{_fmt_price(q.get('price'))}</b> "
                f"({_format_pct(q.get('change_pct'))})"
            )

        alert_line = None
        if btc_price is not None:
            if _btc_alert_reference in (None, 0):
                _btc_alert_reference = btc_price
            else:
                movement = abs((btc_price - _btc_alert_reference) / _btc_alert_reference) * 100
                cooldown_ok = (
                    _last_btc_alert_at is None
                    or (now - _last_btc_alert_at).total_seconds() >= 5400
                )
                if movement >= 1.50 and cooldown_ok:
                    direction = "📈 صعود" if btc_price > _btc_alert_reference else "📉 هبوط"
                    alert_line = f"🚨 BTC: {direction} <b>{movement:.2f}%</b> منذ آخر تنبيه"
                    _last_btc_alert_at = now
                    _btc_alert_reference = btc_price

        lines += [
            "",
            f"🕐 {datetime.now(RIYADH).strftime('%H:%M')} بتوقيت السعودية",
        ]

        if alert_line:
            lines += ["", alert_line, "🔕 نفس شروط تنبيه بيتكوين السابقة."]

        lines += [
            "",
            "⚠️ رصد معلوماتي للأسعار فقط أثناء عطلة السوق.",
        ]

        await send_message(settings.telegram_channel_id, "\n".join(lines))
        _last_snapshot_at = now
        return {"sent": True, "assets": ["BTC", "GOLD", "OIL"]}

    except Exception as exc:
        return {"sent": False, "reason": f"holiday radar failed: {exc}"}


async def holiday_radar_scheduler():
    interval = max(10, settings.holiday_radar_interval_minutes) * 60
    while True:
        try:
            await publish_holiday_radar()
        except Exception:
            pass
        await asyncio.sleep(interval)


def stock_radar_enabled() -> bool:
    """Stock radar works continuously across weekday extended hours; holidays/weekends use holiday radar."""
    status = market_status()
    return not status["holiday"] and status["session"] != "weekend"
