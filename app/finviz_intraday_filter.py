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
) -> dict[str, Any]:
    """Return eligibility, reasons, and observed change from session open."""
    reasons: list[str] = []
    change_from_open_pct: float | None = None

    if price is None or price <= 0:
        reasons.append("السعر غير متوفر أو غير صالح")
    elif not 1.0 <= price <= 10.0:
        reasons.append("السعر خارج نطاق 1–10 دولار")

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

    return {
        "eligible": not reasons,
        "change_from_open_pct": (
            round(change_from_open_pct, 2)
            if change_from_open_pct is not None
            else None
        ),
        "reasons": reasons,
        "experimental": True,
    }
