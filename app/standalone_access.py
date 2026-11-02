"""Standalone anonymous installation access and protected admin management.

Public installation identity is a random opaque token. Only its SHA-256 hash is
stored server-side. Admin credentials are supplied through deployment secrets.
"""
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import (
    SessionLocal, StandaloneInstallation, StandaloneAdminSession,
    StandaloneAdminAudit, RadarSignal, get_session,
)

router = APIRouter()
ADMIN_COOKIE = "sas_standalone_admin"
ADMIN_SESSION_HOURS = max(1, min(int(settings.standalone_admin_session_hours or 8), 24))
INITIAL_ACCESS_DAYS = 30
_LOGIN_FAILURES = {}
_REGISTRATION_ATTEMPTS = {}
_LOGIN_WINDOW_SECONDS = 900
_LOGIN_MAX_FAILURES = 8


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def installation_state(row: StandaloneInstallation, now=None) -> str:
    now = as_utc(now or utc_now())
    if row.status in {"blocked", "revoked"}:
        return row.status
    if row.permanent:
        return "active"
    expiry = as_utc(row.access_expires_at)
    if expiry is None or expiry <= now:
        return "expired"
    return "active"


def ensure_same_origin(request: Request):
    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        parsed = urlsplit(origin)
        origin_host = (parsed.hostname or "").lower()
        request_host = (request.url.hostname or "").lower()
        origin_port = parsed.port
        request_port = request.url.port
    except ValueError:
        raise HTTPException(403, "طلب غير موثوق")
    if parsed.scheme not in {"https", "http"} or origin_host != request_host or origin_port != request_port:
        raise HTTPException(403, "طلب غير موثوق")


def _check_login_rate(ip: str):
    now = time.monotonic()
    item = _LOGIN_FAILURES.get(ip)
    if item and now - item[0] < _LOGIN_WINDOW_SECONDS and item[1] >= _LOGIN_MAX_FAILURES:
        raise HTTPException(429, "محاولات كثيرة. حاول بعد 15 دقيقة")
    if not item or now - item[0] >= _LOGIN_WINDOW_SECONDS:
        _LOGIN_FAILURES[ip] = (now, 0)


def _check_registration_rate(ip: str):
    now = time.monotonic()
    item = _REGISTRATION_ATTEMPTS.get(ip)
    if item and now - item[0] < 600 and item[1] >= 30:
        raise HTTPException(429, "محاولات تسجيل كثيرة من الشبكة. حاول لاحقًا")
    count = item[1] + 1 if item and now - item[0] < 600 else 1
    _REGISTRATION_ATTEMPTS[ip] = (now, count)
    if len(_REGISTRATION_ATTEMPTS) > 2000:
        for key, value in list(_REGISTRATION_ATTEMPTS.items()):
            if now - value[0] >= 600:
                _REGISTRATION_ATTEMPTS.pop(key, None)


def _record_login_failure(ip: str):
    now = time.monotonic()
    item = _LOGIN_FAILURES.get(ip)
    count = item[1] + 1 if item and now - item[0] < _LOGIN_WINDOW_SECONDS else 1
    _LOGIN_FAILURES[ip] = (now, count)
    if len(_LOGIN_FAILURES) > 2000:
        for key, value in list(_LOGIN_FAILURES.items()):
            if now - value[0] >= _LOGIN_WINDOW_SECONDS:
                _LOGIN_FAILURES.pop(key, None)


