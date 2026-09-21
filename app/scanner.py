import httpx
from .config import settings
from .panwatch import technical_targets

MIN_PRICE = 0.50
MAX_PRICE = 30.00
DISCOVERY_LIMIT = 100
CANDIDATE_LIMIT = 12

async def discover_low_price_stocks():
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        r = await client.get(
            f"{base}/api/discovery/stocks",
            params={"market": "US", "mode": "gainers", "limit": DISCOVERY_LIMIT},
        )
        r.raise_for_status()
        rows = r.json()

    out = []
    seen = set()
    for row in rows or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            continue
        if not symbol or symbol in seen or not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        seen.add(symbol)
        out.append(row)
        if len(out) >= CANDIDATE_LIMIT:
            break
    return out

async def classify_stock(symbol: str, quote: dict | None = None):
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        r = await client.get(
            f"{base}/api/klines/{symbol.upper()}",
            params={"market": "US", "days": 90, "interval": "1d"},
        )
        r.raise_for_status()
        rows = r.json().get("klines", [])

    candles = []
    for row in rows:
        try:
            candles.append({
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
            })
        except (TypeError, ValueError, KeyError):
            continue

    if len(candles) < 30:
        return {"type": "غير واضح", "emoji": "⚪", "score": 0, "reason": "بيانات غير كافية"}

    closes = [c["close"] for c in candles]
    returns = []
    trs = []
    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]
        if prev["close"]:
            returns.append(abs(cur["close"] / prev["close"] - 1))
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))

    price = closes[-1]
    atr14 = sum(trs[-14:]) / min(14, len(trs))
    atr_pct = atr14 / price if price else 1.0
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    avg_vol20 = sum(c["volume"] for c in candles[-20:]) / 20
    vol_ratio = candles[-1]["volume"] / avg_vol20 if avg_vol20 else 0.0
    change_pct = float((quote or {}).get("change_pct") or 0.0)

    if (
        atr_pct >= 0.10
        or abs(change_pct) >= 12
        or (price < 2.0 and vol_ratio >= 1.8)
    ):
        stock_type, emoji = "مضاربي", "🔴"
        reason = "تذبذب وحركة سريعة"
    elif (
        sma20 > sma50
        and atr_pct >= 0.04
        and atr_pct < 0.10
        and (vol_ratio >= 1.05 or change_pct >= 3)
    ):
        stock_type, emoji = "سوينق", "🔵"
        reason = "اتجاه وحركة مناسبة لعدة أيام إلى أسابيع"
    elif (
        sma20 >= sma50
        and atr_pct < 0.06
        and price >= 5.0
        and abs(change_pct) < 8
    ):
        stock_type, emoji = "استثماري", "🟢"
        reason = "حركة أهدأ واتجاه أكثر استقرارًا"
    else:
        stock_type, emoji = "سوينق", "🔵"
        reason = "سلوك سعري متوسط ومناسب للمراقبة"

    return {
        "type": stock_type,
        "emoji": emoji,
        "score": round(min(100, max(0, 50 + (sma20 / sma50 - 1) * 500 + min(vol_ratio, 3) * 5), 1)),
        "reason": reason,
        "atr_pct": round(atr_pct * 100, 2),
        "volume_ratio": round(vol_ratio, 2),
        "sma20": round(sma20, 4),
        "sma50": round(sma50, 4),
    }

async def scan_us_low_price_stocks():
    candidates = await discover_low_price_stocks()
    results = []
    for row in candidates:
        symbol = str(row.get("symbol") or "").upper()
        try:
            classification = await classify_stock(symbol, row)
            targets = await technical_targets(symbol)
        except Exception:
            continue
        results.append({
            **row,
            "symbol": symbol,
            "classification": classification,
            "targets": targets,
        })
    results.sort(
        key=lambda x: (
            float(x.get("change_pct") or 0),
            float((x.get("classification") or {}).get("volume_ratio") or 0),
        ),
        reverse=True,
    )
    return results
