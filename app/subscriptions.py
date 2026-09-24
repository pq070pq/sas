import json
import secrets
from datetime import timedelta
from .config import settings
from .db import SessionLocal, User, Subscription, Payment, Invite, Setting
from .telegram import bot_api, send_message
from .timeutil import utcnow, aware

PLAN_DEFAULTS = {
    "monthly": {"label": "شهري", "sar": settings.monthly_sar, "days": 30, "stars": settings.pro_monthly_stars},
    "3month": {"label": "3 أشهر", "sar": settings.three_month_sar, "days": 90, "stars": settings.pro_3month_stars},
    "6month": {"label": "6 أشهر", "sar": settings.six_month_sar, "days": 180, "stars": settings.pro_6month_stars},
    "yearly": {"label": "سنة", "sar": settings.yearly_sar, "days": 365, "stars": settings.pro_yearly_stars},
}

TERMS_VERSION = "3.0"
TERMS_TEXT = """إقرار المستخدم وإخلاء المسؤولية

قبل استخدام SAS PRO أو الاشتراك في خدماتها، أقر بأنني قرأت وفهمت ما يلي:

SAS PRO هي منصة توفر أدوات لرصد وتحليل بيانات الأسواق المالية وإرسال التنبيهات والمعلومات المتعلقة بحركة الأسهم والأسواق.

المعلومات والتنبيهات والتحليلات التي تظهر في SAS PRO هي لأغراض المعلومات والرصد فقط، وليست وعدًا بتحقيق أرباح أو ضمانًا لارتفاع أو انخفاض أي سهم.

لا تعد أي من التنبيهات أو التحليلات أو التقارير توصية بشراء أو بيع أو الاحتفاظ بأي سهم.

قد تتغير أسعار الأسهم والبيانات وحجم التداول بسرعة، وقد تحدث أخطاء أو تأخيرات أو نقص في بعض البيانات. لذلك لا يمكن ضمان دقة أو اكتمال جميع المعلومات المعروضة في جميع الأوقات.

التداول والاستثمار في الأسواق المالية ينطويان على مخاطر، وقد ينتج عنهما خسارة جزء من رأس المال أو رأس المال بالكامل.

أنا المسؤول عن قراراتي الاستثمارية والتداولية، وعن التحقق من المعلومات قبل اتخاذ أي قرار، وعن تحديد حجم المخاطرة المناسب لي.

لا تتحمل SAS PRO مسؤولية الأرباح أو الخسائر الناتجة عن قرارات التداول التي أتخذها بناءً على المعلومات أو التنبيهات أو التحليلات المعروضة في المنصة.

⚠️ لا يعد استخدام SAS PRO بديلًا عن دراسة السهم وفهم مخاطره واتخاذ القرار الاستثماري بشكل مستقل.

وبالضغط على «أوافق وأتابع»، أؤكد أنني قرأت هذا الإقرار وفهمته وأوافق على استخدام SAS PRO وفقًا للشروط والأحكام.

☑️ أقر بقراءة إخلاء المسؤولية وفهمه والموافقة عليه."""


async def setting_get(db, key, default=None):
    row = await db.get(Setting, key)
    return row.value if row else default

async def setting_set(db, key, value):
    row = await db.get(Setting, key)
    if row:
        row.value = str(value)
        row.updated_at = utcnow()
    else:
        db.add(Setting(key=key, value=str(value), updated_at=utcnow()))

async def ensure_subscription_settings():
    async with SessionLocal() as db:
        defaults = {
            "monthly_sar": settings.monthly_sar, "monthly_days": 30, "monthly_stars": settings.pro_monthly_stars,
            "3month_sar": settings.three_month_sar, "3month_days": 90, "3month_stars": settings.pro_3month_stars,
            "6month_sar": settings.six_month_sar, "6month_days": 180, "6month_stars": settings.pro_6month_stars,
            "yearly_sar": settings.yearly_sar, "yearly_days": 365, "yearly_stars": settings.pro_yearly_stars,
            "trial_days": settings.trial_days, "invite_hours": settings.invite_hours,
        }
        for key, value in defaults.items():
            if await db.get(Setting, key) is None:
                await setting_set(db, key, value)
        await db.commit()

async def get_plans():
    async with SessionLocal() as db:
        result = {}
        for key, base in PLAN_DEFAULTS.items():
            prefix = key
            result[key] = {
                "label": base["label"],
                "sar": int(await setting_get(db, f"{prefix}_sar", base["sar"]) or 0),
                "days": int(await setting_get(db, f"{prefix}_days", base["days"]) or 0),
                "stars": int(await setting_get(db, f"{prefix}_stars", base["stars"]) or 0),
            }
        return result

