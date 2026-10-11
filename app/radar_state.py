"""Pure helpers for keeping only currently confirmed SAS radar opportunities active."""
from __future__ import annotations


def is_confirmed_opportunity(row: object) -> bool:
    """A saved opportunity is eligible only when the current scan confirms it."""
    if not isinstance(row, dict):
        return False
    classification = row.get("classification")
    if not isinstance(classification, dict):
        return False
    return (
        classification.get("opportunity_status") == "confirmed"
        and bool(classification.get("confirmation_ready"))
    )


def mark_opportunity_inactive(payload: object, reason: str, session: str | None = None) -> dict:
    """Deactivate a saved record without deleting its historical evidence."""
    result = dict(payload) if isinstance(payload, dict) else {}
    result["radar_active"] = False
    result["strategy_status"] = "inactive"
    result["strategy_inactive_reason"] = str(reason or "لم تعد الفرصة مستوفية للشروط")
    gate = result.get("channel_gate")
    gate = dict(gate) if isinstance(gate, dict) else {}
    gate["passed"] = False
    gate["reason"] = result["strategy_inactive_reason"]
    if session:
        gate["session"] = session
    result["channel_gate"] = gate
    return result
