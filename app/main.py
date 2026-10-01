import asyncio
import logging
import json
import base64
import hashlib
import hmac
import html
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
from .subscriptions import TERMS_VERSION, TERMS_TEXT, get_plans, get_subscription_config, setting_set, setting_get, start_trial_for_user, create_invoice_for_user, apply_successful_payment, grant_access, active_subscription, ensure_subscription_settings, create_user_channel_invite
from .admin import PERMISSIONS, ROLE_DEFAULTS, get_admin, has_permission, audit

scheduler_task = None
holiday_radar_task = None

app = FastAPI(title="SAS PRO", version="2.1.0")
app.mount("/assets", StaticFiles(directory="web/assets"), name="assets")

DISCLAIMER = "لا يعد توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️"
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
            if settings.telegram_webhook_auto_configure and settings.app_base_url and settings.telegram_webhook_secret:
                await bot_api("setWebhook", {
                    "url": settings.app_base_url.rstrip("/") + "/api/telegram/webhook",
                    "secret_token": settings.telegram_webhook_secret,
                    "allowed_updates": ["message", "chat_join_request", "chat_member", "pre_checkout_query"],
                    "drop_pending_updates": False,
                })
        except Exception:
            pass
    global scheduler_task, holiday_radar_task
    scheduler_task = asyncio.create_task(scheduler(), name="saspro-scheduler")
    holiday_radar_task = asyncio.create_task(holiday_radar_scheduler(), name="saspro-holiday-radar")
    logging.getLogger(__name__).warning("Background tasks started: scheduler=%s holiday_radar=%s", scheduler_task.get_name(), holiday_radar_task.get_name())

