"""Conservative ICT-style liquidity sweep detection from observed OHLCV candles.

This is a compact adaptation for the SAS PRO US-stock radar, not a copy of
the original TradingView screener. It only uses the last completed candle and
prior candles; it never creates synthetic prices or future-looking levels.
"""
from __future__ import annotations


def detect_liquidity_sweep(candles, lookback: int = 20, min_penetration_pct: float = 0.10):
    """Return bullish/bearish sweep metadata from chronological OHLCV candles.

    A bullish sweep requires the latest low to pierce the lowest prior low,
    close back above that level, and finish as a bullish candle in the upper
    60% of its range. A bearish sweep is the inverse at prior highs.
    """
    empty = {
        "bullish": False, "bearish": False, "support_level": None,
        "resistance_level": None, "penetration_pct": None,
        "volume_confirmed": False, "reason": "insufficient_data",
    }
    if not isinstance(candles, (list, tuple)) or len(candles) < max(5, lookback + 1):
        return empty.copy()

    prior = candles[-(lookback + 1):-1]
    last = candles[-1]
    try:
        lows = [float(c["low"]) for c in prior]
        highs = [float(c["high"]) for c in prior]
        low = float(last["low"])
        high = float(last["high"])
        opn = float(last["open"])
        close = float(last["close"])
        volume = max(0.0, float(last.get("volume", 0) or 0))
        prior_volumes = [max(0.0, float(c.get("volume", 0) or 0)) for c in prior]
    except (KeyError, TypeError, ValueError):
        return empty.copy()

    if not lows or not highs or min(lows) <= 0 or max(highs) <= 0 or high <= low:
        return empty.copy()

    support = min(lows)
    resistance = max(highs)
    low_penetration = (support - low) / support * 100.0
    high_penetration = (high - resistance) / resistance * 100.0
    close_position = (close - low) / (high - low)
    volume_baseline = sum(prior_volumes[-20:]) / max(1, len(prior_volumes[-20:]))
    volume_confirmed = bool(volume_baseline > 0 and volume >= 1.5 * volume_baseline)

    bullish = bool(
        low < support
        and low_penetration >= min_penetration_pct
        and close > support
        and close > opn
        and close_position >= 0.60
    )
    bearish = bool(
        high > resistance
        and high_penetration >= min_penetration_pct
        and close < resistance
        and close < opn
        and close_position <= 0.40
    )
    return {
        "bullish": bullish,
        "bearish": bearish,
        "support_level": round(support, 6),
        "resistance_level": round(resistance, 6),
        "penetration_pct": round(low_penetration if low < support else high_penetration, 4),
        "volume_confirmed": volume_confirmed,
        "reason": "bullish_reclaim" if bullish else "bearish_rejection" if bearish else "no_confirmed_sweep",
    }
