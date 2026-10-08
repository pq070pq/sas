import math
from typing import Any

import httpx

BINANCE_BASE = "https://api.binance.com"
DEFAULT_INTERVAL = "1h"
DEFAULT_LIMIT = 220

# Symbols commonly entered without the quote currency in SAS PRO search.
COMMON_CRYPTO = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK",
    "DOT", "TRX", "TON", "SHIB", "LTC", "BCH", "NEAR", "ATOM", "UNI",
    "ETC", "APT", "SUI", "FIL", "ARB", "OP", "INJ", "AAVE", "PEPE",
}


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").upper().strip().replace("/", "")
    if value.endswith("USDT"):
        return value
    if value in COMMON_CRYPTO:
        return f"{value}USDT"
    return value


def is_crypto_symbol(symbol: str) -> bool:
    value = str(symbol or "").upper().strip().replace("/", "")
    return value in COMMON_CRYPTO or value.endswith(("USDT", "USDC", "FDUSD", "BUSD"))


async def _get(path: str, params: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.get(f"{BINANCE_BASE}{path}", params=params)
        response.raise_for_status()
        return response.json()


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value * alpha) + (result * (1 - alpha))
    return result


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for a, b in zip(values[-(period + 1):], values[-period:]):
        delta = b - a
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))


def _atr(rows: list[list[Any]], period: int = 14) -> float | None:
    if len(rows) <= period:
        return None
    trs = []
    for i in range(1, len(rows)):
        high = float(rows[i][2])
        low = float(rows[i][3])
        prev_close = float(rows[i - 1][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period if len(trs) >= period else None


def _macd(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if len(values) < 35:
        return None, None, None
    # Build the MACD series from the 12/26 EMA recursively.
    fast_alpha = 2 / 13
    slow_alpha = 2 / 27
    fast = sum(values[:12]) / 12
    slow = sum(values[:26]) / 26
    macd_values = []
    for i, value in enumerate(values):
        if i >= 12:
            fast = value * fast_alpha + fast * (1 - fast_alpha)
        if i >= 26:
            slow = value * slow_alpha + slow * (1 - slow_alpha)
            macd_values.append(fast - slow)
    if len(macd_values) < 9:
        return None, None, None
    signal = _ema(macd_values, 9)
    line = macd_values[-1]
    hist = line - signal if signal is not None else None
    return line, signal, hist


def _adx(rows: list[list[Any]], period: int = 14) -> float | None:
    if len(rows) < period * 2 + 1:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(rows)):
        high, low = float(rows[i][2]), float(rows[i][3])
        prev_high, prev_low, prev_close = float(rows[i - 1][2]), float(rows[i - 1][3]), float(rows[i - 1][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        up = high - prev_high
        down = prev_low - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    if len(trs) < period * 2:
        return None
    dx = []
    for i in range(period, len(trs)):
        atr = sum(trs[i - period + 1:i + 1]) / period
        if atr <= 0:
            continue
        pdi = 100 * (sum(plus_dm[i - period + 1:i + 1]) / period) / atr
        mdi = 100 * (sum(minus_dm[i - period + 1:i + 1]) / period) / atr
        denom = pdi + mdi
        dx.append(100 * abs(pdi - mdi) / denom if denom else 0.0)
    return sum(dx[-period:]) / min(period, len(dx)) if dx else None


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    if value >= 100:
        return round(value, 2)
    if value >= 1:
        return round(value, 4)
    return round(value, 8)


async def quote(symbol: str) -> dict[str, Any]:
    pair = normalize_symbol(symbol)
    data = await _get("/api/v3/ticker/24hr", {"symbol": pair})
    price = float(data["lastPrice"])
    change = float(data["priceChangePercent"])
    return {
        "symbol": pair,
        "display_symbol": pair[:-4] + "/USDT" if pair.endswith("USDT") else pair,
        "price": _round_price(price),
        "change_pct": change,
        "volume": float(data.get("volume") or 0),
        "quote_volume": float(data.get("quoteVolume") or 0),
        "high_24h": float(data.get("highPrice") or 0),
        "low_24h": float(data.get("lowPrice") or 0),
        "source": "Binance Spot",
        "market": "crypto_spot",
        "is_extended_hours": False,
    }


async def candles(symbol: str, interval: str = DEFAULT_INTERVAL, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    pair = normalize_symbol(symbol)
    rows = await _get(
        "/api/v3/klines",
        {"symbol": pair, "interval": interval, "limit": min(max(limit, 50), 1000)},
    )
    return [
        {
            "time": int(row[0]) // 1000,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


async def analyze(symbol: str, interval: str = DEFAULT_INTERVAL) -> dict[str, Any]:
    pair = normalize_symbol(symbol)
    raw = await _get(
        "/api/v3/klines",
        {"symbol": pair, "interval": interval, "limit": DEFAULT_LIMIT},
    )
    if len(raw) < 60:
        raise ValueError("Binance Spot returned insufficient candles")

    closes = [float(x[4]) for x in raw]
    highs = [float(x[2]) for x in raw]
    lows = [float(x[3]) for x in raw]
    volumes = [float(x[5]) for x in raw]
    price = closes[-1]

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200) if len(closes) >= 200 else None
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(raw, 14)
    macd, macd_signal, macd_hist = _macd(closes)
    adx14 = _adx(raw, 14)

    avg_volume = sum(volumes[-21:-1]) / 20
    rvol = volumes[-1] / avg_volume if avg_volume > 0 else None
    resistance = max(highs[-20:-1])
    support = min(lows[-20:-1])

    bullish = bool(
        ema20 is not None and ema50 is not None and price > ema20 > ema50
    )
    breakout = price > resistance
    macd_positive = macd_hist is not None and macd_hist > 0
    rsi_positive = rsi14 is not None and 50 <= rsi14 <= 70
    volume_positive = rvol is not None and rvol >= 1.2
    adx_positive = adx14 is not None and adx14 >= 20

    score = 0
    score += 20 if bullish else 0
    score += 20 if breakout else 0
    score += 15 if volume_positive else 0
    score += 15 if macd_positive else 0
    score += 10 if rsi_positive else 0
    score += 10 if adx_positive else 0
    score += 10 if ema200 is not None and price > ema200 else 0

    stop = (price - (1.5 * atr14)) if atr14 else support
    target1 = price + (1.5 * atr14) if atr14 else None
    target2 = price + (3.0 * atr14) if atr14 else None
    target3 = price + (4.5 * atr14) if atr14 else None

    if score >= 75 and breakout and volume_positive:
        state = "اختراق مؤكد"
        takeaway = "اختراق مدعوم بالحجم؛ انتظر استمرار السعر فوق منطقة الاختراق ولا تطارد الحركة."
    elif score >= 60:
        state = "إيجابية تحت المراقبة"
        takeaway = "الاتجاه والزخم داعمان، لكن تأكيد الاختراق أو الحجم لم يكتمل بالكامل."
    elif score >= 40:
        state = "محايدة"
        takeaway = "هناك إشارات إيجابية جزئية، لكن شروط التأكيد غير مكتملة."
    else:
        state = "ضعيفة"
        takeaway = "البيانات الحالية لا تعطي تأكيدًا فنيًا كافيًا."

    return {
        "symbol": pair,
        "display_symbol": pair[:-4] + "/USDT" if pair.endswith("USDT") else pair,
        "source": "Binance Spot",
        "interval": interval,
        "price": _round_price(price),
        "change_pct": None,
        "volume": volumes[-1],
        "rvol": round(rvol, 2) if rvol is not None else None,
        "ema20": _round_price(ema20),
        "ema50": _round_price(ema50),
        "ema200": _round_price(ema200),
        "rsi14": round(rsi14, 2) if rsi14 is not None else None,
        "atr14": _round_price(atr14),
        "atr_pct": round((atr14 / price) * 100, 2) if atr14 and price else None,
        "adx14": round(adx14, 2) if adx14 is not None else None,
        "macd": round(macd, 8) if macd is not None else None,
        "macd_signal": round(macd_signal, 8) if macd_signal is not None else None,
        "macd_hist": round(macd_hist, 8) if macd_hist is not None else None,
        "support": _round_price(support),
        "resistance": _round_price(resistance),
        "entry": _round_price(price),
        "stop": _round_price(stop),
        "targets": [_round_price(x) for x in (target1, target2, target3) if x is not None],
        "score": score,
        "state": state,
        "takeaway": takeaway,
        "confirmation": {
            "trend": bullish,
            "breakout": breakout,
            "volume": volume_positive,
            "macd": macd_positive,
            "rsi": rsi_positive,
            "adx": adx_positive,
        },
        "candles": [
            {
                "time": int(row[0]) // 1000,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in raw[-90:]
        ],
    }
