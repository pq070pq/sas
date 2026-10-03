import asyncio
import logging
import httpx
from .config import settings
from .twelve_guard import call as twelve_call

logger = logging.getLogger(__name__)

FINNHUB_SYMBOLS = {
    "SPX": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
    "BTC/USD": "BINANCE:BTCUSDT",
    "XAU/USD": "OANDA:XAU_USD",
    "VIX": "CBOE:VIX",
    "WTI/USD": "OANDA:WTICO_USD",
}

FMP_SYMBOLS = {
    "SPX": ["^GSPC", "SPY"],
    "IXIC": ["^IXIC", "QQQ"],
    "DJI": ["^DJI", "DIA"],
    "XAU/USD": ["GCUSD", "GLD"],
    "VIX": ["^VIX", "VIXY"],
    "WTI/USD": ["CLUSD", "USO"],
}

async def _finnhub_quote(symbol: str):
    if not settings.finnhub_api_key:
        return None
    mapped = FINNHUB_SYMBOLS.get(symbol, symbol)
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": mapped, "token": settings.finnhub_api_key},
        )
        r.raise_for_status()
        d = r.json()
    price = d.get("c")
    if not _valid_price(price):
        return None
    return {
        "symbol": symbol,
        "price": price,
        "change_pct": d.get("dp"),
        "source": "Finnhub fallback",
        "is_extended_hours": False,
        "datetime": d.get("t"),
    }


async def _fmp_quote(symbol: str):
    """Reliable macro fallback that does not consume Twelve Data credits."""
    keys = [
        item.strip()
        for item in str(getattr(settings, "fmp_api_keys", "") or "").split(",")
        if item.strip()
    ]
    if getattr(settings, "fmp_api_key", ""):
        keys.insert(0, settings.fmp_api_key.strip())
    # Preserve order while removing duplicate keys.
    keys = list(dict.fromkeys(keys))
    if not keys or symbol not in FMP_SYMBOLS:
        return None

    mapped_symbols = FMP_SYMBOLS[symbol]
    if isinstance(mapped_symbols, str):
        mapped_symbols = [mapped_symbols]
    async with httpx.AsyncClient(timeout=10) as c:
        for key in keys:
            for mapped in mapped_symbols:
                try:
                    r = await c.get(
                        "https://financialmodelingprep.com/stable/quote",
                        params={"symbol": mapped, "apikey": key},
                    )
                    if r.status_code >= 400:
                        continue
                    data = r.json()
                    row = data[0] if isinstance(data, list) and data else None
                    if not isinstance(row, dict):
                        continue
                    price = row.get("price")
                    if not _valid_price(price):
                        continue
                    change_pct = row.get("changePercentage")
                    if change_pct is None and _valid_price(row.get("previousClose")):
                        prev = float(row["previousClose"])
                        change_pct = ((float(price) - prev) / prev) * 100 if prev else None
                    return {
                        "symbol": symbol,
                        "price": float(price),
                        "change_pct": change_pct,
                        "source": "FMP" if mapped == mapped_symbols[0] else f"FMP Proxy ({mapped})",
                        "is_extended_hours": False,
                        "datetime": row.get("timestamp"),
                    }
                except Exception:
                    continue
    return None


def _valid_price(value):
    try:
        return value is not None and float(value) > 0
    except Exception:
        return False


async def _twelve_last_close(symbol: str):
    """Return the latest two daily closes for macro assets when quote endpoints are empty."""
    if not settings.twelve_data_api_key:
        return None
    async with httpx.AsyncClient(timeout=12) as c:
        r = await twelve_call(
            c.get,
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": symbol,
                "interval": "1day",
                "outputsize": 2,
                "apikey": settings.twelve_data_api_key,
            },
        )
        if r.status_code >= 400:
            return None
        data = r.json()
        values = data.get("values") or []
        if not values:
            return None

        latest = values[0]
        price = latest.get("close")
        if not _valid_price(price):
            return None

        change_pct = None
        if len(values) > 1 and _valid_price(values[1].get("close")):
            previous = float(values[1]["close"])
            change_pct = ((float(price) - previous) / previous) * 100 if previous else None

        return {
            "symbol": symbol,
            "price": float(price),
            "change_pct": change_pct,
            "source": "Twelve Data Last Close",
            "is_extended_hours": False,
            "datetime": latest.get("datetime"),
        }


