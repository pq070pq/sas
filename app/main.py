import asyncio
import json
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import SessionLocal, User, Subscription, Payment, StockAnalysis, RadarSignal, AccessRequest, get_session, init_db
from .telegram import validate_init_data, send_message, bot_api
from .market import quote, ticker
from .panwatch import analyze, technical_targets
from .news import company_news, corporate_events
from .jobs import scheduler
from .market_calendar import market_status
from .holiday_radar import stock_radar_enabled
from .holiday_radar import holiday_radar_scheduler
from .timeutil import utcnow, aware

app = FastAPI(title="SAS PRO", version="2.1.0")
app.mount("/assets", StaticFiles(directory="web/assets"), name="assets")

TERMS_VERSION = "1.3"
TERMS_TEXT = """⚠️ إقرار وشروط استخدام SAS PRO:

أقر أنا المتداول أن SAS PRO والقناة تقدمان معلومات وتحليلات عامة لأغراض تعليمية ومعلوماتية، وليستا توصية أو نصيحة استثمارية شخصية.

🎯 الأهداف والمستويات والإشارات التي تظهر في التطبيق هي نتائج تحليل فني وبيانات متاحة وقت التحليل، وليست وعدًا بالربح ولا ضمانًا لتحقيق أي نتيجة.

📊 أسعار الأسهم والأخبار والبيانات قد تتأخر أو تتغير أو تحتوي على أخطاء. لذلك أتحمل مسؤولية التحقق من المعلومات قبل اتخاذ أي قرار.

💰 SAS PRO والقناة لا تستلمان أموالي، ولا تديران محفظتي، ولا تنفذان صفقات نيابة عني، ولا تضمنان أرباحًا أو تمنعان الخسائر.

⚠️ أفهم أن التداول والاستثمار فيهما مخاطر، وقد أخسر جزءًا من رأس المال أو كامل المبلغ الذي أستخدمه.

🧑‍💼 أنا المتداول المسؤول عن قراراتي: الشراء والبيع، اختيار السهم، حجم الصفقة، رأس المال، وإدارة المخاطر. وأتحمل نتائج قرارات التداول والخسائر التي قد تنتج عنها.

🚫 لا أعتبر أي محتوى أو تنبيه أو تحليل في SAS PRO أو القناة تفويضًا لإدارة أموالي أو محفظتي أو تنفيذ صفقات نيابة عني، ولا أعتبره ضمانًا للربح.

🔐 طلب إذن الدخول:
أطلب إذن الدخول إلى SAS PRO بعد قراءة هذه الشروط والموافقة عليها. لا يتم تفعيل أي مدة تلقائيًا؛ الإدارة هي التي تقرر مدة الوصول وتاريخ انتهائه بحسب الطلب.

تنبيه تنظيمي: هذه الشروط توضح طبيعة الخدمة ومسؤوليات المتداول، ولا تعني أن SAS PRO أو القناة مرخصتان من هيئة السوق المالية. ويجب الرجوع إلى الأنظمة واللوائح المحدثة ومتطلبات الترخيص المعمول بها في المملكة العربية السعودية.
"""
DISCLAIMER = "⚠️ تنبيه: المعلومات والتحليلات الواردة هنا لأغراض معلوماتية وتعليمية عامة، ولا تُعد توصية أو مشورة استثمارية، ولا تراعي أهداف المتداول أو وضعه المالي أو احتياجاته الاستثمارية. 🎯 الأهداف والمستويات المذكورة هي نتائج تحليلية وليست ضمانًا لتحقيق أي نتيجة أو ربح. قرار الاستثمار والتداول وإدارة المخاطر مسؤولية المتداول." 
PLANS = {
    "monthly": (settings.pro_monthly_stars, 30),
    "3month": (settings.pro_3month_stars, 90),
    "6month": (settings.pro_6month_stars, 180),
    "yearly": (settings.pro_yearly_stars, 365),
}
PLAN_LABELS = {
    "monthly": "شهري — 150 ريال",
    "3month": "3 أشهر — خصم 10%",
    "6month": "6 أشهر — خصم 20%",
    "yearly": "سنة — خصم 30%",
}

