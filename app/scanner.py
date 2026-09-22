import asyncio
import httpx
from .config import settings
from .panwatch import technical_targets
from .news import company_news

# رادار SAS PRO:
# - السوق: NASDAQ فقط
# - السعر: $0.50 - $30
# - منهج فيصل: السلوك، الفوليوم، RVOL، الدعم/المقاومة والثبات.
MIN_PRICE = 0.50
MAX_PRICE = 30.00
DISCOVERY_LIMIT = 100
CANDIDATE_LIMIT = 15
ALLOWED_EXCHANGE = "NASDAQ"


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nasdaq(row):
    exchange = str(row.get("exchange") or row.get("mic_code") or "").upper().strip()
    return exchange == ALLOWED_EXCHANGE


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


async def _discover_twelvedata(client):
    if not settings.twelve_data_api_key:
        return []

    # market_movers غير متاح لبعض خطط Twelve Data، لذلك لا نعتمد عليه.
    # هذه الدالة تبقى مصدرًا احتياطيًا إذا أصبح endpoint متاحًا لاحقًا.
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
        rows = (r.json().get("values") or [])
    except Exception:
        return []

    out = []
    for row in rows:
        if not _is_nasdaq(row):
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
                "exchange": "NASDAQ",
                "source": "Twelve Data",
            })
    return out


async def _discover_panwatch(client):
    base = settings.panwatch_base_url.rstrip("/")
    try:
        r = await client.get(
            f"{base}/api/discovery/stocks",
            params={"market": "US", "mode": "gainers", "limit": DISCOVERY_LIMIT},
        )
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return []

    out = []
    for row in rows or []:
        if not _is_nasdaq(row):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        price = _f(row.get("price"), -1)
        if symbol and MIN_PRICE <= price <= MAX_PRICE:
            out.append({**row, "exchange": "NASDAQ", "source": "PanWatch"})
    return out


async def discover_low_price_stocks():
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        sources = await asyncio.gather(
            _discover_twelvedata(client),
            _discover_panwatch(client),
            return_exceptions=True,
        )

    merged, seen = [], set()
    for source_rows in sources:
        if isinstance(source_rows, Exception):
            continue
        for row in source_rows:
            if not _is_nasdaq(row):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            merged.append(row)

    return merged[:DISCOVERY_LIMIT]


async def classify_faisal(symbol: str, quote: dict | None = None):
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        r = await client.get(
            f"{base}/api/klines/{symbol.upper()}",
            params={"market": "US", "days": 90, "interval": "1d"},
        )
        r.raise_for_status()
        candles = _parse_candles(r.json().get("klines", []))

    if len(candles) < 30:
        return {
            "behavior": "غير واضح",
            "type": "غير واضح",
            "emoji": "⚪",
            "score": 0,
            "pass": False,
            "reason": "بيانات غير كافية",
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

    momentum = (
        rvol >= 2.0 and change_pct >= 3.0 and
        (breakout or (support is not None and _near(support, price, 0.08)))
    )

    old_high = max(c["high"] for c in candles[-30:-5])
    fill_gap = change_pct > 3 and price < old_high and sma20 >= sma50 * 0.98 and rvol >= 1.2

    distribution_risk = rvol >= 2.5 and abs(change_pct) < 1.5

    scores = {
        "momentum": 0,
        "accumulation": 0,
        "w": 0,
        "sweep": 0,
        "fill_gap": 0,
        "runner_former": 0,
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
    }
    behavior, emoji = behavior_names[behavior_key]

    stock_type = "مضاربي" if momentum or sweep or rvol >= 3 or atr_pct >= 0.10 else "سوينق"

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
    if fill_gap:
        evidence.append("مناطق هبوط/فجوة سابقة")
    if distribution_risk:
        evidence.append("⚠️ فوليوم مرتفع بدون تقدم واضح")

    return {
        "behavior": behavior,
        "type": stock_type,
        "emoji": emoji,
        "score": min(100, sum(scores.values()) * 10),
        "pass": bool((accumulation or momentum or sweep or w_pattern or fill_gap) and not distribution_risk),
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
        "distribution_risk": distribution_risk,
        "data_note": "Float/Short Available/Reverse Split/Level 2 تحتاج مصدر بيانات مباشر؛ لا يتم اختلاقها.",
    }


async def scan_us_low_price_stocks():
    candidates = await discover_low_price_stocks()
    results = []
    for row in candidates:
        symbol = str(row.get("symbol") or "").upper()
        try:
            classification = await classify_faisal(symbol, row)
            if not classification.get("pass"):
                continue
            targets = await technical_targets(symbol)
            news = await company_news(symbol, days=2)
        except Exception:
            continue
        results.append({
            **row,
            "symbol": symbol,
            "exchange": "NASDAQ",
            "classification": classification,
            "targets": targets,
            "catalyst": bool(news),
            "news_count": len(news) if isinstance(news, list) else 0,
        })

    results.sort(
        key=lambda x: (
            int((x.get("classification") or {}).get("score") or 0),
            float((x.get("classification") or {}).get("rvol") or 0),
            float(x.get("change_pct") or 0),
        ),
        reverse=True,
    )
    return results[:CANDIDATE_LIMIT]
