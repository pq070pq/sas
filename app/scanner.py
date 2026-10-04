import asyncio
import httpx
import time
import logging

logger = logging.getLogger(__name__)
from .config import settings
from .panwatch import technical_targets
from .news import company_news, select_catalyst, earnings_calendar_window
from .market import quote
from .twelve_guard import call as twelve_call

# رادار SAS PRO:
# - السوق: NASDAQ / NYSE / AMEX
# - السعر: $0.50 - $20.00
# - لا نعتمد على نسبة الارتفاع وحدها؛ نبحث عن حركة مؤكدة أو تجميع قابل للقياس.
# - Twelve Data ليس مصدر الرصد الأساسي ولا يُستهلك أثناء دورة الرادار.
# - استراتيجية SAS: السلوك، التداول، RVOL، الدعم/المقاومة والثبات.
MIN_PRICE = 0.50
MAX_PRICE = 20.00
MAX_RADAR_RESULTS = 30
MAX_SECTION_RESULTS = 15
MIN_DAILY_DOLLAR_VOLUME = 1_000_000.0
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}

MOMENTUM_SMALL_MIN_GAIN = 10.0
MOMENTUM_SMALL_MIN_VOLUME = 500_000.0
MOMENTUM_LARGE_MIN_GAIN = 3.0
MOMENTUM_LARGE_MIN_MARKET_CAP = 1_000_000_000.0
MOMENTUM_RVOL_THRESHOLDS = {
    10: {"small": 0.50, "large": 0.25},
    11: {"small": 0.93, "large": 0.47},
    12: {"small": 1.26, "large": 0.63},
    13: {"small": 1.56, "large": 0.78},
    14: {"small": 1.86, "large": 0.93},
    15: {"small": 2.22, "large": 1.11},
    16: {"small": 3.00, "large": 1.50},
}

# Daily candles change slowly, so cache them between radar cycles.
_CANDLE_CACHE_TTL = 5400
_candle_cache = {}
_panwatch_semaphore = asyncio.Semaphore(8)
_twelvedata_fallback_semaphore = asyncio.Semaphore(1)
_twelve_data_quota_exhausted = False


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_exchange(value):
    exchange = str(value or "").upper().strip()
    exchange = exchange.replace("_", " ").replace("-", " ")
    if exchange in {"NASDAQ", "NMS", "NGM", "NCM"}:
        return "NASDAQ"
    if exchange in {"NYSE", "NYQ", "NYS"}:
        return "NYSE"
    if exchange in {"AMEX", "ASE", "XASE"}:
        return "AMEX"
    return exchange


def _is_allowed_exchange(row):
    exchange = _normalize_exchange(row.get("exchange") or row.get("mic_code"))
    return exchange in ALLOWED_EXCHANGES


def _parse_candles(rows):
    out = []
    for row in rows or []:
        try:
            out.append({
                "open": _f(row["open"]),
                "high": _f(row["high"]),
                "low": _f(row["low"]),
                "close": _f(row["close"]),
                "volume": _f(row.get("volume")),
            })
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _local_levels(candles):
    supports, resistances = [], []
    for i in range(2, len(candles) - 2):
        c = candles[i]
        if c["low"] <= candles[i-1]["low"] and c["low"] <= candles[i-2]["low"] and c["low"] <= candles[i+1]["low"] and c["low"] <= candles[i+2]["low"]:
            supports.append(c["low"])
        if c["high"] >= candles[i-1]["high"] and c["high"] >= candles[i-2]["high"] and c["high"] >= candles[i+1]["high"] and c["high"] >= candles[i+2]["high"]:
            resistances.append(c["high"])
    return supports, resistances


def _near(level, price, pct=0.04):
    return price > 0 and abs(level - price) / price <= pct


def _analyze_breakout(candles, price, rvol, resistances):
    """Evaluate breakout evidence using close, volume, continuation and retest.

    This follows the supplied breakout guide: a brief level touch is not enough;
    confirmation comes from closing above resistance, supportive volume,
    continuation and, when present, a successful retest. The function only uses
    observed OHLCV data and never invents a level.
    """
    last = candles[-1]
    prev = candles[-2]
    broken_levels = [x for x in resistances if x < price]
    breakout_level = max(broken_levels, default=None)
    resistance_above = min([x for x in resistances if x > price], default=None)

    empty = {
        "level": breakout_level,
        "next_resistance": resistance_above,
        "close_above": False,
        "volume_confirmed": False,
        "continuation": False,
        "retest": False,
        "fake": False,
        "confirmed": False,
        "extension_pct": None,
        "room_pct": ((resistance_above / price) - 1) * 100 if resistance_above and price else None,
    }
    if breakout_level is None or price <= 0:
        return empty

    close_above = last["close"] > breakout_level * 1.003
    volume_confirmed = rvol >= 1.5
    continuation = close_above and (
        last["close"] >= prev["close"] or last["close"] >= last["open"]
    )
    retest_touched = any(
        c["low"] <= breakout_level * 1.01 and c["close"] >= breakout_level * 0.995
        for c in candles[-6:-1]
    )
    retest = bool(retest_touched and last["close"] > breakout_level * 1.003)
    fake = bool(
        (last["high"] > breakout_level * 1.005 and last["close"] < breakout_level * 0.995)
        or (prev["high"] > breakout_level * 1.005 and last["close"] < breakout_level * 0.995)
    )
    extension_pct = ((price / breakout_level) - 1) * 100 if breakout_level else None
    confirmed = bool(
        close_above
        and volume_confirmed
        and continuation
        and not fake
        and extension_pct is not None
        and extension_pct <= 8.0
    )

    return {
        "level": breakout_level,
        "next_resistance": resistance_above,
        "close_above": close_above,
        "volume_confirmed": volume_confirmed,
        "continuation": continuation,
        "retest": retest,
        "fake": fake,
        "confirmed": confirmed,
        "extension_pct": round(extension_pct, 2) if extension_pct is not None else None,
        "room_pct": round(((resistance_above / price) - 1) * 100, 2) if resistance_above and price else None,
    }


def _pivot_points(candles, lookback=60):
    """Extract alternating swing highs/lows for chart-pattern detection."""
    rows = candles[-lookback:]
    pivots = []
    for i in range(2, len(rows) - 2):
        cur = rows[i]
        is_low = cur["low"] <= rows[i-1]["low"] and cur["low"] <= rows[i-2]["low"] and cur["low"] <= rows[i+1]["low"] and cur["low"] <= rows[i+2]["low"]
        is_high = cur["high"] >= rows[i-1]["high"] and cur["high"] >= rows[i-2]["high"] and cur["high"] >= rows[i+1]["high"] and cur["high"] >= rows[i+2]["high"]
        if is_low:
            pivots.append(("L", i, cur["low"]))
        if is_high:
            pivots.append(("H", i, cur["high"]))
    clean = []
    for p in pivots:
        if clean and clean[-1][0] == p[0]:
            if (p[0] == "L" and p[2] < clean[-1][2]) or (p[0] == "H" and p[2] > clean[-1][2]):
                clean[-1] = p
        else:
            clean.append(p)
    return clean


def _detect_chart_patterns(candles, price, rvol):
    """Detect confirmed bullish double-bottom / inverse H&S and bearish H&S."""
    pivots = _pivot_points(candles, 70)
    patterns = {
        "double_bottom": False,
        "double_bottom_neckline": None,
        "double_bottom_confirmed": False,
        "inverse_head_shoulders": False,
        "inverse_hs_neckline": None,
        "inverse_hs_confirmed": False,
        "head_shoulders": False,
        "head_shoulders_neckline": None,
    }

    for i in range(len(pivots) - 2):
        a, b, d = pivots[i:i+3]
        if a[0] != "L" or b[0] != "H" or d[0] != "L":
            continue
        if abs(a[2] - d[2]) / max(price, 0.0001) > 0.10:
            continue
        if b[2] <= max(a[2], d[2]) * 1.04:
            continue
        neckline = b[2]
        patterns["double_bottom"] = True
        patterns["double_bottom_neckline"] = neckline
        patterns["double_bottom_confirmed"] = price > neckline * 1.005 and rvol >= 1.15

    for i in range(len(pivots) - 4):
        p = pivots[i:i+5]
        if [x[0] for x in p] != ["L", "H", "L", "H", "L"]:
            continue
        left_shoulder, left_neck, head, right_neck, right_shoulder = p
        shoulders = (left_shoulder[2] + right_shoulder[2]) / 2
        if abs(left_shoulder[2] - right_shoulder[2]) / max(price, 0.0001) > 0.10:
            continue
        if head[2] >= shoulders * 0.96:
            continue
        neckline = (left_neck[2] + right_neck[2]) / 2
        if neckline <= shoulders:
            continue
        patterns["inverse_head_shoulders"] = True
        patterns["inverse_hs_neckline"] = neckline
        patterns["inverse_hs_confirmed"] = price > neckline * 1.005 and rvol >= 1.15

    for i in range(len(pivots) - 4):
        p = pivots[i:i+5]
        if [x[0] for x in p] != ["H", "L", "H", "L", "H"]:
            continue
        left_shoulder, left_neck, head, right_neck, right_shoulder = p
        shoulders = (left_shoulder[2] + right_shoulder[2]) / 2
        if abs(left_shoulder[2] - right_shoulder[2]) / max(price, 0.0001) > 0.10:
            continue
        if head[2] <= shoulders * 1.04:
            continue
        neckline = (left_neck[2] + right_neck[2]) / 2
        patterns["head_shoulders"] = True
        patterns["head_shoulders_neckline"] = neckline

    return patterns

def _parse_money(value):
    if value is None:
        return 0.0
    text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _momentum_time_filter():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    hour = now.hour
    if hour < 10:
        return None, now
    effective_hour = min(16, hour)
    return MOMENTUM_RVOL_THRESHOLDS.get(effective_hour), now


def _is_excluded_security(row):
    name = str(row.get("name") or "").upper()
    symbol = str(row.get("symbol") or "").upper()
    quote_type = str(row.get("quote_type") or row.get("quoteType") or "").upper()
    security_type = str(row.get("security_type") or "").upper()
    excluded = (
        "PREFERRED", "PREF ", "WARRANT", "RIGHT", "UNIT",
        "DEPOSITARY", "ADR", "AMERICAN DEPOSITARY", "SPAC",
        "SPECIAL PURPOSE ACQUISITION",
    )
    if any(token in name for token in excluded):
        return True
    if quote_type not in {"", "EQUITY"}:
        return True
    if security_type and security_type not in {"EQUITY", "COMMON STOCK"}:
        return True
    if symbol.endswith((".WS", ".WT", ".W", ".U")):
        return True
    return False


