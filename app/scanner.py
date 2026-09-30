import asyncio
import httpx
import time
from .config import settings
from .panwatch import technical_targets
from .news import company_news
from .market import quote

# رادار SAS PRO:
# - السوق: NASDAQ فقط
# - السعر: $0.30 - $6
# - منهج فيصل: السلوك، التداول، RVOL، الدعم/المقاومة والثبات.
MIN_PRICE = 0.30
MAX_PRICE = 6.00
ALLOWED_EXCHANGES = {"NASDAQ"}

# Daily candles change slowly, so cache them between radar cycles.
_CANDLE_CACHE_TTL = 1800
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
        r = await client.get(
            "https://api.twelvedata.com/market_movers/stocks",
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
                    params={"market": "US", "days": 90, "interval": "1d"},
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
                    r = await client.get(
                        "https://api.twelvedata.com/time_series",
                        params={
                            "symbol": key,
                            "interval": "1day",
                            "outputsize": 90,
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


async def classify_faisal(symbol: str, quote: dict | None = None, allow_twelve_fallback: bool = False):
    async with httpx.AsyncClient(timeout=min(settings.panwatch_timeout_seconds, 30)) as client:
        candles, data_source = await _get_analysis_candles(client, symbol, allow_twelve_fallback)

    if len(candles) < 30:
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

    scores = {
        "momentum": 0,
        "accumulation": 0,
        "w": 0,
        "sweep": 0,
        "fill_gap": 0,
        "runner_former": 0,
        "double_bottom": 0,
        "inverse_head_shoulders": 0,
    }
    if momentum:
        scores["momentum"] += 3
    if accumulation:
        scores["accumulation"] += 3
    if w_pattern:
        scores["w"] += 3
    if sweep:
        scores["sweep"] += 3
    if fill_gap:
        scores["fill_gap"] += 2
    if former_runner:
        scores["runner_former"] += 2
    if double_bottom_confirmed:
        scores["double_bottom"] += 4
    if inverse_hs_confirmed:
        scores["inverse_head_shoulders"] += 4
    if rvol >= 3:
        for k in scores:
            scores[k] += 1

    behavior_key = max(scores, key=scores.get)
    behavior_names = {
        "momentum": ("زخم", "🔵"),
        "accumulation": ("ارتكاز/تجميع", "🟢"),
        "w": ("W", "🟢"),
        "sweep": ("سحب سيولة", "🟡"),
        "fill_gap": ("تغطية فجوة", "🟡"),
        "runner_former": ("Runner Former", "🟣"),
        "double_bottom": ("قاع مزدوج مؤكد", "🟢"),
        "inverse_head_shoulders": ("رأس وكتفين مقلوب مؤكد", "🟢"),
    }
    behavior, emoji = behavior_names[behavior_key]

    # تصنيف نوع السهم — أربع فئات فقط.
    # الوصف مبني على الحركة/التذبذب/الحجم والسلوك المرصود، وليس توصية.
    if (
        (momentum and rvol >= 5)
        or (former_runner and rvol >= 4)
        or (sweep and rvol >= 5)
        or atr_pct >= 0.15
        or (change_pct >= 12 and rvol >= 3)
    ):
        stock_type = "مضاربي سريع خطير"
    elif (
        momentum
        or sweep
        or rvol >= 3
        or atr_pct >= 0.10
        or former_runner
        or change_pct >= 8
    ):
        stock_type = "مضاربي"
    elif (
        accumulation
        or w_pattern
        or double_bottom_confirmed
        or inverse_hs_confirmed
        or breakout
        or (sma20 >= sma50 * 1.02 and fill_gap)
    ):
        stock_type = "سوينق"
    else:
        stock_type = "استثماري"

    evidence = []
    if former_runner:
        evidence.append("سلوك سابق قوي")
    if rvol >= 2:
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
        "score": min(100, sum(scores.values()) * 10),
        "pass": bool(
            (
                accumulation or momentum or sweep or w_pattern or fill_gap
                or double_bottom_confirmed or inverse_hs_confirmed
            )
            and not distribution_risk
            and not late_chase
            and not bearish_head_shoulders
        ),
        "reason": " + ".join(evidence) if evidence else "لا توجد تركيبة واضحة من منهج فيصل",
        "rvol": round(rvol, 2),
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




_INTRADAY_CACHE_TTL = 600
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
            r = await client.get(
                "https://api.twelvedata.com/time_series",
                params={
                    "symbol": ",".join(missing),
                    "interval": "5min",
                    "outputsize": 60,
                    "apikey": settings.twelve_data_api_key,
                    "prepost": "true",
                },
            )
            if r.status_code == 429:
                global _twelve_data_quota_exhausted
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
    global _twelve_data_quota_exhausted
    _twelve_data_quota_exhausted = False
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
        for row in shortlist[:settings.twelve_data_scan_fallback_symbols]
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

                result_row = {
                    **row,
                    "symbol": symbol,
                    "exchange": _normalize_exchange(row.get("exchange")),
                    "classification": classification,
                    "targets": targets,
                    "catalyst": bool(news),
                    "news_count": len(news) if isinstance(news, list) else 0,
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

    # Intraday liquidity is requested once per cycle for only the top few
    # passed symbols. The result is cached for 30 minutes.
    results.sort(
        key=lambda x: (
            int((x.get("classification") or {}).get("score") or 0),
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

    results.sort(
        key=lambda x: (
            int((x.get("classification") or {}).get("score") or 0),
            float((x.get("intraday") or {}).get("intraday_rvol") or 0),
            float(x.get("change_pct") or 0),
        ),
        reverse=True,
    )
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
            "price_source": "Nasdaq Screener + cached/PanWatch data; Twelve Data only for limited fallbacks",
            "twelve_data_quota_exhausted": _twelve_data_quota_exhausted,
        },
    }