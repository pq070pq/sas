"""Persistence services for standalone SAS accounts.

These functions are intentionally not exposed as HTTP routes yet. The caller owns
transaction boundaries and must send verification mail before presenting success.
Telegram users and Telegram subscription records are not modified here.
"""
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SASAccount, SASAuthToken, SASLoginSession
from .standalone_auth import digest_token, hash_password, new_bearer_token, normalize_email, verify_password


class AccountAlreadyExists(ValueError):
    """The normalized email already belongs to a standalone account."""


class InvalidCredentials(ValueError):
    """Email/password combination is invalid or account cannot log in."""


class InvalidOrExpiredToken(ValueError):
    """A verification or session token is invalid, expired, consumed, or revoked."""


def _token_digest_or_raise(raw_token: str) -> str:
    try:
        return digest_token(raw_token)
    except ValueError:
        raise InvalidOrExpiredToken("Token is invalid or expired.") from None


async def register_account(session: AsyncSession, email: str, password: str, *, verification_ttl_minutes: int = 30) -> tuple[SASAccount, str]:
    """Create an unverified account and return a one-time raw verification token.

    Send the raw token only to the account email; never log it. Caller owns commit
    and must not mark email verified if delivery fails.
    """
    if not 5 <= verification_ttl_minutes <= 1440:
        raise ValueError("Verification token lifetime must be between 5 minutes and 24 hours.")
    normalized = normalize_email(email)
    password_hash = hash_password(password)
    existing = await session.scalar(select(SASAccount.id).where(SASAccount.email == normalized))
    if existing is not None:
        raise AccountAlreadyExists("An account already exists for this email.")
    now = datetime.now(timezone.utc)
    account = SASAccount(id=str(uuid.uuid4()), email=normalized, password_hash=password_hash,
                         email_verified_at=None, is_active=True, created_at=now, updated_at=now)
    raw_token = new_bearer_token()
    session.add(account)
    await session.flush()
    session.add(SASAuthToken(account_id=account.id, purpose="email_verification",
                             token_digest=digest_token(raw_token),
                             expires_at=now + timedelta(minutes=verification_ttl_minutes), created_at=now))
    await session.flush()
    return account, raw_token


