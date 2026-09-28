import asyncio
import httpx
from .config import settings


def _valid_price(value):
    try:
        return value is not None and float(value) > 0
    except Exception:
        return False


async def quote(symbol: str):
    if not settings.twelve_data_api_key:
        return {"symbol": symbol, "price": None, "change_pct": None, "source": "not_configured"}

    async with httpx.AsyncClient(timeout=15) as c:
        params = {
            "symbol": symbol,
            "apikey": settings.twelve_data_api_key,
            # يدعم بيانات قبل/بعد السوق في خطط Twelve Data التي توفر Extended Hours.
            "prepost": "true",
        }
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

        # بعض الردود قد ترجع "0" كسعر غير صالح. لا نعتبر الصفر سعراً حقيقياً.
        extended_price = d.get("extended_price")
        regular_price = d.get("close")
        if not _valid_price(regular_price):
            regular_price = d.get("price")

        price = extended_price if _valid_price(extended_price) else regular_price
        change_pct = (
            d.get("extended_percent_change")
            if d.get("extended_percent_change") is not None
            else d.get("percent_change")
        )
        source = "Twelve Data Extended Hours" if _valid_price(extended_price) else "Twelve Data"

        # Fallback خفيف لمصادر الأصول التي لا يعيد لها /quote سعراً صالحاً.
        if not _valid_price(price):
            price_r = await c.get(
                "https://api.twelvedata.com/price",
                params={"symbol": symbol, "apikey": settings.twelve_data_api_key, "prepost": "true"},
            )
            price_r.raise_for_status()
            pd = price_r.json()
            fallback_price = pd.get("price")
            if _valid_price(fallback_price):
                price = fallback_price
                source = "Twelve Data Price"

        # آخر fallback: آخر إغلاق متاح من /time_series، مفيد خصوصاً عند إغلاق السوق.
        if not _valid_price(price):
            ts_r = await c.get(
                "https://api.twelvedata.com/time_series",
                params={
                    "symbol": symbol,
                    "interval": "1day",
                    "outputsize": 1,
                    "apikey": settings.twelve_data_api_key,
                },
            )
            ts_r.raise_for_status()
            td = ts_r.json()
            values = td.get("values") or []
            if values and _valid_price(values[0].get("close")):
                price = values[0].get("close")
                source = "Twelve Data Last Close"

        if d.get("status") == "error" and not _valid_price(price):
            raise RuntimeError(d.get("message", "Twelve Data error"))

        if change_pct is None and _valid_price(price) and _valid_price(d.get("previous_close")):
            prev = float(d["previous_close"])
            change_pct = ((float(price) - prev) / prev) * 100 if prev else None

    return {
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "source": source if _valid_price(price) else "unavailable",
        "is_extended_hours": bool(d.get("is_extended_hours")) or _valid_price(extended_price),
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
