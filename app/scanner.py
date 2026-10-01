import asyncio
import httpx
import time
from .config import settings
from .panwatch import technical_targets
from .news import company_news, select_catalyst
from .market import quote
from .twelve_guard import call as twelve_call

# رادار SAS PRO:
# - السوق: NASDAQ فقط
# - السعر: $0.30 - $15
# - لا تُرسل القناة إلا الإشارات النوعية ذات السيولة والأهداف الصالحة.
# - منهج فيصل: السلوك، التداول، RVOL، الدعم/المقاومة والثبات.
MIN_PRICE = 0.30
MAX_PRICE = 15.00
MAX_RADAR_RESULTS = 5
MIN_DAILY_DOLLAR_VOLUME = 1_000_000.0
ALLOWED_EXCHANGES = {"NASDAQ"}

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
    if exchange == "NASDAQ":
        return "NASDAQ"
    return exchange


def _is_allowed_exchange(row):
    exchange = _normalize_exchange(row.get("exchange") or row.get("mic_code"))
    return exchange == "NASDAQ"


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


async def _discover_us_exchanges(client):
    # Nasdaq public screener: NASDAQ only.
    out = []
    for exchange in ("NASDAQ",):
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
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/146.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json,text/plain,*/*",
                    "Origin": "https://www.nasdaq.com",
                    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
                },
            )
            r.raise_for_status()
            payload = r.json()
            rows = ((payload.get("data") or {}).get("rows") or [])
        except Exception:
            continue

        excluded_words = (
            "WARRANT", "RIGHT", "UNIT", "PREFERRED", "ETF",
            "NOTE", "DEPOSITARY", "TRUST",
        )
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            name = str(row.get("name") or "").strip()
            price = _parse_money(row.get("lastsale"))
            change_pct = _parse_money(row.get("pctchange"))
            volume = _parse_money(row.get("volume"))
            if not symbol or not name:
                continue
            if any(word in name.upper() for word in excluded_words):
                continue
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue
            out.append({
                "symbol": symbol,
                "name": name,
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "exchange": exchange,
                "source": "Nasdaq Screener",
            })
    out.sort(key=lambda x: x["change_pct"], reverse=True)
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
        if exchange != "NASDAQ" or not symbol or not (MIN_PRICE <= price <= MAX_PRICE):
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
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        sources = await asyncio.gather(
            _discover_us_exchanges(client),
            _discover_openterminal(client),
            _discover_panwatch(client),
            return_exceptions=True,
        )

    merged, seen = [], set()
    for source_rows in sources:
        if isinstance(source_rows, Exception):
            continue
        for row in source_rows:
            if not _is_allowed_exchange(row):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            merged.append(row)

    return merged


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
        and settings.twelve_data_api_key
        and not _twelve_data_quota_exhausted
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

async def classify_faisal(symbol: str, quote: dict | None = None, allow_twelve_fallback: bool = False):
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

    breakout = bool(resistance and price > resistance and candles[-1]["close"] > resistance)

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
        or (breakout and resistance is not None and abs(distance_from_ema20_pct or 99) <= 5.0)
    )

    # منهج فيصل: لا نطارد الحركة المتأخرة.
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

    patterns = _detect_chart_patterns(candles, price, rvol)
    double_bottom_confirmed = patterns["double_bottom_confirmed"]
    inverse_hs_confirmed = patterns["inverse_hs_confirmed"]

    distribution_risk = rvol >= 2.5 and abs(change_pct) < 1.5
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
    chase_risk = bool(
        (distance_from_ema20_pct is not None and distance_from_ema20_pct > 8)
        or (rsi14 is not None and rsi14 > 73)
        or (power_trend and power_trend_age > 5)
    )

    score = trend_score + momentum_score + volume_score + relative_strength_score + breakout_quality_score + risk_score

    # The requested concept is an early-trend detector, not a generic gainer filter.
    # Existing Faisal exclusions remain: distribution/bearish H&S/late chase.
    core_pass = bool(
        power_trend
        and early_timing
        and 55 <= (rsi14 or 0) <= 70
        and rvol >= 1.5
        and near_entry
        and not distribution_risk
        and not bearish_head_shoulders
        and not chase_risk
    )

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
        evidence.append("اختراق مع ثبات")
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
        "score": score,
        "pass": core_pass,
        "reason": " + ".join(evidence) if evidence else "لا توجد تركيبة واضحة من منهج فيصل",
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
        "score_breakdown": {
            "trend": trend_score,
            "momentum": momentum_score,
            "volume": volume_score,
            "relative_strength": relative_strength_score,
            "breakout_quality": breakout_quality_score,
            "risk": risk_score,
        },
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
    candidate_count = len(candidates)

    # رادار دوري: نستخدم مسحاً مرحلياً حتى لا تعلق دورة الرصد
    # على آلاف طلبات البيانات. نأخذ أعلى الأسهم حركةً + أعلى الأسهم تداولاً،
    # ثم نطبق منهج فيصل كاملاً على هذه القائمة.
    by_change = sorted(
        candidates,
        key=lambda x: float(x.get("change_pct") or 0),
        reverse=True,
    )
    by_volume = sorted(
        candidates,
        key=lambda x: float(x.get("volume") or 0),
        reverse=True,
    )
    shortlist = []
    seen_shortlist = set()
    for row in by_change[:120] + by_volume[:80]:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or symbol in seen_shortlist:
            continue
        seen_shortlist.add(symbol)
        shortlist.append(row)

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
                classification = await classify_faisal(
                    symbol,
                    row,
                    allow_twelve_fallback=symbol in twelve_fallback_symbols,
                )
                if not classification.get("pass"):
                    return None, {
                        "symbol": symbol,
                        "exchange": row.get("exchange"),
                        "status": "filtered",
                        "reason": classification.get("reason"),
                        "data_source": classification.get("data_source"),
                    }

                # فلترة السيولة: لا يكفي أن يكون السهم رابحًا؛ نريد تداولًا
                # نقديًا فعليًا وحجمًا متوافقًا مع الحركة، مع الحفاظ على الأسهم
                # التي يثبتها منهج فيصل حتى لو لم تكن في أعلى قائمة الحجم.
                entry_price = _f(row.get("price"), 0)
                daily_volume = _f(row.get("volume"), 0)
                dollar_volume = entry_price * daily_volume
                daily_rvol = _f(classification.get("rvol"), 0)
                if (
                    entry_price <= 0
                    or daily_volume <= 0
                    or dollar_volume < MIN_DAILY_DOLLAR_VOLUME
                    or daily_rvol < 1.0
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
                    if risk_reward < 1.5:
                        return None, {
                            "symbol": symbol,
                            "exchange": row.get("exchange"),
                            "status": "filtered",
                            "reason": "نسبة المخاطرة إلى الهدف الأول أقل من 1.5",
                            "data_source": targets.get("method") or "PanWatch",
                        }
                    targets["risk_reward"] = round(risk_reward, 2)

                result_row = {
                    **row,
                    "symbol": symbol,
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
            int((x.get("classification") or {}).get("score") or 0),
            float((x.get("classification") or {}).get("rvol") or 0),
            float((x.get("intraday") or {}).get("buy_pressure") or 0),
            float(x.get("change_pct") or 0),
        ),
        reverse=True,
    )
    # القناة تستقبل عددًا محدودًا من الإشارات النوعية فقط.
    results = results[:MAX_RADAR_RESULTS]
    filtered = [x for x in diagnostics if x.get("status") == "filtered"]
    errors = [x for x in diagnostics if x.get("status") == "error"]

    return {
        "stocks": results,
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
            "price_source": "Nasdaq/OpenTerminal/PanWatch + limited Twelve Data fallbacks",
            "twelve_data_quota_exhausted": _twelve_data_quota_exhausted,
        },
    }