async def verify_account_email(session: AsyncSession, raw_token: str) -> SASAccount:
    now = datetime.now(timezone.utc)
    token = await session.scalar(select(SASAuthToken).where(
        SASAuthToken.token_digest == _token_digest_or_raise(raw_token), SASAuthToken.purpose == "email_verification",
        SASAuthToken.consumed_at.is_(None), SASAuthToken.expires_at > now))
    if token is None:
        raise InvalidOrExpiredToken("Verification token is invalid or expired.")
    account = await session.get(SASAccount, token.account_id)
    if account is None or not account.is_active:
        raise InvalidOrExpiredToken("Verification token is invalid or expired.")
    # Atomically consume the token to prevent two concurrent requests from
    # successfully using the same verification link.
    consumed = await session.execute(
        update(SASAuthToken)
        .where(
            SASAuthToken.id == token.id,
            SASAuthToken.consumed_at.is_(None),
            SASAuthToken.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        raise InvalidOrExpiredToken("Verification token is invalid or expired.")
    token.consumed_at = now
    account.email_verified_at = account.email_verified_at or now
    account.updated_at = now
    await session.flush()
    return account


async def login_account(session: AsyncSession, email: str, password: str, *, session_ttl_days: int = 30) -> tuple[SASAccount, str]:
    if not 1 <= session_ttl_days <= 90:
        raise ValueError("Session lifetime must be between 1 and 90 days.")
    try:
        normalized = normalize_email(email)
    except ValueError:
        raise InvalidCredentials("Invalid email or password.") from None
    account = await session.scalar(select(SASAccount).where(SASAccount.email == normalized))
    if (account is None or not account.is_active or account.password_hash is None
            or not verify_password(password, account.password_hash) or account.email_verified_at is None):
        raise InvalidCredentials("Invalid email or password.")
    now = datetime.now(timezone.utc)
    raw_token = new_bearer_token()
    session.add(SASLoginSession(account_id=account.id, token_digest=digest_token(raw_token),
                                expires_at=now + timedelta(days=session_ttl_days), created_at=now))
    await session.flush()
    return account, raw_token


async def authenticate_session(session: AsyncSession, raw_token: str) -> SASAccount:
    now = datetime.now(timezone.utc)
    login_session = await session.scalar(select(SASLoginSession).where(
        SASLoginSession.token_digest == _token_digest_or_raise(raw_token), SASLoginSession.revoked_at.is_(None),
        SASLoginSession.expires_at > now))
    if login_session is None:
        raise InvalidOrExpiredToken("Session is invalid or expired.")
    account = await session.get(SASAccount, login_session.account_id)
    if account is None or not account.is_active or account.email_verified_at is None:
        raise InvalidOrExpiredToken("Session is invalid or expired.")
    return account


async def logout_account(session: AsyncSession, raw_token: str) -> None:
    try:
        token_digest = digest_token(raw_token)
    except ValueError:
        return
    login_session = await session.scalar(select(SASLoginSession).where(
        SASLoginSession.token_digest == token_digest, SASLoginSession.revoked_at.is_(None)))
    if login_session is not None:
        login_session.revoked_at = datetime.now(timezone.utc)
        await session.flush()


async def create_password_reset_token(session: AsyncSession, email: str, *, ttl_minutes: int = 20) -> str | None:
    """Return a one-time reset token, or None for an unknown/unverified account.

    HTTP handlers must return the same public message for both outcomes to avoid
    leaking whether an email is registered. Raw token must be emailed, never logged.
    """
    if not 5 <= ttl_minutes <= 120:
        raise ValueError("Password reset token lifetime must be between 5 minutes and 2 hours.")
    try:
        normalized = normalize_email(email)
    except ValueError:
        return None
    account = await session.scalar(select(SASAccount).where(SASAccount.email == normalized))
    if account is None or not account.is_active or account.email_verified_at is None:
        return None
    now = datetime.now(timezone.utc)
    raw_token = new_bearer_token()
    session.add(SASAuthToken(account_id=account.id, purpose="password_reset",
                             token_digest=digest_token(raw_token),
                             expires_at=now + timedelta(minutes=ttl_minutes), created_at=now))
    await session.flush()
    return raw_token


async def reset_password(session: AsyncSession, raw_token: str, new_password: str) -> None:
    """Consume a reset token, change the password, and revoke all account sessions."""
    new_hash = hash_password(new_password)
    now = datetime.now(timezone.utc)
    token = await session.scalar(select(SASAuthToken).where(
        SASAuthToken.token_digest == _token_digest_or_raise(raw_token), SASAuthToken.purpose == "password_reset",
        SASAuthToken.consumed_at.is_(None), SASAuthToken.expires_at > now))
    if token is None:
        raise InvalidOrExpiredToken("Password reset token is invalid or expired.")
    account = await session.get(SASAccount, token.account_id)
    if account is None or not account.is_active or account.email_verified_at is None:
        raise InvalidOrExpiredToken("Password reset token is invalid or expired.")
    # Atomically consume the reset token; a concurrent replay must fail.
    consumed = await session.execute(
        update(SASAuthToken)
        .where(
            SASAuthToken.id == token.id,
            SASAuthToken.consumed_at.is_(None),
            SASAuthToken.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        raise InvalidOrExpiredToken("Password reset token is invalid or expired.")
    token.consumed_at = now
    account.password_hash = new_hash
    account.updated_at = now
    sessions = (await session.scalars(select(SASLoginSession).where(
        SASLoginSession.account_id == account.id, SASLoginSession.revoked_at.is_(None)))).all()
    for login_session in sessions:
        login_session.revoked_at = now
    # Other outstanding reset links are invalidated too.
    other_tokens = (await session.scalars(select(SASAuthToken).where(
        SASAuthToken.account_id == account.id, SASAuthToken.purpose == "password_reset",
        SASAuthToken.consumed_at.is_(None), SASAuthToken.id != token.id))).all()
    for other in other_tokens:
        other.consumed_at = now
    await session.flush()