async def require_admin_session(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    raw = request.cookies.get(ADMIN_COOKIE, "")
    if not raw:
        raise HTTPException(401, "يلزم تسجيل دخول الأدمن")
    row = (await db.execute(
        select(StandaloneAdminSession).where(
            StandaloneAdminSession.token_hash == token_digest(raw)
        )
    )).scalars().first()
    if not row or as_utc(row.expires_at) <= utc_now():
        if row:
            await db.delete(row)
            await db.commit()
        raise HTTPException(401, "انتهت جلسة الأدمن")
    row.last_seen_at = utc_now()
    await db.commit()
    return row


async def require_installation(
    x_sas_installation: str = Header(default="", alias="X-SAS-Installation"),
    db: AsyncSession = Depends(get_session),
):
    if not x_sas_installation or len(x_sas_installation) > 160:
        raise HTTPException(401, "معرّف التثبيت غير موجود")
    row = (await db.execute(
        select(StandaloneInstallation).where(
            StandaloneInstallation.token_hash == token_digest(x_sas_installation)
        )
    )).scalars().first()
    if not row:
        raise HTTPException(401, "معرّف التثبيت غير صالح")
    state = installation_state(row)
    if state == "blocked":
        raise HTTPException(403, "تم إيقاف الوصول لهذا التثبيت")
    if state == "revoked":
        raise HTTPException(403, "تم إلغاء الوصول لهذا التثبيت")
    if state == "expired":
        raise HTTPException(403, "انتهت مدة الاستخدام")
    row.last_seen_at = utc_now()
    await db.commit()
    return row


async def _audit(db: AsyncSession, actor: str, action: str, installation_id=None, details=None):
    db.add(StandaloneAdminAudit(
        actor_username=actor,
        action=action,
        installation_id=installation_id,
        details=json.dumps(details or {}, ensure_ascii=False),
    ))
    await db.commit()


@router.post("/api/standalone/register")
async def register_installation(
    request: Request,
    x_sas_installation: str = Header(default="", alias="X-SAS-Installation"),
    db: AsyncSession = Depends(get_session),
):
    """Create a 30-day installation record or validate an existing token."""
    ip = request.client.host if request.client else "unknown"
    _check_registration_rate(ip)
    now = utc_now()
    if x_sas_installation:
        if len(x_sas_installation) > 160:
            raise HTTPException(400, "معرّف التثبيت غير صالح")
        row = (await db.execute(
            select(StandaloneInstallation).where(
                StandaloneInstallation.token_hash == token_digest(x_sas_installation)
            )
        )).scalars().first()
        if not row:
            # The native app generates a high-entropy installation token and
            # persists it in Keychain/Keystore before its first registration.
            if len(x_sas_installation) < 40:
                raise HTTPException(400, "معرّف التثبيت قصير وغير آمن")
            row = StandaloneInstallation(
                id=str(uuid4()),
                token_hash=token_digest(x_sas_installation),
                status="active",
                access_starts_at=now,
                access_expires_at=now + timedelta(days=INITIAL_ACCESS_DAYS),
                permanent=False,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
            await db.commit()
            return {
                "ok": True, "installation_id": row.id, "status": "active",
                "access_starts_at": now.isoformat(),
                "access_expires_at": as_utc(row.access_expires_at).isoformat(),
                "days_granted": INITIAL_ACCESS_DAYS, "new_installation": True,
            }
        state = installation_state(row, now)
        if state != "active":
            raise HTTPException(403, "الوصول موقوف أو منتهي")
        row.last_seen_at = now
        await db.commit()
        return {
            "ok": True, "installation_id": row.id, "status": "active",
            "access_expires_at": as_utc(row.access_expires_at).isoformat() if row.access_expires_at else None,
            "permanent": bool(row.permanent), "new_installation": False,
        }

    token = secrets.token_urlsafe(40)
    row = StandaloneInstallation(
        id=str(uuid4()),
        token_hash=token_digest(token),
        status="active",
        access_starts_at=now,
        access_expires_at=now + timedelta(days=INITIAL_ACCESS_DAYS),
        permanent=False,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(row)
    await db.commit()
    return {
        "ok": True, "installation_id": row.id, "installation_token": token,
        "status": "active", "access_starts_at": now.isoformat(),
        "access_expires_at": row.access_expires_at.isoformat(),
        "days_granted": INITIAL_ACCESS_DAYS, "new_installation": True,
    }


@router.get("/api/standalone/access")
async def get_installation_access(row=Depends(require_installation)):
    expiry = as_utc(row.access_expires_at)
    remaining = None if row.permanent else max(0, (expiry - utc_now()).total_seconds())
    return {
        "ok": True, "installation_id": row.id, "status": "permanent" if row.permanent else "active",
        "access_starts_at": as_utc(row.access_starts_at).isoformat(),
        "access_expires_at": expiry.isoformat() if expiry else None,
        "seconds_remaining": remaining,
    }


@router.get("/admin/standalone")
async def standalone_admin_page():
    return FileResponse("web/standalone-admin.html")


@router.post("/api/standalone/admin/login")
async def standalone_admin_login(request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    ensure_same_origin(request)
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
    if not settings.standalone_admin_username or not settings.standalone_admin_password:
        raise HTTPException(503, "إعداد دخول الأدمن غير مكتمل على الخادم")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "بيانات الدخول غير صالحة")
    if not isinstance(payload, dict):
        raise HTTPException(400, "بيانات الدخول غير صالحة")
    username = str(payload.get("username") or "")[:128]
    password = str(payload.get("password") or "")
    valid_user = hmac.compare_digest(username, settings.standalone_admin_username)
    valid_password = hmac.compare_digest(password, settings.standalone_admin_password)
    if not (valid_user and valid_password):
        _record_login_failure(ip)
        raise HTTPException(401, "بيانات الدخول غير صحيحة")
    _LOGIN_FAILURES.pop(ip, None)
    raw = secrets.token_urlsafe(48)
    db.add(StandaloneAdminSession(
        token_hash=token_digest(raw),
        admin_username=settings.standalone_admin_username,
        expires_at=utc_now() + timedelta(hours=ADMIN_SESSION_HOURS),
    ))
    await db.commit()
    response.set_cookie(
        ADMIN_COOKIE, raw, max_age=ADMIN_SESSION_HOURS * 3600, httponly=True,
        secure=bool(settings.standalone_admin_cookie_secure), samesite="strict", path="/",
    )
    return {"ok": True, "username": settings.standalone_admin_username, "expires_in": ADMIN_SESSION_HOURS * 3600}


@router.get("/api/standalone/admin/me")
async def standalone_admin_me(session=Depends(require_admin_session)):
    return {"ok": True, "username": session.admin_username, "expires_at": as_utc(session.expires_at).isoformat()}


@router.post("/api/standalone/admin/logout")
async def standalone_admin_logout(
    request: Request,
    response: Response,
    session=Depends(require_admin_session),
    db: AsyncSession = Depends(get_session),
):
    ensure_same_origin(request)
    await db.delete(session)
    await db.commit()
    response.delete_cookie(ADMIN_COOKIE, path="/", httponly=True, samesite="strict", secure=bool(settings.standalone_admin_cookie_secure))
    return {"ok": True}


@router.get("/api/standalone/admin/summary")
async def standalone_admin_summary(
    session=Depends(require_admin_session),
    db: AsyncSession = Depends(get_session),
):
    rows = (await db.execute(select(StandaloneInstallation))).scalars().all()
    states = [installation_state(row) for row in rows]
    return {
        "ok": True, "total": len(rows), "active": states.count("active"),
        "expired": states.count("expired"), "blocked": states.count("blocked"),
        "revoked": states.count("revoked"), "permanent": sum(bool(row.permanent) and state == "active" for row, state in zip(rows, states)),
        "initial_access_days": INITIAL_ACCESS_DAYS,
    }


@router.get("/api/standalone/admin/installations")
async def standalone_admin_installations(
    q: str = Query(default="", max_length=100),
    status: str = Query(default="all", max_length=20),
    session=Depends(require_admin_session),
    db: AsyncSession = Depends(get_session),
):
    rows = (await db.execute(
        select(StandaloneInstallation).order_by(StandaloneInstallation.last_seen_at.desc()).limit(500)
    )).scalars().all()
    items = []
    needle = q.strip().lower()
    for row in rows:
        state = installation_state(row)
        display_state = "permanent" if row.permanent and state == "active" else state
        if status != "all" and display_state != status:
            continue
        if needle and needle not in row.id.lower() and needle not in (row.note or "").lower():
            continue
        expiry = as_utc(row.access_expires_at)
        items.append({
            "id": row.id, "status": display_state,
            "first_seen_at": as_utc(row.first_seen_at).isoformat(),
            "last_seen_at": as_utc(row.last_seen_at).isoformat(),
            "access_starts_at": as_utc(row.access_starts_at).isoformat(),
            "access_expires_at": expiry.isoformat() if expiry else None,
            "permanent": bool(row.permanent), "note": row.note or "",
        })
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/api/standalone/admin/installations/{installation_id}/access")
async def standalone_admin_update_access(
    installation_id: str,
    request: Request,
    session=Depends(require_admin_session),
    db: AsyncSession = Depends(get_session),
):
    ensure_same_origin(request)
    row = await db.get(StandaloneInstallation, installation_id)
    if not row:
        raise HTTPException(404, "المستخدم غير موجود")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "الطلب غير صالح")
    if not isinstance(payload, dict):
        raise HTTPException(400, "الطلب غير صالح")
    now = utc_now()
    action = "updated"
    if payload.get("blocked") is True:
        row.status = "blocked"
        row.blocked_at = now
        action = "blocked"
    elif payload.get("blocked") is False:
        row.status = "active"
        row.blocked_at = None
        action = "unblocked"
    elif payload.get("revoked") is True:
        row.status = "revoked"
        row.permanent = False
        row.access_expires_at = now
        action = "revoked"
    elif payload.get("permanent") is True:
        row.permanent = True
        if row.status != "blocked":
            row.status = "active"
            row.blocked_at = None
        action = "made_permanent"
    elif "extend_days" in payload:
        try:
            days = int(payload["extend_days"])
        except (TypeError, ValueError):
            raise HTTPException(400, "عدد الأيام غير صالح")
        if days < 1 or days > 3650:
            raise HTTPException(400, "المدة يجب أن تكون بين يوم واحد و10 سنوات")
        current = as_utc(row.access_expires_at)
        base = current if current and current > now else now
        row.access_expires_at = base + timedelta(days=days)
        row.permanent = False
        if row.status != "blocked":
            row.status = "active"
            row.blocked_at = None
        action = "extended"
    elif "expires_at" in payload:
        try:
            expiry = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            expiry = expiry.astimezone(timezone.utc)
        except (TypeError, ValueError):
            raise HTTPException(400, "تاريخ الانتهاء غير صالح")
        row.access_expires_at = expiry
        row.permanent = False
        if row.status != "blocked":
            row.status = "active"
            row.blocked_at = None
        action = "expiry_set"
    else:
        raise HTTPException(400, "لم يتم تحديد إجراء")
    if "note" in payload:
        row.note = str(payload.get("note") or "")[:1000] or None
    row.updated_at = now
    await db.commit()
    await _audit(db, session.admin_username, action, row.id, {
        "status": row.status, "permanent": row.permanent,
        "access_expires_at": as_utc(row.access_expires_at).isoformat() if row.access_expires_at else None,
    })
    return {
        "ok": True, "id": row.id, "status": "permanent" if row.permanent else installation_state(row),
        "access_expires_at": as_utc(row.access_expires_at).isoformat() if row.access_expires_at else None,
        "permanent": bool(row.permanent),
    }


@router.get("/api/standalone/admin/audit")
async def standalone_admin_audit(
    session=Depends(require_admin_session),
    db: AsyncSession = Depends(get_session),
):
    rows = (await db.execute(
        select(StandaloneAdminAudit).order_by(StandaloneAdminAudit.created_at.desc()).limit(100)
    )).scalars().all()
    return {"ok": True, "items": [{
        "actor": row.actor_username, "action": row.action,
        "installation_id": row.installation_id, "details": row.details,
        "created_at": as_utc(row.created_at).isoformat(),
    } for row in rows]}


@router.get("/api/standalone/market/status")
async def standalone_market_status(row=Depends(require_installation)):
    from .market_calendar import market_status
    return market_status()


@router.get("/api/standalone/market/ticker")
async def standalone_market_ticker(row=Depends(require_installation)):
    from .main import market_ticker
    return await market_ticker()


@router.get("/api/standalone/market/movers")
async def standalone_market_movers(row=Depends(require_installation)):
    from .main import perpequities_market_movers
    return await perpequities_market_movers()


@router.get("/api/standalone/market/news")
async def standalone_market_news(row=Depends(require_installation)):
    from .main import market_news_endpoint
    return await market_news_endpoint()


@router.get("/api/standalone/radar/signals")
async def standalone_radar_signals(
    limit: int = Query(default=30, ge=1, le=100),
    row=Depends(require_installation),
    db: AsyncSession = Depends(get_session),
):
    records = (await db.execute(
        select(RadarSignal).order_by(RadarSignal.created_at.desc()).limit(limit)
    )).scalars().all()
    items = []
    for record in records:
        try:
            payload = json.loads(record.payload or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        items.append({
            "id": record.id,
            "symbol": record.symbol,
            "session_date": record.session_date,
            "created_at": as_utc(record.created_at).isoformat() if record.created_at else None,
            "payload": payload,
        })
    return {"ok": True, "items": items, "count": len(items)}
