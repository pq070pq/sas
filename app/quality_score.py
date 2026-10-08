"""SAS PRO Quality Score.

A deterministic, provider-independent ranking layer. It never creates a signal;
it scores evidence already produced by the radar and keeps missing evidence
neutral rather than inventing values.
"""
from __future__ import annotations

from typing import Any


DEFAULT_WEIGHTS = {
    "trend": 30,
    "momentum": 20,
    "market": 15,
    "catalyst": 20,
    "risk": 15,
}


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _bool(value: Any) -> bool:
    return bool(value)


def _trend_score(row: dict) -> float:
    checks = row.get("radar_checks") or {}
    score = 0.0
    if _bool(checks.get("trend")) or _bool(checks.get("trend_alignment")):
        score += 35
    if _bool(checks.get("breakout")) or _bool(row.get("breakout_confirmed")):
        score += 25
    if _bool(row.get("intraday_confirmation")):
        score += 20
    classification = row.get("classification") or {}
    if _bool(classification.get("above_ema20")):
        score += 10
    if _bool(classification.get("above_ema50")):
        score += 10
    # Some scanner versions expose trend as text rather than a boolean.
    trend = str(row.get("trend") or classification.get("trend") or "").lower()
    if trend in {"bullish", "صاعد", "up", "positive", "إيجابي"}:
        score = max(score, 70)
    return _clamp(score)


def _momentum_score(row: dict) -> float:
    rvol = _num(
        row.get("momentum_rvol_10d"),
        _num((row.get("classification") or {}).get("rvol"), 0.0),
    ) or 0.0
    change = _num(row.get("change_pct"), 0.0) or 0.0
    score = 0.0
    if rvol >= 3:
        score += 55
    elif rvol >= 2:
        score += 45
    elif rvol >= 1.5:
        score += 35
    elif rvol >= 1:
        score += 20
    if change >= 10:
        score += 35
    elif change >= 5:
        score += 28
    elif change >= 3:
        score += 20
    elif change > 0:
        score += 10
    if _bool(row.get("intraday_confirmation")):
        score += 10
    return _clamp(score)


def _market_score(row: dict) -> float:
    status = str(
        row.get("market_bias")
        or row.get("market_state")
        or (row.get("classification") or {}).get("market_state")
        or ""
    ).lower()
    if status in {"bullish", "positive", "صاعد", "إيجابي", "supportive", "داعم"}:
        return 90.0
    if status in {"bearish", "negative", "هابط", "سلبي", "risk_off"}:
        return 25.0
    # If no market-bias field exists, use the existing technical gate as
    # neutral evidence. Missing evidence must not become a fabricated positive.
    return 60.0


def _catalyst_score(row: dict) -> float:
    news = row.get("news_items") or []
    events = row.get("corporate_events") or row.get("events") or {}
    catalyst = _bool(row.get("catalyst")) or bool(news) or bool(events)
    if not catalyst:
        return 30.0
    score = 60.0
    news_count = _num(row.get("news_count"), len(news)) or 0
    if news_count >= 3:
        score += 15
    elif news_count >= 1:
        score += 8
    if _bool(row.get("earnings_within_5_days")):
        score += 10
    return _clamp(score)


def _risk_score(row: dict) -> float:
    """Higher is safer, using existing risk evidence only."""
    checks = row.get("radar_checks") or {}
    if _bool(checks.get("risk_reward_warning")) or _bool(row.get("chase_risk")):
        return 20.0
    if _bool(row.get("distribution_risk")) or _bool(row.get("bearish_head_shoulders")):
        return 25.0

    rr = _num(
        row.get("risk_reward"),
        _num((row.get("targets") or {}).get("risk_reward"), None),
    )
    score = 55.0
    if rr is not None:
        if rr >= 3:
            score = 95
        elif rr >= 2:
            score = 85
        elif rr >= 1.5:
            score = 72
        elif rr >= 1:
            score = 55
        else:
            score = 30
    if _bool(row.get("live_levels_verified")):
        score += 5
    return _clamp(score)


def score_quality(row: dict, weights: dict[str, float] | None = None) -> dict:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    components = {
        "trend": _trend_score(row),
        "momentum": _momentum_score(row),
        "market": _market_score(row),
        "catalyst": _catalyst_score(row),
        "risk": _risk_score(row),
    }
    total_weight = sum(float(v) for v in weights.values()) or 100.0
    total = sum(components[k] * float(weights.get(k, 0)) for k in components) / total_weight
    total = round(_clamp(total), 1)

    if total >= 85:
        label = "ممتاز"
        label_detail = "عدة عوامل قوية ومتوافقة"
    elif total >= 75:
        label = "قوي"
        label_detail = "عوامل جيدة مع بعض نقاط المتابعة"
    elif total >= 65:
        label = "متوسط"
        label_detail = "فرصة تحتاج تأكيدًا إضافيًا"
    else:
        label = "مراقبة"
        label_detail = "الأدلة غير كافية لرفع الجودة"

    return {
        "score": total,
        "label": label,
        "label_detail": label_detail,
        "weights": weights,
        "components": {k: round(v, 1) for k, v in components.items()},
        "method": "SAS Quality Score — ترتيب آلي من بيانات الرادار، وليس توصية شراء أو بيع",
    }
