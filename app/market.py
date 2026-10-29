import asyncio
import csv
import io
import logging
import time
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
    "SPX": ["^GSPC"],
    "IXIC": ["^IXIC"],
    "DJI": ["^DJI"],
    # Do not use GLD as a gold spot proxy: its ETF share price is not USD/oz.
    "XAU/USD": ["GCUSD"],
    "VIX": ["^VIX"],
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
    """Fetch benchmark quotes through FMP's dedicated index endpoints.

    Stable stock quote endpoints may return an empty list for index symbols.
    Try the documented batch-index feed first, then legacy index quotes, and
    finally the stable single-symbol quote endpoint. Never substitute an ETF.
    """
    keys = [x.strip() for x in str(getattr(settings, "fmp_api_keys", "") or "").split(",") if x.strip()]
    if getattr(settings, "fmp_api_key", ""):
        keys.insert(0, settings.fmp_api_key.strip())
    keys = list(dict.fromkeys(keys))
    mapped = {"SPX": "^GSPC", "IXIC": "^IXIC", "DJI": "^DJI"}.get(symbol)
    if not keys or not mapped:
        return None

    def matching_row(data):
        rows = data if isinstance(data, list) else (
            data.get("data", []) if isinstance(data, dict) else []
        )
        aliases = {
            "SPX": {"^GSPC", "GSPC", "SP500", "S&P 500", "S&P500"},
            "IXIC": {"^IXIC", "IXIC", "NASDAQCOM", "NASDAQ COMPOSITE"},
            "DJI": {"^DJI", "DJI", "DJIA", "DOW JONES", "DOW JONES INDUSTRIAL AVERAGE"},
        }[symbol]
        for candidate in rows:
            if not isinstance(candidate, dict):
                continue
            candidate_symbol = str(candidate.get("symbol") or candidate.get("name") or "").strip().upper()
            if candidate_symbol not in aliases and str(candidate.get("symbol") or "").strip().upper() != mapped:
                continue
            price = candidate.get("price")
            if not _valid_price(price):
                price = candidate.get("indexValue")
            if not _valid_price(price):
                price = candidate.get("value")
            if _valid_price(price):
                return {
                    "symbol": symbol,
                    "price": float(price),
                    "change_pct": candidate.get("changePercentage", candidate.get("changesPercentage")),
                    "source": "FMP Index",
                    "is_extended_hours": False,
                    "datetime": candidate.get("timestamp") or candidate.get("date"),
                }
        return None

    async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
        for key in keys:
            requests = [
                ("https://financialmodelingprep.com/stable/batch-index-quotes", {"apikey": key}),
                ("https://financialmodelingprep.com/api/v3/quotes/index", {"apikey": key}),
                ("https://financialmodelingprep.com/stable/quote", {"symbol": mapped, "apikey": key}),
            ]
            for url, params in requests:
                try:
                    response = await client.get(url, params=params)
                    if response.status_code >= 400:
                        continue
                    result = matching_row(response.json())
                    if result:
                        return result
                except Exception as exc:
                    logger.info("FMP_INDEX_SOURCE_FAILED symbol=%s endpoint=%s error=%s", symbol, url.rsplit("/", 1)[-1], type(exc).__name__)
    return None


