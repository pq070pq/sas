import asyncio
import json
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import SessionLocal, User, Subscription, Payment, StockAnalysis, RadarSignal, AccessRequest, Setting, Invite, get_session, init_db
from .telegram import validate_init_data, send_message, bot_api
from .market import quote, ticker
from .panwatch import analyze, technical_targets
from .news import company_news, corporate_events
from .jobs import scheduler
from .market_calendar import market_status
from .holiday_radar import stock_radar_enabled
from .holiday_radar import holiday_radar_scheduler
from .timeutil import utcnow, aware
from .subscriptions import TERMS_VERSION, TERMS_TEXT, get_plans, start_trial_for_user, create_invoice_for_user, apply_successful_payment, grant_access, active_subscription, ensure_subscription_settings
from .admin import PERMISSIONS, ROLE_DEFAULTS, get_admin, has_permission, audit

app = FastAPI(title="SAS PRO", version="2.1.0")
app.mount("/assets", StaticFiles(directory="web/assets"), name="assets")

DISCLAIMER = "🚨 لايعد توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️"
PLANS = {}
PLAN_LABELS = {}

@app.on_event("startup")
async def startup():
    await init_db()
    await ensure_subscription_settings()
    if settings.telegram_bot_token:
        try:
            await bot_api("setMyCommands", {"commands": [
                {"command":"start","description":"فتح SAS PRO"},
                {"command":"terms","description":"شروط الاستخدام"},
                {"command":"paysupport","description":"دعم المدفوعات"},
            ]})
            if settings.owner_telegram_id:
                await bot_api("setMyCommands", {
                    "scope": {"type":"chat","chat_id":settings.owner_telegram_id},
                    "commands": [
                        {"command":"start","description":"فتح SAS PRO"},
                        {"command":"status","description":"حالة الاشتراكات"},
                        {"command":"grant","description":"منح اشتراك"},
                        {"command":"revoke","description":"إلغاء اشتراك"},
                    ],
                })
        except Exception:
            pass
    asyncio.create_task(scheduler())
    asyncio.create_task(holiday_radar_scheduler())

def build_report(symbol: str, q: dict, tech: dict, classification: dict | None = None) -> str:
    price = q.get("price")
    change = q.get("change_pct")
    targets = tech.get("targets") or []
    exit_level = tech.get("exit")
    volume_ratio = tech.get("volume_ratio")
    classification = classification or {}
    stock_type = classification.get("type", "غير واضح")
    type_emoji = classification.get("emoji", "⚪")
    type_reason = classification.get("reason", "بيانات غير كافية")
    behavior = classification.get("behavior", "غير واضح")

    move = f"{float(change):+.2f}%" if change is not None else "غير واضح"
    trading = "قوي" if volume_ratio is not None and volume_ratio >= 1.15 else "عادي"
    liquidity = "عالية" if volume_ratio is not None and volume_ratio >= 1.50 else "مو واضحة"
    strength = "قوية" if volume_ratio is not None and volume_ratio >= 1.50 else "متوسطة"

    labels = ["الأول", "الثاني", "الثالث"]
    target_lines = [
        f"الهدف {labels[i]}: {_money(level)}"
        for i, level in enumerate(targets[:3])
    ]
    if not target_lines:
        target_lines = ["الأهداف: غير واضحة حاليًا"]

    exit_text = f"🛑 حد الخروج: {_money(exit_level)}" if exit_level is not None else "🛑 حد الخروج: غير واضح"

    return (
        f"🚨 SAS PRO RADAR\n\n"
        f"🔹 السهم: {symbol}\n"
        f"💵 السعر: {_money(float(price)) if price is not None else 'غير واضح'}\n"
        f"📈 مرتفع/منخفض: {move}\n"
        f"📊 التداول: {trading}\n"
        f"💰 السيولة: {liquidity}\n"
        f"🔥 حركة السهم: {strength}\n"
        f"🏷️ نوع السهم: {type_emoji} {stock_type}\n"
        f"🧭 السلوك: {behavior}\n"
        f"↳ {type_reason}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🤖 قراءة SAS PRO\n\n"
        f"📈 الاتجاه: صاعد إذا حافظ على مستوياته الحالية\n"
        f"💪 قوة الحركة: {strength}\n"
        f"📊 التداول: {'يدعم استمرار الحركة' if volume_ratio is not None and volume_ratio >= 1.15 else 'يحتاج متابعة'}\n"
        f"⚠️ مستوى الخطورة: {'مرتفع' if volume_ratio is not None and volume_ratio >= 1.50 else 'متوسط'}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🎯 الأهداف\n\n"
        f"{chr(10).join(target_lines)}\n\n"
        f"{exit_text}\n\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🧠 الزبدة\n\n"
        f"الأهداف محسوبة من مقاومات فعلية ظهرت في بيانات السعر، "
        f"وما ينحط هدف رقمي إذا ما فيه مستوى واضح.\n\n"
        f"🚨 إذا ضعف التداول أو كسر السهم حد الخروج، تتغير النظرة.\n\n"
        f"{DISCLAIMER}"
    )

def _money(value):
    if value is None:
        return "غير واضح"
    return f"${value:,.4f}".rstrip("0").rstrip(".")

async def set_channel_access(telegram_id: int, allow: bool, expires_at=None):
    """Lock/unlock channel access for SAS PRO users."""
    if not settings.telegram_channel_id:
        return {"ok": False, "reason": "telegram_channel_id_not_configured"}

    if not allow:
        try:
            await bot_api("banChatMember", {
                "chat_id": settings.telegram_channel_id,
                "user_id": telegram_id,
                "revoke_messages": False,
            })
            return {"ok": True, "action": "banned"}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    # Remove any previous ban first.
    try:
        await bot_api("unbanChatMember", {
            "chat_id": settings.telegram_channel_id,
            "user_id": telegram_id,
            "only_if_banned": True,
        })
    except Exception:
        pass

    # If the user has an outstanding join request, approve it automatically.
    try:
        await bot_api("approveChatJoinRequest", {
            "chat_id": settings.telegram_channel_id,
            "user_id": telegram_id,
        })
        return {"ok": True, "action": "join_request_approved"}
    except Exception:
        pass

    # Telegram Bot API cannot silently add an arbitrary user to a private
    # channel. Give the approved user a one-use invite as the fallback.
    try:
        payload = {
            "chat_id": settings.telegram_channel_id,
            "name": f"SAS PRO {telegram_id}",
            "member_limit": 1,
            "creates_join_request": False,
        }
        if expires_at:
            payload["expire_date"] = int(expires_at.timestamp())
        link = await bot_api("createChatInviteLink", payload)
        invite = link.get("invite_link") if isinstance(link, dict) else link
        if invite:
            await send_message(
                telegram_id,
                "✅ <b>تم قبول إذن دخولك إلى قناة SAS PRO</b>\n\n"
                "رابط الدخول الخاص بك مرفق أدناه.\n"
                f"⏳ ينتهي الإذن: <b>{expires_at.strftime('%d/%m/%Y')}</b>",
                {"inline_keyboard": [[{"text": "🚀 دخول قناة SAS PRO", "url": invite}]]},
            )
            return {"ok": True, "action": "invite_sent"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}

    return {"ok": False, "reason": "channel_access_not_granted"}

def is_active(sub):
    return bool(sub and sub.active and aware(sub.expires_at) > utcnow())

