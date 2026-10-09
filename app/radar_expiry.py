"""Shared expiry rules for published SAS PRO radar opportunities."""
from datetime import datetime, timedelta, timezone

RADAR_SIGNAL_TTL = timedelta(hours=24)


def _utc(value: datetime) -> datetime:
    """Normalize SQLite's sometimes-naive datetimes to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expiry_at(created_at: datetime | None = None) -> datetime:
    """Return the fixed expiry timestamp for a newly created radar signal."""
    created_at = _utc(created_at or datetime.now(timezone.utc))
    return created_at + RADAR_SIGNAL_TTL


def is_expired(expires_at: datetime | None, now: datetime | None = None) -> bool:
    """Return whether a signal has reached its expiry time."""
    if expires_at is None:
        return False
    now = _utc(now or datetime.now(timezone.utc))
    return _utc(expires_at) <= now
