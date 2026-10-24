"""Optional, isolated shadow review inspired by TradingAgents.

This module is intentionally not imported by the production scanner. It is a
safe building block for a later, explicitly enabled integration: no candidate
selection, ranking, publication, or trading decision is made here.
"""
from __future__ import annotations
from typing import Any
from .config import settings


def review_enabled() -> bool:
    """The shadow review is opt-in and remains off unless explicitly enabled."""
    return bool(getattr(settings, "tradingagents_shadow_review_enabled", False))


def build_shadow_review(symbol: str, ai_analysis: dict[str, Any] | None, *, evidence_ids: set[str] | None = None) -> dict[str, Any]:
    """Convert validated AI output into a separate review record; makes no network calls."""
    if not review_enabled():
        return {"enabled": False, "status": "disabled", "symbol": str(symbol).upper()}
    analysis = ai_analysis if isinstance(ai_analysis, dict) else {}
    base = {"enabled": True, "symbol": str(symbol).upper(), "mode": "shadow_only",
            "decision_role": "تفسير فقط — لا يضيف أو يحذف أو يرتب فرص الرادار"}
    if not analysis.get("enabled") or analysis.get("status") != "ok":
        return {**base, "status": "insufficient_evidence"}
    allowed = set(evidence_ids or ())
    primary = str(analysis.get("primary_source_id") or "").strip()
    supporting = [str(x) for x in (analysis.get("supporting_source_ids") or []) if str(x).strip()]
    if allowed and primary and primary not in allowed:
        return {**base, "status": "rejected_invalid_evidence"}
    if allowed:
        supporting = [x for x in supporting if x in allowed]
    risks = analysis.get("risk_flags")
    if not isinstance(risks, list):
        risks = []
    return {
        **base, "status": "ok",
        "evidence": {"primary_source_id": primary, "supporting_source_ids": supporting[:4]},
        "perspectives": {
            "momentum": {"assessment": str(analysis.get("momentum_assessment") or "غير واضح"),
                         "basis": "بيانات الزخم المقدمة للمحلل الحالي فقط"},
            "catalyst": {"assessment": str(analysis.get("news_assessment") or "غير واضح"),
                         "summary": str(analysis.get("headline_summary") or "غير واضح")[:500]},
            "risk": {"flags": [str(x)[:240] for x in risks[:4] if str(x).strip()],
                     "assessment": str(analysis.get("dilution_risk") or "غير واضح")},
        },
        "key_takeaway": str(analysis.get("key_takeaway") or "غير واضح")[:500],
        "provider": str(analysis.get("provider") or ""),
        "model": str(analysis.get("model") or ""),
    }
