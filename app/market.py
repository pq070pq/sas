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
    """Finnhub quote with independent-key rotation and provider failover."""
    keys = [
        x.strip()
        for x in str(getattr(settings, "finnhub_api_keys", "") or "").replace("\n", ",").replace(";", ",").split(",")
        if x.strip()
    ]
    if getattr(settings, "finnhub_api_key", ""):
        keys.insert(0, settings.finnhub_api_key.strip())
    if not keys:
        return None
    mapped = FINNHUB_SYMBOLS.get(symbol, symbol)
    # Use the shared pool so a bad/rate-limited key is cooled down and the next
    # independently provisioned key is tried automatically.
    from .key_pool import KeyPool
    pool = getattr(_finnhub_quote, "_pool", None)
    if pool is None:
        pool = KeyPool(dict.fromkeys(keys), settings.api_key_cooldown_seconds)
        setattr(_finnhub_quote, "_pool", pool)
    async with httpx.AsyncClient(timeout=8) as client:
        for _ in range(max(1, pool.size)):
            key = await pool.acquire()
            if not key:
                break
            try:
                response = await client.get(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": mapped, "token": key},
                )
                if response.status_code in (401, 403, 429):
                    retry = response.headers.get("Retry-After")
                    await pool.mark_failure(key, retry_after=int(retry) if retry and retry.isdigit() else None)
                    continue
                response.raise_for_status()
                data = response.json()
                price = data.get("c")
                if not _valid_price(price):
                    await pool.mark_failure(key)
                    continue
                await pool.mark_success(key)
                return {
                    "symbol": symbol,
                    "price": float(price),
                    "change_pct": data.get("dp"),
                    "source": "Finnhub",
                    "is_extended_hours": False,
                    "datetime": data.get("t"),
                }
            except Exception:
                await pool.mark_failure(key)
    return None


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


_FRED_INDEX_MAP = {
    "SPX": "SP500",
    "IXIC": "NASDAQCOM",
    "DJI": "DJIA",
}
_FRED_CACHE = {}


async def _fred_index_quote(symbol: str):
    """Keyless daily-close fallback for US indices.

    Used only after the configured market-data providers fail. FRED carries
    daily closes for S&P 500, Nasdaq Composite and Dow Jones Industrial
    Average, so a weekend/holiday message can still show the last real close.
    """
    series = _FRED_INDEX_MAP.get(symbol)
    if not series:
        return None

    cached = _FRED_CACHE.get(series)
    if cached:
        cached_at, cached_item = cached
        if (asyncio.get_running_loop().time() - cached_at) < 900:
            return dict(cached_item)

    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params={"id": series},
                headers={"User-Agent": "SAS-PRO/2.1"},
            )
            r.raise_for_status()

        rows = list(csv.DictReader(io.StringIO(r.text)))
        values = []
        for row in rows:
            try:
                value = float(row.get(series, ""))
                if value > 0:
                    values.append((row.get("DATE"), value))
            except (TypeError, ValueError):
                continue

        if not values:
            return None

        date, price = values[-1]
        previous = values[-2][1] if len(values) > 1 else None
        change_pct = ((price - previous) / previous) * 100 if previous else None
        item = {
            "symbol": symbol,
            "price": float(price),
            "change_pct": change_pct,
            "source": "FRED Last Close",
            "is_extended_hours": False,
            "datetime": date,
        }
        _FRED_CACHE[series] = (asyncio.get_running_loop().time(), dict(item))
        return item
    except Exception as exc:
        logger.warning("FRED_INDEX_FAILED symbol=%s series=%s error=%s", symbol, series, type(exc).__name__)
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