@app.on_event("startup")
async def startup():
    await init_db()
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
    async with SessionLocal() as db:
        row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
        if not row or row.terms_version != TERMS_VERSION or not row.terms_accepted_at:
            raise HTTPException(409, "يجب الموافقة على شروط استخدام SAS PRO أولاً")
        sub = (await db.execute(
            select(Subscription)
            .where(Subscription.telegram_id == user["id"], Subscription.active == True)
            .order_by(Subscription.expires_at.desc())
        )).scalars().first()
        if not is_active(sub):
            raise HTTPException(403, "اشتراك SAS PRO منتهي أو غير موجود")
    return user

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
    return {"ok": True, "status": "pending", "request_id": req.id, "message": "تم إرسال طلبك للإدارة. بعد القرار ستصلك مدة الوصول من SAS PRO."}

@app.get("/api/terms")
async def terms():
    return {"version": TERMS_VERSION, "text": TERMS_TEXT}

@app.get("/api/plans")
async def plans(_: dict = Depends(telegram_user)):
    return {
        key: {"sar": label, "stars": stars, "days": days}
        for key, (stars, days), label in [
            ("monthly", PLANS["monthly"], "150 ريال"),
            ("3month", PLANS["3month"], "405 ريال"),
            ("6month", PLANS["6month"], "720 ريال"),
            ("yearly", PLANS["yearly"], "1260 ريال"),
        ]
    }

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
        db.add(User(telegram_id=user["id"], username=user.get("username"), first_name=user.get("first_name")))
        await db.commit()
    sub = (await db.execute(
        select(Subscription)
        .where(Subscription.telegram_id == user["id"], Subscription.active == True)
        .order_by(Subscription.expires_at.desc())
    )).scalars().first()
    return {
        "user": user,
        "pro": is_active(sub),
        "expires_at": sub.expires_at.isoformat() if sub else None,
        "trial_available": bool(existing and existing.terms_accepted_at and existing.terms_version == TERMS_VERSION and existing.trial_used_at is None),
        "terms_accepted": bool(existing and existing.terms_accepted_at and existing.terms_version == TERMS_VERSION),
        "terms_version": existing.terms_version if existing else None,
    }

@app.get("/api/market/status")
async def market_status_api(_: dict = Depends(telegram_user)):
    return market_status()

@app.get("/api/radar/status-message")
async def radar_status_message(_: dict = Depends(telegram_user)):
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
async def radar_status(_: dict = Depends(telegram_user)):
    status = market_status()
    return {
        **status,
        "stock_radar_enabled": stock_radar_enabled(),
        "closed_market_macro_radar": not status["open"],
    }

@app.get("/api/market/ticker")
async def market_ticker(_: dict = Depends(telegram_user)):
    return await ticker()


@app.get("/api/dashboard/home")
async def dashboard_home(_: dict = Depends(telegram_user)):
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
async def radar_scan(_: dict = Depends(telegram_user)):
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
async def stock_news(symbol: str, _: dict = Depends(telegram_user)):
    return await company_news(symbol.upper())

@app.get("/api/stocks/{symbol}/events")
async def stock_events(symbol: str, _: dict = Depends(telegram_user)):
    return await corporate_events(symbol.upper())

@app.get("/api/stocks/{symbol}/quote")
async def stock_quote(symbol: str, _: dict = Depends(telegram_user)):
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
    return [{"telegram_id": s.telegram_id, "plan": s.plan, "active": is_active(s), "expires_at": s.expires_at.isoformat(), "warning_3d_sent_at": s.warning_3d_sent_at.isoformat() if s.warning_3d_sent_at else None} for s in rows]

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
    if days <= 0:
        raise HTTPException(400, "days must be positive")
    now = utcnow()
    exp = now + timedelta(days=days)
    await db.execute(update(Subscription).where(
        Subscription.telegram_id == telegram_id, Subscription.active == True
    ).values(active=False))
    db.add(Subscription(telegram_id=telegram_id, plan="admin", starts_at=now, expires_at=exp, active=True))
    req = (await db.execute(select(AccessRequest).where(
        AccessRequest.telegram_id == telegram_id, AccessRequest.status == "pending"
    ).order_by(AccessRequest.requested_at.desc()))).scalars().first()
    if req:
        req.status = "approved"
        req.decided_at = now
        req.decided_days = days
    await db.commit()
    channel_result = await set_channel_access(telegram_id, allow=True, expires_at=exp)
    try:
        await send_message(telegram_id,
            "✅ <b>تم قبول طلب إذن الدخول إلى SAS PRO</b>\n\n"
            f"⏳ مدة الوصول: <b>{days} يوم</b>\n"
            f"📅 ينتهي: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
            "يمكنك الآن فتح Mini App واستخدام مزايا SAS PRO."
        )
    except Exception:
        pass
    return {"ok": True, "expires_at": exp.isoformat(), "days": days}

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

