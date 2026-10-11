"""Shared eligibility policy for SAS PRO active saved radar opportunities."""


def is_confirmed_opportunity(row):
    """Only an explicitly confirmed, complete technical setup is an active opportunity."""
    if not isinstance(row, dict):
        return False
    classification = row.get("classification")
    if not isinstance(classification, dict):
        return False
    return (
        classification.get("opportunity_status") == "confirmed"
        and bool(classification.get("confirmation_ready"))
    )