async def _yahoo_index_quote(symbol: str):
    """Last-resort US index quote during an active trading session only.

    Disabled on weekends and US market holidays. Used only after configured
    market-data providers fail.
    """
    if symbol not in {"SPX", "IXIC", "DJI"}:
        return None
    try:
        from .market_calendar import market_status
        status = market_status()
        if status.get("holiday") or status.get("session") not in {"premarket", "main", "afterhours"}:
            return None
    except Exception:
        return None

    mapped = {"SPX": "^GSPC", "IXIC": "^IXIC", "DJI": "^DJI"}[symbol]
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/" + mapped,
                params={"range": "1d", "interval": "1m", "includePrePost": "true"},
                headers={"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"},
            )
            r.raise_for_status()
            payload = r.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            meta = (result or {}).get("meta") or {}
            price = meta.get("regularMarketPrice")
            previous = meta.get("previousClose")
            if not _valid_price(price):
                closes = (((result or {}).get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                valid = [x for x in closes if _valid_price(x)]
                price = valid[-1] if valid else None
            if not _valid_price(price):
                return None
            change_pct = None
            if _valid_price(previous) and float(previous) != 0:
                change_pct = (float(price) - float(previous)) / float(previous) * 100
            return {
                "symbol": symbol,
                "price": float(price),
                "change_pct": change_pct,
                "source": "Yahoo Chart Fallback",
                "is_extended_hours": False,
                "datetime": meta.get("regularMarketTime"),
            }
    except Exception as exc:
        logger.info("YAHOO_INDEX_FALLBACK_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
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

    # Active-session fallback only; never used on weekends/US holidays.
    try:
        fallback = await _yahoo_index_quote(symbol)
        if fallback:
            return fallback
        diagnostics.append("YahooIndex:no_data")
    except Exception as exc:
        diagnostics.append(f"YahooIndex:{type(exc).__name__}")

    # Final keyless fallback for the three cash indices. This is intentionally
    # after all configured providers so it cannot mask a live/provider quote.
    try:
        fred = await _fred_index_quote(symbol)
        if fred:
            return fred
        diagnostics.append("FRED:no_data")
    except Exception as exc:
        diagnostics.append(f"FRED:{type(exc).__name__}")

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
    """Return a real quote using rotating keys and independent provider fallbacks."""
    if prefer_extended and (settings.twelve_data_api_key or settings.twelve_data_api_keys):
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await twelve_call(
                    client.get,
                    "https://api.twelvedata.com/quote",
                    params={"symbol": symbol, "prepost": "true"},
                )
                r.raise_for_status()
                d = r.json()
                extended_price = d.get("extended_price")
                if _valid_price(extended_price):
                    return {
                        "symbol": symbol,
                        "price": float(extended_price),
                        "change_pct": d.get("extended_percent_change", d.get("percent_change")),
                        "source": "Twelve Data Extended Hours",
                        "is_extended_hours": True,
                        "datetime": d.get("datetime"),
                    }
        except Exception as exc:
            logger.info("EXTENDED_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

    # 1) Finnhub: rotate all independently configured keys.
    fallback = await _finnhub_quote(symbol)
    if fallback:
        return fallback

    # 2) Twelve Data: twelve_guard rotates its own key pool.
    if settings.twelve_data_api_key or settings.twelve_data_api_keys:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await twelve_call(
                    client.get,
                    "https://api.twelvedata.com/quote",
                    params={"symbol": symbol, "prepost": "true"},
                )
                r.raise_for_status()
                d = r.json()

                price = d.get("extended_price") if _valid_price(d.get("extended_price")) else d.get("close")
                if not _valid_price(price):
                    price = d.get("price")

                source = "Twelve Data Extended Hours" if _valid_price(d.get("extended_price")) else "Twelve Data"
                change_pct = d.get("extended_percent_change")
                if change_pct is None:
                    change_pct = d.get("percent_change")

                if not _valid_price(price):
                    price_r = await twelve_call(
                        client.get,
                        "https://api.twelvedata.com/price",
                        params={"symbol": symbol, "prepost": "true"},
                    )
                    price_r.raise_for_status()
                    pd = price_r.json()
                    price = pd.get("price")
                    source = "Twelve Data Price"

                if _valid_price(price):
                    return {
                        "symbol": symbol,
                        "price": float(price),
                        "change_pct": change_pct,
                        "source": source,
                        "is_extended_hours": _valid_price(d.get("extended_price")),
                        "datetime": d.get("datetime"),
                    }
        except Exception as exc:
            logger.warning("TWELVE_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

    # 3) Keyless last-close fallback. A provider outage must not kill private analysis.
    stooq = await _stooq_quote(symbol)
    if stooq:
        return stooq
    return {"symbol": symbol, "price": None, "change_pct": None, "source": "unavailable"}


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
