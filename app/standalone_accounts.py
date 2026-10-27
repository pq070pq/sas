"""Persistence services for standalone SAS accounts.

These functions are intentionally not exposed as HTTP routes yet. The caller owns
transaction boundaries and must send verification mail before presenting success.
Telegram users and Telegram subscription records are not modified here.
"""
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SASAccount, SASAuthToken, SASLoginSession
from .standalone_auth import digest_token, hash_password, new_bearer_token, normalize_email, verify_password


class AccountAlreadyExists(ValueError):
    """The normalized email already belongs to a standalone account."""


class InvalidCredentials(ValueError):
    """Email/password combination is invalid or account cannot log in."""


class InvalidOrExpiredToken(ValueError):
    """A verification or session token is invalid, expired, consumed, or revoked."""


async def register_account(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    verification_ttl_minutes: int = 30,
) -> tuple[SASAccount, str]:
    """Create an unverified account and return a one-time raw verification token.

    The raw token must only be sent to the account email and must never be logged.
    Caller must commit the transaction and handle delivery failure without marking
    the email verified.
    """
    if not 5 <= verification_ttl_minutes <= 1440:
        raise ValueError("Verification token lifetime must be between 5 minutes and 24 hours.")
    normalized = normalize_email(email)
    # Hash before querying to avoid making duplicate-address checks a cheap timing oracle.
    password_hash = hash_password(password)
    existing = await session.scalar(select(SASAccount.id).where(SASAccount.email == normalized))
    if existing is not None:
        raise AccountAlreadyExists("An account already exists for this email.")

    now = datetime.now(timezone.utc)
    account = SASAccount(
        id=str(uuid.uuid4()), email=normalized, password_hash=password_hash,
        email_verified_at=None, is_active=True, created_at=now, updated_at=now,
    )
    raw_token = new_bearer_token()
    session.add(account)
    await session.flush()
    session.add(SASAuthToken(
        account_id=account.id, purpose="email_verification", token_digest=digest_token(raw_token),
        expires_at=now + timedelta(minutes=verification_ttl_minutes), created_at=now,
    ))
    await session.flush()
    return account, raw_token


async def verify_account_email(session: AsyncSession, raw_token: str) -> SASAccount:
    """Consume a valid verification token once and mark its account verified."""
    now = datetime.now(timezone.utc)
    token = await session.scalar(select(SASAuthToken).where(
        SASAuthToken.token_digest == digest_token(raw_token),
        SASAuthToken.purpose == "email_verification",
        SASAuthToken.consumed_at.is_(None),
        SASAuthToken.expires_at > now,
    ))
    if token is None:
        raise InvalidOrExpiredToken("Verification token is invalid or expired.")
    account = await session.get(SASAccount, token.account_id)
    if account is None or not account.is_active:
        raise InvalidOrExpiredToken("Verification token is invalid or expired.")
    token.consumed_at = now
    account.email_verified_at = account.email_verified_at or now
    account.updated_at = now
    await session.flush()
    return account


async def login_account(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    session_ttl_days: int = 30,
) -> tuple[SASAccount, str]:
    """Validate credentials and issue a revocable session only after email verification."""
    if not 1 <= session_ttl_days <= 90:
        raise ValueError("Session lifetime must be between 1 and 90 days.")
    try:
        normalized = normalize_email(email)
    except ValueError:
        raise InvalidCredentials("Invalid email or password.") from None
    account = await session.scalar(select(SASAccount).where(SASAccount.email == normalized))
    if (
        account is None or not account.is_active or account.password_hash is None
        or not verify_password(password, account.password_hash)
        or account.email_verified_at is None
    ):
        raise InvalidCredentials("Invalid email or password.")

    now = datetime.now(timezone.utc)
    raw_token = new_bearer_token()
    session.add(SASLoginSession(
        account_id=account.id, token_digest=digest_token(raw_token),
        expires_at=now + timedelta(days=session_ttl_days), created_at=now,
    ))
    await session.flush()
    return account, raw_token


async def authenticate_session(session: AsyncSession, raw_token: str) -> SASAccount:
    """Resolve a live bearer token to an active account."""
    now = datetime.now(timezone.utc)
    login_session = await session.scalar(select(SASLoginSession).where(
        SASLoginSession.token_digest == digest_token(raw_token),
        SASLoginSession.revoked_at.is_(None),
        SASLoginSession.expires_at > now,
    ))
    if login_session is None:
        raise InvalidOrExpiredToken("Session is invalid or expired.")
    account = await session.get(SASAccount, login_session.account_id)
    if account is None or not account.is_active or account.email_verified_at is None:
        raise InvalidOrExpiredToken("Session is invalid or expired.")
    return account


async def logout_account(session: AsyncSession, raw_token: str) -> None:
    """Revoke a live session; repeated/invalid logout requests do not reveal state."""
    login_session = await session.scalar(select(SASLoginSession).where(
        SASLoginSession.token_digest == digest_token(raw_token),
        SASLoginSession.revoked_at.is_(None),
    ))
    if login_session is not None:
        login_session.revoked_at = datetime.now(timezone.utc)
        await session.flush()
