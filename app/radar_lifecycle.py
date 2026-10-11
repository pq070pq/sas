"""Shared lifecycle rules for saved SAS PRO radar opportunities."""


def is_current_active_opportunity(payload):
    """Only a still-active, confirmed signal that passed the publish gate is saved as an opportunity."""
    if not isinstance(payload, dict):
        return False
    gate = payload.get("channel_gate")
    classification = payload.get("classification")
    if not isinstance(gate, dict) or not isinstance(classification, dict):
        return False
    return bool(
        payload.get("radar_active") is True
        and gate.get("passed") is True
        and classification.get("opportunity_status") == "confirmed"
        and classification.get("confirmation_ready") is True
    )
