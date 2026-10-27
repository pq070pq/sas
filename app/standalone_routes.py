"""Unmounted standalone account API.

This router is deliberately NOT included by app/main.py. Before mounting it, add
shared persistent rate limiting, CSRF protection for cookie-authenticated writes,
a transactional email outbox, and route-level integration tests.
"""
import smtplib

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .standalone_auth import normalize_email
from .standalone_accounts import (
    AccountAlreadyExists,
    InvalidCredentials,
    InvalidOrExpiredToken,
    authenticate_session,
    create_password_reset_token,
    login_account,
    logout_account,
    register_account,
    reset_password,
    verify_account_email,
)
from .standalone_email import EmailDeliveryNotConfigured, send_standalone_email
from .standalone_email_templates import password_reset_email, verification_email

router = APIRouter(prefix="/api/standalone/auth", tags=["standalone-auth"])
SESSION_COOKIE = "sas_standalone_session"
SESSION_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


class RegisterInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class TokenInput(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class ResetInput(TokenInput):
    new_password: str = Field(min_length=12, max_length=1024)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


def _origin(request: Request) -> str:
    # Never trust Host or forwarded headers to construct password-bearing links.
    # Configure the fixed origin in deployment before enabling this router.
    from .standalone_routes_config import standalone_public_origin
    return standalone_public_origin()


async def _send_mail_or_fail(recipient: str, subject: str, body: str) -> None:
    try:
        await send_standalone_email(recipient, subject, body)
    except (EmailDeliveryNotConfigured, OSError, ValueError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable.") from exc


@router.post("/register", status_code=202)
async def register(payload: RegisterInput, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        account, raw_token = await register_account(session, str(payload.email), payload.password)
        subject, body = verification_email(_origin(request), raw_token)
        await _send_mail_or_fail(account.email, subject, body)
        await session.commit()
    except AccountAlreadyExists:
        await session.rollback()
        # Same public response as a new registration; do not reveal account existence.
    except Exception:
        await session.rollback()
        raise
    return {"message": "If the address can be registered, a verification email will be sent."}


@router.post("/verify-email")
async def verify_email(payload: TokenInput, session: AsyncSession = Depends(get_session)):
    try:
        account = await verify_account_email(session, payload.token)
        await session.commit()
    except InvalidOrExpiredToken as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired.") from exc
    return {"message": "Email verified.", "account_id": account.id}


@router.post("/login")
async def login(payload: LoginInput, response: Response, session: AsyncSession = Depends(get_session)):
    try:
        account, raw_token = await login_account(session, str(payload.email), payload.password)
        await session.commit()
    except InvalidCredentials as exc:
        await session.rollback()
        raise HTTPException(status_code=401, detail="Invalid email or password.") from exc
    response.set_cookie(
        key=SESSION_COOKIE, value=raw_token, max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return {"account_id": account.id, "email": account.email}


@router.post("/logout", status_code=204)
async def logout(response: Response, request: Request, session: AsyncSession = Depends(get_session)):
    await logout_account(session, request.cookies.get(SESSION_COOKIE, ""))
    await session.commit()
    response.delete_cookie(key=SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.status_code = 204
    return response


@router.get("/me")
async def me(request: Request, session: AsyncSession = Depends(get_session)):
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    try:
        account = await authenticate_session(session, raw_token)
    except InvalidOrExpiredToken as exc:
        raise HTTPException(status_code=401, detail="Authentication required.") from exc
    return {"account_id": account.id, "email": account.email}


@router.post("/request-password-reset", status_code=202)
async def request_password_reset(payload: PasswordResetRequest, request: Request, session: AsyncSession = Depends(get_session)):
    raw_token = await create_password_reset_token(session, str(payload.email))
    if raw_token:
        subject, body = password_reset_email(_origin(request), raw_token)
        await _send_mail_or_fail(str(payload.email), subject, body)
    await session.commit()
    return {"message": "If a verified account exists for that address, a reset email will be sent."}


@router.post("/reset-password", status_code=204)
async def reset(payload: ResetInput, session: AsyncSession = Depends(get_session)):
    try:
        await reset_password(session, payload.token, payload.new_password)
        await session.commit()
    except InvalidOrExpiredToken as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired.") from exc
    return Response(status_code=204)
