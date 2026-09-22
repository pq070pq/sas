import asyncio
import httpx
from .config import settings

async def quote(symbol: str):
    if not settings.twelve_data_api_key:
        return {"symbol": symbol, "price": None, "change_pct": None, "source": "not_configured"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.twelvedata.com/quote", params={
            "symbol": symbol, "apikey": settings.twelve_data_api_key
        })
        r.raise_for_status()
        d = r.json()
    if d.get("status") == "error":
        raise RuntimeError(d.get("message", "Twelve Data error"))
    return {
        "symbol": symbol,
        "price": d.get("close") or d.get("price"),
        "change_pct": d.get("percent_change"),
        "source": "Twelve Data",
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
