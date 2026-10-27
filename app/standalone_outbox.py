"""Encrypted durable outbox primitives for standalone account emails.

This module is not started automatically. Configure STANDALONE_EMAIL_FERNET_KEY
with a Fernet key stored in deployment secrets before enqueueing or sending mail.
Never log decrypted bodies or tokens.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SASEmailOutbox
from .standalone_email import send_standalone_email

logger = logging.getLogger(__name__)


class OutboxEncryptionNotConfigured(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("STANDALONE_EMAIL_FERNET_KEY", "").strip()
    if not key:
        raise OutboxEncryptionNotConfigured("Standalone outbox encryption key is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise OutboxEncryptionNotConfigured("Standalone outbox encryption key is invalid.") from exc


def encrypt_message(subject: str, body: str) -> str:
    payload = (subject + "\n" + body).encode("utf-8")
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_message(ciphertext: str) -> tuple[str, str]:
    try:
        decoded = _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
        raise OutboxEncryptionNotConfigured("Outbox message cannot be decrypted with the configured key.") from exc
    subject, separator, body = decoded.partition("\n")
    if not separator or not subject or not body:
        raise ValueError("Malformed encrypted outbox payload.")
    return subject, body


async def enqueue_email(
    session: AsyncSession,
    *,
    dedupe_key: str,
    recipient: str,
    subject: str,
    body: str,
) -> SASEmailOutbox:
    """Add encrypted email to the caller's transaction; caller commits atomically."""
    if not dedupe_key or len(dedupe_key) > 128:
        raise ValueError("A stable deduplication key is required.")
    if not recipient or len(recipient) > 254 or "\n" in recipient or "\r" in recipient:
        raise ValueError("Invalid recipient.")
    if not subject or "\n" in subject or "\r" in subject or len(subject) > 255:
        raise ValueError("Invalid subject.")
    encrypted = encrypt_message(subject, body)
    existing = await session.scalar(
        select(SASEmailOutbox).where(SASEmailOutbox.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return existing
    row = SASEmailOutbox(
        dedupe_key=dedupe_key,
        recipient=recipient,
        subject="[encrypted]",
        body=encrypted,
        status="pending",
        attempts=0,
        available_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


async def deliver_one(session: AsyncSession, *, now: datetime | None = None) -> bool:
    """Deliver one due message. Safe to run manually; no background task is started here."""
    current = now or datetime.now(timezone.utc)
    row = await session.scalar(
        select(SASEmailOutbox)
        .where(
            SASEmailOutbox.status == "pending",
            SASEmailOutbox.available_at <= current,
        )
        .order_by(SASEmailOutbox.id)
        .limit(1)
    )
    if row is None:
        return False
    # Commit a lease before network I/O so two workers are less likely to pick it.
    row.status = "sending"
    row.locked_until = current + timedelta(minutes=2)
    row.attempts = (row.attempts or 0) + 1
    await session.commit()
    try:
        subject, body = decrypt_message(row.body)
        await send_standalone_email(row.recipient, subject, body)
    except Exception as exc:
        await session.refresh(row)
        row.status = "pending" if row.attempts < 8 else "failed"
        row.locked_until = None
        row.available_at = current + timedelta(seconds=min(3600, 30 * (2 ** min(row.attempts, 7))))
        # Store only the exception class; never exception text, which can contain payloads.
        row.last_error = type(exc).__name__[:64]
        await session.commit()
        logger.warning("standalone email delivery failed; outbox_id=%s error_type=%s", row.id, type(exc).__name__)
        return True
    await session.refresh(row)
    row.status = "sent"
    row.sent_at = datetime.now(timezone.utc)
    row.locked_until = None
    row.last_error = None
    await session.commit()
    return True


def dedupe_key(purpose: str, raw_token: str) -> str:
    """Stable opaque idempotency key; the raw token is never persisted as a key."""
    if not purpose or not raw_token:
        raise ValueError("Purpose and token are required.")
    return hashlib.sha256((purpose + "\0" + raw_token).encode("utf-8")).hexdigest()
