"""US Alpha Scanner daily signal layer adapted for SAS PRO.

Uses observed daily OHLCV only. This is an explanatory layer; it does not
replace the existing SAS Core/Smart Levels confirmation gate.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")


def _ema(values, period):
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = item * alpha + value * (1.0 - alpha)
    return value


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _completed_daily_rows(candles, now=None):
    """Drop today's unfinished daily candle when its date is available."""
    now = now or datetime.now(NY)
    rows = [dict(row) for row in (candles or []) if isinstance(row, dict)]
    if not rows:
        return rows
    raw_stamp = rows[-1].get("datetime") or rows[-1].get("date") or rows[-1].get("time")
    if raw_stamp:
        try:
            stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(NY)
            if stamp.date() == now.date() and now.time() < time(16, 15):
                rows.pop()
        except (TypeError, ValueError, OverflowError):
            # Do not guess timestamp formats; downstream will identify the data
            # as timestamp-unverified rather than inventing a completed bar.
            pass
    return rows


def _at_position(candles, pos, spy_return_20=None):
    rows = candles[:pos + 1]
    closes = [float(x.get("close") or 0) for x in rows]
    highs = [float(x.get("high") or 0) for x in rows]
    volumes = [float(x.get("volume") or 0) for x in rows]
    if len(rows) < 201 or any(x <= 0 for x in closes[-201:]):
        return {"status": "INSUFFICIENT_DATA", "checks": {}, "metrics": {}}

    price = closes[-1]
    ema20, ema50, ema200 = (_ema(closes, n) for n in (20, 50, 200))
    rsi14 = _rsi(closes, 14)
    prior_volumes = volumes[-21:-1]
    average_prior_volume = sum(prior_volumes) / 20 if len(prior_volumes) == 20 else 0
    rvol20 = volumes[-1] / average_prior_volume if average_prior_volume > 0 else None
    hi20_prev = max(highs[-21:-1]) if len(highs) >= 21 else None
    hi52_prev = max(highs[-253:-1]) if len(highs) >= 201 else None
    stock_return_20 = (price / closes[-21] - 1.0) if closes[-21] else None
    rs20_spy = (
        stock_return_20 - spy_return_20
        if stock_return_20 is not None and spy_return_20 is not None else None
    )

    checks = {
        "close_above_ema20": price > ema20 if ema20 is not None else None,
        "ema_stack_20_50_200": ema20 > ema50 > ema200 if all(x is not None for x in (ema20, ema50, ema200)) else None,
        "rsi_at_least_55": rsi14 >= 55 if rsi14 is not None else None,
        "within_5pct_ema20": price <= ema20 * 1.05 if ema20 is not None else None,
        "within_10pct_52w_high": price >= hi52_prev * 0.90 if hi52_prev is not None else None,
        "relative_strength_vs_spy_positive": rs20_spy > 0 if rs20_spy is not None else None,
        "close_above_previous_20d_high": price > hi20_prev if hi20_prev is not None else None,
        "rvol20_at_least_2": rvol20 >= 2.0 if rvol20 is not None else None,
    }
    trend_keys = (
        "close_above_ema20", "ema_stack_20_50_200", "rsi_at_least_55",
        "within_5pct_ema20", "within_10pct_52w_high",
        "relative_strength_vs_spy_positive",
    )
    trend_good = all(checks[k] is True for k in trend_keys)
    breakout = bool(
        trend_good and checks["close_above_previous_20d_high"] is True
        and checks["rvol20_at_least_2"] is True
    )
    status = "BREAKOUT" if breakout else ("WATCH" if trend_good else "FILTERED")
    return {
        "status": status,
        "trend_good": trend_good,
        "breakout": breakout,
        "checks": checks,
        "metrics": {
            "price": round(price, 4),
            "ema20": round(ema20, 4) if ema20 is not None else None,
            "ema50": round(ema50, 4) if ema50 is not None else None,
            "ema200": round(ema200, 4) if ema200 is not None else None,
            "rsi14": round(rsi14, 2) if rsi14 is not None else None,
            "rvol20": round(rvol20, 2) if rvol20 is not None else None,
            "hi20_prev": round(hi20_prev, 4) if hi20_prev is not None else None,
            "hi52_prev": round(hi52_prev, 4) if hi52_prev is not None else None,
            "rs20_spy_pct_points": round(rs20_spy * 100, 2) if rs20_spy is not None else None,
        },
        "reason": (
            "اتجاه وزخم وقوة نسبية مستوفاة دون اختراق 20 جلسة"
            if status == "WATCH" else
            "إغلاق فوق قمة 20 جلسة مع RVOL20 ≥ 2.0× واستيفاء شروط الاتجاه"
            if status == "BREAKOUT" else
            "لم تستوفَ كل شروط الاتجاه والزخم والقوة النسبية"
        ),
    }


def evaluate_alpha_guide(candles, spy_return_20=None, now=None):
    """Return WATCH/BREAKOUT/CONFIRMED/FILTERED without replacing SAS gates."""
    rows = _completed_daily_rows(candles, now=now)
    if len(rows) < 201:
        return {
            "status": "INSUFFICIENT_DATA",
            "label": "بيانات يومية مكتملة غير كافية",
            "checks": {},
            "metrics": {},
            "reason": "يحتاج الدليل إلى نحو 200 جلسة مكتملة لحساب EMA200 وقمة 52 أسبوعًا.",
            "bars": len(rows),
        }
    current = _at_position(rows, len(rows) - 1, spy_return_20)
    if current.get("status") == "INSUFFICIENT_DATA":
        return {
            "status": "INSUFFICIENT_DATA",
            "label": "بيانات يومية مكتملة غير كافية",
            "checks": current.get("checks", {}),
            "metrics": current.get("metrics", {}),
            "reason": "تعذر حساب المؤشرات من البيانات المتاحة.",
            "bars": len(rows),
        }

    previous = _at_position(rows, len(rows) - 2, spy_return_20)
    current_close = float(rows[-1].get("close") or 0)
    previous_close = float(rows[-2].get("close") or 0)
    current_rvol = (current.get("metrics") or {}).get("rvol20")
    if (
        previous.get("status") == "BREAKOUT"
        and current.get("trend_good")
        and current_close > previous_close
        and current_rvol is not None
        and current_rvol >= 1.2
    ):
        status = "CONFIRMED"
        reason = "اختراق الجلسة السابقة تأكد بإغلاق أعلى مع بقاء الاتجاه وRVOL20 ≥ 1.2×."
    else:
        status = current.get("status", "FILTERED")
        reason = current.get("reason", "لا توجد بيانات كافية.")

    labels = {
        "WATCH": "مراقبة",
        "BREAKOUT": "اختراق",
        "CONFIRMED": "اختراق مؤكد وفق الدليل",
        "FILTERED": "لم يستوفِ شروط الدليل",
        "INSUFFICIENT_DATA": "بيانات غير كافية",
    }
    return {
        **current,
        "status": status,
        "label": labels.get(status, status),
        "reason": reason,
        "bars": len(rows),
        "source": "OHLCV يومي متاح للرادار",
        "note": "طبقة فنية تعليمية إضافية؛ لا تستبدل بوابة SAS PRO ولا تضمن النتيجة.",
    }
