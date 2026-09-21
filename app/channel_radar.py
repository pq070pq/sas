from datetime import datetime, timezone
from .config import settings
from .market import quote
from .market_mode import market_status
from .telegram import send_message

MACRO = [
    ("BTC/USD", "₿ بيتكوين"),
    ("XAU/USD", "🥇 الذهب"),
    ("WTI/USD", "🛢 النفط"),
    ("SPX", "🇺🇸 S&P 500"),
    ("IXIC", "📈 Nasdaq"),
    ("DJI", "📊 Dow Jones"),
]

_last_macro_at = None
_last_btc_at = None
_last_btc_price = None

async def _macro_snapshot():
    rows = []
    for symbol, label in MACRO:
        try:
            q = await quote(symbol)
            if q.get("price") is not None:
                rows.append((label, q))
        except Exception:
            continue
    return rows

def _fmt(q):
    price = q.get("price")
    ch = q.get("change_pct")
    p = f"{float(price):,.4f}" if price is not None and float(price) < 1000 else (f"{float(price):,.2f}" if price is not None else "—")
    c = f"{float(ch):+.2f}%" if ch is not None else "—"
    return p, c

async def broadcast_closed_market():
    global _last_macro_at, _last_btc_at, _last_btc_price
    if not settings.telegram_channel_id:
        return
    state = market_status()
    if state["open"]:
        return

    now = datetime.now(timezone.utc)
    # Macro snapshot: every 2 hours while the US session is closed.
    if _last_macro_at is None or (now - _last_macro_at).total_seconds() >= 7200:
        rows = await _macro_snapshot()
        if rows:
            title = "🌙 <b>SAS HOLIDAY RADAR</b>" if state["mode"] == "holiday" else "🌙 <b>SAS MARKET RADAR</b>"
            text = title + "\n\n"
            text += "السوق الأمريكي مغلق — متابعة الأسواق العالمية بدون إزعاج.\n\n"
            for label, q in rows:
                price, change = _fmt(q)
                text += f"{label}: <b>{price}</b>  {change}\n"
            text += "\n🕒 تحديث دوري كل ساعتين أثناء الإغلاق."
            try:
                await send_message(settings.telegram_channel_id, text)
                _last_macro_at = now
            except Exception:
                pass

    # Bitcoin movement: only meaningful moves, with a 90-minute cooldown.
    try:
        btc = await quote("BTC/USD")
        price = float(btc.get("price")) if btc.get("price") is not None else None
        if price:
            movement = None if _last_btc_price in (None, 0) else abs((price - _last_btc_price) / _last_btc_price) * 100
            cooldown_ok = _last_btc_at is None or (now - _last_btc_at).total_seconds() >= 5400
            threshold = movement is not None and movement >= 1.50
            if cooldown_ok and threshold:
                direction = "📈 صعود" if price > _last_btc_price else "📉 هبوط"
                text = (
                    "₿ <b>تحرك بيتكوين</b>\n\n"
                    f"{direction} بمقدار <b>{movement:.2f}%</b> منذ آخر تنبيه.\n"
                    f"السعر الحالي: <b>{price:,.2f}</b>\n"
                    "تنبيه مختصر أثناء إغلاق السوق."
                )
                try:
                    await send_message(settings.telegram_channel_id, text)
                    _last_btc_at = now
                except Exception:
                    pass
            _last_btc_price = price
    except Exception:
        pass

def stock_radar_enabled():
    """The stock radar is allowed only during the regular US session."""
    return market_status()["open"]