async def macro_quote(symbol: str):
    """Holiday/macro quote with independent fallbacks and automatic diagnostics."""
    diagnostics = []

    try:
        fallback = await _fmp_quote(symbol)
        if fallback:
            return fallback
        diagnostics.append("FMP:no_data")
    except Exception as exc:
        diagnostics.append(f"FMP:{type(exc).__name__}")

    try:
        fallback = await _finnhub_quote(symbol)
        if fallback:
            return fallback
        diagnostics.append("Finnhub:no_data")
    except Exception as exc:
        diagnostics.append(f"Finnhub:{type(exc).__name__}")

    try:
        item = await quote(symbol)
        if _valid_price(item.get("price")):
            return item
        diagnostics.append(f"TwelveQuote:{item.get('source', 'no_data')}")
    except Exception as exc:
        diagnostics.append(f"TwelveQuote:{type(exc).__name__}")

    try:
        last_close = await _twelve_last_close(symbol)
        if last_close:
            return last_close
        diagnostics.append("TwelveLastClose:no_data")
    except Exception as exc:
        diagnostics.append(f"TwelveLastClose:{type(exc).__name__}")

    logger.warning("HOLIDAY_RADAR_PRICE_FAILED symbol=%s diagnostics=%s", symbol, " | ".join(diagnostics))
    return {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "source": "unavailable",
        "diagnostic": " | ".join(diagnostics),
        "is_extended_hours": False,
    }

async def quote(symbol: str):
    # Prefer Finnhub for live quote polling so Twelve Data credits are reserved
    # for the limited candle/intraday analysis budget.
    fallback = await _finnhub_quote(symbol)
    if fallback:
        return fallback
    if not settings.twelve_data_api_key:
        return {"symbol": symbol, "price": None, "change_pct": None, "source": "not_configured"}

    async with httpx.AsyncClient(timeout=12) as c:
        params = {
            "symbol": symbol,
            "apikey": settings.twelve_data_api_key,
            # يدعم بيانات قبل/بعد السوق في خطط Twelve Data التي توفر Extended Hours.
            "prepost": "true",
        }
        r = await twelve_call(c.get, "https://api.twelvedata.com/quote", params=params)
        if r.status_code == 429:
            fallback = await _finnhub_quote(symbol)
            if fallback:
                return fallback
            r.raise_for_status()
        r.raise_for_status()
        d = r.json()

        # إذا لم تكن بيانات Extended Hours متاحة على الخطة، نرجع تلقائيًا
        # إلى السعر العادي بدل تعطيل الرادار بالكامل.
        if d.get("status") == "error":
            fallback = await twelve_call(c.get, "https://api.twelvedata.com/quote",
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
            price_r = await twelve_call(c.get, "https://api.twelvedata.com/price",
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
            ts_r = await twelve_call(c.get, "https://api.twelvedata.com/time_series",
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
            # المؤشرات/الذهب تستخدم مسار macro_quote المستقل عن حصة Twelve Data.
            # هذا يمنع ظهور 0.00 أو فراغ عندما تكون الحصة محمية.
            item = await macro_quote(symbol) if symbol in {"SPX", "IXIC", "DJI", "XAU/USD", "VIX", "WTI/USD"} else await quote(symbol)
            item["label"] = label
            if not _valid_price(item.get("price")):
                item["price"] = None
            return item
        except Exception as exc:
            logger.warning("MARKET_TICKER_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
            return {
                "symbol": symbol, "label": label,
                "price": None, "change_pct": None, "source": "unavailable",
            }

    return await asyncio.gather(*(one(symbol, label) for symbol, label in symbols))