def active_subscription(user: User):
    if not user:
        return False
    if user.free_access:
        return True
    return bool(user.subscription_expires and aware(user.subscription_expires) > utcnow() and user.status == "active")

async def sync_user_subscription(db, user, sub=None):
    if sub is None:
        from sqlalchemy import select
        sub = (await db.execute(
            select(Subscription).where(
                Subscription.telegram_id == user.telegram_id,
                Subscription.active == True
            ).order_by(Subscription.expires_at.desc())
        )).scalars().first()
    if sub:
        user.subscription_start = sub.starts_at
        user.subscription_expires = sub.expires_at
        user.plan = sub.plan
        user.status = "active" if aware(sub.expires_at) > utcnow() else "expired"
        user.warning_sent_at = sub.warning_3d_sent_at
    elif not user.free_access:
        user.status = "expired"
    user.updated_at = utcnow()

async def get_channel_join_link(channel_id: str):
    """Return one reusable join-request link; eligible users are auto-approved by the webhook."""
    if not channel_id:
        raise RuntimeError("لم يتم إعداد قناة SAS PRO")
    async with SessionLocal() as db:
        key = f"channel_join_request_link:{channel_id}"
        cached = await setting_get(db, key)
        if cached:
            return cached
    chat = await bot_api("getChat", {"chat_id": channel_id})
    username = chat.get("username") if isinstance(chat, dict) else None
    if username:
        # Public channels cannot force a join request; use the public channel URL.
        return f"https://t.me/{username}"
    link = await bot_api("createChatInviteLink", {
        "chat_id": channel_id,
        "name": "SAS PRO Join Requests",
        "creates_join_request": True,
    })
    invite = link.get("invite_link") if isinstance(link, dict) else None
    if not invite:
        raise RuntimeError("تعذر إنشاء رابط طلب الانضمام للقناة")
    async with SessionLocal() as db:
        await setting_set(db, f"channel_join_request_link:{channel_id}", invite)
        await db.commit()
    return invite

async def start_trial_for_user(user_data):
    telegram_id = int(user_data["id"])
    now = utcnow()
    async with SessionLocal() as db:
        user = (await db.execute(
            __import__("sqlalchemy").select(User).where(User.telegram_id == telegram_id)
        )).scalars().first()
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=user_data.get("username"),
                first_name=user_data.get("first_name"),
                last_name=user_data.get("last_name"),
            )
            db.add(user)
            await db.flush()
        if user.terms_version != TERMS_VERSION or not user.terms_accepted_at:
            raise ValueError("يجب الموافقة على الشروط أولًا")
        if user.trial_used_at:
            raise ValueError("التجربة المجانية استُخدمت سابقًا")
        if user.trial_expires and aware(user.trial_expires) > now:
            raise ValueError("التجربة المجانية فعالة حاليًا")
        days = int(await setting_get(db, "trial_days", settings.trial_days) or 3)
        trial_exp = now + timedelta(days=days)
        user.trial_start = now
        user.trial_expires = trial_exp
        user.trial_used_at = now
        user.status = "trial"
        user.updated_at = now
        await db.commit()
    channel_link = await get_channel_join_link(settings.telegram_channel_id)
    return {"trial_expires": trial_exp, "channel_link": channel_link}

async def create_invoice_for_user(user_data, plan_key):
    plans = await get_plans()
    if plan_key not in plans:
        raise ValueError("الباقة غير موجودة")
    plan = plans[plan_key]
    if plan["days"] <= 0 or plan["sar"] <= 0:
        raise ValueError("بيانات الباقة غير صحيحة")
    if plan["stars"] <= 0:
        raise ValueError("قيمة Telegram Stars لهذه الباقة غير مضبوطة من الإدارة")
    payload = f"saspro:{plan_key}:{int(user_data['id'])}:{secrets.token_urlsafe(12)}"
    invoice = await bot_api("createInvoiceLink", {
        "title": f"SAS PRO — {plan['label']}",
        "description": f"اشتراك SAS PRO لمدة {plan['days']} يوم — {plan['sar']} ريال",
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": f"{plan['label']} — {plan['sar']} ريال", "amount": int(plan["stars"])}],
    })
    return {"invoice_link": invoice, "payload": payload, "plan": plan}