async def telegram_user(x_telegram_init_data: str = Header(default="")):
    try:
        return validate_init_data(x_telegram_init_data)
    except Exception as e:
        raise HTTPException(401, str(e))

async def require_terms(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
    if not row or row.terms_version != TERMS_VERSION or not row.terms_accepted_at:
        raise HTTPException(409, "يجب الموافقة على شروط استخدام SAS PRO أولاً")
    return user

async def require_pro(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    # المالك يدخل مباشرة. المستخدم يدخل SAS PRO عند وجود اشتراك فعال،
    # تجربة فعالة، أو وصول مجاني منحه المشرف.
    if int(user["id"]) == int(settings.owner_telegram_id):
        return user

    row = (await db.execute(
        select(User).where(User.telegram_id == user["id"])
    )).scalars().first()
    if not row or row.terms_version != TERMS_VERSION or not row.terms_accepted_at:
        raise HTTPException(409, "يجب الموافقة على الشروط أولاً")

    now = utcnow()
    trial_active = bool(
        row.status == "trial"
        and row.trial_expires
        and aware(row.trial_expires) > now
    )
    subscription_active = bool(
        row.status == "active"
        and row.subscription_expires
        and aware(row.subscription_expires) > now
    )

    if row.free_access or subscription_active or trial_active:
        return user

    raise HTTPException(403, "صلاحيات SAS PRO غير مفعلة أو منتهية")

@app.get("/")
async def home():
    return FileResponse("web/index.html")

@app.get("/health")
async def health():
    return {"ok": True, "app": "SAS PRO", "version": app.version}


@app.post("/api/access/request")
async def request_access(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
    if not row:
        row = User(telegram_id=user["id"], username=user.get("username"), first_name=user.get("first_name"))
        db.add(row)
        await db.flush()
    if row.terms_version != TERMS_VERSION or not row.terms_accepted_at:
        raise HTTPException(409, "يجب الموافقة على شروط استخدام SAS PRO أولاً")
    active = (await db.execute(select(Subscription).where(
        Subscription.telegram_id == user["id"], Subscription.active == True
    ).order_by(Subscription.expires_at.desc()))).scalars().first()
    if is_active(active):
        return {"ok": True, "status": "active", "message": "لديك إذن دخول فعال حاليًا.", "expires_at": active.expires_at.isoformat()}
    pending = (await db.execute(select(AccessRequest).where(
        AccessRequest.telegram_id == user["id"], AccessRequest.status == "pending"
    ).order_by(AccessRequest.requested_at.desc()))).scalars().first()
    if pending:
        return {"ok": True, "status": "pending", "request_id": pending.id, "message": "طلبك موجود لدى الإدارة وتحت المراجعة."}
    req = AccessRequest(
        telegram_id=user["id"],
        username=user.get("username"),
        first_name=user.get("first_name"),
        terms_version=TERMS_VERSION,
        status="pending",
    )
    db.add(req)
    await db.commit()
    try:
        await send_message(
            settings.owner_telegram_id,
            "🔐 <b>طلب إذن دخول جديد — SAS PRO</b>\n\n"
            f"👤 الاسم: <b>{user.get('first_name') or 'بدون اسم'}</b>\n"
            f"🔗 المستخدم: <b>{('@'+user.get('username')) if user.get('username') else 'بدون اسم مستخدم'}</b>\n"
            f"🆔 Telegram ID: <code>{user['id']}</code>\n"
            f"📋 الشروط المقبولة: <b>{TERMS_VERSION}</b>\n"
            f"📝 رقم الطلب: <b>#{req.id}</b>\n\n"
            "استخدم: <code>/grant ID DAYS</code> لتحديد مدة الدخول، أو <code>/revoke ID</code> للإلغاء."
        )
    except Exception:
        pass
    channel_link = None
    if settings.telegram_channel_id:
        try:
            link = await bot_api("createChatInviteLink", {
                "chat_id": settings.telegram_channel_id,
                "name": "SAS PRO Access Requests",
                "creates_join_request": True,
            })
            channel_link = link.get("invite_link") if isinstance(link, dict) else link
        except Exception:
            channel_link = None

    user_message = "تم إرسال طلبك للإدارة. بعد اعتماد المدة سيتم تفعيل وصولك."
    if channel_link:
        user_message += "\nأرسل طلب الانضمام للقناة من الزر التالي؛ بعد اعتماد الإدارة سيوافق البوت على طلب الانضمام تلقائيًا."
        try:
            await send_message(
                user["id"],
                "📡 <b>طلب الانضمام إلى قناة SAS PRO</b>\n\n"
                "أرسل طلب الانضمام الآن وسيبقى معلّقًا حتى اعتماد الإدارة.\n"
                "بعد اعتماد المدة سيضيفك البوت تلقائيًا إلى القناة.",
                {"inline_keyboard": [[{"text": "📡 طلب الانضمام للقناة", "url": channel_link}]]},
            )
        except Exception:
            pass
    return {"ok": True, "status": "pending", "request_id": req.id, "message": user_message, "channel_join_request_link": channel_link}

@app.get("/api/terms")
async def terms():
    return {"version": TERMS_VERSION, "text": TERMS_TEXT}



@app.get("/api/terms/my")
async def my_terms(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
    if not row or not row.terms_accepted_at:
        return {"accepted": False, "version": None, "accepted_at": None}
    return {
        "accepted": True,
        "version": row.terms_version,
        "accepted_at": aware(row.terms_accepted_at).isoformat(),
        "text": TERMS_TEXT,
    }


@app.get("/api/admin/access-requests")
async def admin_access_requests(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    rows = (await db.execute(select(AccessRequest).order_by(AccessRequest.requested_at.desc()).limit(100))).scalars().all()
    return [{
        "id": r.id,
        "telegram_id": r.telegram_id,
        "username": r.username,
        "first_name": r.first_name,
        "terms_version": r.terms_version,
        "requested_at": aware(r.requested_at).isoformat(),
        "status": r.status,
        "decided_at": aware(r.decided_at).isoformat() if r.decided_at else None,
        "decided_days": r.decided_days,
        "admin_note": r.admin_note,
    } for r in rows]

@app.get("/api/admin/terms")
async def admin_terms(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    rows = (await db.execute(
        select(User).where(User.terms_accepted_at.is_not(None)).order_by(User.terms_accepted_at.desc())
    )).scalars().all()
    return [{
        "telegram_id": u.telegram_id,
        "username": u.username,
        "first_name": u.first_name,
        "version": u.terms_version,
        "accepted_at": aware(u.terms_accepted_at).isoformat(),
    } for u in rows]


async def terms():
    return {"version": TERMS_VERSION, "text": TERMS_TEXT}

@app.post("/api/terms/accept")
async def accept_terms(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
    if not row:
        row = User(telegram_id=user["id"], username=user.get("username"), first_name=user.get("first_name"))
        db.add(row)
    row.terms_accepted_at = utcnow()
    row.terms_version = TERMS_VERSION
    await db.commit()
    return {"ok": True, "version": TERMS_VERSION}

@app.get("/api/me")
async def me(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    existing = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
    if not existing:
        existing = User(telegram_id=user["id"], username=user.get("username"), first_name=user.get("first_name"), last_name=user.get("last_name"))
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
    else:
        existing.username = user.get("username")
        existing.first_name = user.get("first_name")
        existing.last_name = user.get("last_name")
        existing.updated_at = utcnow()
        await db.commit()

    sub = (await db.execute(
        select(Subscription).where(Subscription.telegram_id == user["id"], Subscription.active == True)
        .order_by(Subscription.expires_at.desc())
    )).scalars().first()
    if sub:
        await sync_user_subscription(db, existing, sub) if False else None
    admin_info = await get_admin(int(user["id"]))
    admin = bool(admin_info)
    now = utcnow()
    trial_active = bool(
        existing.status == "trial"
        and existing.trial_expires
        and aware(existing.trial_expires) > now
    )
    subscription_active = bool(
        existing.status == "active"
        and existing.subscription_expires
        and aware(existing.subscription_expires) > now
    )
    pro = admin or existing.free_access or subscription_active or trial_active
    expires = None
    if not admin:
        if trial_active:
            expires = aware(existing.trial_expires).isoformat()
        elif existing.subscription_expires:
            expires = aware(existing.subscription_expires).isoformat()
    return {
        "user": user,
        "admin": admin,
        "admin_role": admin_info.get("role") if admin_info else None,
        "admin_permissions": admin_info.get("permissions", []) if admin_info else [],
        "pro": pro,
        "access": {
            "enabled": pro,
            "source": "admin" if admin else ("free" if existing.free_access else ("trial" if trial_active else ("subscription" if subscription_active else None))),
            "radar": pro,
            "market": pro,
            "analysis": pro,
            "news": pro,
            "events": pro,
            "history": pro,
            "terminal": pro,
        },
        "expires_at": expires,
        "trial_available": existing.trial_used_at is None and not admin,
        "trial_expires": existing.trial_expires.isoformat() if existing.trial_expires else None,
        "terms_accepted": bool(existing.terms_accepted_at and existing.terms_version == TERMS_VERSION),
        "terms_version": existing.terms_version,
    }

@app.get("/api/subscription/plans")
async def subscription_plans(user=Depends(telegram_user)):
    plans = await get_plans()
    return {
        "plans": plans,
        "terms_version": TERMS_VERSION,
        "terms_text": TERMS_TEXT,
    }

@app.post("/api/subscription/trial")
async def subscription_trial(user=Depends(telegram_user)):
    if int(user["id"]) == int(settings.owner_telegram_id):
        raise HTTPException(400, "حساب المالك لا يحتاج تجربة")
    try:
        result = await start_trial_for_user(user)
        try:
            await send_message(user["id"],
                "🎁 <b>بدأت تجربتك المجانية في SAS PRO</b>\n\n"
                "⏳ المدة: <b>3 أيام</b>\n"
                f"📅 تنتهي: <b>{result['trial_expires'].strftime('%d/%m/%Y %H:%M')}</b>\n\n"
                "🚀 رابط دخول قناة التجربة الخاص بك:",
                {"inline_keyboard": [[{"text": "🎁 دخول قناة التجربة", "url": result["invite_link"]}]]},
            )
        except Exception:
            pass
        return {"ok": True, **result, "trial_expires": result["trial_expires"].isoformat(), "invite_expires": result["invite_expires"].isoformat()}
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@app.post("/api/subscription/invoice/{plan}")
async def subscription_invoice(plan: str, user=Depends(telegram_user)):
    if int(user["id"]) == int(settings.owner_telegram_id):
        raise HTTPException(400, "حساب المالك لا يحتاج شراء")
    async with SessionLocal() as db:
        row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
        if not row or row.terms_version != TERMS_VERSION or not row.terms_accepted_at:
            raise HTTPException(409, "يجب الموافقة على الشروط قبل الدفع")
    try:
        return await create_invoice_for_user(user, plan)
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@app.get("/api/admin/overview")
async def admin_overview(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "users")
    now = utcnow()
    users = (await db.execute(select(User))).scalars().all()
    active = 0
    expired = 0
    trial = 0
    for u in users:
        if u.free_access or (u.subscription_expires and aware(u.subscription_expires) > now and u.status == "active"):
            active += 1
        elif u.subscription_expires:
            expired += 1
        if u.trial_used_at:
            trial += 1
    payments = (await db.execute(select(Payment))).scalars().all()
    return {
        "active": active,
        "expired": expired,
        "trial_users": trial,
        "new_users": len([u for u in users if u.created_at and (now - aware(u.created_at)).days < 30]),
        "payments": len(payments),
        "stars": sum(p.stars for p in payments),
    }

@app.get("/api/admin/users")
async def admin_users(q: str = "", user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "users")
    q = q.strip()
    stmt = select(User).order_by(User.created_at.desc()).limit(100)
    if q:
        from sqlalchemy import or_
        filters = [User.username.ilike(f"%{q.lstrip('@')}%"), User.first_name.ilike(f"%{q}%")]
        try:
            filters.append(User.telegram_id == int(q))
        except ValueError:
            pass
        stmt = select(User).where(or_(*filters)).order_by(User.created_at.desc()).limit(100)
    rows = (await db.execute(stmt)).scalars().all()
    return [{
        "telegram_id": u.telegram_id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name,
        "status": u.status, "trial_start": aware(u.trial_start).isoformat() if u.trial_start else None,
        "trial_expires": aware(u.trial_expires).isoformat() if u.trial_expires else None,
        "subscription_start": aware(u.subscription_start).isoformat() if u.subscription_start else None,
        "subscription_expires": aware(u.subscription_expires).isoformat() if u.subscription_expires else None,
        "plan": u.plan, "free_access": u.free_access, "terms_accepted_at": aware(u.terms_accepted_at).isoformat() if u.terms_accepted_at else None,
    } for u in rows]

@app.get("/api/admin/plans")
async def admin_plans(user=Depends(telegram_user)):
    await require_admin_permission(user, "settings")
    return await get_plans()

@app.post("/api/admin/plans")
async def admin_update_plans(request: Request, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "settings")
    body = await request.json()
    for key in ("monthly", "3month", "6month", "yearly"):
        item = body.get(key) or {}
        sar = int(item.get("sar", 0))
        days = int(item.get("days", 0))
        stars = int(item.get("stars", 0))
        if sar <= 0 or days <= 0 or stars < 0 or stars > 100000:
            raise HTTPException(400, f"بيانات الباقة {key} غير صحيحة")
        await __import__("app.subscriptions", fromlist=["setting_set"]).setting_set(db, f"{key}_sar", sar)
        await __import__("app.subscriptions", fromlist=["setting_set"]).setting_set(db, f"{key}_days", days)
        await __import__("app.subscriptions", fromlist=["setting_set"]).setting_set(db, f"{key}_stars", stars)
    await db.commit()
    return {"ok": True, "plans": await get_plans()}

@app.post("/api/admin/grant/{telegram_id}")
async def admin_grant(telegram_id: int, days: str = "30", user=Depends(telegram_user)):
    await require_admin_permission(user, "subscriptions")
    try:
        if days.lower() == "forever":
            exp, link, link_exp = await grant_access(telegram_id, forever=True)
        else:
            try:
                n = int(days)
            except ValueError:
                raise HTTPException(400, "استخدم مدة صحيحة أو forever")
            if n <= 0 or n > 3650:
                raise HTTPException(400, "المدة يجب أن تكون بين 1 و3650 يومًا")
            exp, link, link_exp = await grant_access(telegram_id, days=n)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"تعذر تفعيل الاشتراك: {exc}")
    await audit(int(user["id"]), "grant_access_legacy", telegram_id, {"days": days})
    try:
        await send_message(telegram_id,
            "✅ <b>تم تفعيل وصول SAS PRO</b>\n\n"
            f"📅 تاريخ الانتهاء: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
            "🚀 رابط دخول القناة:",
            {"inline_keyboard": [[{"text": "🚀 دخول قناة SAS PRO", "url": link}], [{"text": "📱 فتح SAS PRO", "web_app": {"url": settings.app_base_url}}]]},
        )
    except Exception:
        pass
    return {"ok": True, "expires_at": exp.isoformat(), "invite_expires": link_exp.isoformat()}

@app.post("/api/admin/revoke/{telegram_id}")
async def admin_revoke(telegram_id: int, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "subscriptions")
    await db.execute(update(Subscription).where(Subscription.telegram_id == telegram_id, Subscription.active == True).values(active=False))
    row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
    if row:
        row.status = "revoked"; row.free_access = False; row.subscription_expires = utcnow(); row.updated_at = utcnow()
    await db.commit()
    await set_channel_access(telegram_id, allow=False)
    return {"ok": True}



async def require_admin_permission(user, permission: str):
    if not await has_permission(int(user["id"]), permission):
        raise HTTPException(403, "لا تملك هذه الصلاحية")
    return user

@app.get("/api/admin/me")
async def admin_me(user=Depends(telegram_user)):
    admin = await get_admin(int(user["id"]))
    if not admin:
        raise HTTPException(403, "لا تملك صلاحيات الإدارة")
    return {"ok": True, **admin, "permissions_catalog": PERMISSIONS, "role_defaults": ROLE_DEFAULTS}

@app.get("/api/admin/staff")
async def admin_staff(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "admins")
    from .db import AdminRole
    rows = (await db.execute(select(AdminRole).order_by(AdminRole.created_at.desc()))).scalars().all()
    out=[]
    for row in rows:
        try: perms=json.loads(row.permissions or "[]")
        except Exception: perms=[]
        out.append({"telegram_id":row.telegram_id,"role":row.role,"permissions":perms,"enabled":row.enabled,"created_by":row.created_by,"created_at":aware(row.created_at).isoformat()})
    out.insert(0, {"telegram_id":int(settings.owner_telegram_id),"role":"owner","permissions":list(PERMISSIONS),"enabled":True,"created_by":int(settings.owner_telegram_id),"created_at":None})
    return out

@app.post("/api/admin/staff")
async def admin_staff_upsert(request: Request, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "admins")
    from .db import AdminRole
    body=await request.json()
    telegram_id=int(body.get("telegram_id") or 0)
    if not telegram_id or telegram_id == int(settings.owner_telegram_id):
        raise HTTPException(400, "لا يمكن تعديل المالك بهذه الطريقة")
    role=str(body.get("role") or "moderator")
    if role not in ROLE_DEFAULTS:
        raise HTTPException(400, "الدور غير معروف")
    perms=body.get("permissions")
    if perms is None: perms=ROLE_DEFAULTS[role]
    perms=[p for p in perms if p in PERMISSIONS]
    row=await db.get(AdminRole, telegram_id)
    if not row:
        row=AdminRole(telegram_id=telegram_id, role=role, permissions=json.dumps(perms,ensure_ascii=False), enabled=True, created_by=int(user["id"]))
        db.add(row)
        action="staff_added"
    else:
        row.role=role; row.permissions=json.dumps(perms,ensure_ascii=False); row.enabled=True; row.updated_at=utcnow()
        action="staff_updated"
    await db.commit()
    await audit(int(user["id"]), action, telegram_id, {"role":role,"permissions":perms})
    return {"ok":True,"telegram_id":telegram_id,"role":role,"permissions":perms,"enabled":True}

@app.post("/api/admin/staff/{telegram_id}/disable")
async def admin_staff_disable(telegram_id:int, user=Depends(telegram_user), db: AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"admins")
    from .db import AdminRole
    row=await db.get(AdminRole,telegram_id)
    if not row: raise HTTPException(404,"المشرف غير موجود")
    row.enabled=False; row.updated_at=utcnow(); await db.commit()
    await audit(int(user["id"]),"staff_disabled",telegram_id)
    return {"ok":True}

@app.post("/api/admin/staff/{telegram_id}/enable")
async def admin_staff_enable(telegram_id:int, user=Depends(telegram_user), db: AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"admins")
    from .db import AdminRole
    row=await db.get(AdminRole,telegram_id)
    if not row: raise HTTPException(404,"المشرف غير موجود")
    row.enabled=True; row.updated_at=utcnow(); await db.commit()
    await audit(int(user["id"]),"staff_enabled",telegram_id)
    return {"ok":True}

@app.delete("/api/admin/staff/{telegram_id}")
async def admin_staff_delete(telegram_id:int, user=Depends(telegram_user), db: AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"admins")
    from .db import AdminRole
    row=await db.get(AdminRole,telegram_id)
    if not row: raise HTTPException(404,"المشرف غير موجود")
    await db.delete(row); await db.commit()
    await audit(int(user["id"]),"staff_deleted",telegram_id)
    return {"ok":True}

@app.get("/api/admin/audit")
async def admin_audit(user=Depends(telegram_user), db: AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"audit")
    from .db import AuditLog
    rows=(await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200))).scalars().all()
    return [{"id":r.id,"actor_telegram_id":r.actor_telegram_id,"action":r.action,"target_telegram_id":r.target_telegram_id,"details":r.details,"created_at":aware(r.created_at).isoformat()} for r in rows]

@app.post("/api/admin/access/grant/{telegram_id}")
async def staff_grant(telegram_id:int, days:str="30", user=Depends(telegram_user)):
    await require_admin_permission(user,"subscriptions")
    if days.lower()=="forever":
        exp,link,link_exp=await grant_access(telegram_id,forever=True)
    else:
        n=int(days)
        if n<=0 or n>3650: raise HTTPException(400,"المدة يجب أن تكون بين 1 و3650 يومًا")
        exp,link,link_exp=await grant_access(telegram_id,days=n)
    await audit(int(user["id"]),"grant_access",telegram_id,{"days":days,"expires_at":exp.isoformat()})
    try:
        kb={"inline_keyboard":[
            [{"text":"🚀 دخول قناة SAS PRO","url":link}],
            *([[{"text":"📱 فتح SAS PRO","web_app":{"url":settings.app_base_url}}]] if settings.app_base_url else [])
        ]}
        await send_message(telegram_id,"✅ <b>تم تفعيل SAS PRO</b>\n\n📅 الانتهاء: <b>"+exp.strftime("%d/%m/%Y")+"</b>\n\n🔗 رابط القناة صالح 48 ساعة ويستخدم مرة واحدة.\n📱 يمكنك فتح التطبيق من الزر التالي.",kb)
    except Exception: pass
    return {"ok":True,"expires_at":exp.isoformat(),"invite_expires":link_exp.isoformat()}

@app.post("/api/admin/access/revoke/{telegram_id}")
async def staff_revoke(telegram_id:int, user=Depends(telegram_user), db:AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"subscriptions")
    await db.execute(update(Subscription).where(Subscription.telegram_id==telegram_id,Subscription.active==True).values(active=False))
    row=(await db.execute(select(User).where(User.telegram_id==telegram_id))).scalars().first()
    if row: row.status="revoked"; row.free_access=False; row.subscription_expires=utcnow(); row.updated_at=utcnow()
    await db.commit(); await set_channel_access(telegram_id,False); await audit(int(user["id"]),"revoke_access",telegram_id)
    return {"ok":True}

@app.post("/api/admin/access/free/{telegram_id}")
async def staff_free_access(telegram_id:int, user=Depends(telegram_user), db:AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"subscriptions")
    now=utcnow()
    row=(await db.execute(select(User).where(User.telegram_id==telegram_id))).scalars().first()
    if not row:
        row=User(telegram_id=telegram_id); db.add(row); await db.flush()
    row.free_access=True; row.status="active"; row.plan="admin_free"; row.subscription_expires=None; row.updated_at=now
    await db.execute(update(Subscription).where(Subscription.telegram_id==telegram_id,Subscription.active==True).values(active=False))
    await db.commit()
    await set_channel_access(telegram_id,True,None)
    await audit(int(user["id"]),"free_access_granted",telegram_id)
    return {"ok":True,"free_access":True}

@app.post("/api/admin/access/free/{telegram_id}/revoke")
async def staff_free_access_revoke(telegram_id:int, user=Depends(telegram_user), db:AsyncSession=Depends(get_session)):
    await require_admin_permission(user,"subscriptions")
    row=(await db.execute(select(User).where(User.telegram_id==telegram_id))).scalars().first()
    if row: row.free_access=False; row.status="revoked"; row.updated_at=utcnow()
    await db.commit(); await set_channel_access(telegram_id,False); await audit(int(user["id"]),"free_access_revoked",telegram_id)
    return {"ok":True,"free_access":False}


def _terminal_token(telegram_id: int, expires_at):
    secret = settings.sas_terminal_secret
    base_url = settings.openterminal_base_url.rstrip("/")
    if not secret or not base_url:
        raise HTTPException(503, "محطة SAS غير مهيأة في الخادم")
    exp = int(aware(expires_at).timestamp())
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": int(telegram_id), "exp": exp}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{base_url}?sas_token={body}.{signature}"


@app.get("/api/terminal/access")
async def terminal_access(user=Depends(require_pro), db: AsyncSession = Depends(get_session)):
    if int(user["id"]) == int(settings.owner_telegram_id):
        expires = utcnow() + timedelta(days=3650)
    else:
        row = (await db.execute(
            select(User).where(User.telegram_id == user["id"])
        )).scalars().first()
        if not row:
            raise HTTPException(403, "صلاحيات SAS PRO غير مفعلة")
        if row.status == "active" and row.subscription_expires:
            expires = aware(row.subscription_expires)
        elif row.status == "trial" and row.trial_expires:
            expires = aware(row.trial_expires)
        elif row.free_access:
            expires = utcnow() + timedelta(days=3650)
        else:
            raise HTTPException(403, "صلاحيات SAS PRO منتهية")
    return {
        "ok": True,
        "url": _terminal_token(int(user["id"]), expires),
        "expires_at": expires.isoformat(),
    }

@app.get("/api/market/status")
async def market_status_api(_: dict = Depends(require_pro)):
    return market_status()

@app.get("/api/radar/status-message")
async def radar_status_message(_: dict = Depends(require_pro)):
    status = market_status()
    active = stock_radar_enabled()
    if active:
        message = (
            "📡 SAS PRO RADAR\n\n"
            "🟢 الرصد يعمل الآن... 🕒\n\n"
            "🛰️ نتابع السوق لحظة بلحظة\n"
            "📊 نفحص الأسهم والنماذج والسلوك\n"
            "🎯 لا يتم إرسال أي سهم إلا بعد تحقق الشروط المطلوبة."
        )
    else:
        message = (
            "📡 SAS PRO RADAR\n\n"
            f"{status['label_ar']}\n\n"
            "⏸️ رصد الأسهم الأمريكي متوقف خارج الجلسة الرئيسية.\n"
            "📌 لن يتم إرسال فرص وهمية قبل الافتتاح أو بعد الإغلاق.\n"
            "🛰️ يعود الرصد تلقائيًا مع بداية الجلسة الرئيسية."
        )
    return {
        "active": active,
        "session": status["session"],
        "message": message + "\n\n⚠️ قرار التداول وإدارة المخاطر مسؤولية المتداول."
    }

@app.get("/api/market/radar-status")
async def radar_status(_: dict = Depends(require_pro)):
    status = market_status()
    return {
        **status,
        "stock_radar_enabled": stock_radar_enabled(),
        "closed_market_macro_radar": not status["open"],
    }

@app.get("/api/market/ticker")
async def market_ticker(_: dict = Depends(require_pro)):
    return await ticker()


@app.get("/api/dashboard/home")
async def dashboard_home(_: dict = Depends(require_pro)):
    status = market_status()
    # Radar signals use the same New York trading date as the market calendar.
    session_date = status["date"]
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(RadarSignal)
            .where(RadarSignal.session_date == session_date)
            .order_by(RadarSignal.created_at.desc())
        )).scalars().all()

    moves = []
    volumes = []
    for row in rows:
        try:
            payload = json.loads(row.payload or "{}")
            if payload.get("change_pct") is not None:
                moves.append(float(payload["change_pct"]))
            if payload.get("volume") is not None:
                volumes.append(float(payload["volume"]))
        except Exception:
            continue

    return {
        "market": status,
        "radar": {
            "enabled": stock_radar_enabled(),
            "opportunities": len(rows),
            "watched": len(rows),
            "top_move_pct": max(moves) if moves else None,
            "top_volume": max(volumes) if volumes else None,
            "last_signal_at": rows[0].created_at.isoformat() if rows and rows[0].created_at else None,
        },
        "updated_at": utcnow().isoformat(),
    }

@app.get("/api/radar/scan")
async def radar_scan(_: dict = Depends(require_pro)):
    from .scanner import scan_us_low_price_stocks
    status = market_status()
    if not stock_radar_enabled():
        return {
            "enabled": False,
            "reason": status["label_ar"],
            "session": status["session"],
            "stocks": [],
            "diagnostics": {"candidates": 0, "passed": 0, "filtered": 0, "errors": 0},
        }
    result = await scan_us_low_price_stocks()
    return {
        "enabled": True,
        "range": {"min": 0.50, "max": 30.00},
        "method": "Faisal",
        "session": status["session"],
        "stocks": result.get("stocks", []),
        "diagnostics": result.get("diagnostics", {}),
    }

@app.get("/api/stocks/{symbol}/news")
async def stock_news(symbol: str, _: dict = Depends(require_pro)):
    return await company_news(symbol.upper())

@app.get("/api/stocks/{symbol}/events")
async def stock_events(symbol: str, _: dict = Depends(require_pro)):
    return await corporate_events(symbol.upper())

@app.get("/api/stocks/{symbol}/quote")
async def stock_quote(symbol: str, _: dict = Depends(require_pro)):
    return await quote(symbol.upper())

@app.post("/api/stocks/{symbol}/analyze")
async def stock_analyze(symbol: str, user=Depends(require_pro), db: AsyncSession = Depends(get_session)):
    symbol = symbol.upper().strip()
    result = await analyze(symbol)
    targets = await technical_targets(symbol)
    q = await quote(symbol)
    from .scanner import classify_faisal
    classification = await classify_faisal(symbol, q)
    payload = {
        "analysis": result,
        "quote": q,
        "sas_pro": {
            "targets": targets,
            "classification": classification,
            "report": build_report(symbol, q, targets, classification),

            "disclaimer": DISCLAIMER,
        },
    }

    db.add(StockAnalysis(
        telegram_id=user["id"],
        symbol=symbol,
        payload=json.dumps(payload, ensure_ascii=False),
    ))
    await db.commit()
    return payload

@app.get("/api/history/{symbol}")
async def history(symbol: str, user=Depends(require_pro), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(StockAnalysis)
        .where(StockAnalysis.telegram_id == user["id"], StockAnalysis.symbol == symbol.upper())
        .order_by(StockAnalysis.created_at.desc())
        .limit(50)
    )).scalars().all()
    return [
        {"id": r.id, "symbol": r.symbol, "created_at": r.created_at.isoformat(), "analysis": json.loads(r.payload)}
        for r in rows
    ]

@app.get("/api/admin/stats")
async def admin_stats(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    users = (await db.execute(select(User))).scalars().all()
    subs = (await db.execute(select(Subscription).where(Subscription.active == True))).scalars().all()
    payments = (await db.execute(select(Payment))).scalars().all()
    return {
        "users": len(users),
        "active_subscriptions": sum(is_active(s) for s in subs),
        "payments": len(payments),
        "stars": sum(p.stars for p in payments),
    }

@app.get("/api/admin/subscriptions")
async def admin_subscriptions(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    rows = (await db.execute(select(Subscription).order_by(Subscription.expires_at.desc()))).scalars().all()
    return [{"id": s.id, "telegram_id": s.telegram_id, "plan": s.plan, "active": is_active(s), "starts_at": aware(s.starts_at).isoformat(), "expires_at": aware(s.expires_at).isoformat(), "warning_3d_sent_at": aware(s.warning_3d_sent_at).isoformat() if s.warning_3d_sent_at else None} for s in rows]

@app.get("/api/admin/radar")
async def admin_radar(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    rows = (await db.execute(select(RadarSignal).order_by(RadarSignal.created_at.desc()).limit(100))).scalars().all()
    return [{"symbol": r.symbol, "session_date": r.session_date, "created_at": r.created_at.isoformat(), "payload": json.loads(r.payload)} for r in rows]

@app.post("/api/admin/grant/{telegram_id}")
async def grant(telegram_id: int, days: int = 30, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    if days <= 0 or days > 3650:
        raise HTTPException(400, "مدة الاشتراك يجب أن تكون بين 1 و3650 يومًا")
    now = utcnow()

    active = (await db.execute(
        select(Subscription)
        .where(Subscription.telegram_id == telegram_id, Subscription.active == True)
        .order_by(Subscription.expires_at.desc())
    )).scalars().first()

    # التجديد يضيف المدة إلى المتبقي بدل حذف الاشتراك الحالي.
    if active and is_active(active):
        base = aware(active.expires_at)
        exp = base + timedelta(days=days)
        active.expires_at = exp
        active.warning_3d_sent_at = None
        active.plan = "admin"
    else:
        await db.execute(update(Subscription).where(
            Subscription.telegram_id == telegram_id, Subscription.active == True
        ).values(active=False))
        exp = now + timedelta(days=days)
        db.add(Subscription(
            telegram_id=telegram_id, plan="admin",
            starts_at=now, expires_at=exp, active=True
        ))

    req = (await db.execute(select(AccessRequest).where(
        AccessRequest.telegram_id == telegram_id, AccessRequest.status == "pending"
    ).order_by(AccessRequest.requested_at.desc()))).scalars().first()
    if req:
        req.status = "approved"
        req.decided_at = now
        req.decided_days = days

    await db.commit()
    await set_channel_access(telegram_id, allow=True, expires_at=exp)
    try:
        await send_message(telegram_id,
            "✅ <b>تم اعتماد دخولك إلى SAS PRO</b>\n\n"
            f"⏳ المدة المضافة: <b>{days} يوم</b>\n"
            f"📅 تاريخ الانتهاء: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
            "يمكنك الآن فتح Mini App واستخدام مزايا SAS PRO."
        )
    except Exception:
        pass
    return {"ok": True, "expires_at": exp.isoformat(), "days": days}

@app.post("/api/admin/access-requests/{request_id}/reject")
async def reject_access_request(request_id: int, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    req = await db.get(AccessRequest, request_id)
    if not req:
        raise HTTPException(404, "طلب الدخول غير موجود")
    if req.status != "pending":
        raise HTTPException(409, "الطلب تمت معالجته مسبقًا")
    now = utcnow()
    req.status = "rejected"
    req.decided_at = now
    req.admin_note = "تم رفض طلب الدخول من الإدارة"
    await db.commit()
    try:
        await send_message(req.telegram_id,
            "⛔ <b>تم رفض طلب دخول SAS PRO حاليًا</b>\n\n"
            "يمكنك تقديم طلب جديد لاحقًا."
        )
    except Exception:
        pass
    return {"ok": True, "status": "rejected"}

@app.post("/api/admin/revoke/{telegram_id}")
async def revoke(telegram_id: int, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    await db.execute(update(Subscription).where(
        Subscription.telegram_id == telegram_id, Subscription.active == True
    ).values(active=False))
    await db.commit()
    await set_channel_access(telegram_id, allow=False)
    try:
        await send_message(
            telegram_id,
            "⛔ <b>تم إيقاف إذن دخول SAS PRO</b>\n\n"
            "تم استبعادك من قناة SAS PRO حتى يتم اعتماد إذن جديد من الإدارة."
        )
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/trial/start")
async def start_trial():
    raise HTTPException(410, "التجربة التلقائية غير مستخدمة. اطلب إذن الدخول وتنتظر قرار الإدارة.")

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    expected = settings.telegram_webhook_secret
    if expected and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
        raise HTTPException(403, "Invalid Telegram webhook secret")
    data = await request.json()

    if "chat_member" in data:
        cm = data.get("chat_member") or {}
        chat = cm.get("chat") or {}
        member = cm.get("new_chat_member") or {}
        member_user = member.get("user") or {}
        uid = int(member_user.get("id") or 0)
        if uid and str(chat.get("id")) in {str(settings.telegram_channel_id), str(settings.trial_channel_id)}:
            async with SessionLocal() as db:
                invite = (await db.execute(select(Invite).where(
                    Invite.telegram_id == uid,
                    Invite.channel_id == str(chat.get("id")),
                    Invite.used == False
                ).order_by(Invite.created_at.desc()))).scalars().first()
                if invite:
                    invite.used = True
                    await db.commit()
        return {"ok": True}

    if "chat_join_request" in data:
        jr = data.get("chat_join_request") or {}
        chat = jr.get("chat") or {}
        sender = jr.get("from") or {}
        telegram_id = int(sender.get("id") or 0)
        if telegram_id and str(chat.get("id")) == str(settings.telegram_channel_id):
            now = utcnow()
            async with SessionLocal() as db:
                row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
                if not row:
                    row = User(
                        telegram_id=telegram_id,
                        username=sender.get("username"),
                        first_name=sender.get("first_name"),
                    )
                    db.add(row)
                row.channel_join_requested_at = now
                sub = (await db.execute(select(Subscription).where(
                    Subscription.telegram_id == telegram_id,
                    Subscription.active == True
                ).order_by(Subscription.expires_at.desc()))).scalars().first()
                active = is_active(sub)
                await db.commit()
            if active:
                try:
                    await bot_api("approveChatJoinRequest", {
                        "chat_id": settings.telegram_channel_id,
                        "user_id": telegram_id,
                    })
                except Exception:
                    pass
            # Keep requests pending until admin grants SAS PRO access.
            return {"ok": True}

    if "pre_checkout_query" in data:
        q = data["pre_checkout_query"] or {}
        payload = str(q.get("invoice_payload") or "")
        ok = False
        error_message = "الفاتورة غير صالحة"
        try:
            parts = payload.split(":", 3)
            plans = await get_plans()
            if len(parts) == 4 and parts[0] == "saspro":
                plan = plans.get(parts[1])
                user_id = int(parts[2])
                amount = int(q.get("total_amount") or 0)
                currency = q.get("currency")
                ok = bool(plan and currency == "XTR" and amount == int(plan["stars"]) and user_id == int((q.get("from") or {}).get("id") or 0) and int(plan["stars"]) > 0)
                if not ok:
                    error_message = "بيانات الفاتورة أو السعر غير صحيح"
        except Exception:
            ok = False
        payload_answer = {"pre_checkout_query_id": q.get("id"), "ok": ok}
        if not ok:
            payload_answer["error_message"] = error_message
        await bot_api("answerPreCheckoutQuery", payload_answer)
        return {"ok": True}

    message = data.get("message", {})
    if message.get("successful_payment"):
        return await successful_payment(request)

    sender = message.get("from") or {}
    chat_id = (message.get("chat") or {}).get("id") or sender.get("id")
    text = (message.get("text") or "").strip()
    telegram_id = int(sender.get("id") or 0)

    if not telegram_id:
        return {"ok": True}

    async with SessionLocal() as db:
        user_row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
        if not user_row:
            db.add(User(
                telegram_id=telegram_id,
                username=sender.get("username"),
                first_name=sender.get("first_name"),
            ))
        else:
            user_row.username = sender.get("username")
            user_row.first_name = sender.get("first_name")
        await db.commit()

    if text.lower() == "/terms":
        await send_message(chat_id, "<b>📋 شروط استخدام SAS PRO</b>\n\n" + TERMS_TEXT)
        return {"ok": True}

    if text.lower() == "/paysupport":
        await send_message(chat_id, "💳 <b>دعم المدفوعات SAS PRO</b>\n\nأرسل Telegram ID أو رقم عملية الدفع والمشكلة بالتفصيل، وسيتم مراجعة العملية.")
        return {"ok": True}

    # Admin commands are available only to the owner.
    if telegram_id == settings.owner_telegram_id:
        if text.upper() == "/STATUS":
            async with SessionLocal() as db:
                now = utcnow()
                users = (await db.execute(select(User))).scalars().all()
                payments_rows = (await db.execute(select(Payment))).scalars().all()
            active = sum(1 for u in users if u.free_access or (u.subscription_expires and aware(u.subscription_expires) > now and u.status == "active"))
            expired = sum(1 for u in users if u.subscription_expires and aware(u.subscription_expires) <= now and not u.free_access)
            trials = sum(1 for u in users if u.trial_used_at)
            new_users = sum(1 for u in users if u.created_at and (now - aware(u.created_at)).days < 30)
            await send_message(chat_id, "🛠️ <b>SAS PRO — STATUS</b>\n\n"
                f"🟢 النشطون: <b>{active}</b>\n🔴 المنتهية: <b>{expired}</b>\n"
                f"🎁 مستخدمو التجربة: <b>{trials}</b>\n👥 الجدد: <b>{new_users}</b>\n"
                f"💳 عمليات الدفع: <b>{len(payments_rows)}</b>\n⭐ إجمالي Stars: <b>{sum(p.stars for p in payments_rows)}</b>")
            return {"ok": True}

        if text.startswith("/grant "):
            parts = text.split()
            try:
                target = int(parts[1]); duration = parts[2]
                exp, link, link_exp = await grant_access(target, forever=(duration.lower()=="forever"), days=None if duration.lower()=="forever" else int(duration))
                await send_message(chat_id, f"✅ تم منح الوصول للمستخدم <code>{target}</code> حتى <b>{exp.strftime('%d/%m/%Y')}</b>")
                try:
                    await send_message(target, "🚀 <b>تم تفعيل SAS PRO</b>\n\nرابط الدخول:", {"inline_keyboard":[[{"text":"🚀 دخول SAS PRO","url":link}]]})
                except Exception:
                    pass
            except Exception:
                await send_message(chat_id, "❌ الصيغة: /grant TELEGRAM_ID DAYS أو /grant TELEGRAM_ID forever")
            return {"ok": True}

        if text.startswith("/revoke "):
            parts = text.split()
            try:
                target = int(parts[1])
                async with SessionLocal() as db:
                    await db.execute(update(Subscription).where(Subscription.telegram_id == target, Subscription.active == True).values(active=False))
                    row = (await db.execute(select(User).where(User.telegram_id == target))).scalars().first()
                    if row:
                        row.status = "revoked"; row.free_access = False; row.subscription_expires = utcnow(); row.updated_at = utcnow()
                    await db.commit()
                await set_channel_access(target, allow=False)
                await send_message(chat_id, f"⛔ تم إلغاء وصول <code>{target}</code>")
            except Exception:
                await send_message(chat_id, "❌ الصيغة: /revoke TELEGRAM_ID")
            return {"ok": True}

        if text == "/admin":
            async with SessionLocal() as db:
                users = len((await db.execute(select(User))).scalars().all())
                subs = (await db.execute(select(Subscription).where(Subscription.active == True))).scalars().all()
                payments_rows = (await db.execute(select(Payment))).scalars().all()
            msg = (
                "🛠️ <b>SAS PRO — لوحة الإدارة</b>\n\n"
                f"👥 المستخدمون: <b>{users}</b>\n"
                f"🟢 الاشتراكات الفعالة: <b>{sum(is_active(s) for s in subs)}</b>\n"
                f"💳 المدفوعات: <b>{len(payments_rows)}</b>\n"
                f"⭐ النجوم: <b>{sum(p.stars for p in payments_rows)}</b>\n\n"
                "الأوامر:\n"
                "/grant ID DAYS — تفعيل اشتراك\n"
                "/revoke ID — إيقاف اشتراك\n"
                "/requests — طلبات إذن الدخول\n"
                "/subs — عرض الاشتراكات"
            )
            await send_message(chat_id, msg)
            return {"ok": True}

        if text.startswith("/grant "):
            parts = text.split()
            try:
                target = int(parts[1])
                days = int(parts[2]) if len(parts) > 2 else 30
                now = utcnow()
                exp = now + timedelta(days=days)
                async with SessionLocal() as db:
                    await db.execute(update(Subscription).where(
                        Subscription.telegram_id == target,
                        Subscription.active == True
                    ).values(active=False))
                    db.add(Subscription(
                        telegram_id=target, plan="admin",
                        starts_at=now, expires_at=exp, active=True
                    ))
                    req = (await db.execute(select(AccessRequest).where(
                        AccessRequest.telegram_id == target, AccessRequest.status == "pending"
                    ).order_by(AccessRequest.requested_at.desc()))).scalars().first()
                    if req:
                        req.status = "approved"
                        req.decided_at = now
                        req.decided_days = days
                    await db.commit()
                channel_result = await set_channel_access(target, allow=True, expires_at=exp)
                await send_message(
                    chat_id,
                    f"✅ <b>تم تفعيل الاشتراك</b>\n\n"
                    f"👤 المستخدم: <code>{target}</code>\n"
                    f"⏳ المدة: <b>{days} يوم</b>\n"
                    f"📅 الانتهاء: <b>{exp.strftime('%d/%m/%Y')}</b>"
                )
                try:
                    await send_message(
                        target,
                        "✅ <b>تم قبول طلب إذن الدخول إلى SAS PRO</b>\n\n"
                        f"⏳ مدة الوصول: <b>{days} يوم</b>\n"
                        f"📅 ينتهي: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
                        "افتح Mini App الآن لاستخدام مزايا SAS PRO."
                    )
                except Exception:
                    pass
            except Exception:
                await send_message(chat_id, "❌ الصيغة الصحيحة: /grant ID DAYS")
            return {"ok": True}

        if text.startswith("/revoke "):
            parts = text.split()
            try:
                target = int(parts[1])
                async with SessionLocal() as db:
                    await db.execute(update(Subscription).where(
                        Subscription.telegram_id == target,
                        Subscription.active == True
                    ).values(active=False))
                    await db.commit()
                await set_channel_access(target, allow=False)
                await send_message(chat_id, f"⛔ <b>تم إيقاف اشتراك</b>\nالمستخدم: <code>{target}</code>")
            except Exception:
                await send_message(chat_id, "❌ الصيغة الصحيحة: /revoke ID")
            return {"ok": True}

        if text == "/requests":
            async with SessionLocal() as db:
                rows = (await db.execute(
                    select(AccessRequest).where(AccessRequest.status == "pending").order_by(AccessRequest.requested_at.asc())
                )).scalars().all()
            lines = ["🔐 <b>طلبات إذن الدخول المعلقة</b>", ""]
            for r in rows:
                name = r.first_name or (f"@{r.username}" if r.username else "بدون اسم")
                lines.append(f"• #{r.id} — {name} — <code>{r.telegram_id}</code> — {r.requested_at.strftime('%d/%m/%Y %H:%M')}")
            await send_message(chat_id, "\n".join(lines) if len(lines) > 2 else "لا توجد طلبات معلقة.")
            return {"ok": True}

        if text == "/subs":
            async with SessionLocal() as db:
                rows = (await db.execute(
                    select(Subscription).order_by(Subscription.expires_at.desc()).limit(50)
                )).scalars().all()
                users = {
                    u.telegram_id: u for u in
                    (await db.execute(select(User))).scalars().all()
                }
            lines = ["👥 <b>إدارة اشتراكات SAS PRO</b>", ""]
            for s in rows:
                u = users.get(s.telegram_id)
                name = (u.first_name if u else None) or (f"@{u.username}" if u and u.username else "بدون اسم")
                lines.append(
                    f"• {name} — <code>{s.telegram_id}</code> — "
                    f"{'🟢 فعال' if is_active(s) else '🔴 منتهي'} — {s.expires_at.strftime('%d/%m/%Y')}"
                )
            await send_message(chat_id, "\n".join(lines) if len(lines) > 2 else "لا توجد اشتراكات.")
            return {"ok": True}

    if text == "/access":
        name = sender.get("first_name") or sender.get("username") or "عزيزي المستخدم"
        kb = None
        if settings.app_base_url:
            kb = {"inline_keyboard": [[{"text": "🔐 فتح SAS PRO وطلب إذن الدخول", "web_app": {"url": settings.app_base_url}}]]}
        await send_message(
            chat_id,
            f"👋 <b>أهلًا {name}</b>\n\n"
            "🔐 <b>طلب إذن الدخول إلى SAS PRO</b>\n\n"
            "اقرأ الشروط ووافق عليها داخل التطبيق، وبعدها يصل طلبك للإدارة.\n"
            "الإدارة هي التي تحدد مدة الوصول حسب الطلب، ولا توجد باقات أو أسعار معروضة للمستخدم.",
            kb,
        )
        return {"ok": True}

    if text.startswith("/start"):
        name = sender.get("first_name") or sender.get("username") or "عزيزي المستخدم"
        if telegram_id == settings.owner_telegram_id:
            await send_message(
                chat_id,
                f"👋 <b>أهلًا {name}</b>\n\n"
                "🛠️ <b>لوحة إدارة SAS PRO</b>\n"
                "هذه الصفحة مخصصة لإدارة المشتركين فقط.",
                {"inline_keyboard": [[{"text": "🛠️ فتح لوحة الإدارة", "web_app": {"url": settings.app_base_url}}]]},
            )
            return {"ok": True}
        async with SessionLocal() as db:
            sub = (await db.execute(
                select(Subscription)
                .where(Subscription.telegram_id == telegram_id, Subscription.active == True)
                .order_by(Subscription.expires_at.desc())
            )).scalars().first()
        if is_active(sub):
            msg = (
                f"👋 <b>أهلًا {name}</b>\n\n"
                "🚀 <b>SAS PRO</b>\n"
                "اشتراكك فعال ويمكنك الدخول إلى الخدمة."
            )
        else:
            msg = (
                f"👋 <b>أهلًا {name}</b>\n\n"
                "🚀 <b>SAS PRO — سوق الأسهم الأمريكية</b>\n"
                "الاشتراك والتجربة والدخول تتم من داخل Mini App."
            )
        await send_message(
            chat_id,
            msg,
            {"inline_keyboard": [[{"text": "🚀 دخول SAS PRO", "web_app": {"url": settings.app_base_url}}]]},
        )
        return {"ok": True}

@app.post("/api/telegram/precheckout")
async def precheckout(request: Request):
    data = await request.json()
    q = data.get("pre_checkout_query", {})
    await bot_api("answerPreCheckoutQuery", {
        "pre_checkout_query_id": q.get("id"),
        "ok": True,
    })
    return {"ok": True}


@app.post("/api/telegram/success")
async def successful_payment(request: Request, db: AsyncSession = Depends(get_session)):
    data = await request.json()
    message = data.get("message") or {}
    try:
        result = await apply_successful_payment(message, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if result.get("duplicate") or result.get("ignored"):
        return result
    telegram_id = result["telegram_id"]
    exp = result["expires_at"]
    try:
        await send_message(
            telegram_id,
            "✅ <b>تم تفعيل اشتراك SAS PRO</b>\n\n"
            f"📦 الباقة: <b>{result['plan']['label']}</b>\n"
            f"💰 القيمة: <b>{result['plan']['sar']} ريال</b> / <b>{result['plan']['stars']} ⭐</b>\n"
            f"📅 الانتهاء: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
            "🚀 رابط الدخول الخاص بك صالح للاستخدام مرة واحدة لمدة 48 ساعة:",
            {"inline_keyboard": [[{"text": "🚀 دخول قناة SAS PRO", "url": result["invite_link"]}], [{"text": "📱 فتح SAS PRO", "web_app": {"url": settings.app_base_url}}]]},
        )
    except Exception:
        pass
    try:
        from .google_sheets import sync_payment
        await sync_payment([
            telegram_id, message.get("from", {}).get("username") or "",
            result["plan"]["label"], result["plan"]["sar"], result["plan"]["stars"],
            utcnow().isoformat(), result["starts_at"].strftime("%Y-%m-%d"),
            result["expires_at"].strftime("%Y-%m-%d"),
            "active", "paid", "لا", utcnow().isoformat()
        ])
    except Exception:
        pass
    return {"ok": True, "expires_at": exp.isoformat(), "invite_expires": result["invite_expires"].isoformat()}
