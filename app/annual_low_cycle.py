"""Annual-low cycle analysis helpers for the isolated SAS PRO scanner.

This module is intentionally independent of SAS Core qualification logic.
It analyzes observed OHLCV data supplied by a caller; it does not fetch or
invent market prices.
"""
from __future__ import annotations
from typing import Any

MIN_PRICE = 1.0
MAX_PRICE = 15.0


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
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
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
    symbol: str, price: float, daily_candles: list[dict] | None,
    four_hour_candles: list[dict] | None, *, reverse_split_date: str | None = None,
    cycle_target_hit_after_split: bool | None = None, reverse_split_verified: bool = False,
) -> dict:
    """Return early-watch/confirmed state; RSI is informational, never a gate."""
    ticker, live_price = str(symbol or "").strip().upper(), _num(price)
    daily, four_hour = _valid_candles(daily_candles), _valid_candles(four_hour_candles)
    base = {
        "symbol": ticker, "price": live_price, "price_min": MIN_PRICE, "price_max": MAX_PRICE,
        "annual_low": None, "nearest_valid_support": None, "distance_to_support_pct": None,
        "daily_rsi": None, "daily_cycle_state": "بيانات غير كافية",
        "four_hour_confirmation": False, "status": "insufficient_data", "reason": None,
        "reverse_split_date": reverse_split_date, "reverse_split_verified": bool(reverse_split_verified),
        "cycle_target_hit_after_split": cycle_target_hit_after_split,
    }
    if not ticker or live_price is None or not MIN_PRICE <= live_price <= MAX_PRICE:
        base.update(status="out_of_price_range", reason="السعر خارج نطاق 1–15 دولارًا")
        return base
    if len(daily) < 60 or len(four_hour) < 30:
        base["reason"] = "لا توجد شموع كافية لتحليل اليومي وتأكيد 4 ساعات"
        return base
    if reverse_split_verified and cycle_target_hit_after_split is None:
        base.update(status="needs_corporate_action_review", reason="تم رصد تقسيم عكسي لكن لم يُتحقق من استهلاك هدف الدورة بعده")
        return base
    if reverse_split_verified and cycle_target_hit_after_split:
        base.update(status="cycle_target_already_hit", reason="تحقق هدف الدورة بعد آخر تقسيم عكسي")
        return base
    annual_low = min(c["low"] for c in daily[-252:])
    supports = _pivot_lows(daily)
    eligible = [level for level in supports if level <= live_price * 1.03 and level >= live_price * 0.80]
    support = min(eligible, key=lambda level: abs(live_price - level)) if eligible else None
    rsi_value = _rsi([c["close"] for c in daily])
    last4, prev4 = four_hour[-1], four_hour[-2]
    confirmed = bool(last4["close"] > last4["open"] and last4["close"] > prev4["high"] and last4["close"] > prev4["close"])
    distance = ((live_price / support) - 1) * 100 if support else None
    near_low = live_price <= annual_low * 1.15
    base.update({
        "annual_low": round(annual_low, 4), "nearest_valid_support": round(support, 4) if support is not None else None,
        "distance_to_support_pct": round(distance, 2) if distance is not None else None,
        "daily_rsi": rsi_value, "daily_cycle_state": "قريب من القاع السنوي" if near_low else "بعيد عن القاع السنوي",
        "four_hour_confirmation": confirmed,
    })
    if support is None:
        base.update(status="watch_no_support", reason="لم يُعثر على دعم محوري صالح قريب من السعر")
    elif not near_low:
        base.update(status="watch_not_near_annual_low", reason="السهم ليس قريبًا بما يكفي من القاع السنوي")
    elif confirmed:
        base.update(status="confirmed", reason="الدورة اليومية قريبة من القاع وتأكدت حركة 4 ساعات")
    else:
        base.update(status="early_watch", reason="مرشح مبكر قرب الدعم؛ ينتظر تأكيد 4 ساعات")
    return base
