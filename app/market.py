import asyncio
import csv
import io
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
}

FMP_SYMBOLS = {
    "SPX": ["^GSPC", "SPY"],
    "IXIC": ["^IXIC", "QQQ"],
    "DJI": ["^DJI", "DIA"],
    "XAU/USD": ["GCUSD", "GLD"],
    "VIX": ["^VIX", "VIXY"],
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


async def _fmp_index_quote(symbol: str):
    """Use FMP's dedicated index quote endpoint for the three US benchmarks."""
    keys = [x.strip() for x in str(getattr(settings, "fmp_api_keys", "") or "").split(",") if x.strip()]
    if getattr(settings, "fmp_api_key", ""):
        keys.insert(0, settings.fmp_api_key.strip())
    keys = list(dict.fromkeys(keys))
    mapped = {"SPX": "^GSPC", "IXIC": "^IXIC", "DJI": "^DJI"}.get(symbol)
    if not keys or not mapped:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        for key in keys:
            try:
                r = await client.get(
                    "https://financialmodelingprep.com/stable/quote",
                    params={"symbol": mapped, "apikey": key},
                )
                if r.status_code >= 400:
                    continue
                data = r.json()
                row = data[0] if isinstance(data, list) and data else None
                if not isinstance(row, dict) or not _valid_price(row.get("price")):
                    continue
                return {
                    "symbol": symbol,
                    "price": float(row["price"]),
                    "change_pct": row.get("changePercentage"),
                    "source": "FMP Index",
                    "is_extended_hours": False,
                    "datetime": row.get("timestamp"),
                }
            except Exception:
                continue
    return None


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


async def _stooq_quote(symbol: str):
    """Keyless fallback for macro prices.

    Stooq exposes two useful CSV endpoints. Some environments return an empty
    snapshot from q/l, while the daily endpoint still works, so we try both.
    """
    mapped = {
        "SPX": "^spx",
        "IXIC": "^ndq",
        "DJI": "^dji",
        "VIX": "^vix",
        "XAU/USD": "xauusd",
        "BTC/USD": "btcusd",
    }.get(symbol, f"{str(symbol).lower()}.us")
    if not mapped:
        return None

    headers = {"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            # 1) Intraday/latest snapshot.
            r = await client.get(
                "https://stooq.com/q/l/",
                params={"s": mapped, "f": "sd2t2ohlcvn", "e": "csv"},
                headers=headers,
            )
            if r.status_code < 400:
                rows = list(csv.DictReader(io.StringIO(r.text)))
                row = rows[0] if rows else None
                if isinstance(row, dict) and _valid_price(row.get("Close")):
                    price = float(row["Close"])
                    return {
                        "symbol": symbol,
                        "price": price,
                        "change_pct": None,
                        "source": "Stooq Snapshot",
                        "is_extended_hours": False,
                        "datetime": f"{row.get('Date', '')} {row.get('Time', '')}".strip(),
                    }

            # 2) Daily history. Calculate the change from the last two closes.
            r = await client.get(
                "https://stooq.com/q/d/l/",
                params={"s": mapped, "i": "d"},
                headers=headers,
            )
            if r.status_code >= 400:
                return None
            rows = list(csv.DictReader(io.StringIO(r.text)))
            valid = [x for x in rows if isinstance(x, dict) and _valid_price(x.get("Close"))]
            if not valid:
                return None
            latest = valid[-1]
            previous = valid[-2] if len(valid) > 1 else None
            price = float(latest["Close"])
            change_pct = None
            if previous and _valid_price(previous.get("Close")):
                prev = float(previous["Close"])
                change_pct = ((price - prev) / prev) * 100 if prev else None
            return {
                "symbol": symbol,
                "price": price,
                "change_pct": change_pct,
                "source": "Stooq Last Close",
                "is_extended_hours": False,
                "datetime": latest.get("Date"),
            }
    except Exception as exc:
        logger.warning("STOOQ_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
        return None


async def _public_macro_quote(symbol: str):
    """Public no-key fallbacks for BTC and gold.

    Each provider is isolated so a network failure in one gold source does not
    prevent the next provider from being tried.
    """
    headers = {"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"}

    if symbol == "BTC/USD":
        try:
            async with httpx.AsyncClient(
                timeout=6, follow_redirects=True, headers=headers
            ) as client:
                r = await client.get(
                    "https://data-api.binance.vision/api/v3/ticker/24hr",
                    params={"symbol": "BTCUSDT"},
                )
                if r.status_code < 400:
                    d = r.json()
                    if _valid_price(d.get("lastPrice")):
                        return {
                            "symbol": symbol,
                            "price": float(d["lastPrice"]),
                            "change_pct": d.get("priceChangePercent"),
                            "source": "Binance Public",
                            "is_extended_hours": False,
                            "datetime": d.get("closeTime"),
                        }
        except Exception as exc:
            logger.warning(
                "PUBLIC_MACRO_SOURCE_FAILED symbol=%s source=Binance error=%s",
                symbol,
                type(exc).__name__,
            )
        return None

    if symbol != "XAU/USD":
        return None

    # Gold source 1: XAUS public API (keyless, explicit freshness state).
    try:
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=True, headers=headers
        ) as client:
            r = await client.get("https://xaus.com/api/v1/spot?currency=USD")
            if r.status_code < 400:
                d = r.json()
                price = d.get("spot_usd_oz")
                if not _valid_price(price):
                    xau = d.get("xau") or {}
                    price = xau.get("price")
                if _valid_price(price):
                    state = d.get("data_state") or {}
                    freshness = str(state.get("status") or "").strip().lower()
                    source = "XAUS Public"
                    if freshness == "stale":
                        source += " (stale last real price)"
                    elif freshness:
                        source += f" ({freshness})"
                    return {
                        "symbol": symbol,
                        "price": float(price),
                        "change_pct": None,
                        "source": source,
                        "is_extended_hours": False,
                        "datetime": d.get("updated_at") or state.get("as_of"),
                    }
    except Exception as exc:
        logger.warning(
            "PUBLIC_MACRO_SOURCE_FAILED symbol=%s source=XAUS error=%s",
            symbol,
            type(exc).__name__,
        )

    # Gold source 1: Metals.live.
    try:
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=True, headers=headers
        ) as client:
            r = await client.get("https://api.metals.live/v1/spot")
            if r.status_code < 400:
                data = r.json()
                rows = data if isinstance(data, list) else []
                for row in rows:
                    if isinstance(row, dict) and _valid_price(row.get("gold")):
                        return {
                            "symbol": symbol,
                            "price": float(row["gold"]),
                            "change_pct": None,
                            "source": "Metals.live Public",
                            "is_extended_hours": False,
                            "datetime": (
                                rows[-1].get("timestamp")
                                if rows and isinstance(rows[-1], dict)
                                else None
                            ),
                        }
                    if (
                        isinstance(row, dict)
                        and str(row.get("metal", "")).lower() == "gold"
                        and _valid_price(row.get("price"))
                    ):
                        return {
                            "symbol": symbol,
                            "price": float(row["price"]),
                            "change_pct": None,
                            "source": "Metals.live Public",
                            "is_extended_hours": False,
                            "datetime": row.get("timestamp"),
                        }
    except Exception as exc:
        logger.warning(
            "PUBLIC_MACRO_SOURCE_FAILED symbol=%s source=Metals.live error=%s",
            symbol,
            type(exc).__name__,
        )

    # Gold source 2: GoldPrice.org.
    try:
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=True, headers=headers
        ) as client:
            r = await client.get("https://data-asg.goldprice.org/dbXRates/USD")
            if r.status_code < 400:
                d = r.json()
                items = d.get("items") or []
                if items and _valid_price(items[0].get("xauPrice")):
                    item = items[0]
                    return {
                        "symbol": symbol,
                        "price": float(item["xauPrice"]),
                        "change_pct": item.get("pcXau"),
                        "source": "GoldPrice.org Public",
                        "is_extended_hours": False,
                        "datetime": d.get("ts"),
                    }
    except Exception as exc:
        logger.warning(
            "PUBLIC_MACRO_SOURCE_FAILED symbol=%s source=GoldPrice.org error=%s",
            symbol,
            type(exc).__name__,
        )

    return None

