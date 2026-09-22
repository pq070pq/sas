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

MACRO = [
    ("BTC/USD", "₿ BTC"),
    ("XAU/USD", "🥇 Gold"),
    ("WTI/USD", "🛢 Oil"),
    ("SPX", "📊 S&P 500"),
    ("IXIC", "💻 Nasdaq"),
    ("DJI", "📈 Dow Jones"),
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
    """في عطلة السوق/نهاية الأسبوع: راقب بيتكوين بنفس شروط الرادار السابقة."""
    global _last_btc_alert_at, _btc_alert_reference

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram channel/bot is not configured"}

    status = market_status()
    if not status["holiday"] and status["session"] != "weekend":
        return {"sent": False, "reason": "Stock radar session is available"}

    now = datetime.now(timezone.utc)

    # نفس شرط الرادار السابق: حركة 1.50% على الأقل + مهلة 90 دقيقة.
    try:
        btc = await quote("BTC/USD")
        price = btc.get("price")
        if price is None:
            return {"sent": False, "reason": "BTC price unavailable"}

        price = float(price)
        if _btc_alert_reference in (None, 0):
            _btc_alert_reference = price
            return {"sent": False, "reason": "BTC reference initialized"}

        movement = abs((price - _btc_alert_reference) / _btc_alert_reference) * 100
        cooldown_ok = (
            _last_btc_alert_at is None
            or (now - _last_btc_alert_at).total_seconds() >= 5400
        )

        if movement >= 1.50 and cooldown_ok:
            direction = "📈 صعود" if price > _btc_alert_reference else "📉 هبوط"
            period = "عطلة السوق" if status["holiday"] else "نهاية الأسبوع"
            text = (
                "₿ <b>SAS BTC RADAR</b>\n\n"
                f"🇺🇸 الوضع: <b>{period}</b>\n"
                f"{direction} <b>{movement:.2f}%</b> منذ آخر تنبيه.\n"
                f"💵 السعر الحالي: <b>{_fmt_price(price)}</b>\n"
                f"🕐 {datetime.now(RIYADH).strftime('%H:%M')} بتوقيت السعودية\n\n"
                "🔕 نفس شروط رادار بيتكوين السابقة."
            )
            try:
                await send_message(settings.telegram_channel_id, text)
                _last_btc_alert_at = now
                _btc_alert_reference = price
                return {"sent": True, "asset": "BTC", "movement_pct": movement}
            except Exception:
                return {"sent": False, "reason": "telegram send failed"}

        return {"sent": False, "reason": "no BTC movement threshold"}
    except Exception:
        return {"sent": False, "reason": "BTC quote failed"}

async def holiday_radar_scheduler():
    interval = max(10, settings.holiday_radar_interval_minutes) * 60
    while True:
        try:
            await publish_holiday_radar()
        except Exception:
            pass
        await asyncio.sleep(interval)

def stock_radar_enabled() -> bool:
    """Stock radar works all weekday hours; holidays/weekends switch to BTC radar."""
    status = market_status()
    return not status["holiday"] and status["session"] != "weekend"
