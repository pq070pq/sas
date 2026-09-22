import httpx
from .config import settings


async def analyze(symbol: str):
    base = settings.panwatch_base_url.rstrip("/")
    params = {
        "allow_unbound": "true",
        "bypass_market_hours": "true",
        "wait": "true",
        "force_refresh": "true",
        "symbol": symbol.upper(),
        "market": "US",
        "name": symbol.upper(),
    }
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        r = await client.post(
            f"{base}/api/stocks/0/agents/tradingagents/trigger",
            params=params,
        )
        r.raise_for_status()
        return r.json()


async def technical_targets(symbol: str):
    """Build targets only from observed OHLCV structure; never invent prices."""
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        rows = []
        try:
            r = await client.get(
                f"{base}/api/klines/{symbol.upper()}",
                params={"market": "US", "days": 90, "interval": "1d"},
            )
            r.raise_for_status()
            rows = r.json().get("klines", [])
        except Exception:
            # Fallback to Twelve Data when PanWatch has no daily candles.
            if settings.twelve_data_api_key:
                try:
                    r = await client.get(
                        "https://api.twelvedata.com/time_series",
                        params={
                            "symbol": symbol.upper(),
                            "interval": "1day",
                            "outputsize": 90,
                            "apikey": settings.twelve_data_api_key,
                        },
                    )
                    r.raise_for_status()
                    payload = r.json()
                    rows = list(reversed(payload.get("values") or []))
                except Exception:
                    rows = []

    candles = []
    for row in rows:
        try:
            candles.append({
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
                "date": row.get("date"),
            })
        except (TypeError, ValueError, KeyError):
            continue

    if len(candles) < 20:
        return {"status": "insufficient_data", "targets": [], "exit": None}

    last = candles[-1]
    price = last["close"]
    recent = candles[-20:-1]

    # Confirmed resistance/support: local swing levels with two candles on each side.
    resistances = []
    supports = []
    for i in range(2, len(candles) - 2):
        h = candles[i]["high"]
        l = candles[i]["low"]
        if h >= candles[i-1]["high"] and h >= candles[i-2]["high"] and h >= candles[i+1]["high"] and h >= candles[i+2]["high"]:
            if h > price * 1.003:
                resistances.append(h)
        if l <= candles[i-1]["low"] and l <= candles[i-2]["low"] and l <= candles[i+1]["low"] and l <= candles[i+2]["low"]:
            if l < price * 0.997:
                supports.append(l)

    def unique_levels(levels):
        out = []
        for level in sorted(levels):
            if not out or abs(level - out[-1]) / out[-1] > 0.01:
                out.append(level)
        return out

    resistances = unique_levels(resistances)
    supports = unique_levels(supports)

    # ATR(14) is used as a sanity check so targets are not placed unrealistically close.
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
    atr = sum(trs[-14:]) / min(14, len(trs[-14:]))
    min_distance = max(atr * 0.35, price * 0.01)

    targets = []
    for level in resistances:
        if level - price >= min_distance:
            targets.append(round(level, 4))
        if len(targets) == 3:
            break

    avg_volume = sum(c["volume"] for c in recent) / max(1, len(recent))
    current_volume = last["volume"]
    volume_ratio = current_volume / avg_volume if avg_volume else None

    # Target 2/3 are only considered when current activity supports continuation.
    if len(targets) > 1 and (volume_ratio is None or volume_ratio < 1.15):
        targets = targets[:1]
    if len(targets) > 2 and (volume_ratio is None or volume_ratio < 1.50):
        targets = targets[:2]

    # Exit is the nearest confirmed support below price, with ATR fallback only when a
    # real support is unavailable. The fallback is calculated from observed price/ATR,
    # not a hardcoded percentage.
    below = [s for s in supports if s < price]
    exit_level = max(below) if below else price - atr

    return {
        "status": "ok",
        "price": round(price, 4),
        "atr": round(atr, 4),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "resistances": targets,
        "support": round(exit_level, 4),
        "targets": targets,
        "exit": round(exit_level, 4),
        "method": "swing-resistance + ATR + volume confirmation",
    }