async def apply_successful_payment(message, db):
    payment = message.get("successful_payment") or {}
    payload = str(payment.get("invoice_payload") or "")
    parts = payload.split(":", 3)
    if len(parts) != 4 or parts[0] != "saspro":
        return {"ok": True, "ignored": True}
    plan_key, tid_text = parts[1], parts[2]
    sender_id = int((message.get("from") or {}).get("id") or 0)
    telegram_id = int(tid_text)
    if sender_id != telegram_id:
        raise ValueError("بيانات الدفع لا تطابق صاحب الفاتورة")
    plans = await get_plans()
    plan = plans.get(plan_key)
    if not plan or plan["stars"] <= 0:
        raise ValueError("الباقة غير متاحة")
    if payment.get("currency") != "XTR":
        raise ValueError("عملة الدفع غير صحيحة")
    if int(payment.get("total_amount") or 0) != int(plan["stars"]):
        raise ValueError("قيمة الدفع لا تطابق سعر الباقة الحالي")
    charge = payment.get("telegram_payment_charge_id")
    if not charge:
        raise ValueError("Telegram payment charge ID مفقود")
    exists = (await db.execute(__import__("sqlalchemy").select(Payment).where(Payment.telegram_charge_id == charge))).scalars().first()
    if exists:
        return {"ok": True, "duplicate": True}

    now = utcnow()
    user = (await db.execute(__import__("sqlalchemy").select(User).where(User.telegram_id == telegram_id))).scalars().first()
    if not user:
        sender = message.get("from") or {}
        user = User(telegram_id=telegram_id, username=sender.get("username"), first_name=sender.get("first_name"), last_name=sender.get("last_name"))
        db.add(user)
        await db.flush()

    old = (await db.execute(__import__("sqlalchemy").select(Subscription).where(
        Subscription.telegram_id == telegram_id, Subscription.active == True
    ).order_by(Subscription.expires_at.desc()))).scalars().first()

    if old and aware(old.expires_at) > now:
        start = aware(old.expires_at)
        old.active = False
    else:
        start = now
        if old:
            old.active = False
    expires = start + timedelta(days=plan["days"])
    user.subscription_start = start
    user.subscription_expires = expires
    user.plan = plan_key
    user.status = "active"
    user.free_access = False
    user.warning_sent_at = None
    user.updated_at = now
    db.add(Payment(
        telegram_id=telegram_id,
        plan=plan_key,
        stars=int(plan["stars"]),
        sar_amount=int(plan["sar"]),
        payload=payload,
        telegram_charge_id=charge,
        paid_at=now,
    ))
    db.add(Subscription(
        telegram_id=telegram_id,
        plan=plan_key,
        starts_at=start,
        expires_at=expires,
        active=True,
        warning_3d_sent_at=None,
        telegram_charge_id=charge,
    ))
    await db.commit()

    try:
        await bot_api("unbanChatMember", {"chat_id": settings.telegram_channel_id, "user_id": telegram_id, "only_if_banned": True})
    except Exception:
        pass
    channel_link = await get_channel_join_link(settings.telegram_channel_id)
    return {
        "ok": True,
        "expires_at": expires,
        "starts_at": start,
        "channel_link": channel_link,
        "plan": plan,
        "telegram_id": telegram_id,
        "charge_id": charge,
    }

async def grant_access(telegram_id, days=None, forever=False):
    now = utcnow()
    async with SessionLocal() as db:
        from sqlalchemy import select
        user = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
        if not user:
            user = User(telegram_id=telegram_id)
            db.add(user)
            await db.flush()
        old = (await db.execute(select(Subscription).where(
            Subscription.telegram_id == telegram_id, Subscription.active == True
        ).order_by(Subscription.expires_at.desc()))).scalars().first()
        if forever:
            exp = now + timedelta(days=3650)
            plan = "forever"
        else:
            base = aware(old.expires_at) if old and aware(old.expires_at) > now else now
            exp = base + timedelta(days=int(days))
            plan = "admin"
        if old:
            old.active = False
        db.add(Subscription(telegram_id=telegram_id, plan=plan, starts_at=now if not old else aware(old.expires_at), expires_at=exp, active=True))
        user.subscription_start = now if not old else aware(old.expires_at)
        user.subscription_expires = exp
        user.plan = plan
        user.free_access = bool(forever)
        user.status = "active"
        user.warning_sent_at = None
        user.updated_at = now
        await db.commit()
    try:
        await bot_api("unbanChatMember", {"chat_id": settings.telegram_channel_id, "user_id": telegram_id, "only_if_banned": True})
    except Exception:
        pass
    channel_link = await get_channel_join_link(settings.telegram_channel_id)
    return exp, channel_link
