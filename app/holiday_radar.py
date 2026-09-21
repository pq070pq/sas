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
    """Publish a quiet macro snapshot only while the US regular session is closed."""
    global _last_snapshot_at, _last_btc_alert_at, _btc_alert_reference

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram channel/bot is not configured"}

    status = market_status()
    if status["open"]:
        # The moment the regular session opens, holiday/macro broadcasting stops.
        return {"sent": False, "reason": "US regular session is open"}

    now = datetime.now(timezone.utc)

    # Main macro snapshot: every 2 hours, not every scheduler tick.
    if _last_snapshot_at is None or (now - _last_snapshot_at).total_seconds() >= 7200:
        rows = await holiday_snapshot()
        title = "🟡 <b>SAS HOLIDAY RADAR</b>" if status["holiday"] else "🌙 <b>SAS MARKET RADAR</b>"
        text = (
            f"{title}\n\n"
            "🇺🇸 السوق الأمريكي مغلق.\n"
            f"🕐 {datetime.now(RIYADH).strftime('%H:%M')} بتوقيت السعودية\n\n"
        )
        for label, q in rows:
            text += f"{label}: <b>{_fmt_price(q.get('price'))}</b>  {_fmt_pct(q.get('change_pct'))}\n"
        text += "\n🔕 التحديث الدوري كل ساعتين أثناء الإغلاق."
        try:
            await send_message(settings.telegram_channel_id, text)
            _last_snapshot_at = now
        except Exception:
            pass

    # Bitcoin: alert only for a meaningful move from the last BTC alert reference.
    try:
        btc = await quote("BTC/USD")
        price = btc.get("price")
        if price is not None:
            price = float(price)
            if _btc_alert_reference in (None, 0):
                _btc_alert_reference = price
            movement = abs((price - _btc_alert_reference) / _btc_alert_reference) * 100
            cooldown_ok = _last_btc_alert_at is None or (now - _last_btc_alert_at).total_seconds() >= 5400
            if movement >= 1.50 and cooldown_ok:
                direction = "📈 صعود" if price > _btc_alert_reference else "📉 هبوط"
                text = (
                    "₿ <b>تحرك بيتكوين</b>\n\n"
                    f"{direction} <b>{movement:.2f}%</b> منذ آخر تنبيه.\n"
                    f"السعر الحالي: <b>{_fmt_price(price)}</b>\n"
                    "🔕 تنبيه مختصر لتجنب الإزعاج."
                )
                try:
                    await send_message(settings.telegram_channel_id, text)
                    _last_btc_alert_at = now
                    _btc_alert_reference = price
                except Exception:
                    pass
    except Exception:
        pass

    return {"sent": True}

async def holiday_radar_scheduler():
    interval = max(10, settings.holiday_radar_interval_minutes) * 60
    while True:
        try:
            await publish_holiday_radar()
        except Exception:
            pass
        await asyncio.sleep(interval)

def stock_radar_enabled() -> bool:
    """Stock radar is enabled only during the US regular session."""
    return market_status()["open"]