async def _fmp_quote(symbol: str):
    """FMP quote with per-key cooldown and automatic failover."""
    from .key_pool import KeyPool, parse_keys
    keys = parse_keys(getattr(settings, "fmp_api_keys", ""), getattr(settings, "fmp_api_key", ""))
    if not keys:
        return None
    pool = getattr(_fmp_quote, "_pool", None)
    if pool is None or getattr(_fmp_quote, "_pool_keys", None) != tuple(keys):
        pool = KeyPool(keys, settings.api_key_cooldown_seconds)
        setattr(_fmp_quote, "_pool", pool)
        setattr(_fmp_quote, "_pool_keys", tuple(keys))

    mapped_symbols = FMP_SYMBOLS.get(symbol, [symbol])
    if isinstance(mapped_symbols, str):
        mapped_symbols = [mapped_symbols]
    async with httpx.AsyncClient(timeout=8) as c:
        for _ in range(max(1, pool.size)):
            key = await pool.acquire()
            if not key:
                break
            key_failed = False
            failure_marked = False
            for mapped in mapped_symbols:
                try:
                    r = await c.get(
                        "https://financialmodelingprep.com/stable/quote",
                        params={"symbol": mapped, "apikey": key},
                    )
                    if r.status_code in (401, 403, 429):
                        retry = r.headers.get("Retry-After")
                        await pool.mark_failure(key, retry_after=int(retry) if retry and retry.isdigit() else None)
                        failure_marked = True
                        key_failed = True
                        break
                    if r.status_code >= 500:
                        key_failed = True
                        break
                    r.raise_for_status()
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
                    await pool.mark_success(key)
                    return {
                        "symbol": symbol,
                        "price": float(price),
                        "change_pct": change_pct,
                        "source": "FMP" if mapped == mapped_symbols[0] else f"FMP Proxy ({mapped})",
                        "is_extended_hours": False,
                        "datetime": row.get("timestamp"),
                    }
                except Exception as exc:
                    logger.info("FMP_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
                    key_failed = True
                    break
            if key_failed:
                # Cool down only this credential; preserve Retry-After when supplied.
                if not failure_marked:
                    await pool.mark_failure(key)
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
    """Keyless stock quote fallback with a verified daily change when possible.

    The snapshot endpoint does not include change percentage, so keep its price
    and read daily history before returning. Change is calculated only against
    an actual prior close from the same symbol.
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
    snapshot = None
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            # Latest snapshot. Do not return yet: it has no verified change%.
            try:
                r = await client.get(
                    "https://stooq.com/q/l/",
                    params={"s": mapped, "f": "sd2t2ohlcvn", "e": "csv"},
                    headers=headers,
                )
                if r.status_code < 400:
                    rows = list(csv.DictReader(io.StringIO(r.text)))
                    row = rows[0] if rows else None
                    if isinstance(row, dict) and _valid_price(row.get("Close")):
                        snapshot = {
                            "price": float(row["Close"]),
                            "date": str(row.get("Date", "")).strip(),
                            "time": str(row.get("Time", "")).strip(),
                        }
            except Exception as exc:
                logger.info("STOOQ_SNAPSHOT_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

            # Daily history gives the previous close needed for a real change.
            try:
                r = await client.get(
                    "https://stooq.com/q/d/l/",
                    params={"s": mapped, "i": "d"},
                    headers=headers,
                )
                if r.status_code < 400:
                    rows = list(csv.DictReader(io.StringIO(r.text)))
                    valid = [
                        row for row in rows
                        if isinstance(row, dict) and _valid_price(row.get("Close"))
                    ]
                    if valid:
                        latest = valid[-1]
                        latest_date = str(latest.get("Date", "")).strip()
                        snapshot_date = snapshot.get("date", "") if snapshot else ""
                        if snapshot:
                            if snapshot_date and latest_date == snapshot_date:
                                previous = valid[-2] if len(valid) > 1 else None
                            elif snapshot_date and latest_date < snapshot_date:
                                previous = latest
                            elif snapshot_date:
                                before_snapshot = [
                                    row for row in valid
                                    if str(row.get("Date", "")).strip() < snapshot_date
                                ]
                                previous = before_snapshot[-1] if before_snapshot else None
                            else:
                                previous = valid[-2] if len(valid) > 1 else None
                            price = snapshot["price"]
                            timestamp = " ".join(
                                part for part in (snapshot_date, snapshot.get("time", "")) if part
                            )
                        else:
                            price = float(latest["Close"])
                            previous = valid[-2] if len(valid) > 1 else None
                            timestamp = latest_date

                        change_pct = None
                        previous_close = None
                        if previous and _valid_price(previous.get("Close")):
                            previous_close = float(previous["Close"])
                            if previous_close > 0:
                                change_pct = ((price - previous_close) / previous_close) * 100

                        return {
                            "symbol": symbol,
                            "price": price,
                            "change_pct": change_pct,
                            "previous_close": previous_close,
                            "source": (
                                "Stooq Snapshot" if snapshot else "Stooq Last Close"
                            ),
                            # Daily history is not an intraday/live quote. Keep this
                            # explicit so the UI can label it as the last close.
                            "stale": not bool(snapshot),
                            "is_extended_hours": False,
                            "datetime": timestamp,
                        }
            except Exception as exc:
                logger.info("STOOQ_DAILY_HISTORY_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

            # Snapshot-only quote is valid, but its change must remain null.
            if snapshot:
                return {
                    "symbol": symbol,
                    "price": snapshot["price"],
                    "change_pct": None,
                    "source": "Stooq Snapshot",
                    "is_extended_hours": False,
                    "datetime": " ".join(
                        part for part in (snapshot["date"], snapshot["time"]) if part
                    ),
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
    """Return market indicators with bounded provider timeouts and honest freshness labels."""
    diagnostics = []

    async def attempt(name, factory, timeout=5.0):
        try:
            result = await asyncio.wait_for(factory(), timeout=timeout)
            if result and _valid_price(result.get("price")):
                return result
            diagnostics.append(f"{name}:no_data")
        except Exception as exc:
            diagnostics.append(f"{name}:{type(exc).__name__}")
        return None

    # Keyless crypto/gold sources have been verified to return fresh data in production.
    if symbol in {"BTC/USD", "XAU/USD"}:
        result = await attempt("Public", lambda: _public_macro_quote(symbol), timeout=8.0)
        if result:
            return result

    # VIX had been timing out because several slow providers ran sequentially.
    # Try its keyless index quote first, then bounded paid-provider fallbacks.
    if symbol == "VIX":
        result = await attempt("Stooq", lambda: _stooq_quote(symbol), timeout=8.0)
        if result:
            return result
        result = await attempt("FMP", lambda: _fmp_quote(symbol), timeout=5.0)
        if result:
            return result
        result = await attempt("Finnhub", lambda: _finnhub_quote(symbol), timeout=5.0)
        if result:
            return result

    # Configured providers first for the three US benchmarks, but each attempt
    # is bounded so a long API-key rotation cannot starve the last-close fallback.
    if symbol in {"SPX", "IXIC", "DJI"}:
        result = await attempt("FMPIndex", lambda: _fmp_index_quote(symbol), timeout=5.0)
        if result:
            return result
        result = await attempt("FMP", lambda: _fmp_quote(symbol), timeout=5.0)
        if result:
            return result
        result = await attempt("Finnhub", lambda: _finnhub_quote(symbol), timeout=5.0)
        if result:
            return result
        # A real, verified daily close is preferable to a blank card or a fake live quote.
        result = await attempt("FRED", lambda: _fred_index_quote(symbol), timeout=5.0)
        if result:
            result["stale"] = True
            result["source"] = str(result.get("source") or "FRED Last Close") + " — آخر إغلاق موثق"
            return result
        result = await attempt("Stooq", lambda: _stooq_quote(symbol), timeout=8.0)
        if result:
            return result

    # General fallback path for any other macro symbols.
    result = await attempt("FMP", lambda: _fmp_quote(symbol), timeout=5.0)
    if result:
        return result
    result = await attempt("Finnhub", lambda: _finnhub_quote(symbol), timeout=5.0)
    if result:
        return result
    result = await attempt("Stooq", lambda: _stooq_quote(symbol), timeout=8.0)
    if result:
        return result

    try:
        item = await asyncio.wait_for(quote(symbol), timeout=5.0)
        if _valid_price(item.get("price")):
            return item
        diagnostics.append(f"Quote:{item.get('source', 'no_data')}")
    except Exception as exc:
        diagnostics.append(f"Quote:{type(exc).__name__}")

    try:
        last_close = await asyncio.wait_for(_twelve_last_close(symbol), timeout=5.0)
        if last_close:
            last_close["stale"] = True
            return last_close
        diagnostics.append("TwelveLastClose:no_data")
    except Exception as exc:
        diagnostics.append(f"TwelveLastClose:{type(exc).__name__}")

    logger.warning("MARKET_INDICATOR_PRICE_FAILED symbol=%s diagnostics=%s", symbol, " | ".join(diagnostics))
    return {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "source": "unavailable",
        "diagnostic": " | ".join(diagnostics),
        "is_extended_hours": False,
    }

async def _panwatch_extended_quote(symbol: str, session: str | None, now=None) -> dict | None:
    """Return a fresh, session-bounded quote from observed PanWatch 1-minute bars.

    This is a fallback only when the quote API does not expose a distinct
    extended-hours price. Historical/daily candles are never promoted to live.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    if session not in {"premarket", "afterhours", "night"}:
        return None

    now = now or datetime.now(ZoneInfo("America/New_York"))
    if session == "premarket":
        start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        end = now.replace(hour=9, minute=30, second=0, microsecond=0)
    elif session == "afterhours":
        start = now.replace(hour=16, minute=0, second=0, microsecond=0)
        end = now.replace(hour=20, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if now.hour < 4:
            start -= timedelta(days=1)
        end = now

    if now < start or now > end:
        return None

    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            result = await client.get(
                f"{settings.panwatch_base_url.rstrip('/')}/api/klines/{symbol.upper()}",
                params={"market": "US", "days": 2, "interval": "1min"},
            )
            result.raise_for_status()
            payload = result.json()

        data = payload.get("data") or payload if isinstance(payload, dict) else payload
        rows = data
        if isinstance(data, dict):
            rows = next(
                (data.get(key) for key in ("klines", "candles", "values", "data", "results")
                 if isinstance(data.get(key), list)),
                [],
            )

        bars = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            lower = {str(k).strip().lower(): v for k, v in row.items()}
            raw_stamp = next(
                (lower.get(key) for key in ("datetime", "date", "time", "timestamp", "t")
                 if lower.get(key) not in (None, "")),
                None,
            )
            try:
                if isinstance(raw_stamp, (int, float)):
                    stamp_num = float(raw_stamp)
                    if stamp_num > 1e12:
                        stamp_num /= 1000
                    from datetime import timezone
                    stamp = datetime.fromtimestamp(stamp_num, timezone.utc).astimezone(ZoneInfo("America/New_York"))
                else:
                    stamp_text = str(raw_stamp or "").strip()
                    if not stamp_text:
                        continue
                    stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York"))
                    else:
                        stamp = stamp.astimezone(ZoneInfo("America/New_York"))
                op = float(lower.get("open", lower.get("o")))
                close = float(lower.get("close", lower.get("c")))
                volume = float(lower.get("volume", lower.get("v", 0)) or 0)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            if start <= stamp <= min(end, now) and op > 0 and close > 0 and volume >= 0:
                bars.append((stamp, op, close, volume))

        bars.sort(key=lambda item: item[0])
        if not bars or (now - bars[-1][0]).total_seconds() > 20 * 60:
            return None
        session_open = bars[0][1]
        last_price = bars[-1][2]
        session_volume = sum(item[3] for item in bars)
        if session_open <= 0 or last_price <= 0 or session_volume <= 0:
            return None
        return {
            "symbol": symbol.upper(),
            "price": last_price,
            "change_pct": ((last_price / session_open) - 1.0) * 100.0,
            "source": "PanWatch Extended 1m",
            "is_extended_hours": True,
            "is_overnight_hours": session == "night",
            "session_volume": session_volume,
            "datetime": bars[-1][0].isoformat(),
        }
    except Exception as exc:
        logger.info("PANWATCH_EXTENDED_QUOTE_FAILED symbol=%s session=%s error=%s", symbol, session, type(exc).__name__)
        return None


async def quote(symbol: str, prefer_extended: bool = False, session: str | None = None):
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

    if prefer_extended:
        panwatch_quote = await _panwatch_extended_quote(symbol, session)
        if panwatch_quote:
            return panwatch_quote

    # Prefer FMP's direct equity quote before Finnhub because the current
    # session can advance while a provider's last quote remains behind.
    # Both are existing configured sources; this does not consume Twelve Data credits.
    fmp = await _fmp_quote(symbol)
    if fmp:
        return fmp

    # Finnhub: rotate all independently configured keys if FMP has no valid quote.
    fallback = await _finnhub_quote(symbol)
    if fallback:
        return fallback

    # 3) Twelve Data: twelve_guard rotates its own key pool.
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

                # During the regular session, never promote an extended-hours
                # quote to the live price. Extended price is only valid when the
                # caller explicitly requested an extended-session quote.
                if prefer_extended and _valid_price(d.get("extended_price")):
                    price = d.get("extended_price")
                    source = "Twelve Data Extended Hours"
                    change_pct = d.get("extended_percent_change")
                else:
                    price = d.get("price")
                    source = "Twelve Data"
                    change_pct = d.get("percent_change")
                    if not _valid_price(price):
                        price = d.get("close")
                        source = "Twelve Data Last Quote"
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
                        "is_extended_hours": bool(prefer_extended and _valid_price(d.get("extended_price"))),
                        "datetime": d.get("datetime"),
                    }
        except Exception as exc:
            logger.warning("TWELVE_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

    # 4) Keyless Stooq fallback before the scraped CUSP page. Stooq can provide
    # a timestamped snapshot and a daily change derived from an actual prior close.
    stooq = await _stooq_quote(symbol)
    if stooq:
        return stooq

    # 5) CUSP is a last-resort published page value, not a guaranteed live quote.
    # The UI marks it as delayed; its change stays null unless the page publishes
    # a current price and previous close from which a real percentage can be derived.
    try:
        from .cuspwealth_provider import get_cusp_asset
        cusp = await get_cusp_asset(symbol)
        if isinstance(cusp, dict) and _valid_price(cusp.get("price")):
            return {
                "symbol": symbol,
                "price": float(cusp["price"]),
                "change_pct": cusp.get("change_pct"),
                "source": "CUSP Wealth",
                "is_delayed": True,
                "is_extended_hours": False,
                "market_status": cusp.get("market_status"),
                "datetime": cusp.get("data_timestamp"),
                "cusp_metrics": cusp.get("metrics", {}),
                "source_url": cusp.get("source_url"),
            }
    except Exception as exc:
        logger.info("CUSP_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

    logger.warning(
        "QUOTE_UNAVAILABLE symbol=%s attempted_sources=FMP,Finnhub,TwelveData,Stooq,CUSP",
        symbol,
    )
    return {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "source": "unavailable",
        "diagnostic": "no_provider_returned_a_valid_price",
    }


_TICKER_CACHE = {}
_TICKER_REFRESH_TASK = None
_TICKER_SYMBOLS = [
    ("BTC/USD", "BTC"),
    ("XAU/USD", "GOLD"),
    ("SPX", "S&P 500"),
    ("IXIC", "NASDAQ"),
    ("DJI", "DOW JONES"),
]


async def _refresh_ticker_cache():
    async def one(symbol, label):
        # Seed index cards quickly from a real last-close source while slower
        # live providers are queried. This prevents blank cards during cold start.
        if symbol in {"SPX", "IXIC", "DJI"} and not _valid_price((_TICKER_CACHE.get(symbol) or {}).get("price")):
            try:
                seed = await asyncio.wait_for(_fred_index_quote(symbol), timeout=9.0)
                if seed and _valid_price(seed.get("price")):
                    seed["label"] = label
                    seed["stale"] = True
                    seed["source"] = str(seed.get("source") or "FRED Last Close") + " — آخر إغلاق موثق"
                    _TICKER_CACHE[symbol] = dict(seed)
            except Exception as exc:
                logger.info("MARKET_INDEX_SEED_FAILED symbol=%s error=%s", symbol, type(exc).__name__)

        try:
            item = await asyncio.wait_for(macro_quote(symbol), timeout=35)
            item["label"] = label
            if _valid_price(item.get("price")):
                item["price"] = float(item["price"])
                _TICKER_CACHE[symbol] = dict(item)
                return item
            cached = _TICKER_CACHE.get(symbol)
            if cached:
                stale = dict(cached)
                stale["label"] = label
                stale["source"] = f"{stale.get('source', 'مصدر سابق')} — آخر سعر حقيقي محفوظ"
                stale["stale"] = True
                return stale
            return {
                "symbol": symbol, "label": label, "price": None,
                "change_pct": None, "source": "unavailable",
                "diagnostic": "provider_returned_no_valid_price",
            }
        except Exception as exc:
            cached = _TICKER_CACHE.get(symbol)
            if cached:
                stale = dict(cached)
                stale["label"] = label
                stale["source"] = f"{stale.get('source', 'مصدر سابق')} — آخر سعر حقيقي محفوظ"
                stale["stale"] = True
                return stale
            logger.warning("MARKET_TICKER_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
            return {
                "symbol": symbol, "label": label, "price": None,
                "change_pct": None, "source": "unavailable",
                "diagnostic": f"timeout_or_source_error:{type(exc).__name__}",
            }

    return await asyncio.gather(*(one(symbol, label) for symbol, label in _TICKER_SYMBOLS))


async def ticker():
    """Return cached macro quotes immediately while refreshing providers in background."""
    global _TICKER_REFRESH_TASK

    def snapshot():
        rows = []
        for symbol, label in _TICKER_SYMBOLS:
            item = _TICKER_CACHE.get(symbol)
            if item and _valid_price(item.get("price")):
                row = dict(item)
                row["label"] = label
                rows.append(row)
            else:
                rows.append({
                    "symbol": symbol, "label": label, "price": None,
                    "change_pct": None, "source": "جارٍ تحديث المصدر",
                    "diagnostic": "quote_refresh_in_progress",
                })
        return rows

    cached_rows = snapshot()
    if _TICKER_REFRESH_TASK is None or _TICKER_REFRESH_TASK.done():
        _TICKER_REFRESH_TASK = asyncio.create_task(_refresh_ticker_cache())

    # Keep this endpoint comfortably below the browser's 15-second request timeout.
    # The full provider chain can take 35 seconds, so wait only briefly and return
    # verified keyless snapshots while that refresh continues in the background.
    if any(not _valid_price(row.get("price")) for row in cached_rows):
        try:
            await asyncio.wait_for(asyncio.shield(_TICKER_REFRESH_TASK), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            logger.warning("MARKET_TICKER_REFRESH_FAILED error=%s", type(exc).__name__)
        cached_rows = snapshot()

    missing_indices = [
        (symbol, label) for symbol, label in _TICKER_SYMBOLS
        if symbol in {"SPX", "IXIC", "DJI"}
        and not _valid_price((_TICKER_CACHE.get(symbol) or {}).get("price"))
    ]
    # Run both no-key providers concurrently. The previous sequential 3-second
    # caps cancelled Stooq/FRED before their own HTTP timeouts, leaving cards blank.
    # Only verified daily closes are returned from this path and are marked stale.
    if missing_indices:
        async def resolve_index(item):
            symbol, label = item
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        _stooq_quote(symbol),
                        _fred_index_quote(symbol),
                        return_exceptions=True,
                    ),
                    timeout=8.0,
                )
            except asyncio.TimeoutError:
                logger.info("MARKET_INDEX_FALLBACK_TIMEOUT symbol=%s", symbol)
                return item, None, None
            stooq, fred = results
            if isinstance(stooq, dict) and _valid_price(stooq.get("price")):
                return item, stooq, "Stooq"
            if isinstance(fred, dict) and _valid_price(fred.get("price")):
                return item, fred, "FRED"
            errors = [type(x).__name__ for x in results if isinstance(x, Exception)]
            if errors:
                logger.info("MARKET_INDEX_FALLBACK_FAILED symbol=%s errors=%s", symbol, ",".join(errors))
            return item, None, None

        resolved = await asyncio.gather(*(resolve_index(item) for item in missing_indices))
        for (symbol, label), item, provider_name in resolved:
            if isinstance(item, dict) and _valid_price(item.get("price")):
                saved = dict(item)
                saved["symbol"] = symbol
                saved["label"] = label
                saved["stale"] = True
                saved["source"] = str(saved.get("source") or provider_name) + " — آخر إغلاق موثق"
                _TICKER_CACHE[symbol] = saved
    cached_rows = snapshot()
    return cached_rows
