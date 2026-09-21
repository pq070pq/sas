import httpx
from .config import settings
from .panwatch import technical_targets
from .news import company_news

# تطبيق فلترة "طريقة فيصل" على كون الرادار المطلوب:
# السعر في SAS PRO: $0.50 - $30 (توسيع للنطاق الذي طلبه المستخدم)
# مع الاحتفاظ بمنطق المنهج: فلووم، RVOL، سلوك سابق، دعم/طلب، محفز، وثبات.
MIN_PRICE = 0.50
MAX_PRICE = 30.00
DISCOVERY_LIMIT = 100
CANDIDATE_LIMIT = 15


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


async def discover_low_price_stocks():
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        r = await client.get(
            f"{base}/api/discovery/stocks",
            params={"market": "US", "mode": "gainers", "limit": DISCOVERY_LIMIT},
        )
        r.raise_for_status()
        rows = r.json()

    out, seen = [], set()
    for row in rows or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        price = _f(row.get("price"), -1)
        if not symbol or symbol in seen or not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        seen.add(symbol)
        out.append(row)
    return out


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

    # Runner Former: سبق للسهم أن حقق موجة قوية من قاعدة تاريخية.
    max_30d = max(closes[-30:])
    min_30d = min(closes[-30:])
    former_runner = min_30d > 0 and (max_30d / min_30d - 1) >= 1.00

    # تجميع: تماسك قريب من دعم + هبوط أقل + قيعان أعلى + انخفاض نسبي في فوليوم البيع.
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

    # W: قاعان متقاربان ثم اختراق القمة بينهما.
    lows = [c["low"] for c in candles[-40:]]
    mid = len(lows) // 2
    left_low = min(lows[:mid])
    right_low = min(lows[mid:])
    neckline = max(c["high"] for c in candles[-40:] if c["low"] not in (left_low, right_low))
    w_pattern = abs(left_low - right_low) / max(price, 0.0001) <= 0.10 and price >= neckline * 0.995

    # Sweep: كسر دعم قريب ثم استرداده في آخر الجلسات.
    sweep = False
    if support is not None:
        last = candles[-1]
        prev = candles[-2]
        sweep = prev["low"] < support and last["close"] > support

    # اختراق ثابت: تجاوز مقاومة وإغلاق فوقها بدل الاختراق اللحظي.
    breakout = bool(resistance and price > resistance and candles[-1]["close"] > resistance)

    # لا نعتبر ارتفاعاً سريعاً وحده إشارة. نحتاج ربطه بالفوليوم/الدعم/السلوك.
    momentum = (
        rvol >= 2.0 and change_pct >= 3.0 and
        (breakout or (support is not None and _near(support, price, 0.08)))
    )

    # Fill Gap: هبوط سابق كبير ثم استرداد باتجاه مناطق الهبوط القديمة.
    old_high = max(c["high"] for c in candles[-30:-5])
    fill_gap = change_pct > 3 and price < old_high and sma20 >= sma50 * 0.98 and rvol >= 1.2

    # استبعاد سلوك الخطر المذكور في المنهج: ارتفاع بلا دعم/ثبات أو فوليوم كبير بلا تقدم.
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

    # تصنيف المنتج السابق: طريقة فيصل هنا تركز على المضاربة/السوينق أكثر من الاستثمار.
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