@app.post("/api/payments/invoice/{plan}")
async def invoice(plan: str):
    raise HTTPException(410, "نظام الاشتراك بالأسعار مخفي. يتم تفعيل SAS PRO يدويًا بعد طلب إذن الدخول وقرار الإدارة.")

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    expected = settings.telegram_webhook_secret
    if expected and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
        raise HTTPException(403, "Invalid Telegram webhook secret")
    data = await request.json()

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
            try:
                await bot_api(
                    "approveChatJoinRequest" if active else "declineChatJoinRequest",
                    {"chat_id": settings.telegram_channel_id, "user_id": telegram_id},
                )
            except Exception:
                pass
            return {"ok": True}

    if "pre_checkout_query" in data:
        q = data["pre_checkout_query"]
        await bot_api("answerPreCheckoutQuery", {
            "pre_checkout_query_id": q.get("id"),
            "ok": True,
        })
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

    # Admin commands are available only to the owner.
    if telegram_id == settings.owner_telegram_id:
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
        async with SessionLocal() as db:
            sub = (await db.execute(
                select(Subscription)
                .where(Subscription.telegram_id == telegram_id, Subscription.active == True)
                .order_by(Subscription.expires_at.desc())
            )).scalars().first()

        name = sender.get("first_name") or sender.get("username") or "عزيزي المستخدم"
        if is_active(sub):
            msg = (
                f"👋 <b>أهلًا {name}</b>\n\n"
                "🚀 <b>مرحبًا بك في SAS PRO</b>\n\n"
                "🟢 <b>اشتراكك فعال</b>\n"
                f"📅 تاريخ الانتهاء: <b>{sub.expires_at.strftime('%d/%m/%Y')}</b>\n\n"
                "يمكنك الآن الدخول إلى Mini App واستخدام مزايا SAS PRO."
            )
        else:
            msg = (
                f"👋 <b>أهلًا {name}</b>\n\n"
                "🚀 <b>مرحبًا بك في SAS PRO</b>\n\n"
                "🔒 <b>لا يوجد إذن دخول فعال حاليًا</b>\n\n"
                "🔐 أرسل <code>/access</code> لفتح SAS PRO وقراءة الشروط وإرسال طلب الدخول للإدارة.\n\n"
                "⚡ <b>SAS PRO</b>\n"
                "الدقة أولًا • بدون مطاردة • بدون إشارات وهمية"
            )
        await send_message(chat_id, msg)
        return {"ok": True}

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
    message = data.get("message", {})
    payment = message.get("successful_payment", {})
    payload = payment.get("invoice_payload", "")

    if not payload.startswith("saspro:"):
        return {"ok": True}

    parts = payload.split(":", 3)
    if len(parts) != 4:
        return {"ok": True}

    _, plan, tid, _ = parts
    telegram_id = int(tid)
    stars, days = PLANS.get(plan, (0, 0))
    if not stars or not days:
        return {"ok": True}

    charge = payment.get("telegram_payment_charge_id")
    if not charge:
        raise HTTPException(400, "Missing charge id")

    exists = (await db.execute(
        select(Payment).where(Payment.telegram_charge_id == charge)
    )).scalars().first()
    if exists:
        return {"ok": True, "duplicate": True}

    now = datetime.now(timezone.utc)
    old = (await db.execute(
        select(Subscription)
        .where(Subscription.telegram_id == telegram_id, Subscription.active == True)
        .order_by(Subscription.expires_at.desc())
    )).scalars().first()

    start = max(now, aware(old.expires_at)) if old else now
    exp = start + timedelta(days=days)

    if old:
        old.active = False

    db.add(Payment(
        telegram_id=telegram_id,
        plan=plan,
        stars=stars,
        telegram_charge_id=charge,
    ))
    db.add(Subscription(
        telegram_id=telegram_id,
        plan=plan,
        starts_at=start,
        expires_at=exp,
        active=True,
        warning_3d_sent_at=None,
        telegram_charge_id=charge,
    ))
    await db.commit()

    try:
        await send_message(
            telegram_id,
            "✅ <b>تم تفعيل اشتراك SAS PRO</b>\n\n"
            f"📦 الباقة: <b>{plan}</b>\n"
            f"📅 تاريخ الانتهاء: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
            "🚀 أهلًا بك في SAS PRO."
        )
    except Exception:
        pass

    return {"ok": True, "expires_at": exp.isoformat()}
