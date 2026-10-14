"""Annual-low cycle analysis helpers for the isolated SAS PRO scanner.

This module is intentionally independent of SAS Core qualification logic.
It only analyzes observed OHLCV data supplied by a caller; it does not fetch,
invent, or adjust market prices itself.
"""
from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _valid_candles(rows: list[dict] | None) -> list[dict]:
    result = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        candle = {key: _num(row.get(key)) for key in ("open", "high", "low", "close", "volume")}
        if all(candle[k] is not None and candle[k] > 0 for k in ("open", "high", "low", "close")):
            if candle["high"] >= max(candle["open"], candle["close"], candle["low"]) and candle["low"] <= min(candle["open"], candle["close"], candle["high"]):
                result.append(candle)
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(v, 0.0) for v in changes[-period:]]
    losses = [max(-v, 0.0) for v in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def _pivot_lows(candles: list[dict], lookback: int = 252) -> list[float]:
    rows = candles[-lookback:]
    lows = []
    for i in range(2, len(rows) - 2):
        low = rows[i]["low"]
        if low <= rows[i - 1]["low"] and low <= rows[i - 2]["low"] and low <= rows[i + 1]["low"] and low <= rows[i + 2]["low"]:
            lows.append(low)
    return lows


def analyze_annual_low_cycle(
    symbol: str,
    price: float,
    daily_candles: list[dict] | None,
    four_hour_candles: list[dict] | None,
    *,
    reverse_split_date: str | None = None,
    cycle_target_hit_after_split: bool | None = None,
    reverse_split_verified: bool = False,
) -> dict:
    """Return watch/confirmed status without treating RSI as a rejection gate.

    Corporate-action facts must be verified by the caller. If a reverse split
    is known but target-hit status is unknown, the result is flagged for review
    rather than asserting that the cycle is unused.
    """
    ticker = str(symbol or "").strip().upper()
    live_price = _num(price)
    daily = _valid_candles(daily_candles)
    four_hour = _valid_candles(four_hour_candles)
    base = {
        "symbol": ticker,
        "price": live_price,
        "price_min": 1.0,
        "price_max": 10.0,
        "annual_low": None,
        "nearest_valid_support": None,
        "distance_to_support_pct": None,
        "daily_rsi": None,
        "daily_cycle_state": "بيانات غير كافية",
        "four_hour_confirmation": False,
        "status": "insufficient_data",
        "reason": None,
        "reverse_split_date": reverse_split_date,
        "reverse_split_verified": bool(reverse_split_verified),
        "cycle_target_hit_after_split": cycle_target_hit_after_split,
    }
    if not ticker or live_price is None or not 1.0 <= live_price <= 10.0:
        base.update(status="out_of_price_range", reason="السعر خارج نطاق 1–10 دولارات")
        return base
    if len(daily) < 60 or len(four_hour) < 30:
        base["reason"] = "لا توجد شموع كافية لتحليل اليومي وتأكيد 4 ساعات"
        return base
    if reverse_split_verified and cycle_target_hit_after_split is None:
        base["status"] = "needs_corporate_action_review"
        base["reason"] = "تم رصد تقسيم عكسي لكن لم يُتحقق من استهلاك هدف الدورة بعده"
        return base
    if reverse_split_verified and cycle_target_hit_after_split:
        base["status"] = "cycle_target_already_hit"
        base["reason"] = "تحقق هدف الدورة بعد آخر تقسيم عكسي"
        return base

    daily_lows = [c["low"] for c in daily[-252:]]
    annual_low = min(daily_lows) if daily_lows else None
    supports = _pivot_lows(daily)
    # A support is valid only if it is an observed pivot low not materially
    # above current price; use the closest such level below/near current price.
    eligible = [level for level in supports if level <= live_price * 1.03 and level >= live_price * 0.80]
    support = min(eligible, key=lambda level: abs(live_price - level)) if eligible else None
    closes = [c["close"] for c in daily]
    rsi_value = _rsi(closes)
    last4 = four_hour[-1]
    prev4 = four_hour[-2]
    # Confirmation is deliberately explicit and modest: close above prior
    # candle high, green candle, and close above the prior candle close.
    four_hour_confirmed = bool(
        last4["close"] > last4["open"]
        and last4["close"] > prev4["high"]
        and last4["close"] > prev4["close"]
    )
    distance = ((live_price / support) - 1) * 100 if support and live_price else None
    near_annual_low = annual_low is not None and live_price <= annual_low * 1.15
    base.update({
        "annual_low": round(annual_low, 4) if annual_low is not None else None,
        "nearest_valid_support": round(support, 4) if support is not None else None,
        "distance_to_support_pct": round(distance, 2) if distance is not None else None,
        "daily_rsi": rsi_value,
        "daily_cycle_state": "قريب من القاع السنوي" if near_annual_low else "بعيد عن القاع السنوي",
        "four_hour_confirmation": four_hour_confirmed,
    })
    if support is None:
        base.update(status="watch_no_support", reason="لم يُعثر على دعم محوري صالح قريب من السعر")
    elif not near_annual_low:
        base.update(status="watch_not_near_annual_low", reason="السهم ليس قريبًا بما يكفي من القاع السنوي")
    elif four_hour_confirmed:
        base.update(status="confirmed", reason="الدورة اليومية قريبة من القاع وتأكدت حركة 4 ساعات")
    else:
        base.update(status="early_watch", reason="مرشح مبكر قرب الدعم؛ ينتظر تأكيد 4 ساعات")
    # RSI is always informational and never changes status.
    return base
