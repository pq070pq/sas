import asyncio
import httpx
from .config import settings

async def quote(symbol: str):
    if not settings.twelve_data_api_key:
        return {"symbol": symbol, "price": None, "change_pct": None, "source": "not_configured"}

    params = {
        "symbol": symbol,
        "apikey": settings.twelve_data_api_key,
        # يدعم بيانات قبل/بعد السوق في خطط Twelve Data التي توفر Extended Hours.
        "prepost": "true",
    }

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.twelvedata.com/quote", params=params)
        r.raise_for_status()
        d = r.json()

        # إذا لم تكن بيانات Extended Hours متاحة على الخطة، نرجع تلقائيًا
        # إلى السعر العادي بدل تعطيل الرادار بالكامل.
        if d.get("status") == "error":
            fallback = await c.get(
                "https://api.twelvedata.com/quote",
                params={"symbol": symbol, "apikey": settings.twelve_data_api_key},
            )
            fallback.raise_for_status()
            d = fallback.json()

    if d.get("status") == "error":
        raise RuntimeError(d.get("message", "Twelve Data error"))

    extended_price = d.get("extended_price")
    regular_price = d.get("close") or d.get("price")
    price = extended_price or regular_price

    extended_pct = d.get("extended_percent_change")
    regular_pct = d.get("percent_change")
    change_pct = extended_pct if extended_pct is not None else regular_pct

    return {
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "source": "Twelve Data Extended Hours" if extended_price is not None else "Twelve Data",
        "is_extended_hours": bool(d.get("is_extended_hours")) or extended_price is not None,
        "datetime": d.get("datetime"),
    }

async def ticker():
    symbols = [
        ("BTC/USD", "BTC"),
        ("XAU/USD", "GOLD"),
        ("WTI/USD", "OIL"),
        ("SPX", "S&P 500"),
        ("IXIC", "NASDAQ"),
        ("DJI", "DOW JONES"),
        ("VIX", "VIX"),
    ]

    async def one(symbol, label):
        try:
            item = await quote(symbol)
            item["label"] = label
            return item
        except Exception:
            return {
                "symbol": symbol, "label": label,
                "price": None, "change_pct": None, "source": "error",
            }

    return await asyncio.gather(*(one(symbol, label) for symbol, label in symbols))