def build_report(symbol: str, q: dict, tech: dict, classification: dict | None = None, outcome=None) -> str:
    """Stable Arabic Telegram radar report using observed/calculated values only."""
    classification = classification or {}
    intraday = tech.get("intraday") or {}

    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    price = num(q.get("price"))
    change = num(q.get("change_pct"))
    score = num(classification.get("score"))
    rvol = num(intraday.get("rvol") or intraday.get("intraday_rvol") or classification.get("rvol") or tech.get("volume_ratio"))
    vwap = num(intraday.get("vwap"))
    dollar_volume = num(classification.get("dollar_volume"))
    buy_pressure = num(intraday.get("buy_pressure"))
    acceleration = num(intraday.get("volume_acceleration"))
    float_shares = num(tech.get("float_shares"))
    shares_outstanding = num(tech.get("shares_outstanding"))
    support = num(tech.get("support"))
    resistance = num(tech.get("resistance"))
    breakout = num(tech.get("breakout"))
    stop = num(tech.get("exit"))
    targets = [num(x) for x in (tech.get("targets") or [])]
    targets = [x for x in targets if x is not None and x > 0]

    catalyst = tech.get("catalyst_news")
    achieved = int(getattr(outcome, "achieved_target", 0) or 0) if outcome is not None else 0
    current_stop = num(getattr(outcome, "current_stop", None)) if outcome is not None else None
    current_stop = current_stop or stop
    status = str(getattr(outcome, "status", "active") or "active") if outcome is not None else "active"

    def txt(value, suffix=""):
        return "غير متوفر" if value is None else f"{value}{suffix}"

    if status == "failed":
        status_text = "🛑 تم تفعيل الوقف"
    elif targets and achieved >= len(targets):
        status_text = "🏆 اكتملت أهداف الرصد"
    elif achieved:
        status_text = f"🎯 تحقق {achieved} هدف — الرصد مستمر"
    else:
        status_text = "⏳ تحت المتابعة"

    target_lines = []
    for idx, target in enumerate(targets, 1):
        if idx <= achieved:
            state = "✅ محقق"
        elif idx == achieved + 1:
            state = "🎯 الهدف التالي"
        else:
            state = "⏳ لاحق"
        target_lines.append(f"{state} — الهدف {idx}: <b>\x24{_money(target)}</b>")
    if not target_lines:
        target_lines = ["⚪ لا يوجد هدف فني مؤكد متاح"]

    report = [
        "🔎 <b>التحليل العميق للسهم | SAS PRO 📡</b>",
        "",
        f"📈 <b>\x24{symbol}</b> 🇺🇸",
        f"💵 السعر الحالي: <b>\x24{_money(price)}</b>" if price is not None else "💵 السعر الحالي: <b>غير متوفر</b>",
        f"📊 التغير: <b>{change:+.2f}%</b>" if change is not None else "📊 التغير: <b>غير متوفر</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "📌 <b>سبب اختيار السهم</b>",
        f"⭐ قوة الإشارة: <b>{score:.0f}/100</b>" if score is not None else "⭐ قوة الإشارة: <b>غير محسوب</b>",
        f"💧 السيولة بالدولار: <b>{dollar_volume:,.0f}\x24</b>" if dollar_volume is not None else "💧 السيولة بالدولار: <b>غير متوفر</b>",
        f"📊 RVOL: <b>{rvol:.2f}×</b>" if rvol is not None else "📊 RVOL: <b>غير متوفر</b>",
        f"📈 ضغط الشراء: <b>{buy_pressure:.1f}%</b>" if buy_pressure is not None else "📈 ضغط الشراء: <b>غير محسوب</b>",
        f"⚡ تسارع الحجم: <b>{acceleration:.2f}×</b>" if acceleration is not None else "⚡ تسارع الحجم: <b>غير محسوب</b>",
        f"🔥 التجميع: <b>{classification.get('accumulation_label') or classification.get('behavior') or 'غير واضح'}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "📰 <b>المحفز الإخباري</b>",
    ]
    if catalyst:
        report.extend([
            f"🔹 الخبر: <b>{catalyst.get('headline') or 'غير متوفر'}</b>",
            f"🕐 الوقت: <b>{catalyst.get('published_at') or 'غير متوفر'}</b>",
            f"📰 المصدر: <b>{catalyst.get('source') or 'غير متوفر'}</b>",
            f"🔗 الرابط: {catalyst.get('url') or 'غير متوفر'}",
            "📌 الارتباط بالحركة: لا يُعد سببًا مؤكدًا إلا إذا دعمه توقيت ومحتوى الخبر.",
        ])
    else:
        report.append("📰 المحفز الإخباري: <b>غير واضح — لا يوجد خبر موثوق يمكن ربط الحركة به حاليًا.</b>")

    ai = tech.get("ai_analysis") or {}
    news_items = tech.get("news_items") or []
    ai_source = None
    source_id = str(ai.get("primary_source_id") or "")
    if source_id.startswith("N"):
        try:
            idx = int(source_id[1:]) - 1
            if 0 <= idx < len(news_items):
                ai_source = news_items[idx]
        except (TypeError, ValueError):
            ai_source = None

    report.extend(["", "━━━━━━━━━━━━━━━━━━", "", "🤖 <b>تحليل الخبر بالذكاء الاصطناعي</b>"])
    if ai.get("enabled") and ai.get("status") == "ok":
        esc = lambda value: html.escape(str(value or "غير متوفر"))
        report.extend([
            f"🧠 <b>ملخص الخبر الموثق:</b> {esc(ai.get('headline_summary'))}",
            f"📌 <b>تفسير الحركة:</b> {esc(ai.get('why_rising'))}",
            f"🔎 <b>درجة الارتباط:</b> {esc(ai.get('news_assessment'))}",
        ])
        if ai_source:
            source_name = esc(ai_source.get("source") or "المصدر")
            source_url = html.escape(str(ai_source.get("url") or ""), quote=True)
            report.append(f"📰 <b>المصدر الذي بُني عليه التحليل:</b> {source_name}")
            if source_url:
                report.append(f'🔗 <a href="{source_url}">فتح الخبر الأصلي</a>')
        else:
            report.append("📰 <b>المصدر:</b> غير واضح — لم يثبت مصدر صالح للتحليل.")

        report.append(f"🏢 <b>التحليل المالي المبسط:</b> {esc(ai.get('financial_summary'))}")
        risks = ai.get("risk_flags") or []
        if risks:
            report.append("⚠️ <b>مخاطر مثبتة في البيانات:</b> " + " • ".join(esc(x) for x in risks[:4]))
        report.append(f"💡 <b>الخلاصة:</b> {esc(ai.get('key_takeaway'))}")
        report.append(f"⚙️ مزود التحليل: <b>{esc(ai.get('provider'))}</b>")
        report.append("🔒 الأسعار والأهداف والوقف في هذا التقرير لا تأتي من الذكاء الاصطناعي؛ تبقى من مصادر السوق والتحليل الفني الأصلية.")
    elif ai.get("status") == "no_api_key":
        report.append("🤖 تحليل الذكاء الاصطناعي: <b>غير مفعّل — لم يتم ضبط مفتاح مزود LLM.</b>")
    elif ai.get("status") == "provider_error":
        report.append("🤖 تحليل الذكاء الاصطناعي: <b>تعذر مؤقتًا؛ تم إرسال الرادار بالبيانات الأصلية.</b>")
    else:
        report.append("🤖 تحليل الذكاء الاصطناعي: <b>غير متوفر حاليًا.</b>")

    report.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "📊 <b>التحليل الفني</b>",
        f"↕️ الاتجاه: <b>{classification.get('behavior') or 'غير واضح'}</b>",