def _parse_yahoo_number(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    if text and text[-1].upper() in multipliers:
        try:
            return float(text[:-1]) * multipliers[text[-1].upper()]
        except ValueError:
            return 0.0
    return _f(text, 0.0)


def _momentum_rvol_10d(candles):
    if len(candles) < 11:
        return None
    today_volume = _f(candles[-1].get("volume"), 0)
    baseline = sum(_f(x.get("volume"), 0) for x in candles[-11:-1]) / 10.0
    return today_volume / baseline if baseline > 0 else None


async def _discover_yahoo_top_gainers(client):
    """Yahoo custom screener: exact daily momentum rules for both sections."""
    threshold, now = _momentum_time_filter()
    if threshold is None:
        return []

    def equity_query(operands):
        return {
            "operator": "and",
            "operands": [
                {"operator": "eq", "operands": ["region", "us"]},
                {"operator": "or", "operands": [
                    {"operator": "eq", "operands": ["exchange", "NMS"]},
                    {"operator": "eq", "operands": ["exchange", "NYQ"]},
                    {"operator": "eq", "operands": ["exchange", "ASE"]},
                ]},
                *operands,
            ],
        }

    query_sets = [
        (
            "small",
            equity_query([
                {"operator": "gt", "operands": ["percentchange", MOMENTUM_SMALL_MIN_GAIN]},
                {"operator": "btwn", "operands": ["intradayprice", MIN_PRICE, MAX_PRICE]},
                {"operator": "gt", "operands": ["dayvolume", MOMENTUM_SMALL_MIN_VOLUME]},
            ]),
        ),
        (
            "large",
            equity_query([
                {"operator": "gt", "operands": ["percentchange", MOMENTUM_LARGE_MIN_GAIN]},
                {"operator": "gt", "operands": ["intradaymarketcap", MOMENTUM_LARGE_MIN_MARKET_CAP]},
            ]),
        ),
    ]

    out = []
    seen = set()
    for section, query in query_sets:
        payload = {
            "offset": 0,
            "size": 250,
            "sortField": "percentchange",
            "sortType": "DESC",
            "quoteType": "EQUITY",
            "query": query,
            "userId": "",
            "userIdType": "guid",
        }
        response_payload = None
        for base in (
            "https://query1.finance.yahoo.com/v1/finance/screener",
            "https://query2.finance.yahoo.com/v1/finance/screener",
        ):
            try:
                r = await client.post(
                    base,
                    params={
                        "formatted": "false",
                        "lang": "en-US",
                        "region": "US",
                        "corsDomain": "finance.yahoo.com",
                    },
                    json=payload,
                    headers={
                        "User-Agent": "Mozilla/5.0 SAS-PRO/2.0",
                        "Content-Type": "application/json",
                    },
                )
                r.raise_for_status()
                response_payload = r.json()
                break
            except Exception:
                continue

        rows = (((response_payload or {}).get("finance") or {}).get("result") or [{}])
        rows = rows[0].get("quotes") or [] if rows else []
        for raw in rows:
            row = dict(raw)
            exchange = _normalize_exchange(row.get("exchange") or row.get("fullExchangeName"))
            if exchange not in ALLOWED_EXCHANGES:
                continue
            row["symbol"] = str(row.get("symbol") or "").upper().strip()
            row["name"] = row.get("longName") or row.get("shortName") or row["symbol"]
            row["quote_type"] = row.get("quoteType")
            if not row["symbol"] or row["symbol"] in seen or _is_excluded_security(row):
                continue

            price = _parse_yahoo_number(row.get("regularMarketPrice") or row.get("postMarketPrice"))
            change_pct = _parse_yahoo_number(row.get("regularMarketChangePercent"))
            volume = _parse_yahoo_number(row.get("regularMarketVolume"))
            market_cap = _parse_yahoo_number(row.get("marketCap"))
            if price <= 0 or change_pct <= 0 or volume <= 0:
                continue

            # Re-check exact user rules after Yahoo returns the live row.
            if section == "small":
                if not (MIN_PRICE <= price <= MAX_PRICE and
                        change_pct > MOMENTUM_SMALL_MIN_GAIN and
                        volume > MOMENTUM_SMALL_MIN_VOLUME):
                    continue
            else:
                if not (market_cap > MOMENTUM_LARGE_MIN_MARKET_CAP and
                        change_pct > MOMENTUM_LARGE_MIN_GAIN):
                    continue

            seen.add(row["symbol"])
            out.append({
                "symbol": row["symbol"],
                "name": row["name"],
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "market_cap": market_cap,
                "exchange": exchange,
                "quote_type": row.get("quoteType"),
                "source": "Yahoo Finance Custom Gainers",
                "momentum_section": section,
                "momentum_session_date": now.strftime("%Y-%m-%d"),
                "momentum_asof_ny": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "momentum_rvol_threshold": threshold[section],
            })
    return out

def _classify_momentum_candidate(row):
    """Classify a fallback row using the same user-defined momentum rules."""
    price = _f(row.get("price"), 0)
    change_pct = _f(row.get("change_pct"), 0)
    volume = _f(row.get("volume"), 0)
    market_cap = _f(row.get("market_cap"), 0)
    if (
        MIN_PRICE <= price <= MAX_PRICE
        and change_pct > MOMENTUM_SMALL_MIN_GAIN
        and volume > MOMENTUM_SMALL_MIN_VOLUME
    ):
        return "small"
    if (
        market_cap > MOMENTUM_LARGE_MIN_MARKET_CAP
        and change_pct > MOMENTUM_LARGE_MIN_GAIN
    ):
        return "large"
    return None


async def _apply_daily_momentum_filter(candidates):
    threshold, now = _momentum_time_filter()
    if threshold is None:
        return [], {"session_date": now.strftime("%Y-%m-%d"), "status": "outside_main_session"}
    semaphore = asyncio.Semaphore(12)

    async def enrich(row):
        symbol = str(row.get("symbol") or "").upper()
        async with semaphore:
            async with httpx.AsyncClient(timeout=min(settings.panwatch_timeout_seconds, 20)) as client:
                candles, source = await _get_analysis_candles(client, symbol, allow_twelve_fallback=False)
        rvol10 = _momentum_rvol_10d(candles)
        if rvol10 is None:
            return None
        section = str(row.get("momentum_section") or "").lower()
        if section not in threshold:
            price = _f(row.get("price"), 0)
            change_pct = _f(row.get("change_pct"), 0)
            volume = _f(row.get("volume"), 0)
            market_cap = _f(row.get("market_cap"), 0)
            if (
                MIN_PRICE <= price <= MAX_PRICE
                and change_pct > MOMENTUM_SMALL_MIN_GAIN
                and volume > MOMENTUM_SMALL_MIN_VOLUME
            ):
                section = "small"
            elif (
                market_cap > MOMENTUM_LARGE_MIN_MARKET_CAP
                and change_pct > MOMENTUM_LARGE_MIN_GAIN
            ):
                section = "large"
            else:
                return None

        required = threshold[section]
        if rvol10 <= required:
            return None
        result = dict(row)
        result["momentum_section"] = section
        result["momentum_rvol_10d"] = round(rvol10, 2)
        result["momentum_rvol_threshold"] = required
        result["momentum_candle_source"] = source
        result["momentum_session_verified"] = True
        result["momentum_warning_under_1"] = bool(result["price"] < 1.0)
        return result

    enriched = await asyncio.gather(*(enrich(row) for row in candidates), return_exceptions=False)
    accepted = [x for x in enriched if x]
    accepted.sort(key=lambda x: float(x.get("momentum_rvol_10d") or 0), reverse=True)
    return accepted, {
        "session_date": now.strftime("%Y-%m-%d"),
        "asof_ny": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "rvol_thresholds": threshold,
        "accepted": len(accepted),
    }


async def _discover_us_exchanges(client):
    """Public exchange screener used as the broad, keyless radar universe."""
    out = []
    excluded_words = (
        "WARRANT", "RIGHT", "UNIT", "PREFERRED", "ETF",
        "NOTE", "DEPOSITARY", "TRUST", "FUND",
    )
    for exchange in ("NASDAQ", "NYSE", "AMEX"):
        try:
            r = await client.get(
                "https://api.nasdaq.com/api/screener/stocks",
                params={
                    "tableonly": "true",
                    "limit": 5000,
                    "offset": 0,
                    "exchange": exchange,
                    "download": "true",
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
                    "Accept": "application/json,text/plain,*/*",
                    "Origin": "https://www.nasdaq.com",
                    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
                },
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
            rows = ((payload.get("data") or {}).get("rows") or [])
        except Exception as exc:
            logger.warning("PUBLIC_SCREENER_FAILED exchange=%s error=%s", exchange, type(exc).__name__)
            continue

        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            name = str(row.get("name") or "").strip()
            price = _parse_money(row.get("lastsale"))
            change_pct = _parse_money(row.get("pctchange"))
            volume = _parse_money(row.get("volume"))
            market_cap = _parse_money(row.get("marketCap"))
            if not symbol or not name or _is_excluded_security(row):
                continue
            if any(word in name.upper() for word in excluded_words):
                continue
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue
            if volume <= 0:
                continue
            out.append({
                "symbol": symbol,
                "name": name,
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "market_cap": market_cap,
                "exchange": exchange,
                "source": "Public US Exchange Screener",
            })
    return out

async def _discover_openterminal(client):
    """Secondary live screener source from the bundled OpenTerminal service."""
    base = settings.openterminal_api_url.rstrip("/")
    if not base:
        return []
    try:
        r = await client.get(
            f"{base}/api/screener",
            params={
                "market": "us",
                "changeMin": 0,
                "sort": "changePercent",
                "dir": "desc",
            },
        )
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return []

    out = []
    for row in rows or []:
        exchange = _normalize_exchange(row.get("exchange"))
        symbol = str(row.get("symbol") or "").upper().strip()
        price = _f(row.get("price"), -1)
        if exchange not in ALLOWED_EXCHANGES or not symbol or not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        out.append({
            "symbol": symbol,
            "name": row.get("name") or symbol,
            "price": price,
            "change_pct": _f(row.get("changePercent")),
            "volume": _f(row.get("volume")),
            "market_cap": _f(row.get("marketCap")),
            "exchange": "NASDAQ",
            "source": "OpenTerminal / TradingView",
        })
    return out

async def _discover_twelvedata(client):
    # Optional fallback only. /market_movers may require a higher plan.
    if not settings.twelve_data_api_key:
        return []
    try:
        r = await twelve_call(client.get, "https://api.twelvedata.com/market_movers/stocks",
            params={
                "apikey": settings.twelve_data_api_key,
                "direction": "gainers",
                "outputsize": 50,
                "country": "USA",
            },
        )
        r.raise_for_status()
        rows = r.json().get("values") or []
    except Exception:
        return []
    out = []
    for row in rows:
        if not _is_allowed_exchange(row):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        price = _f(row.get("last"), -1)
        if symbol and MIN_PRICE <= price <= MAX_PRICE:
            out.append({
                "symbol": symbol,
                "name": row.get("name") or symbol,
                "price": price,
                "change_pct": _f(row.get("percent_change")),
                "volume": _f(row.get("volume")),
                "exchange": _normalize_exchange(row.get("exchange")),
                "source": "Twelve Data",
            })
    return out


async def _discover_panwatch(client):
    base = settings.panwatch_base_url.rstrip("/")
    try:
        r = await client.get(
            f"{base}/api/discovery/stocks",
            params={"market": "US", "mode": "gainers", "limit": 5000},
        )
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return []

    out = []
    for row in rows or []:
        if not _is_allowed_exchange(row):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        price = _f(row.get("price"), -1)
        if symbol and MIN_PRICE <= price <= MAX_PRICE:
            out.append({**row, "exchange": _normalize_exchange(row.get("exchange")), "source": "PanWatch"})
    return out


async def discover_low_price_stocks():
    """Build a broad, keyless US universe and stage candidates by evidence.

    The staging layer deliberately does not require a large price increase.
    It preserves:
      1) real movers,
      2) unusual-volume names,
      3) liquid quiet/compressing names that may be accumulating,
      4) high-dollar-volume names.
    Detailed OHLCV confirmation happens after staging.
    """
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        sources = await asyncio.gather(
            _discover_us_exchanges(client),
            _discover_openterminal(client),
            _discover_panwatch(client),
            return_exceptions=True,
        )

    merged = {}
    for source_rows in sources:
        if isinstance(source_rows, Exception):
            continue
        for row in source_rows:
            if not isinstance(row, dict) or not _is_allowed_exchange(row):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            price = _f(row.get("price"), 0)
            volume = _f(row.get("volume"), 0)
            if not symbol or _is_excluded_security(row):
                continue
            if not (MIN_PRICE <= price <= MAX_PRICE) or volume <= 0:
                continue
            current = merged.get(symbol)
            # Prefer the row carrying the strongest volume/liquidity evidence.
            if current is None or (price * volume) > (_f(current.get("price")) * _f(current.get("volume"))):
                merged[symbol] = dict(row)

    universe = list(merged.values())
    if not universe:
        return []

    # Stage a diverse set rather than only gainers.
    by_dollar = sorted(universe, key=lambda x: _f(x.get("price")) * _f(x.get("volume")), reverse=True)
    by_volume = sorted(universe, key=lambda x: _f(x.get("volume")), reverse=True)
    by_move = sorted(universe, key=lambda x: abs(_f(x.get("change_pct"))), reverse=True)
    # Quiet names are useful for accumulation; keep liquid names with small daily moves.
    quiet = sorted(
        [x for x in universe if abs(_f(x.get("change_pct"))) <= 4.0],
        key=lambda x: _f(x.get("price")) * _f(x.get("volume")),
        reverse=True,
    )

    staged = {}
    for rows, limit in ((by_dollar, 250), (by_volume, 200), (by_move, 200), (quiet, 200)):
        for row in rows[:limit]:
            staged[row["symbol"]] = row

    # Hard cap protects PanWatch/local resources without narrowing the price universe.
    candidates = sorted(
        staged.values(),
        key=lambda x: (
            _f(x.get("price")) * _f(x.get("volume")),
            abs(_f(x.get("change_pct"))),
        ),
        reverse=True,
    )[:settings.radar_staging_limit]
    semaphore = asyncio.Semaphore(16)

    async def stage(row):
        symbol = str(row.get("symbol") or "").upper()
        async with semaphore:
            async with httpx.AsyncClient(timeout=min(settings.panwatch_timeout_seconds, 20)) as client:
                candles, source = await _get_analysis_candles(client, symbol, allow_twelve_fallback=False)
        if len(candles) < 30:
            return None

        closes = [x["close"] for x in candles]
        volumes = [max(0.0, x["volume"]) for x in candles]
        price = _f(row.get("price"), closes[-1])
        if price <= 0:
            price = closes[-1]

        avg20 = sum(volumes[-21:-1]) / max(1, len(volumes[-21:-1]))
        rvol = volumes[-1] / avg20 if avg20 else 0.0
        recent = candles[-10:]
        ranges = [max(0.0, x["high"] - x["low"]) for x in recent]
        higher_lows = all(recent[i]["low"] >= recent[i-1]["low"] * 0.995 for i in range(1, len(recent)))
        compression = bool(ranges and sum(ranges[-3:]) / 3 <= (sum(ranges) / len(ranges)) * 0.85)
        prior_red = [x["volume"] for x in candles[-20:-10] if x["close"] < x["open"]]
        recent_red = [x["volume"] for x in recent if x["close"] < x["open"]]
        prior_sell_avg = sum(prior_red) / len(prior_red) if prior_red else 0.0
        recent_sell_avg = sum(recent_red) / len(recent_red) if recent_red else 0.0
        selling_dry = bool(
            prior_sell_avg > 0
            and recent_sell_avg <= prior_sell_avg * 0.75
        )

        supports, resistances = _local_levels(candles)
        support = max([x for x in supports if x < price], default=None)
        resistance = min([x for x in resistances if x > price], default=None)
        near_support = bool(support and _near(support, price, 0.08))
        dollar_volume = price * _f(row.get("volume"), 0)
        change = _f(row.get("change_pct"), 0)

        accumulation_signal = bool(
            near_support
            and higher_lows
            and (compression or selling_dry)
            and rvol >= 0.80
            and dollar_volume >= MIN_DAILY_DOLLAR_VOLUME
        )
        movement_signal = bool(
            change >= 1.5
            and rvol >= 1.20
            and dollar_volume >= MIN_DAILY_DOLLAR_VOLUME
        )
        breakout_setup = bool(
            resistance
            and 0 <= ((resistance / price) - 1) * 100 <= 5
            and rvol >= 1.10
            and higher_lows
        )

        # Reject only if there is no evidence of either movement or accumulation.
        if not (accumulation_signal or movement_signal or breakout_setup):
            return None

        result = dict(row)
        result.update({
            "momentum_section": "accumulation" if accumulation_signal else "movement",
            "momentum_rvol_10d": round(rvol, 2),
            "momentum_rvol_threshold": 0.80 if accumulation_signal else 1.20,
            "momentum_candle_source": source,
            "momentum_session_verified": True,
            "accumulation_signal": accumulation_signal,
            "movement_signal": movement_signal,
            "breakout_setup": breakout_setup,
            "staging_reason": (
                "تجميع: قرب دعم + قيعان أعلى + انكماش/جفاف بيع"
                if accumulation_signal
                else "حركة: تغير سعري + RVOL غير عادي"
                if movement_signal
                else "اقتراب من مقاومة مع حجم داعم"
            ),
        })
        return result

    staged_rows = await asyncio.gather(*(stage(row) for row in candidates), return_exceptions=False)
    accepted = [x for x in staged_rows if x]
    accepted.sort(
        key=lambda x: (
            bool(x.get("accumulation_signal")),
            _f(x.get("momentum_rvol_10d")),
            abs(_f(x.get("change_pct"))),
            _f(x.get("price")) * _f(x.get("volume")),
        ),
        reverse=True,
    )
    return accepted[:settings.radar_shortlist_limit]



async def _get_analysis_candles(client, symbol: str, allow_twelve_fallback: bool = False):
    """Get daily OHLCV with caching and a strictly limited Twelve Data fallback."""
    global _twelve_data_quota_exhausted
    key = symbol.upper()
    now = time.monotonic()
    cached = _candle_cache.get(key)
    if cached and now - cached[0] < _CANDLE_CACHE_TTL:
        return cached[1], cached[2]

    base = settings.panwatch_base_url.rstrip("/")
    candles = []

    # Primary: PanWatch, rate-limited and bounded so one slow request
    # cannot leave the whole radar waiting behind the semaphore.
    try:
        async with asyncio.timeout(8):
            async with _panwatch_semaphore:
                r = await client.get(
                    f"{base}/api/klines/{key}",
                    params={"market": "US", "days": 260, "interval": "1d"},
                    timeout=7,
                )
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") or payload
            candles = _parse_candles(data.get("klines", []))
            if len(candles) >= 30:
                _candle_cache[key] = (now, candles, "PanWatch")
                return candles, "PanWatch"
    except (asyncio.TimeoutError, httpx.HTTPError, Exception):
        candles = []

    # Only the most important shortlist symbols get a single Twelve Data fallback.
    # This prevents 429 storms when 100+ symbols have no PanWatch candles.
    if (
        allow_twelve_fallback
        and False  # Radar is keyless-first; Twelve Data is reserved for manual/explicit fallbacks.
    ):
        try:
            async with asyncio.timeout(8):
                async with _twelvedata_fallback_semaphore:
                    r = await twelve_call(client.get, "https://api.twelvedata.com/time_series",
                        params={
                            "symbol": key,
                            "interval": "1day",
                            "outputsize": 260,
                            "apikey": settings.twelve_data_api_key,
                        },
                        timeout=7,
                    )
                if r.status_code == 429:
                    _twelve_data_quota_exhausted = True
                    raise httpx.HTTPStatusError("Twelve Data quota exhausted", request=r.request, response=r)
                r.raise_for_status()
                payload = r.json()
                candles = _parse_candles(payload.get("values", []))
                if len(candles) >= 30:
                    candles.reverse()
                    _candle_cache[key] = (now, candles, "Twelve Data")
                    return candles, "Twelve Data"
        except (asyncio.TimeoutError, httpx.HTTPError, Exception):
            pass

    # Cache the failure briefly too, so the same unavailable symbol is not hammered
    # again on every 30-minute cycle.
    _candle_cache[key] = (now, [], "unavailable")
    return [], "unavailable"



_benchmark_cache = {}

def _ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = value * k + ema * (1 - k)
    return ema


def _rsi_series(values, period=14):
    """Return RSI values aligned to the close series using Wilder smoothing."""
    if len(values) < period + 1:
        return [None] * len(values)
    out = [None] * len(values)
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        out[i + 1] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def _advanced_structure(candles):
    """Score recent HH/HL versus LH/LL market structure from observed OHLCV."""
    if len(candles) < 12:
        return {"trend": "غير واضح", "bullish": False, "bearish": False, "score": 0}
    recent = candles[-12:]
    highs = [x["high"] for x in recent]
    lows = [x["low"] for x in recent]
    hh = highs[-1] > max(highs[-5:-1])
    hl = lows[-1] > min(lows[-5:-1])
    lh = highs[-1] < max(highs[-5:-1])
    ll = lows[-1] < min(lows[-5:-1])
    if hh and hl:
        return {"trend": "صاعد HH/HL", "bullish": True, "bearish": False, "score": 15}
    if lh and ll:
        return {"trend": "هابط LH/LL", "bullish": False, "bearish": True, "score": 0}
    if hh or hl:
        return {"trend": "يميل للصعود", "bullish": True, "bearish": False, "score": 8}
    if lh or ll:
        return {"trend": "يميل للهبوط", "bullish": False, "bearish": True, "score": 2}
    return {"trend": "محايد", "bullish": False, "bearish": False, "score": 5}


def _fibonacci_context(candles, price, lookback=60):
    """Use the latest observed swing range; never invent a Fibonacci anchor."""
    rows = candles[-lookback:]
    if len(rows) < 20 or price <= 0:
        return {"valid": False, "zone": None, "level": None, "levels": {}, "score": 0}
    low = min(x["low"] for x in rows)
    high = max(x["high"] for x in rows)
    span = high - low
    if span <= 0:
        return {"valid": False, "zone": None, "level": None, "levels": {}, "score": 0}
    levels = {
        "0.382": high - span * 0.382,
        "0.500": high - span * 0.500,
        "0.618": high - span * 0.618,
    }
    nearest = min(levels.items(), key=lambda item: abs(price - item[1]))
    distance_pct = abs(price - nearest[1]) / price * 100
    zone = nearest[0] if distance_pct <= 2.5 else None
    return {
        "valid": True,
        "zone": zone,
        "level": round(nearest[1], 4),
        "distance_pct": round(distance_pct, 2),
        "levels": {k: round(v, 4) for k, v in levels.items()},
        "swing_low": round(low, 4),
        "swing_high": round(high, 4),
        "score": 10 if zone else (5 if distance_pct <= 5 else 0),
    }


def _fair_value_gap(candles, price, lookback=40):
    """Detect the latest unfilled bullish/bearish three-candle gap."""
    rows = candles[-lookback:]
    latest = None
    for i in range(2, len(rows)):
        left, right = rows[i - 2], rows[i]
        if right["low"] > left["high"]:
            latest = {
                "type": "bullish",
                "low": left["high"],
                "high": right["low"],
                "index": i,
            }
        elif right["high"] < left["low"]:
            latest = {
                "type": "bearish",
                "low": right["high"],
                "high": left["low"],
                "index": i,
            }
    if not latest:
        return {"found": False, "active": False, "type": None, "low": None, "high": None, "score": 0}

    gap_low, gap_high = latest["low"], latest["high"]
    if latest["type"] == "bullish":
        active = price >= gap_low * 0.995
        score = 10 if active and price <= gap_high * 1.08 else 5 if active else 0
    else:
        active = price <= gap_high * 1.005
        score = 0
    return {
        "found": True,
        "active": bool(active),
        "type": latest["type"],
        "low": round(gap_low, 4),
        "high": round(gap_high, 4),
        "score": score,
    }


def _rsi_divergence(candles, rsi_values, lookback=60):
    """Compare the two latest confirmed swing lows/highs with RSI."""
    rows = candles[-lookback:]
    rsis = rsi_values[-lookback:]
    lows, highs = [], []
    for i in range(2, len(rows) - 2):
        if rows[i]["low"] <= rows[i-1]["low"] and rows[i]["low"] <= rows[i-2]["low"] and rows[i]["low"] <= rows[i+1]["low"] and rows[i]["low"] <= rows[i+2]["low"]:
            if rsis[i] is not None:
                lows.append((i, rows[i]["low"], rsis[i]))
        if rows[i]["high"] >= rows[i-1]["high"] and rows[i]["high"] >= rows[i-2]["high"] and rows[i]["high"] >= rows[i+1]["high"] and rows[i]["high"] >= rows[i+2]["high"]:
            if rsis[i] is not None:
                highs.append((i, rows[i]["high"], rsis[i]))
    bullish = len(lows) >= 2 and lows[-1][1] < lows[-2][1] and lows[-1][2] > lows[-2][2]
    bearish = len(highs) >= 2 and highs[-1][1] > highs[-2][1] and highs[-1][2] < highs[-2][2]
    if bullish:
        state, score = "إيجابي", 10
    elif bearish:
        state, score = "سلبي", 0
    else:
        state, score = "محايد", 5
    return {"state": state, "bullish": bullish, "bearish": bearish, "score": score}


def _advanced_confirmation_score(*, structure, fibonacci, fvg, divergence, breakout_confirmed,
                                 rvol, rsi14, near_entry, resistance_distance_pct, breakout_room_pct):
    """Second-layer confirmation score; advisory until the final radar gate."""
    score = 0
    score += int(structure.get("score", 0))
    if breakout_confirmed:
        score += 15
    elif near_entry:
        score += 8
    if rvol >= 2.0:
        score += 15
    elif rvol >= 1.5:
        score += 10
    elif rvol >= 1.2:
        score += 5
    if rsi14 is not None:
        if 55 <= rsi14 <= 70:
            score += 10
        elif 50 <= rsi14 <= 75:
            score += 6
    if (
        (resistance_distance_pct is not None and 0 <= resistance_distance_pct <= 5)
        or (breakout_room_pct is not None and breakout_room_pct >= 3)
        or near_entry
    ):
        score += 10
    score += int(fibonacci.get("score", 0))
    score += int(fvg.get("score", 0))
    score += int(divergence.get("score", 0))
    return min(100, score)

def _rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def _power_trend_age(closes):
    if len(closes) < 205:
        return None
    states = []
    # Recalculate the three EMAs at each historical endpoint. This is deliberately
    # bounded to the last 30 sessions; it identifies when the current alignment began.
    start = max(200, len(closes) - 35)
    for end in range(start, len(closes) + 1):
        window = closes[:end]
        e20, e50, e200 = _ema(window, 20), _ema(window, 50), _ema(window, 200)
        states.append(bool(e20 is not None and e50 is not None and e200 is not None and e20 > e50 > e200))
    if not states[-1]:
        return 0
    age = 0
    for state in reversed(states):
        if not state:
            break
        age += 1
    return age

async def _benchmark_return(symbol="QQQ", lookback=20):
    key = symbol.upper()
    now = time.monotonic()
    cached = _benchmark_cache.get(key)
    if cached and now - cached[0] < _CANDLE_CACHE_TTL:
        candles = cached[1]
    else:
        async with httpx.AsyncClient(timeout=min(settings.panwatch_timeout_seconds, 20)) as client:
            candles, source = await _get_analysis_candles(client, key, allow_twelve_fallback=False)
        if not candles:
            return None, None
        _benchmark_cache[key] = (now, candles)
    closes = [x["close"] for x in candles]
    if len(closes) <= lookback:
        return None, None
    return closes[-1] / closes[-1-lookback] - 1.0, key



def _trading_profile(*, price, atr_pct, rvol, power_trend, accumulation, breakout,
                     change_pct, chase_risk, distribution_risk, support, bearish_divergence):
    """Classify the trading style and risk from observed daily behavior only."""
    risk_score = 0
    reasons = []

    if atr_pct >= 10:
        risk_score += 4
        reasons.append("ATR مرتفع جدًا")
    elif atr_pct >= 7:
        risk_score += 3
        reasons.append("تذبذب سعري مرتفع")
    elif atr_pct >= 4:
        risk_score += 2
        reasons.append("تذبذب سعري متوسط")
    elif atr_pct >= 2:
        risk_score += 1

    if rvol >= 3:
        risk_score += 2
        reasons.append("RVOL مرتفع جدًا")
    elif rvol >= 2:
        risk_score += 1
        reasons.append("RVOL مرتفع")

    if price < 1:
        risk_score += 2
        reasons.append("سعر منخفض جدًا")
    elif price < 2:
        risk_score += 1
        reasons.append("سعر منخفض")

    if abs(change_pct) >= 10:
        risk_score += 2
        reasons.append("حركة يومية حادة")
    elif abs(change_pct) >= 6:
        risk_score += 1

    if chase_risk:
        risk_score += 2
        reasons.append("خطر مطاردة السعر")
    if distribution_risk:
        risk_score += 2
        reasons.append("إشارة توزيع محتملة")
    if bearish_divergence:
        risk_score += 1
        reasons.append("انحراف RSI سلبي")
    if support is None:
        risk_score += 1
        reasons.append("لا يوجد دعم قريب موثوق")

    risk_score = min(10, risk_score)
    if risk_score >= 9:
        risk_level = "مرتفع جدًا"
        risk_emoji = "🔴"
    elif risk_score >= 6:
        risk_level = "مرتفع"
        risk_emoji = "🟠"
    elif risk_score >= 3:
        risk_level = "متوسط"
        risk_emoji = "🟡"
    else:
        risk_level = "منخفض"
        risk_emoji = "🟢"

    # The style is descriptive: it tells the user how the stock currently behaves,
    # not whether it is personally suitable for their portfolio.
    if (
        atr_pct >= 7 or rvol >= 2.0 or price < 2 or abs(change_pct) >= 8
        or chase_risk or breakout and risk_score >= 6
    ):
        trading_style = "مضاربي"
        horizon = "من دقائق إلى عدة جلسات"
    elif (
        power_trend and atr_pct <= 6 and not distribution_risk
        and (accumulation or breakout or rvol >= 1.1)
    ):
        trading_style = "سوينق"
        horizon = "عدة أيام إلى عدة أسابيع"
    elif (
        power_trend and atr_pct <= 4 and rvol < 1.8
        and not chase_risk and not distribution_risk
    ):
        trading_style = "استثماري"
        horizon = "متوسط إلى طويل الأجل"
    else:
        trading_style = "سوينق"
        horizon = "عدة أيام إلى عدة أسابيع"

    if not reasons:
        reasons.append("تذبذب وسيولة ضمن النطاق الطبيعي للرصد")

    return {
        "trading_style": trading_style,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "risk_emoji": risk_emoji,
        "risk_reasons": reasons[:5],
        "holding_horizon": horizon,
        "risk_note": "التصنيف وصفي مبني على التذبذب والحجم والبنية اليومية، وليس حكمًا على ملاءمة السهم لمحفظتك.",
    }

async def classify_sas(symbol: str, quote: dict | None = None, allow_twelve_fallback: bool = False):
    async with httpx.AsyncClient(timeout=min(settings.panwatch_timeout_seconds, 30)) as client:
        candles, data_source = await _get_analysis_candles(client, symbol, allow_twelve_fallback)

    if len(candles) < 205:
        return {
            "behavior": "غير واضح",
            "type": "غير واضح",
            "emoji": "⚪",
            "score": 0,
            "pass": False,
            "reason": "بيانات غير كافية من PanWatch وTwelve Data",
            "data_source": data_source,
        }

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    price = closes[-1]
    change_pct = _f((quote or {}).get("change_pct"))
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    rsi14 = _rsi(closes, 14)
    power_trend = bool(ema20 is not None and ema50 is not None and ema200 is not None and ema20 > ema50 > ema200)
    power_trend_age = _power_trend_age(closes) if power_trend else 0
    avg_vol20 = sum(volumes[-20:]) / 20 if any(volumes[-20:]) else 0
    rvol = volumes[-1] / avg_vol20 if avg_vol20 else 0

    returns = []
    trs = []
    for i in range(1, len(candles)):
        prev, cur = candles[i-1], candles[i]
        if prev["close"]:
            returns.append(abs(cur["close"] / prev["close"] - 1))
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
    atr14 = sum(trs[-14:]) / min(14, len(trs))
    atr_pct = atr14 / price if price else 1

    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50

    supports, resistances = _local_levels(candles)
    support = max([x for x in supports if x < price], default=None)
    resistance = min([x for x in resistances if x > price], default=None)
    breakout_info = _analyze_breakout(candles, price, rvol, resistances)
    breakout_level = breakout_info["level"]
    next_resistance = breakout_info["next_resistance"]

    max_30d = max(closes[-30:])
    min_30d = min(closes[-30:])
    former_runner = min_30d > 0 and (max_30d / min_30d - 1) >= 1.00

    recent = candles[-10:]
    higher_lows = all(
        recent[i]["low"] >= recent[i-1]["low"] * 0.995
        for i in range(1, len(recent))
    )
    ranges = [x["high"] - x["low"] for x in recent]
    compression = sum(ranges[-3:]) / 3 <= (sum(ranges) / len(ranges)) * 0.85
    prior_sell_vol = sum(x["volume"] for x in candles[-20:-10]) / 10
    recent_sell_vol = sum(x["volume"] for x in recent if x["close"] < x["open"]) / max(
        1, sum(1 for x in recent if x["close"] < x["open"])
    )
    selling_dry = recent_sell_vol < prior_sell_vol if prior_sell_vol else False
    accumulation = support is not None and _near(support, price, 0.06) and higher_lows and (compression or selling_dry)

    lows = [c["low"] for c in candles[-40:]]
    mid = len(lows) // 2
    left_low = min(lows[:mid])
    right_low = min(lows[mid:])
    neckline = max(c["high"] for c in candles[-40:] if c["low"] not in (left_low, right_low))
    w_pattern = abs(left_low - right_low) / max(price, 0.0001) <= 0.10 and price >= neckline * 0.995

    sweep = False
    if support is not None:
        last = candles[-1]
        prev = candles[-2]
        sweep = prev["low"] < support and last["close"] > support

    # المقاومة القادمة فوق السعر ليست مستوى الاختراق؛ مستوى الاختراق هو آخر مقاومة
    # تاريخية تحت السعر. هذا يمنع التعارض القديم الذي جعل breakout شبه مستحيل.
    breakout = bool(breakout_level and breakout_info["close_above"])
    breakout_confirmed = bool(breakout_info["confirmed"])
    breakout_retest = bool(breakout_info["retest"])
    breakout_fake = bool(breakout_info["fake"])
    breakout_extension_pct = breakout_info["extension_pct"]
    breakout_room_pct = breakout_info["room_pct"]

    # Relative strength: compare the stock's 20-session return with NASDAQ proxy QQQ.
    benchmark_return, benchmark_symbol = await _benchmark_return("QQQ", 20)
    stock_return_20 = (price / closes[-21] - 1.0) if len(closes) > 21 and closes[-21] else None
    relative_strength = (
        stock_return_20 - benchmark_return
        if stock_return_20 is not None and benchmark_return is not None else None
    )

    # "Near entry" is evidence-based: distance to EMA20 or to a real nearby resistance.
    distance_from_ema20_pct = ((price / ema20) - 1.0) * 100.0 if ema20 else None
    resistance_distance_pct = ((resistance / price) - 1.0) * 100.0 if resistance and price else None
    near_entry = bool(
        (distance_from_ema20_pct is not None and 0 <= distance_from_ema20_pct <= 8.0)
        or (resistance_distance_pct is not None and 0 <= resistance_distance_pct <= 5.0)
        or (breakout_confirmed and breakout_extension_pct is not None and breakout_extension_pct <= 8.0)
    )

    # استراتيجية SAS: لا نطارد الحركة المتأخرة.
    # إذا ارتفع السهم بقوة وهو بعيد عن دعم واضح، لا يمر للرادار حتى لو كان RVOL مرتفعاً.
    late_chase = change_pct >= 20 and support is not None and not _near(support, price, 0.08)

    momentum = (
        rvol >= 2.0 and change_pct >= 3.0 and
        not late_chase and
        (breakout or (support is not None and _near(support, price, 0.08)))
    )

    old_high = max(c["high"] for c in candles[-30:-5])
    fill_gap = (
        change_pct > 3 and price < old_high and
        sma20 >= sma50 * 0.98 and rvol >= 1.2 and
        support is not None
    )

    structure = _advanced_structure(candles)
    rsi_values = _rsi_series(closes, 14)
    fibonacci = _fibonacci_context(candles, price)
    fvg = _fair_value_gap(candles, price)
    divergence = _rsi_divergence(candles, rsi_values)

    patterns = _detect_chart_patterns(candles, price, rvol)
    double_bottom_confirmed = patterns["double_bottom_confirmed"]
    inverse_hs_confirmed = patterns["inverse_hs_confirmed"]

    distribution_risk = rvol >= 2.5 and abs(change_pct) < 1.5

    advanced_score = _advanced_confirmation_score(
        structure=structure,
        fibonacci=fibonacci,
        fvg=fvg,
        divergence=divergence,
        breakout_confirmed=breakout_confirmed,
        rvol=rvol,
        rsi14=rsi14,
        near_entry=near_entry,
        resistance_distance_pct=resistance_distance_pct,
        breakout_room_pct=breakout_room_pct,
    )
    accumulation_hint = bool((quote or {}).get("accumulation_signal"))
    # التأكيد المتقدم طبقة ترجيح وليست بوابة صلبة؛ حتى لا تتعارض
    # Market Structure/Fibonacci/FVG/RSI مع اكتشاف التجميع أو بداية الحركة.
    # الرفض الصريح يقتصر على تناقض هابط قوي، بينما الدرجة تحدد قوة التأكيد.
    advanced_hard_block = bool(
        (divergence.get("bearish") and not breakout_confirmed)
        or (accumulation_hint and distribution_risk)
    )
    if breakout_confirmed:
        advanced_min_score = 30
    elif accumulation_hint and accumulation:
        advanced_min_score = 30
    elif rvol >= 1.20 and change_pct >= 1.5:
        advanced_min_score = 30
    else:
        advanced_min_score = 35
    advanced_confirmation_pass = bool(
        not advanced_hard_block and advanced_score >= advanced_min_score
    )
    advanced_confirmation_status = (
        "حظر هابط قوي"
        if advanced_hard_block
        else "تأكيد قوي"
        if advanced_score >= 50
        else "تأكيد جيد"
        if advanced_score >= advanced_min_score
        else "تأكيد مبكر/ضعيف — لا يُسقط الفرصة وحده"
    )
    bearish_head_shoulders = patterns["head_shoulders"] and (
        patterns["head_shoulders_neckline"] is not None
        and price < patterns["head_shoulders_neckline"] * 0.995
    )

    # Early Breakout score: exactly 100 points.
    # Trend 25 | Momentum 20 | Volume 20 | Relative Strength 15 |
    # Breakout Quality 15 | Risk 5.
    trend_score = 25 if power_trend else 0

    if rsi14 is None:
        momentum_score = 0
    elif 55 <= rsi14 <= 70:
        momentum_score = 20
    elif 50 <= rsi14 < 55 or 70 < rsi14 <= 73:
        momentum_score = 10
    else:
        momentum_score = 0

    if rvol >= 2.5:
        volume_score = 20
    elif rvol >= 2.0:
        volume_score = 18
    elif rvol >= 1.5:
        volume_score = 15
    else:
        volume_score = 0

    if relative_strength is None:
        relative_strength_score = 0
    elif relative_strength >= 0.10:
        relative_strength_score = 15
    elif relative_strength >= 0.05:
        relative_strength_score = 12
    elif relative_strength > 0:
        relative_strength_score = 8
    else:
        relative_strength_score = 0

    if breakout and rvol >= 1.5:
        breakout_quality_score = 15
    elif resistance_distance_pct is not None and 0 <= resistance_distance_pct <= 5 and rvol >= 1.5:
        breakout_quality_score = 12
    elif near_entry and accumulation:
        breakout_quality_score = 8
    else:
        breakout_quality_score = 0

    risk_score = 0
    if support is not None and price > support:
        support_distance_pct = (price - support) / price * 100
        if 3 <= support_distance_pct <= 10:
            risk_score = 5
        elif 1 <= support_distance_pct < 3 or 10 < support_distance_pct <= 12:
            risk_score = 3

    early_timing = bool(power_trend and 1 <= power_trend_age <= 5)
    # عمر الاتجاه وحده ليس مطاردة؛ المطاردة تُقاس بالامتداد السعري/RSI.
    # هذا يسمح باختراق مؤكد داخل اتجاه قائم بدل استبعاده تلقائيًا.
    chase_risk = bool(
        (distance_from_ema20_pct is not None and distance_from_ema20_pct > 8)
        or (rsi14 is not None and rsi14 > 73)
        or (breakout_confirmed and breakout_extension_pct is not None and breakout_extension_pct > 8)
    )

    trading_profile = _trading_profile(
        price=price,
        atr_pct=atr_pct * 100,
        rvol=rvol,
        power_trend=power_trend,
        accumulation=accumulation,
        breakout=breakout,
        change_pct=change_pct,
        chase_risk=chase_risk,
        distribution_risk=distribution_risk,
        support=support,
        bearish_divergence=bool(divergence.get("bearish")),
    )

    score = trend_score + momentum_score + volume_score + relative_strength_score + breakout_quality_score + risk_score

    # مساران للمرور:
    # 1) Early SAS: يحافظ على منطق الاتجاه المبكر لكن لا يشترط عمر 1-5 جلسات وحده.
    # 2) Breakout: يطبق منهج الملف المرفق: إغلاق + فوليوم + استمرار، مع منع المصيدة
    #    والمساحة الضيقة، وإعادة الاختبار كتعزيز وليست شرطًا وحيدًا.
    early_setup_pass = bool(
        power_trend
        and (early_timing or accumulation)
        and 50 <= (rsi14 or 0) <= 72
        and rvol >= 1.2
        and near_entry
        and not distribution_risk
        and not bearish_head_shoulders
    )
    breakout_pass = bool(
        breakout_confirmed
        and 48 <= (rsi14 or 0) <= 75
        and rvol >= 1.5
        and (power_trend or sma20 >= sma50 * 0.98 or accumulation)
        and not distribution_risk
        and not bearish_head_shoulders
        and (breakout_room_pct is None or breakout_room_pct >= 3.0)
    )
    strategy_pass = bool(early_setup_pass or breakout_pass)

    # الاستراتيجيات القديمة تبقى كبيانات تشخيصية فقط ولا تمنع السهم
    # من دخول مراحل الرادار. بوابة الرادار الفعلية هي:
    # الزخم + RVOL + السيولة + الأهداف/الوقف + R:R.
    core_pass = True

    breakout_reject_reasons = []
    if breakout_confirmed and not breakout_pass:
        if not (48 <= (rsi14 or 0) <= 75):
            breakout_reject_reasons.append("RSI خارج 48-75")
        if rvol < 1.5:
            breakout_reject_reasons.append("RVOL أقل من 1.5x")
        if not (power_trend or sma20 >= sma50 * 0.98 or accumulation):
            breakout_reject_reasons.append("البنية/الاتجاه غير كافٍ")
        if distribution_risk:
            breakout_reject_reasons.append("مخاطر توزيع")
        if bearish_head_shoulders:
            breakout_reject_reasons.append("رأس وكتفين هابط")
        if breakout_room_pct is not None and breakout_room_pct < 3.0:
            breakout_reject_reasons.append("المساحة السعرية أقل من 3%")

    # قيم افتراضية دفاعية قبل بناء الوصف؛ لا تغيّر شروط المرور أو النتيجة.
    behavior, stock_type, emoji = "غير واضح", "غير واضح", "⚪"

    # وصف ثابت للاتجاه/نوع الحركة؛ هذه القيم كانت تُستخدم في التقرير
    # دون أن يتم تعريفها، ما كان يوقف تحليل المرشحين بالكامل.
    if power_trend and breakout:
        behavior, stock_type, emoji = "اتجاه صاعد مع اختراق", "اختراق مبكر", "🟢"
    elif power_trend and accumulation:
        behavior, stock_type, emoji = "اتجاه صاعد مع تجميع", "تجميع مبكر", "🟢"
    elif power_trend:
        behavior, stock_type, emoji = "اتجاه صاعد", "زخم صاعد", "🟢"
    elif accumulation:
        behavior, stock_type, emoji = "تجميع", "تجميع تحت المراقبة", "🟡"
    elif breakout:
        behavior, stock_type, emoji = "اختراق", "اختراق تحت المراقبة", "🟡"
    else:
        behavior, stock_type, emoji = "غير واضح", "غير واضح", "⚪"

    evidence = []
    if power_trend:
        evidence.append(f"Power Trend ON — العمر {power_trend_age} جلسة")
    if rsi14 is not None:
        evidence.append(f"RSI {rsi14:.1f}")
    if former_runner:
        evidence.append("سلوك سابق قوي")
    if rvol >= 1.5:
        evidence.append(f"RVOL {rvol:.1f}x")
    if accumulation:
        evidence.append("تجميع قرب دعم")
    if sweep:
        evidence.append("استرداد بعد سحب سيولة")
    if breakout:
        evidence.append("تجاوز مقاومة")
    if breakout_confirmed:
        evidence.append("اختراق مؤكد: إغلاق + فوليوم + استمرار")
    if breakout_retest:
        evidence.append("إعادة اختبار ناجحة")
    if breakout_fake:
        evidence.append("⚠️ اختراق وهمي محتمل")
    if w_pattern:
        evidence.append("نموذج W")
    if patterns["double_bottom"]:
        evidence.append("قاع مزدوج مؤكد باختراق خط العنق" if double_bottom_confirmed else "قاع مزدوج تحت المراقبة")
    if patterns["inverse_head_shoulders"]:
        evidence.append("رأس وكتفين مقلوب مؤكد باختراق خط العنق" if inverse_hs_confirmed else "رأس وكتفين مقلوب تحت المراقبة")
    if bearish_head_shoulders:
        evidence.append("⚠️ رأس وكتفين هابط مؤكد")
    if fill_gap:
        evidence.append("مناطق هبوط/فجوة سابقة")
    if distribution_risk:
        evidence.append("⚠️ فوليوم مرتفع بدون تقدم واضح")

    return {
        "behavior": behavior,
        "type": stock_type,
        "emoji": emoji,
        "trading_style": trading_profile["trading_style"],
        "risk_level": trading_profile["risk_level"],
        "risk_score": trading_profile["risk_score"],
        "risk_emoji": trading_profile["risk_emoji"],
        "risk_reasons": trading_profile["risk_reasons"],
        "holding_horizon": trading_profile["holding_horizon"],
        "risk_note": trading_profile["risk_note"],
        "score": score,
        "pass": core_pass,
        "reason": " + ".join(evidence) if evidence else "بيانات فنية صالحة؛ لا توجد ملاحظة استراتيجية إضافية",
        "rvol": round(rvol, 2),
        "ema20": round(ema20, 4) if ema20 is not None else None,
        "ema50": round(ema50, 4) if ema50 is not None else None,
        "ema200": round(ema200, 4) if ema200 is not None else None,
        "power_trend": power_trend,
        "power_trend_age": power_trend_age,
        "rsi14": round(rsi14, 2) if rsi14 is not None else None,
        "relative_strength": round(relative_strength * 100, 2) if relative_strength is not None else None,
        "relative_strength_benchmark": benchmark_symbol,
        "distance_from_ema20_pct": round(distance_from_ema20_pct, 2) if distance_from_ema20_pct is not None else None,
        "resistance_distance_pct": round(resistance_distance_pct, 2) if resistance_distance_pct is not None else None,
        "near_entry": near_entry,
        "early_timing": early_timing,
        "chase_risk": chase_risk,
        "breakout_level": round(breakout_level, 4) if breakout_level is not None else None,
        "next_resistance": round(next_resistance, 4) if next_resistance is not None else None,
        "breakout_confirmed": breakout_confirmed,
        "breakout_retest": breakout_retest,
        "breakout_fake": breakout_fake,
        "breakout_extension_pct": breakout_extension_pct,
        "breakout_room_pct": breakout_room_pct,
        "sma20_above_sma50": bool(sma20 >= sma50 * 0.98),
        "early_setup_pass": early_setup_pass,
        "breakout_pass": breakout_pass,
        "breakout_reject_reasons": breakout_reject_reasons,
        "strategy_pass": strategy_pass,
        "score_breakdown": {
            "trend": trend_score,
            "momentum": momentum_score,
            "volume": volume_score,
            "relative_strength": relative_strength_score,
            "breakout_quality": breakout_quality_score,
            "risk": risk_score,
        },
        "advanced_confirmation_score": advanced_score,
        "advanced_confirmation_pass": advanced_confirmation_pass,
        "market_structure": structure.get("trend"),
        "market_structure_bullish": bool(structure.get("bullish")),
        "market_structure_bearish": bool(structure.get("bearish")),
        "fibonacci": fibonacci,
        "fvg": fvg,
        "rsi_divergence": divergence,
        "atr_pct": round(atr_pct * 100, 2),
        "support": round(support, 4) if support else None,
        "resistance": round(resistance, 4) if resistance else None,
        "former_runner": former_runner,
        "accumulation": accumulation,
        "sweep": sweep,
        "breakout": breakout,
        "w_pattern": w_pattern,
        "fill_gap": fill_gap,
        "double_bottom": patterns["double_bottom"],
        "double_bottom_confirmed": double_bottom_confirmed,
        "double_bottom_neckline": round(patterns["double_bottom_neckline"], 4) if patterns["double_bottom_neckline"] else None,
        "inverse_head_shoulders": patterns["inverse_head_shoulders"],
        "inverse_hs_confirmed": inverse_hs_confirmed,
        "inverse_hs_neckline": round(patterns["inverse_hs_neckline"], 4) if patterns["inverse_hs_neckline"] else None,
        "head_shoulders": patterns["head_shoulders"],
        "head_shoulders_neckline": round(patterns["head_shoulders_neckline"], 4) if patterns["head_shoulders_neckline"] else None,
        "bearish_head_shoulders": bearish_head_shoulders,
        "distribution_risk": distribution_risk,
        "late_chase": late_chase,
        "data_source": data_source,
        "data_note": "Float/Short Available/Reverse Split/Level 2 وVWAP اللحظي تحتاج مصدر بيانات مباشر؛ لا يتم اختلاقها. نوع السهم تصنيف وصفي للرصد مبني على الزخم والتذبذب والحجم والبنية اليومية، وليس حكماً على ملاءمة السهم للمستثمر.",

    }




_INTRADAY_CACHE_TTL = 3600
_intraday_cache = {}

def _calc_intraday_liquidity(candles):
    rows = _parse_candles(candles)
    if len(rows) < 12:
        return None
    vols = [max(0.0, x["volume"]) for x in rows]
    closes = [x["close"] for x in rows]
    pv = sum(((x["high"] + x["low"] + x["close"]) / 3) * max(0.0, x["volume"]) for x in rows)
    total_vol = sum(vols)
    vwap = pv / total_vol if total_vol else None

    baseline = sum(vols[-21:-1]) / max(1, len(vols[-21:-1]))
    rvol = vols[-1] / baseline if baseline else 0.0
    last5 = sum(vols[-5:])
    prev5 = sum(vols[-10:-5])
    acceleration = last5 / prev5 if prev5 else 0.0

    up_vol = down_vol = 0.0
    signed = 0.0
    for i, row in enumerate(rows):
        prev_close = rows[i-1]["close"] if i else row["open"]
        if row["close"] >= prev_close:
            up_vol += row["volume"]
            signed += row["volume"]
        else:
            down_vol += row["volume"]
            signed -= row["volume"]
    buy_pressure = (up_vol / (up_vol + down_vol) * 100) if (up_vol + down_vol) else None
    cvd_direction = "صاعد" if signed > 0 else ("هابط" if signed < 0 else "محايد")

    return {
        "vwap": round(vwap, 4) if vwap else None,
        "intraday_rvol": round(rvol, 2),
        "volume_acceleration": round(acceleration, 2),
        "volume_spike": round((vols[-1] / baseline), 2) if baseline else 0.0,
        "buy_pressure": round(buy_pressure, 1) if buy_pressure is not None else None,
        "cvd_direction": cvd_direction,
        "bars": len(rows),
        "source": "Twelve Data 5m",
        "data_note": "ضغط الشراء وCVD مؤشرات حجمية تقريبية وليست Level 2/Order Book."
    }

async def _get_intraday_liquidity(symbols):
    global _twelve_data_quota_exhausted
    symbols = [str(x).upper().strip() for x in symbols if x]
    symbols = list(dict.fromkeys(symbols))[:20]
    if not symbols or not settings.twelve_data_api_key:
        return {}
    now = time.monotonic()
    out, missing = {}, []
    for symbol in symbols:
        cached = _intraday_cache.get(symbol)
        if cached and now - cached[0] < _INTRADAY_CACHE_TTL:
            out[symbol] = cached[1]
        else:
            missing.append(symbol)
    if not missing or _twelve_data_quota_exhausted:
        return out

    # طلب دفعة واحدة فقط للدورة بدل طلب مستقل لكل سهم.
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await twelve_call(client.get, "https://api.twelvedata.com/time_series",
                params={
                    "symbol": ",".join(missing),
                    "interval": "5min",
                    "outputsize": 60,
                    "apikey": settings.twelve_data_api_key,
                    "prepost": "true",
                },
            )
            if r.status_code == 429:
                _twelve_data_quota_exhausted = True
                return out
            r.raise_for_status()
            payload = r.json()
    except Exception:
        return out

    for symbol in missing:
        data = payload.get(symbol) if isinstance(payload, dict) else None
        values = data.get("values", []) if isinstance(data, dict) else []
        if values:
            values = list(reversed(values))
            metrics = _calc_intraday_liquidity(values)
        else:
            metrics = None
        _intraday_cache[symbol] = (now, metrics)
        out[symbol] = metrics
    return out


async def scan_us_low_price_stocks():
    # لا نعيد ضبط حالة استنفاد الحصة كل 30 دقيقة؛ عند 429 يتوقف Twelve Data
    # حتى إعادة تشغيل الخدمة، بينما يستمر الرادار بالمصادر الأساسية.
    candidates = await discover_low_price_stocks()
    results = []
    diagnostics = []
    breakout_diagnostics = []
    risk_reward_diagnostics = []
    candidate_count = len(candidates)
    # تشخيص مراحل الفلترة فقط؛ لا يغيّر شروط استراتيجية SAS أو نتيجة الرصد.
    filter_counts = {
        "classify_checked": 0,
        "sas_core_pass": 0,
        "sas_power_trend": 0,
        "sas_early_timing": 0,
        "sas_rsi_55_70": 0,
        "sas_rvol_1_5": 0,
        "sas_near_entry": 0,
        "sas_no_distribution": 0,
        "sas_no_bearish_hs": 0,
        "sas_no_chase": 0,
        "momentum_accumulation_candidates": 0,
        "momentum_movement_candidates": 0,
        "momentum_breakout_candidates": 0,
        "momentum_rvol_pass": 0,
        "sas_breakout_confirmed": 0,
        "sas_breakout_retest": 0,
        "sas_breakout_reject_rsi": 0,
        "sas_breakout_reject_chase": 0,
        "sas_breakout_reject_distribution": 0,
        "sas_breakout_reject_bearish_hs": 0,
        "sas_breakout_reject_structure": 0,
        "sas_breakout_reject_room": 0,
        "sas_breakout_reject_other": 0,
        "sas_breakout_reject_total": 0,
        "sas_strategy_pass": 0,
        "liquidity_pass": 0,
        "targets_pass": 0,
        "live_levels_pass": 0,
        "risk_reward_checked": 0,
        "risk_reward_pass": 0,
        "risk_reward_warning": 0,
        "final_pass": 0,
    }

    # Top Gainers + RVOL 10 أيام هي بوابة الرادار، ثم السيولة والأهداف/الوقف وR:R.
    shortlist = sorted(
        candidates,
        key=lambda x: (float(x.get("momentum_rvol_10d") or 0), float(x.get("change_pct") or 0)),
        reverse=True,
    )[:180]

    earnings_events = await earnings_calendar_window(5)
    earnings_by_symbol = {}
    for event in earnings_events or []:
        if not isinstance(event, dict):
            continue
        event_symbol = str(event.get("symbol") or "").upper().strip()
        if event_symbol:
            earnings_by_symbol[event_symbol] = {
                "date": event.get("date"),
                "hour": event.get("hour"),
                "eps_estimate": event.get("epsEstimate"),
                "revenue_estimate": event.get("revenueEstimate"),
            }

    # Keep the radar responsive: analyze the staged shortlist concurrently.
    semaphore = asyncio.Semaphore(16)
    # Twelve Data fallback is deliberately limited per cycle to preserve
    # the daily API allowance across the full trading day.
    twelve_fallback_symbols = {
        str(row.get("symbol") or "").upper()
        for row in shortlist[:min(settings.twelve_data_scan_fallback_symbols, 3)]
        if row.get("symbol")
    }

    async def analyze_candidate(row):
        symbol = str(row.get("symbol") or "").upper()
        async with semaphore:
            try:
                classification = await classify_sas(
                    symbol,
                    row,
                    allow_twelve_fallback=symbol in twelve_fallback_symbols,
                )
                filter_counts["classify_checked"] += 1
                # عدّ شروط SAS الفردية لتحديد نقطة الاختناق، دون تغيير pass.
                if row.get("momentum_section") == "accumulation":
                    filter_counts["momentum_accumulation_candidates"] += 1
                elif row.get("momentum_section") == "movement":
                    filter_counts["momentum_movement_candidates"] += 1
                elif row.get("momentum_section") == "breakout":
                    filter_counts["momentum_breakout_candidates"] += 1
                if row.get("momentum_rvol_10d") is not None:
                    filter_counts["momentum_rvol_pass"] += 1
                if classification.get("power_trend"):
                    filter_counts["sas_power_trend"] += 1
                if classification.get("early_timing"):
                    filter_counts["sas_early_timing"] += 1
                rsi_value = _f(classification.get("rsi14"), 0)
                if 55 <= rsi_value <= 70:
                    filter_counts["sas_rsi_55_70"] += 1
                if _f(classification.get("rvol"), 0) >= 1.5:
                    filter_counts["sas_rvol_1_5"] += 1
                if classification.get("near_entry"):
                    filter_counts["sas_near_entry"] += 1
                if not classification.get("distribution_risk"):
                    filter_counts["sas_no_distribution"] += 1
                if not classification.get("head_shoulders") or classification.get("price", 0) >= 0:
                    # bearish_head_shoulders غير معاد كحقل مستقل؛ نستنتجه من سبب/حقول التصنيف أدناه.
                    if "⚠️ رأس وكتفين هابط مؤكد" not in str(classification.get("reason") or ""):
                        filter_counts["sas_no_bearish_hs"] += 1
                if not classification.get("chase_risk"):
                    filter_counts["sas_no_chase"] += 1
                if classification.get("breakout_confirmed"):
                    filter_counts["sas_breakout_confirmed"] += 1
                if classification.get("breakout_retest"):
                    filter_counts["sas_breakout_retest"] += 1

                # تشخيص مستقل للاختراقات المؤكدة: نريد معرفة الشرط الذي
                # أسقط كل حالة قبل تعديل الاستراتيجية نفسها.
                if classification.get("breakout_confirmed"):
                    reject_reasons = []
                    rsi_value = _f(classification.get("rsi14"), 0)
                    if not (48 <= rsi_value <= 75):
                        reject_reasons.append("rsi")
                    if classification.get("distribution_risk"):
                        reject_reasons.append("distribution")
                    if classification.get("bearish_head_shoulders"):
                        reject_reasons.append("bearish_hs")
                    structure_ok = bool(
                        classification.get("power_trend")
                        or classification.get("sma20_above_sma50")
                        or classification.get("accumulation")
                    )
                    if not structure_ok:
                        reject_reasons.append("structure")
                    room = classification.get("breakout_room_pct")
                    if room is not None and room < 3.0:
                        reject_reasons.append("room")

                    breakout_diagnostics.append({
                        "symbol": symbol,
                        "price": round(_f(classification.get("price") or row.get("price")), 4),
                        "rsi": classification.get("rsi14"),
                        "rvol": classification.get("rvol"),
                        "breakout_level": classification.get("breakout_level"),
                        "next_resistance": classification.get("next_resistance"),
                        "extension_pct": classification.get("breakout_extension_pct"),
                        "room_pct": classification.get("breakout_room_pct"),
                        "retest": bool(classification.get("breakout_retest")),
                        "chase": bool(classification.get("chase_risk")),
                        "distribution": bool(classification.get("distribution_risk")),
                        "bearish_hs": bool(classification.get("bearish_head_shoulders")),
                        "structure_ok": structure_ok,
                        "breakout_pass": bool(classification.get("breakout_pass")),
                        "strategy_pass": bool(classification.get("strategy_pass")),
                        "reject_reasons": reject_reasons,
                    })
                    if reject_reasons:
                        filter_counts["sas_breakout_reject_total"] += 1
                        for reason in reject_reasons:
                            key = {
                                "rsi": "sas_breakout_reject_rsi",
                                "chase": "sas_breakout_reject_chase",
                                "distribution": "sas_breakout_reject_distribution",
                                "bearish_hs": "sas_breakout_reject_bearish_hs",
                                "structure": "sas_breakout_reject_structure",
                                "room": "sas_breakout_reject_room",
                            }.get(reason)
                            if key:
                                filter_counts[key] += 1
                    else:
                        # إذا لم يطابق أي سبب معروف، نسجلها كسبب غير مصنف.
                        # لا نغيّر نتيجة strategy_pass؛ هذا عداد تشخيصي فقط.
                        if not classification.get("breakout_pass"):
                            filter_counts["sas_breakout_reject_other"] += 1

                if classification.get("strategy_pass"):
                    filter_counts["sas_strategy_pass"] += 1
                if classification.get("pass"):
                    filter_counts["sas_core_pass"] += 1
                if not classification.get("pass"):
                    return None, {
                        "symbol": symbol,
                        "exchange": row.get("exchange"),
                        "status": "filtered",
                        "reason": classification.get("reason"),
                        "data_source": classification.get("data_source"),
                    }

                # التأكيد المتقدم لا يعمل كحاجز ثانٍ فوق بوابة SAS الأساسية.
                # إذا كانت الإشارة الأساسية صالحة، نستخدم التأكيد لرفع/خفض الثقة
                # فقط. الحظر الحقيقي يُترك للتناقضات الهابطة القوية.
                if classification.get("advanced_confirmation_status") == "حظر هابط قوي":
                    return None, {
                        "symbol": symbol,
                        "exchange": row.get("exchange"),
                        "status": "filtered",
                        "reason": "تناقض هابط قوي في التأكيد المتقدم",
                        "advanced_confirmation_score": classification.get("advanced_confirmation_score"),
                        "market_structure": classification.get("market_structure"),
                        "rsi_divergence": classification.get("rsi_divergence"),
                    }

                # فلترة السيولة: بعد بوابة SAS الأساسية، نتحقق من التداول النقدي الفعلي
                # وحجمًا متوافقًا مع الحركة. لا توجد هنا بوابة Strategy قديمة.
                entry_price = _f(row.get("price"), 0)
                daily_volume = _f(row.get("volume"), 0)
                dollar_volume = entry_price * daily_volume
                daily_rvol = _f(classification.get("rvol"), 0)
                if (
                    entry_price <= 0
                    or daily_volume <= 0
                    or dollar_volume < MIN_DAILY_DOLLAR_VOLUME
                    or (
                        daily_rvol < 0.80
                        and not bool(row.get("accumulation_signal"))
                    )
                ):
                    return None, {
                        "symbol": symbol,
                        "exchange": row.get("exchange"),
                        "status": "filtered",
                        "reason": "سيولة يومية غير كافية أو غير مؤكدة",
                        "data_source": classification.get("data_source"),
                    }

                classification["dollar_volume"] = round(dollar_volume, 2)
                classification["liquidity_quality"] = "مقبولة"
                filter_counts["liquidity_pass"] += 1

                # الأهداف والأخبار مستقلان ويمكن جلبهما بالتوازي.
                # لا نطلب Twelve Data quote لكل سهم مقبول؛ مصدر الاكتشاف
                # يحمل السعر/التغير بالفعل، واستدعاء quote الجماعي يسبب 429
                # ويمنع الإشارة من الوصول للقناة. نستخدم quote فقط إذا لم
                # يتوفر سعر صالح من بيانات الاكتشاف.
                targets, news = await asyncio.gather(
                    technical_targets(symbol),
                    company_news(symbol, days=2),
                )

                # لا تُرسل إشارة قابلة للتنفيذ بدون هدف سعري مرصود فعليًا.
                # status=ok مع targets=[] يعني أن البيانات موجودة لكن لا توجد مقاومة
                # مؤكدة فوق السعر يمكن اعتمادها كهدف.
                target_levels = targets.get("targets") if isinstance(targets, dict) else None
                if (
                    not isinstance(targets, dict)
                    or targets.get("status") != "ok"
                    or not isinstance(target_levels, list)
                    or not target_levels
                ):
                    return None, {
                        "symbol": symbol,
                        "exchange": row.get("exchange"),
                        "status": "filtered",
                        "reason": "لا يوجد هدف سعري مؤكد من مقاومة مرصودة",
                        "data_source": (targets or {}).get("method") if isinstance(targets, dict) else None,
                        "target_status": (targets or {}).get("status") if isinstance(targets, dict) else None,
                    }

                filter_counts["targets_pass"] += 1

                live_price = _f(row.get("live_price"), 0)
                if live_price <= 0:
                    live_price = _f(row.get("price"), 0)

                live_change = row.get("live_change_pct")
                if live_change is None:
                    live_change = row.get("change_pct")

                live_source = row.get("live_price_source") or row.get("source") or "scan data"

                # طابق الأهداف/الوقف مع آخر سعر حي للرادار. بيانات الشموع اليومية قد
                # تكون أحدث/أقدم من لقطة الاكتشاف، لذلك لا نسمح بوقف فوق سعر الدخول
                # أو بهدف أصبح أسفل/عند السعر الحالي.
                if live_price > 0 and isinstance(targets, dict):
                    raw_targets = []
                    for value in (targets.get("targets") or [])[:5]:
                        try:
                            level = float(value)
                        except (TypeError, ValueError):
                            continue
                        if level > live_price * 1.001:
                            raw_targets.append(round(level, 4))

                    atr = _f(targets.get("atr"), 0)
                    exit_level = _f(targets.get("exit"), 0)

                    if exit_level >= live_price or exit_level <= 0:
                        exit_level = live_price - atr if atr > 0 else 0

                    if not raw_targets or exit_level <= 0:
                        return None, {
                            "symbol": symbol,
                            "exchange": row.get("exchange"),
                            "status": "filtered",
                            "reason": "لا يوجد هدف فوق السعر الحالي مع وقف أسفل سعر الدخول",
                            "data_source": "PanWatch",
                        }

                    targets = {
                        **targets,
                        "targets": raw_targets,
                        "resistances": raw_targets,
                        "exit": round(exit_level, 4),
                        "support": round(exit_level, 4),
                    }

                filter_counts["live_levels_pass"] += 1

                # fallback وحيد عند الحاجة فقط.
                if live_price <= 0:
                    try:
                        live = await quote(symbol)
                        live_price = _f(live.get("price"), 0)
                        live_change = live.get("change_pct") if live.get("change_pct") is not None else live_change
                        live_source = live.get("source") or "Twelve Data"
                    except Exception:
                        pass

                # لا نرسل إشارة إذا كان الهدف الأول لا يعوض المخاطرة بوضوح.
                # هذا لا يصنع هدفًا جديدًا؛ يستخدم فقط المستويات المرصودة مسبقًا.
                if live_price > 0 and isinstance(targets, dict):
                    first_target = _f((targets.get("targets") or [0])[0], 0)
                    stop = _f(targets.get("exit"), 0)
                    risk = live_price - stop
                    reward = first_target - live_price
                    risk_reward = (reward / risk) if risk > 0 and reward > 0 else 0
                    risk_reward_diagnostics.append({
                        "symbol": symbol,
                        "price": round(live_price, 4),
                        "target1": round(first_target, 4),
                        "stop": round(stop, 4),
                        "risk": round(risk, 4),
                        "reward": round(reward, 4),
                        "risk_reward": round(risk_reward, 4),
                        "passed": risk_reward >= 1.5,
                    })
                    logger.info(
                        "Risk/reward diagnostic: %s | price=%.4f target1=%.4f stop=%.4f risk=%.4f reward=%.4f rr=%.4f",
                        symbol,
                        live_price,
                        first_target,
                        stop,
                        risk,
                        reward,
                        risk_reward,
                    )
                    # R:R is informational here, not a rejection gate.
                    # Keep a qualifying opportunity in the radar and expose
                    # low R:R as a warning in the user-facing report.
                    targets["risk_reward"] = round(risk_reward, 2)
                    targets["risk_reward_threshold"] = 1.5
                    targets["risk_reward_warning"] = risk_reward < 1.5
                    filter_counts["risk_reward_checked"] += 1
                    if risk_reward >= 1.5:
                        filter_counts["risk_reward_pass"] += 1
                    else:
                        filter_counts["risk_reward_warning"] += 1

                filter_counts["final_pass"] += 1
                earnings_warning = earnings_by_symbol.get(symbol)
                # حالة شروط الرادار الفعلية التي اجتازها السهم.
                # هذه بيانات مشتقة من الفلاتر المستخدمة فعليًا، وليست تقييمًا إنشائيًا.
                momentum_section = str(row.get("momentum_section") or "").lower()
                momentum_label = (
                    "تجميع"
                    if momentum_section == "accumulation"
                    else "حركة"
                    if momentum_section == "movement"
                    else "اختراق قريب"
                    if momentum_section == "breakout"
                    else "غير محدد"
                )
                momentum_threshold = _f(row.get("momentum_rvol_threshold"), 0)
                momentum_rvol = _f(row.get("momentum_rvol_10d"), 0)
                radar_checks = {
                    "momentum": True,
                    "momentum_label": momentum_label,
                    "momentum_rvol": round(momentum_rvol, 2) if momentum_rvol else None,
                    "momentum_rvol_threshold": round(momentum_threshold, 2) if momentum_threshold else None,
                    "sas_core": bool(classification.get("pass")),
                    "liquidity": True,
                    "rvol": daily_rvol >= 1.0,
                    "target": True,
                    "live_levels": True,
                    "no_distribution": not bool(classification.get("distribution_risk")),
                    "no_bearish_hs": not bool(classification.get("bearish_head_shoulders")),
                    "no_chase": not bool(classification.get("chase_risk")),
                    "advanced_confirmation": bool(classification.get("advanced_confirmation_pass")),
                    "advanced_confirmation_status": classification.get("advanced_confirmation_status"),
                    "advanced_confirmation_score": classification.get("advanced_confirmation_score"),
                    "market_structure": classification.get("market_structure"),
                    "fibonacci_zone": (classification.get("fibonacci") or {}).get("zone"),
                    "fvg": (classification.get("fvg") or {}).get("type") if (classification.get("fvg") or {}).get("active") else None,
                    "rsi_divergence": (classification.get("rsi_divergence") or {}).get("state"),
                    "risk_reward": round(_f(targets.get("risk_reward"), 0), 2) if isinstance(targets, dict) and targets.get("risk_reward") is not None else None,
                    "risk_reward_warning": bool(isinstance(targets, dict) and targets.get("risk_reward_warning")),
                }

                result_row = {
                    **row,
                    "symbol": symbol,
                    "earnings_warning": earnings_warning,
                    "earnings_within_5_days": bool(earnings_warning),
                    "exchange": _normalize_exchange(row.get("exchange")),
                    "classification": classification,
                    "targets": targets,
                    "catalyst": bool(news),
                    "news_count": len(news) if isinstance(news, list) else 0,
                    "catalyst_news": select_catalyst(news),
                    "news_items": [
                        {
                            "headline": str(item.get("headline") or "").strip(),
                            "source": str(item.get("source") or "Finnhub").strip(),
                            "url": str(item.get("url") or "").strip(),
                            "datetime": item.get("datetime"),
                            "summary": str(item.get("summary") or "").strip()[:800],
                        }
                        for item in (news or [])[:max(1, settings.ai_max_news)]
                        if isinstance(item, dict) and str(item.get("headline") or "").strip()
                    ],
                    "live_price": live_price if live_price > 0 else None,
                    "live_change_pct": live_change,
                    "live_price_source": live_source,
                    "radar_checks": radar_checks,
                }

                if live_price > 0:
                    result_row["price"] = live_price
                if live_change is not None:
                    result_row["change_pct"] = live_change

                return (result_row, None)
            except Exception as exc:
                return None, {
                    "symbol": symbol,
                    "exchange": row.get("exchange"),
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}",
                }

    analyzed = await asyncio.gather(
        *(analyze_candidate(row) for row in shortlist),
        return_exceptions=False,
    )

    for result, diagnostic in analyzed:
        if result:
            results.append(result)
        if diagnostic:
            diagnostics.append(diagnostic)

    # Intraday is a confirmation bonus for the very best candidates only.
    # We deliberately do not request intraday data for the whole shortlist.
    results.sort(
        key=lambda x: (
            int((x.get("classification") or {}).get("score") or 0),
            float((x.get("classification") or {}).get("rvol") or 0),
            float(x.get("change_pct") or 0),
        ),
        reverse=True,
    )
    intraday_symbols = [x.get("symbol") for x in results[:settings.twelve_data_intraday_symbols]]
    intraday = await _get_intraday_liquidity(intraday_symbols)
    for item in results:
        metrics = intraday.get(str(item.get("symbol") or "").upper())
        if metrics:
            item["intraday"] = metrics
            buy_pressure = _f(metrics.get("buy_pressure"), 0)
            acceleration = _f(metrics.get("volume_acceleration"), 0)
            item["classification"]["intraday_confirmation"] = bool(
                buy_pressure >= 55 and acceleration >= 1.05 and metrics.get("cvd_direction") == "صاعد"
            )
        else:
            item["classification"]["intraday_confirmation"] = False

    results.sort(
        key=lambda x: (
            1 if (x.get("classification") or {}).get("intraday_confirmation") else 0,
            float(x.get("momentum_rvol_10d") or 0),
            int((x.get("classification") or {}).get("score") or 0),
            float(x.get("change_pct") or 0),
        ),
        reverse=True,
    )
    small = [x for x in results if x.get("momentum_section") == "small"][:MAX_SECTION_RESULTS]
    large = [x for x in results if x.get("momentum_section") == "large"][:MAX_SECTION_RESULTS]
    results = small + large
    filtered = [x for x in diagnostics if x.get("status") == "filtered"]
    errors = [x for x in diagnostics if x.get("status") == "error"]

    return {
        "stocks": results,
        "momentum_sections": {"small": small, "large": large},
        "diagnostics": {
            "candidates": candidate_count,
            "shortlist": len(shortlist),
            "passed": len(results),
            "filtered": len(filtered),
            "errors": len(errors),
            "filtered_examples": [
                {"symbol": x.get("symbol"), "reason": x.get("reason"), "data_source": x.get("data_source")}
                for x in filtered[:20]
            ],
            "error_examples": [
                {"symbol": x.get("symbol"), "reason": x.get("reason")}
                for x in errors[:10]
            ],
            "passed_examples": [x.get("symbol") for x in results[:20]],
            "breakout_diagnostics": sorted(
                breakout_diagnostics,
                key=lambda x: (
                    len(x.get("reject_reasons") or []),
                    float(x.get("rvol") or 0),
                ),
                reverse=True,
            ),
            "risk_reward_diagnostics": sorted(
                risk_reward_diagnostics,
                key=lambda x: float(x.get("risk_reward") or 0),
                reverse=True,
            ),
            "filter_counts": filter_counts,
            "price_source": "Yahoo Finance Day Gainers + PanWatch + OpenTerminal/Nasdaq fallbacks",
        "momentum_source": "Yahoo Finance Day Gainers",
        "momentum_rules": {
            "small": {"price": "0.5-20", "change_pct": ">10", "volume": ">500000"},
            "large": {"market_cap": ">1B", "change_pct": ">3"},
            "rvol_formula": "today_volume / average_volume_last_10_sessions",
            "rvol_time_thresholds": MOMENTUM_RVOL_THRESHOLDS,
            "exchanges": sorted(ALLOWED_EXCHANGES),
            "earnings_warning_window_days": 5,
        },
            "twelve_data_quota_exhausted": _twelve_data_quota_exhausted,
        },
    }