"""Database-backed fixed-window rate limits for standalone auth endpoints.

Keys are HMAC-like SHA-256 digests; raw IP addresses/emails are not persisted.
SQLite and PostgreSQL use atomic UPSERT so limits are shared across workers.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import math

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SASRateLimitBucket


def _key_digest(scope: str, identity: str, window_start: int) -> str:
    raw = f"{scope.strip().lower()}\0{identity.strip().lower()}\0{window_start}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def enforce_rate_limit(
    session: AsyncSession,
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
    now: datetime | None = None,
) -> None:
    """Atomically record one attempt; raise HTTP 429 when the bucket is exhausted."""
    if not scope or not identity or limit < 1 or window_seconds < 1:
        raise ValueError("Invalid rate-limit parameters.")
    current = now or datetime.now(timezone.utc)
    epoch = int(current.timestamp())
    window_start = math.floor(epoch / window_seconds) * window_seconds
    expires_at = datetime.fromtimestamp(window_start + window_seconds, tz=timezone.utc)
    key = _key_digest(scope, identity, window_start)
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "sqlite":
        insert = sqlite_insert(SASRateLimitBucket)
    elif dialect == "postgresql":
        insert = pg_insert(SASRateLimitBucket)
    else:
        # Fail closed rather than silently running an unsafe process-local limiter.
        raise RuntimeError("Standalone rate limiting requires SQLite or PostgreSQL.")
    statement = insert.values(
        bucket_key=key, hits=1, expires_at=expires_at, created_at=current
    ).on_conflict_do_update(
        index_elements=[SASRateLimitBucket.bucket_key],
        set_={"hits": SASRateLimitBucket.hits + 1},
    ).returning(SASRateLimitBucket.hits)
    hits = (await session.execute(statement)).scalar_one()
    # Persist the counter before route logic can roll back a failed login/reset.
    await session.commit()
    if hits > limit:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(max(1, int(expires_at.timestamp() - current.timestamp())))},
        )
