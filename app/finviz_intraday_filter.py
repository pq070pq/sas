"""Experimental, isolated Finviz-style intraday candidate filter.

This module is intentionally not wired into the production scanner. It evaluates
only supplied observations and fails closed when float/open-price data is missing.
"""
from __future__ import annotations

from typing import Any


def evaluate_intraday_candidate(
    *,
    price: float | None,
    float_shares: float | None,
    volume: float | None,
    session_open: float | None,
    analyst_target: float | None = None,
    analyst_count: int | None = None,
) -> dict[str, Any]:
    """Return eligibility, reasons, and observed change from session open."""
    reasons: list[str] = []
    change_from_open_pct: float | None = None

    if price is None or price <= 0:
        reasons.append("السعر غير متوفر أو غير صالح")
    elif not 0.30 <= price <= 6.00:
        reasons.append("السعر خارج نطاق 0.30–6.00 دولار")

    if float_shares is None or float_shares <= 0:
        reasons.append("عدد الأسهم الحرة غير متوفر؛ يلزم التحقق")
    elif float_shares >= 20_000_000:
        reasons.append("الأسهم الحرة ليست أقل من 20 مليون")

    if volume is None or volume < 0:
        reasons.append("حجم التداول غير متوفر أو غير صالح")
    elif volume <= 500_000:
        reasons.append("حجم التداول لا يتجاوز 500 ألف سهم")

    if session_open is None or session_open <= 0 or price is None or price <= 0:
        reasons.append("لا يمكن حساب التغير من افتتاح الجلسة")
    else:
        change_from_open_pct = ((price - session_open) / session_open) * 100
        if change_from_open_pct < 10.0:
            reasons.append("الارتفاع من افتتاح الجلسة أقل من 10%")

    target_upside_pct: float | None = None
    if analyst_target is None or analyst_target <= 0:
        reasons.append("هدف المحللين غير متوفر؛ لا يُسمح بالنشر")
    elif price is not None and price > 0:
        target_upside_pct = ((analyst_target - price) / price) * 100
        if target_upside_pct < 50.0:
            reasons.append("متوسط هدف المحللين أقل من 50% فوق السعر الحالي")

    if analyst_count is None or analyst_count < 3:
        reasons.append("عدد المحللين المؤكدين أقل من 3 أو غير متوفر")

    return {
        "eligible": not reasons,
        "change_from_open_pct": (
            round(change_from_open_pct, 2)
            if change_from_open_pct is not None
            else None
        ),
        "reasons": reasons,
        "analyst_target": round(analyst_target, 4) if analyst_target and analyst_target > 0 else None,
        "analyst_count": analyst_count,
        "analyst_upside_pct": round(target_upside_pct, 2) if target_upside_pct is not None else None,
        "experimental": False,
    }
