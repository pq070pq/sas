import asyncio
import json
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import SessionLocal, User, Subscription, Payment, StockAnalysis, get_session, init_db
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

DISCLAIMER = "🚨 لايعد توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️"
SHARIAH_DISCLAIMER = "⛔ شرعية الاسهم مسؤوليتك نبرا منها ⛔"
PLANS = {
    "monthly": (settings.pro_monthly_stars, 30),
    "3month": (settings.pro_3month_stars, 90),
    "yearly": (settings.pro_yearly_stars, 365),
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
        f"📈 الاتجاه: طالع إذا حافظ على مستوياته الحالية\n"
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
        f"🚨 إذا ضعف التداول أو انكسر حد الخروج، تتغير نظرة السهم.\n\n"
        f"{DISCLAIMER}\n\n"
        f"{SHARIAH_DISCLAIMER}"
    )

def _money(value):
    if value is None:
        return "غير واضح"
    return f"${value:,.4f}".rstrip("0").rstrip(".")

def is_active(sub):
    return bool(sub and sub.active and aware(sub.expires_at) > utcnow())

async def telegram_user(x_telegram_init_data: str = Header(default="")):
    try:
        return validate_init_data(x_telegram_init_data)
    except Exception as e:
        raise HTTPException(401, str(e))

async def require_pro(user=Depends(telegram_user)):
    async with SessionLocal() as db:
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
    }

@app.get("/api/market/status")
async def market_status_api(_: dict = Depends(telegram_user)):
    return market_status()

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

@app.post("/api/admin/grant/{telegram_id}")
async def grant(telegram_id: int, days: int = 30, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    now = utcnow()
    exp = now + timedelta(days=days)
    db.add(Subscription(telegram_id=telegram_id, plan="admin", starts_at=now, expires_at=exp, active=True))
    await db.commit()
    return {"ok": True, "expires_at": exp.isoformat()}

@app.post("/api/admin/revoke/{telegram_id}")
async def revoke(telegram_id: int, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    if user["id"] != settings.owner_telegram_id:
        raise HTTPException(403, "Admin only")
    await db.execute(update(Subscription).where(
        Subscription.telegram_id == telegram_id, Subscription.active == True
    ).values(active=False))
    await db.commit()
    return {"ok": True}

@app.post("/api/payments/invoice/{plan}")
async def invoice(plan: str, user=Depends(telegram_user)):
    if plan not in PLANS:
        raise HTTPException(400, "Invalid plan")
    stars, days = PLANS[plan]
    payload = f"saspro:{plan}:{user['id']}:{int(datetime.now().timestamp())}"
    result = await bot_api("createInvoiceLink", {
        "title": f"SAS PRO {plan}",
        "description": f"اشتراك SAS PRO لمدة {days} يوم",
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": f"SAS PRO {plan}", "amount": stars}],
    })
    return {"invoice_url": result, "stars": stars, "days": days}

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    expected = settings.telegram_webhook_secret
    if expected and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
        raise HTTPException(403, "Invalid Telegram webhook secret")
    data = await request.json()
    if "pre_checkout_query" in data:
        q = data["pre_checkout_query"]
        await bot_api("answerPreCheckoutQuery", {
            "pre_checkout_query_id": q.get("id"),
            "ok": True,
        })
        return {"ok": True}
    if data.get("message", {}).get("successful_payment"):
        return await successful_payment(request)
    return {"ok": True}

@app.post("/api/telegram/precheckout")
async def precheckout(request: Request):
    data = await request.json()
    q = data.get("pre_checkout_query", {})
    await bot_api("answerPreCheckoutQuery", {"pre_checkout_query_id": q.get("id"), "ok": True})
    return {"ok": True}

@app.post("/api/telegram/success")
async def successful_payment(request: Request, db: AsyncSession = Depends(get_session)):
    data = await request.json()
    message = data.get("message", {})
    payment = message.get("successful_payment", {})
    payload = payment.get("invoice_payload", "")
    if not payload.startswith("saspro:"):
        return {"ok": True}
    _, plan, tid, _ = payload.split(":", 3)
    telegram_id = int(tid)
    stars, days = PLANS.get(plan, (0, 0))
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
    db.add(Payment(telegram_id=telegram_id, plan=plan, stars=stars, telegram_charge_id=charge))
    db.add(Subscription(
        telegram_id=telegram_id, plan=plan, starts_at=start, expires_at=exp,
        active=True, warning_3d_sent_at=None, telegram_charge_id=charge
    ))
    await db.commit()
    return {"ok": True, "expires_at": exp.isoformat()}