async def macro_quote(symbol: str):
    """Holiday/macro quote with independent fallbacks and automatic diagnostics."""
    diagnostics = []

    if symbol in {"BTC/USD", "XAU/USD"}:
        try:
            fallback = await _public_macro_quote(symbol)
            if fallback:
                return fallback
            diagnostics.append("Public:no_data")
        except Exception as exc:
            diagnostics.append(f"Public:{type(exc).__name__}")

    try:
        fallback = await _fmp_index_quote(symbol) if symbol in {"SPX", "IXIC", "DJI"} else None
        if fallback:
            return fallback
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

    try:
        snapshot = await _stooq_quote(symbol)
        if snapshot:
            return snapshot
        diagnostics.append("Stooq:no_data")
    except Exception as exc:
        diagnostics.append(f"Stooq:{type(exc).__name__}")

    logger.warning("HOLIDAY_RADAR_PRICE_FAILED symbol=%s diagnostics=%s", symbol, " | ".join(diagnostics))
    return {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "source": "unavailable",
        "diagnostic": " | ".join(diagnostics),
        "is_extended_hours": False,
    }

async def quote(symbol: str, prefer_extended: bool = False):
    # During pre/after-hours, prefer a provider response that explicitly marks
    # the quote as extended. A regular-session close must never masquerade as
    # live extended-hours activity.
    if prefer_extended and settings.twelve_data_api_key:
        try:
            async with httpx.AsyncClient(timeout=12) as c:
                params = {
                    "symbol": symbol,
                    "apikey": settings.twelve_data_api_key,
                    "prepost": "true",
                }
                r = await twelve_call(c.get, "https://api.twelvedata.com/quote", params=params)
                if r.status_code != 429:
                    r.raise_for_status()
                    d = r.json()
                    extended_price = d.get("extended_price")
                    if _valid_price(extended_price):
                        change_pct = (
                            d.get("extended_percent_change")
                            if d.get("extended_percent_change") is not None
                            else d.get("percent_change")
                        )
                        return {
                            "symbol": symbol,
                            "price": float(extended_price),
                            "change_pct": change_pct,
                            "source": "Twelve Data Extended Hours",
                            "is_extended_hours": True,
                            "datetime": d.get("datetime"),
                        }
        except Exception as exc:
            logger.info("EXTENDED_QUOTE_PRIMARY_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

    # Normal quote path: Finnhub is preferred to preserve Twelve Data credits.
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


_TICKER_CACHE = {}

async def ticker():
    symbols = [
        ("BTC/USD", "BTC"),
        ("XAU/USD", "GOLD"),
        ("SPX", "S&P 500"),
        ("IXIC", "NASDAQ"),
        ("DJI", "DOW JONES"),
        ("VIX", "VIX"),
    ]

    async def one(symbol, label):
        try:
            # لا نسمح لمصدر واحد بطيء أن يحجب لوحة الإدارة.
            item = await asyncio.wait_for(
                macro_quote(symbol),
                timeout=5.5,
            )
            item["label"] = label
            if _valid_price(item.get("price")):
                _TICKER_CACHE[symbol] = dict(item)
            else:
                cached = _TICKER_CACHE.get(symbol)
                if cached:
                    item = dict(cached)
                    item["source"] = f"{item.get('source', 'مصدر سابق')} — آخر سعر حقيقي محفوظ"
                    item["stale"] = True
            item["price"] = float(item["price"]) if _valid_price(item.get("price")) else None
            return item
        except Exception as exc:
            cached = _TICKER_CACHE.get(symbol)
            if cached:
                item = dict(cached)
                item["label"] = label
                item["source"] = f"{item.get('source', 'مصدر سابق')} — آخر سعر حقيقي محفوظ"
                item["stale"] = True
                return item
            logger.warning("MARKET_TICKER_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
            return {
                "symbol": symbol, "label": label,
                "price": None, "change_pct": None, "source": "unavailable",
                "diagnostic": f"timeout_or_source_error:{type(exc).__name__}",
            }

    # جميع الأصول تُجلب بالتوازي.
    return await asyncio.gather(*(one(symbol, label) for symbol, label in symbols))
