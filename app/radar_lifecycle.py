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


from datetime import datetime, timezone


def is_fresh_quote_timestamp(value, *, max_age_seconds=300, now=None):
    """Require a parseable, recent provider timestamp before channel publication."""
    if value in (None, ""):
        return False
    try:
        if isinstance(value, (int, float)):
            stamp = float(value)
            if stamp > 1_000_000_000_000:
                stamp /= 1000.0
            observed = datetime.fromtimestamp(stamp, tz=timezone.utc)
        else:
            raw = str(value).strip()
            if raw.isdigit():
                stamp = float(raw)
                if stamp > 1_000_000_000_000:
                    stamp /= 1000.0
                observed = datetime.fromtimestamp(stamp, tz=timezone.utc)
            else:
                observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                observed = observed.astimezone(timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = (current.astimezone(timezone.utc) - observed).total_seconds()
        return -60 <= age <= max_age_seconds
    except (TypeError, ValueError, OverflowError, OSError):
        return False
