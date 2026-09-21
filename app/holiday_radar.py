import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .market import quote
from .telegram import send_message

RIYADH = ZoneInfo("Asia/Riyadh")

# US market holidays for 2026-2030. Weekends are handled separately.
US_HOLIDAYS = {
    2026: {"01-01","01-19","02-16","04-03","05-25","06-19","07-03","09-07","11-26","12-25"},
    2027: {"01-01","01-18","02-15","03-26","05-31","06-18","07-05","09-06","11-25","12-24"},
    2028: {"01-17","02-21","04-14","05-29","06-19","07-04","09-04","11-23","12-25"},
    2029: {"01-01","01-15","02-19","03-30","05-28","06-19","07-04","09-03","11-22","12-25"},
    2030: {"01-01","01-21","02-18","04-19","05-27","06-19","07-04","09-02","11-28","12-25"},
}

def us_market_holiday_or_weekend(dt: datetime | None = None) -> bool:
    local = (dt or datetime.now(timezone.utc)).astimezone(RIYADH)
    if local.weekday() >= 5:
        return True
    return local.strftime("%m-%d") in US_HOLIDAYS.get(local.year, set())

def _fmt_price(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)

def _fmt_pct(value):
    if value is None:
        return "—"
    try:
        n = float(value)
        return f"{n:+.2f}%"
    except Exception:
        return str(value)

async def bitcoin_move():
    current = await quote("BTC/USD")
    return current

async def holiday_snapshot():
    symbols = [
        ("BTC/USD", "₿ BTC"),
        ("XAU/USD", "🥇 Gold"),
        ("WTI/USD", "🛢 Oil"),
        ("SPX", "📊 S&P 500"),
        ("IXIC", "💻 Nasdaq"),
        ("DJI", "📈 Dow Jones"),
    ]
    rows = []
    for symbol, label in symbols:
        try:
            q = await quote(symbol)
            rows.append(f"{label}: <b>{_fmt_price(q.get('price'))}</b>  {_fmt_pct(q.get('change_pct'))}")
        except Exception:
            rows.append(f"{label}: <b>—</b>  —")
    btc = await bitcoin_move()
    return rows, btc

async def publish_holiday_radar():
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram channel/bot is not configured"}
    if not us_market_holiday_or_weekend():
        return {"sent": False, "reason": "US market is not on holiday/weekend"}

    rows, btc = await holiday_snapshot()
    now = datetime.now(timezone.utc).astimezone(RIYADH)
    text = (
        "🟡 <b>SAS HOLIDAY RADAR</b>\n\n"
        "🇺🇸 <b>السوق الأمريكي في إجازة</b>\n"
        f"🕐 تحديث: {now.strftime('%H:%M')} بتوقيت السعودية\n\n"
        + "\n".join(rows)
        + "\n\n"
        "₿ <b>تحرك البيتكوين</b>\n"
        f"السعر: <b>{_fmt_price(btc.get('price'))}</b>\n"
        f"التغير: <b>{_fmt_pct(btc.get('change_pct'))}</b>\n\n"
        "يتكرر التحديث تلقائيًا أثناء إجازة السوق."
    )
    await send_message(settings.telegram_channel_id, text)
    return {"sent": True}

async def holiday_radar_scheduler():
    interval = max(10, settings.holiday_radar_interval_minutes) * 60
    while True:
        try:
            await publish_holiday_radar()
        except Exception:
            pass
        await asyncio.sleep(interval)
