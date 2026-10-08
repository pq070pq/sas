import asyncio
import copy
import logging
import re
import json
import base64
import hashlib
import hmac
import html
import httpx
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import SessionLocal, User, Subscription, Payment, StockAnalysis, RadarSignal, RadarRun, AccessRequest, Setting, Invite, AdminRole, get_session, init_db
from .telegram import validate_init_data, send_message, bot_api
from .market import quote, ticker
from .panwatch import analyze, technical_targets, ohlcv
from .news import company_news, corporate_events, tipranks_analysis
from .scheduler import scheduler
from .market_calendar import market_status, us_market_holidays
from .holiday_radar import stock_radar_enabled
from .holiday_radar import holiday_radar_scheduler
from .radar_health import radar_health_monitor
from .timeutil import utcnow, aware
from .subscriptions import TERMS_VERSION, TERMS_TEXT, get_plans, get_subscription_config, setting_set, setting_get, start_trial_for_user, create_invoice_for_user, apply_successful_payment, grant_access, active_subscription, ensure_subscription_settings, create_user_channel_invite
from .admin import PERMISSIONS, ROLE_DEFAULTS, get_admin, has_permission, audit
from .fcc_reviewer import review_stock
from .shariah import check_shariah
from .smart_memory import SmartMemory
from .binance_spot import is_crypto_symbol, normalize_symbol as normalize_crypto_symbol, quote as binance_quote, analyze as binance_analyze, candles as binance_candles

logger = logging.getLogger(__name__)

scheduler_task = None
holiday_radar_task = None
telegram_polling_task = None
telegram_config_task = None
private_analysis_task = None
radar_health_task = None
radar_manual_lock = asyncio.Lock()

# ذاكرة SAS PRO الذكية: TTL + حد أقصى + إزالة تلقائية للقديم.
_ANALYSIS_CACHE_TTL = 900
_QUICK_SCAN_CACHE_TTL = 300
_analysis_memory = SmartMemory(_ANALYSIS_CACHE_TTL, max_items=64)
_quick_scan_memory = SmartMemory(_QUICK_SCAN_CACHE_TTL, max_items=128)
_analysis_locks = {}

def _cache_get(cache, symbol, ttl):
    return cache.get(symbol)

def _cache_put(cache, symbol, payload):
    cache.put(symbol, payload)

def _analysis_lock(symbol):
    key = symbol.upper()
    lock = _analysis_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _analysis_locks[key] = lock
    return lock

def _cleanup_analysis_locks():
    if len(_analysis_locks) > 128:
        for key in list(_analysis_locks)[:-64]:
            lock = _analysis_locks.get(key)
            if lock is not None and not lock.locked():
                _analysis_locks.pop(key, None)


app = FastAPI(title="SAS PRO", version="2.1.0")

@app.exception_handler(Exception)
async def saspro_exception_handler(request: Request, exc: Exception):
    # Telegram must receive HTTP 200 even when an unexpected internal error
    # occurs; otherwise it keeps retrying the same update and the user sees
    # no response. Keep the full traceback in the container logs for diagnosis.
    logger.exception("Unhandled SAS PRO request error: %s %s", request.method, request.url.path, exc_info=exc)
    if request.url.path == "/api/telegram/webhook":
        return JSONResponse({"ok": True, "handled_error": True}, status_code=200)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

@app.middleware("http")
async def telegram_webhook_logging(request: Request, call_next):
    if request.url.path == "/api/telegram/webhook":
        logger.info("Telegram webhook request received: method=%s", request.method)
        try:
            response = await call_next(request)
            logger.info("Telegram webhook response: status=%s", response.status_code)
        except Exception as exc:
            logger.exception("Telegram webhook raised exception: %s", exc)
            response = JSONResponse({"ok": True, "handled_error": True}, status_code=200)
    else:
        response = await call_next(request)

    # Prevent Telegram WebView/browser/CDN from serving stale frontend assets.
    # Versioned asset URLs are still used, but this makes cache invalidation
    # automatic even when a file path keeps the same URL.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

app.mount("/assets", StaticFiles(directory="web/assets"), name="assets")

def webapp_url() -> str:
    base = (settings.app_base_url or "").strip()
    if not base:
        return ""
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}v=20261008-06"

DISCLAIMER = "لا يعد توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️"
PLANS = {}
PLAN_LABELS = {}

async def _configure_telegram():
    """Configure Telegram without blocking FastAPI readiness."""
    if not settings.telegram_bot_token:
        return
    try:
        await asyncio.wait_for(
            bot_api("setMyCommands", {"commands": [
                {"command":"start","description":"فتح SAS PRO"},
                {"command":"terms","description":"شروط الاستخدام"},
                {"command":"paysupport","description":"دعم المدفوعات"},
            ]}),
            timeout=8,
        )
        if settings.owner_telegram_id:
            await asyncio.wait_for(
                bot_api("setMyCommands", {
                    "scope": {"type":"chat","chat_id":settings.owner_telegram_id},
                    "commands": [
                        {"command":"start","description":"فتح SAS PRO"},
                        {"command":"status","description":"حالة الاشتراكات"},
                        {"command":"grant","description":"منح اشتراك"},
                        {"command":"revoke","description":"إلغاء اشتراك"},
                    ],
                }),
                timeout=8,
            )
        if settings.telegram_webhook_auto_configure:
            try:
                webhook_info = await asyncio.wait_for(bot_api("getWebhookInfo", {}), timeout=8)
                logger.warning(
                    "Telegram receiver before polling: webhook_url=%r pending=%s last_error=%r",
                    webhook_info.get("url"),
                    webhook_info.get("pending_update_count"),
                    webhook_info.get("last_error_message"),
                )
            except Exception as exc:
                logger.warning("Telegram getWebhookInfo deferred/failed: %s", exc)
            try:
                await asyncio.wait_for(bot_api("deleteWebhook", {"drop_pending_updates": False}), timeout=8)
                webhook_info = await asyncio.wait_for(bot_api("getWebhookInfo", {}), timeout=8)
                logger.warning(
                    "Telegram webhook disabled: webhook_url=%r pending=%s",
                    webhook_info.get("url"),
                    webhook_info.get("pending_update_count"),
                )
            except Exception as exc:
                logger.warning("Telegram deleteWebhook deferred/failed: %s", exc)
    except Exception as exc:
        logger.exception("Telegram background configuration failed: %s", exc)


@app.on_event("startup")
async def startup():
    await init_db()
    await ensure_subscription_settings()
    global scheduler_task, holiday_radar_task, telegram_polling_task, telegram_config_task, private_analysis_task, radar_health_task
    telegram_config_task = asyncio.create_task(_configure_telegram(), name="saspro-telegram-config")
    scheduler_task = asyncio.create_task(scheduler(), name="saspro-scheduler")
    holiday_radar_task = asyncio.create_task(holiday_radar_scheduler(), name="saspro-holiday-radar")
    radar_health_task = asyncio.create_task(radar_health_monitor(), name="saspro-radar-health")
    telegram_polling_task = asyncio.create_task(telegram_polling_loop(), name="saspro-telegram-polling")
    def _telegram_polling_done(task):
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            logging.getLogger(__name__).warning("Telegram polling task cancelled.")
            return
        if exc:
            logging.getLogger(__name__).exception(
                "Telegram polling task crashed: %s", exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        else:
            logging.getLogger(__name__).warning("Telegram polling task stopped unexpectedly.")
    telegram_polling_task.add_done_callback(_telegram_polling_done)
    logging.getLogger(__name__).warning(
        "Background tasks started: scheduler=%s holiday_radar=%s telegram=%s",
        scheduler_task.get_name(), holiday_radar_task.get_name(), telegram_polling_task.get_name()
    )

async def telegram_polling_loop():
    """Receive Telegram updates without relying on the public webhook proxy."""
    if not settings.telegram_bot_token:
        logger.error("Telegram polling disabled: TELEGRAM_BOT_TOKEN is not configured.")
        return
    offset = None
    timeout = 5
    logging.getLogger(__name__).warning("Telegram polling receiver started; short-poll diagnostics enabled.")
    try:
        me = await bot_api("getMe", {})
        logging.getLogger(__name__).warning(
            "Telegram polling bot authenticated: id=%s username=%s",
            me.get("id"), me.get("username"),
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("Telegram polling bot authentication failed: %s", exc)
        await asyncio.sleep(5)
    while True:
        try:
            payload = {
                "timeout": timeout,
                "allowed_updates": ["message", "chat_join_request", "chat_member", "pre_checkout_query"],
            }
            if offset is not None:
                payload["offset"] = offset
            logger.info(
                "Telegram polling requesting updates: offset=%s timeout=%s",
                offset, timeout
            )
            result = await bot_api("getUpdates", payload)
            updates = result or []
            logger.warning("Telegram polling getUpdates returned %d update(s).", len(updates))
            for update_item in updates:
                update_id = int(update_item.get("update_id") or 0)
                try:
                    logger.warning(
                        "Telegram polling processing update=%s keys=%s",
                        update_id, list(update_item.keys())
                    )
                    result = await process_telegram_update(update_item)
                    logger.warning(
                        "Telegram polling processed update=%s result=%r",
                        update_id, result
                    )
                    if update_id:
                        offset = update_id + 1
                except Exception as exc:
                    logger.exception(
                        "Telegram update processing failed: update_id=%s error=%s",
                        update_id, exc
                    )
                    # Do not advance offset; Telegram will retry this update.
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Telegram polling failed: %s", exc)
            await asyncio.sleep(3)

def build_report(symbol: str, q: dict, tech: dict, classification: dict | None = None, outcome=None) -> str:
    """Build the standard SAS PRO beginner-friendly stock report."""
    q = q or {}
    tech = tech or {}
    classification = classification or {}
    intraday = tech.get("intraday") or {}
    radar_checks = tech.get("radar_checks") or {}
    ai = tech.get("ai_analysis") or {}
    shariah = tech.get("shariah") or {}
    news_items = tech.get("news_items") or []
    tipranks = tech.get("tipranks_analysis") or {}

    def _esc(value):
        if value is None:
            return ""
        # بعض مزودي التحليل يعيدون فواصل الأسطر كنص حرفي "\\n".
        # نحولها إلى أسطر فعلية قبل إرسال التقرير إلى Telegram.
        text_value = str(value).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\r")
        return html.escape(text_value)

    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def first_value(*values):
        for value in values:
            if value is not None and value != "":
                return value
        return None

    def add_section(report, title, lines):
        clean = [line for line in lines if line]
        if not clean:
            return
        if report and report[-1] != "":
            report.append("")
        report.extend(["━━━━━━━━━━━━━━━━━━", "", title, "", *clean])

    def reason_lines(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            raw = [str(x).strip() for x in value if str(x).strip()]
        else:
            text_value = str(value).strip()
            raw = [x.strip() for x in re.split(r"\s*(?:\+|;|،|\n)\s*", text_value) if x.strip()]
        cleaned = []
        for item in raw:
            item = re.sub(r"^(?:•|-|\d+[.)])\s*", "", item).strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return [f"• {_esc(item)}" for item in cleaned[:8]]

    price = num(q.get("price"))
    change = num(q.get("change_pct"))
    score = num(classification.get("score"))
    rvol = num(first_value(intraday.get("rvol"), intraday.get("intraday_rvol"),
                           classification.get("rvol"), tech.get("volume_ratio")))
    momentum_rvol = num(radar_checks.get("momentum_rvol"))
    rr = num(tech.get("risk_reward"))
    stop = num(first_value(tech.get("exit"), tech.get("stop"), tech.get("stop_loss")))
    targets = [num(x) for x in (tech.get("targets") or [])]
    targets = [x for x in targets if x is not None and x > 0]

    # قراءة فنية مفهومة للمستخدم، مشتقة من المستويات الفعلية ولا تنشئ سعرًا جديدًا.
    target1 = targets[0] if targets else None
    if rr is not None and rr < 1:
        recommendation = '⛔ لا دخول حاليًا — نسبة العائد للمخاطرة ضعيفة.'
    elif change is not None and change > 10:
        recommendation = '🟡 مراقبة وعدم مطاردة الارتفاع؛ الدخول مشروط بثبات السعر وتأكيد السيولة.'
    elif score is not None and score >= 70:
        recommendation = '🟢 دخول مشروط بعد تأكيد الاختراق والسيولة.'
    else:
        recommendation = '🟡 مراقبة؛ الدخول فقط بعد تأكيد الحركة والسيولة.'

    current_stop = num(getattr(outcome, "current_stop", None)) if outcome is not None else None
    if current_stop is None:
        current_stop = stop

    sas_core = radar_checks.get("sas_core")
    momentum_label = first_value(
        radar_checks.get("momentum_label"),
        classification.get("type"),
        classification.get("behavior"),
        tech.get("classification"),
    )
    target_ok = radar_checks.get("target")
    live_levels = radar_checks.get("live_levels")

    if outcome is None:
        status_text = "الرصد نشط"
    elif getattr(outcome, "status", None) == "failed":
        status_text = "تم إلغاء السيناريو عند الوقف"
    elif targets and int(getattr(outcome, "achieved_target", 0) or 0) >= len(targets):
        status_text = "اكتملت الأهداف المرصودة"
    elif int(getattr(outcome, "achieved_target", 0) or 0) > 0:
        status_text = f"الهدف {int(getattr(outcome, 'achieved_target', 0))} تحقق — الرصد مستمر"
    else:
        status_text = "الرصد نشط"

    report = ["🚀 <b>SAS PRO RADAR | فرصة رصد</b>"]

    summary_lines = [f"📈 <b>السهم:</b> {_esc(symbol.upper())}"]
    if price is not None:
        summary_lines.append(f"💵 <b>السعر:</b> {_money(price)}")
    if change is not None:
        icon = "🟢" if change > 0 else ("🔴" if change < 0 else "⚪")
        summary_lines.append(f"{icon} <b>التغير:</b> {change:+.2f}%")
    if momentum_label:
        summary_lines.append(f"🧭 <b>الاتجاه والحالة:</b> {_esc(momentum_label)}")
    classification_label = first_value(classification.get("label"), classification.get("name"), classification.get("status"))
    if classification_label:
        summary_lines.append(f"🏷️ <b>التصنيف:</b> {_esc(classification_label)}")
    elif momentum_label:
        summary_lines.append(f"🏷️ <b>التصنيف:</b> {_esc(momentum_label)}")

    trading_style = classification.get("trading_style")
    risk_level = classification.get("risk_level")
    risk_score = classification.get("risk_score")
    risk_emoji = classification.get("risk_emoji") or "⚠️"
    holding_horizon = classification.get("holding_horizon")
    if trading_style:
        summary_lines.append(f"🎯 <b>نوع السهم:</b> {_esc(trading_style)}")
    if risk_level:
        risk_text = f"{risk_emoji} <b>{_esc(risk_level)}</b>"
        if risk_score is not None:
            risk_text += f" ({int(risk_score)}/10)"
        summary_lines.append(f"⚠️ <b>درجة الخطورة:</b> {risk_text}")
    if holding_horizon:
        summary_lines.append(f"⏱️ <b>مدة الرصد المتوقعة:</b> {_esc(holding_horizon)}")
    add_section(report, "📋 <b>ملخص سريع للمبتدئ</b>", summary_lines)

    quality = tech.get("quality_score") or {}
    if quality.get("score") is not None:
        components = quality.get("components") or {}
        labels = {
            "trend": "الاتجاه",
            "momentum": "الزخم والحجم",
            "market": "السوق العام",
            "catalyst": "المحفز",
            "risk": "المخاطر",
        }
        quality_lines = [
            f"⭐ <b>جودة الفرصة: {float(quality.get('score')):.0f}/100 — {_esc(quality.get('label') or 'مراقبة')}</b>",
            f"🧭 الاتجاه: {float(components.get('trend') or 0):.0f}/100",
            f"🔥 الزخم والحجم: {float(components.get('momentum') or 0):.0f}/100",
            f"🌎 السوق العام: {float(components.get('market') or 0):.0f}/100",
            f"🚀 المحفز: {float(components.get('catalyst') or 0):.0f}/100",
            f"🛡 المخاطر: {float(components.get('risk') or 0):.0f}/100",
            "ℹ️ هذه درجة ترتيب آلية للمقارنة بين الفرص وليست توصية شراء أو بيع.",
        ]
        add_section(report, "⭐ <b>جودة الفرصة — بشكل مبسط</b>", quality_lines)

    add_section(report, '📌 <b>القراءة الفنية الواضحة</b>', [
        f'🧭 <b>الخلاصة:</b> {_esc(recommendation)}',
        '⚠️ لا تتم مطاردة السهم بعد ارتفاع حاد؛ يُشترط تأكيد السعر والسيولة قبل أي قرار.',
    ])

    if shariah:
        sh_lines = [
            f"🕌 <b>الحكم الحالي:</b> {_esc(shariah.get('status_ar') or 'غير واضح / يحتاج تحقق')}",
            f"📌 <b>الحالة:</b> {_esc(shariah.get('message') or 'لم تتوفر نتيجة موثقة كافية.')}",
        ]
        for src in (shariah.get("sources") or []):
            if not isinstance(src, dict):
                continue
            name = src.get("source") or "مصدر"
            status = src.get("status_ar") or "غير واضح / يحتاج تحقق"
            if src.get("role") == "مرجع منهجي":
                sh_lines.append(f"• {_esc(name)}: <b>مرجع منهجي</b>")
            else:
                sh_lines.append(f"• {_esc(name)}: <b>{_esc(status)}</b>" + (f" — {_esc(src.get('updated_at'))}" if src.get("updated_at") else ""))
        ai_sh = shariah.get("ai") or {}
        if ai_sh.get("summary"):
            sh_lines += ["", f"🧠 <b>تفسير AI:</b> {_esc(ai_sh.get('summary'))}"]
        sh_lines += ["", "⛔ <b>شرعية السهم مسؤوليتك — لا يتم اعتماد نتيجة غير موثقة.</b>"]
        add_section(report, "🕌 <b>نافذة التحقق الشرعي</b>", sh_lines)

    if tipranks.get("summary") or ai.get("tipranks_summary"):
        tr_summary = tipranks.get("summary") or ai.get("tipranks_summary")
        tr_signal = tipranks.get("signal") or ai.get("tipranks_signal") or "غير واضح"
        add_section(report, "🌐 <b>تحليل TipRanks — ترجمة AI</b>", [
            f"🧠 <b>الخلاصة:</b> {_esc(tr_summary)}",
            f"📊 <b>إشارة TipRanks:</b> {_esc(tr_signal)}",
            "ℹ️ هذا تحليل خارجي مترجم؛ لا يغيّر سعر الدخول أو الوقف أو الأهداف التي يرصدها SAS PRO."
        ])
    if momentum_label or classification_label:
        add_section(report, "💡 <b>ماذا يعني ذلك؟</b>", [
            "السهم في حالة فنية مرصودة، لكن استمرار الحركة يحتاج إلى تأكيد من السعر والسيولة."
        ])

    sas_lines = []
    if sas_core is not None:
        sas_lines.append("🟢 <b>SAS اجتاز الشروط الأساسية</b>" if bool(sas_core) else "🔴 <b>SAS لم يجتز الشروط الأساسية</b>")
    if score is not None:
        sas_lines.append(f"⭐ قوة الإشارة: <b>{score:.0f} / 100</b>")
    risk_reasons = classification.get("risk_reasons") or []
    if risk_reasons:
        sas_lines += ["", "⚠️ <b>لماذا هذه الخطورة؟</b>"] + reason_lines(" + ".join(str(x) for x in risk_reasons))
    if rvol is not None:
        sas_lines.append(f"📊 RVOL: <b>{rvol:.2f}×</b>")
    elif momentum_rvol is not None:
        sas_lines.append(f"📊 RVOL: <b>{momentum_rvol:.2f}×</b>")
    reason = first_value(radar_checks.get("reason"), radar_checks.get("summary"),
                         tech.get("reason"), tech.get("technical_reason"))
    if reason:
        sas_lines += ["", "🔎 <b>سبب الرصد:</b>"] + reason_lines(reason)
    if target_ok is not None:
        sas_lines += ["", "🎯 الهدف السعري: <b>مؤكد فنيًا</b>" if bool(target_ok) else "🎯 الهدف السعري: <b>غير مؤكد</b>"]
    if live_levels is not None:
        sas_lines.append(f"📍 المستويات: <b>{'متوفرة للرصد' if bool(live_levels) else 'غير مكتملة'}</b>")
    add_section(report, "📌 <b>لماذا ظهر السهم؟</b>", sas_lines)

    if sas_core is True:
        add_section(report, "💡 <b>للمبتدئ</b>", [
            "اجتياز SAS Core يعني أن الشروط الفنية الأساسية تحققت، لكنه لا يعني أن السهم سيصعد حتمًا."
        ])
    elif sas_core is False:
        add_section(report, "💡 <b>للمبتدئ</b>", [
            "عدم اجتياز SAS Core يعني أن الشروط الأساسية للرصد لم تكتمل."
        ])

    level_lines = []
    if price is not None:
        level_lines += ["🟦 <b>السعر المرجعي</b>", _money(price)]
    if current_stop is not None:
        level_lines += ["🛑 <b>الوقف</b>", _money(current_stop)]
    for idx, target in enumerate(targets, 1):
        level_lines += [f"🎯 <b>الهدف {idx}</b>", _money(target)]
    atr = num(first_value(tech.get("atr"), tech.get("ATR")))
    if atr is not None:
        level_lines += ["📏 <b>ATR</b>", _money(atr)]
    if level_lines:
        add_section(report, "🎯 <b>خطة الرصد</b>", level_lines)
        add_section(report, "📖 <b>شرح بسيط للمستويات</b>", [
            "💵 السعر المرجعي: السعر الذي بدأ منه الرصد.",
            "🛑 الوقف: إذا وصل إليه السهم فسيناريو الرصد لم يعد صالحًا.",
            "🎯 الأهداف: مستويات قد يصل إليها السهم، وليست أسعارًا مضمونة."
        ])

    rr_lines = []
    if rr is not None:
        rr_lines = [
            "📊 R:R",
            f"<b>1 : {rr:.2f}</b>",
            "🟢 <b>التقييم: مقبول</b>" if rr >= 1.5 else "🟠 <b>التقييم: منخفض</b>",
            "📌 مرجع SAS: <b>1 : 1.5</b> — كل 1$ مخاطرة يقابلها 1.5$ عائد محتمل على الأقل.",
            "📖 <b>ببساطة:</b>",
            f"هذا يعني أن كل وحدة مخاطرة تقابلها حوالي <b>{rr:.2f}</b> وحدة عائد محتمل حتى الهدف الأول.",
        ]
        if rr < 1.5:
            rr_lines.append("⚠️ النسبة منخفضة، لذلك يجب الانتباه للمخاطرة.")
    add_section(report, "⚖️ <b>هل العائد المحتمل يستحق المخاطرة؟</b>", rr_lines)

    fundamentals = tech.get("fundamentals") or {}
    company = first_value(tech.get("company_name"), tech.get("company"), q.get("company"), fundamentals.get("name"))
    sector = first_value(tech.get("sector"), q.get("sector"), fundamentals.get("industry"))
    market_cap = num(first_value(
        tech.get("market_cap"),
        tech.get("market_capitalization"),
        fundamentals.get("market_cap_m"),
    ))
    shares_outstanding = num(first_value(
        tech.get("shares_outstanding_m"),
        tech.get("shares_outstanding"),
        fundamentals.get("shares_outstanding_m"),
    ))
    eps = num(first_value(tech.get("eps"), fundamentals.get("eps_ttm")))
    revenue_growth = num(first_value(tech.get("revenue_growth_3y"), tech.get("revenue_growth"), fundamentals.get("revenue_growth_3y")))
    roe = num(first_value(tech.get("roe"), fundamentals.get("roe_ttm")))

    financial_lines = []
    if company:
        financial_lines += ["🏢 الشركة", f"<b>{_esc(company)}</b>"]
    if sector:
        financial_lines += ["🏷️ القطاع", f"<b>{_esc(sector)}</b>"]
    if market_cap is not None:
        financial_lines += ["💰 القيمة السوقية", f"${market_cap:,.2f}M"]
    if eps is not None:
        financial_lines += ["🧮 EPS", f"<b>{eps:.2f}</b>"]
    if revenue_growth is not None:
        financial_lines += ["📈 نمو الإيرادات خلال 3 سنوات", f"<b>{revenue_growth:+.2f}%</b>"]
    if roe is not None:
        financial_lines += ["📊 ROE", f"<b>{roe:+.2f}%</b>"]
    if financial_lines:
        add_section(report, "💼 <b>لمحة مالية عن الشركة</b>", financial_lines)
        if ai.get("financial_summary"):
            add_section(report, "📖 <b>بشكل مبسط</b>", [_esc(ai.get("financial_summary"))])

    # تفاصيل السوق تُعرض فقط عندما تصل من بيانات فعلية داخل الرادار.
    # لا نحول "الأسهم المتاحة" إلى float من تلقاء أنفسنا؛ الأسهم القائمة
    # (shares outstanding) ليست هي الـ float، لذلك نسمّي كل رقم بمصدره الصحيح.
    market_lines = []
    volume = num(first_value(tech.get("volume"), q.get("volume")))
    dollar_volume = num(first_value(
        tech.get("dollar_volume"),
        classification.get("dollar_volume"),
        (radar_checks or {}).get("dollar_volume"),
    ))
    if dollar_volume is None and price is not None and volume is not None and volume > 0:
        dollar_volume = price * volume
    exchange = first_value(tech.get("exchange"), q.get("exchange"), fundamentals.get("exchange"))
    live_source = first_value(q.get("source"), tech.get("live_price_source"))
    session = first_value(tech.get("market_session"), tech.get("market_session_code"))
    if exchange:
        market_lines.append(f"🏦 <b>السوق/البورصة:</b> {_esc(exchange)}")
    if volume is not None and volume > 0:
        market_lines.append(f"📦 <b>حجم التداول:</b> {volume:,.0f} سهم")
    if dollar_volume is not None and dollar_volume > 0:
        market_lines.append(f"💵 <b>قيمة التداول التقريبية:</b> ${dollar_volume:,.0f}")
    if rvol is not None:
        market_lines.append(f"📊 <b>الحجم النسبي RVOL:</b> {rvol:.2f}×")
    if shares_outstanding is not None and shares_outstanding > 0:
        market_lines.append(f"🔢 <b>الأسهم القائمة:</b> {shares_outstanding:,.2f} مليون سهم")
    if session:
        market_lines.append(f"🕒 <b>جلسة السوق:</b> {_esc(session)}")
    if live_source:
        market_lines.append(f"🔎 <b>مصدر السعر:</b> {_esc(live_source)}")
    market_lines.append("🛡️ <b>قاعدة الدقة:</b> لا يظهر أي رقم غير متوفر فعليًا في بيانات الرادار.")
    if market_lines:
        add_section(report, "📊 <b>بيانات السوق الفعلية</b>", market_lines)

    events = tech.get("corporate_events") or tech.get("events")
    event_lines = []
    if isinstance(events, list):
        for event in events[:5]:
            if isinstance(event, dict):
                text_value = first_value(event.get("description"), event.get("headline"), event.get("title"))
                if text_value:
                    event_lines.append(f"• {_esc(text_value)}")
            elif event:
                event_lines.append(f"• {_esc(event)}")
    elif isinstance(events, str) and events.strip():
        event_lines.append(f"• {_esc(events)}")
    if event_lines:
        add_section(report, "🔄 <b>الأحداث المؤثرة</b>", event_lines)

    valid_news = [item for item in news_items if isinstance(item, dict) and item.get("headline")]
    if valid_news:
        news_lines = []
        for idx, item in enumerate(valid_news[:8], 1):
            news_lines += [
                f"<b>{idx}️⃣</b> {_esc(item.get('headline'))}",
                ""
            ]
        while news_lines and news_lines[-1] == "":
            news_lines.pop()
        count = len(news_items) if isinstance(news_items, list) else len(valid_news)
        news_lines += ["", f"📚 عدد الأخبار المتاحة: <b>{count}</b>"]
        add_section(report, "📰 <b>أهم الأخبار الأخيرة</b>", news_lines)

        ai_lines = []
        if ai.get("headline_summary"):
            ai_lines += ["📌 <b>ماذا حدث؟</b>", _esc(ai.get("headline_summary")), ""]
        if ai.get("why_rising"):
            ai_lines += ["📈 <b>لماذا قد يهم المتداول؟</b>", _esc(ai.get("why_rising")), ""]
        if ai.get("news_assessment"):
            ai_lines += ["🔎 <b>علاقة الخبر بحركة السهم</b>", f"<b>{_esc(ai.get('news_assessment'))}</b>", ""]
        if ai.get("momentum"):
            ai_lines += ["🚀 <b>الزخم الإخباري</b>", f"<b>{_esc(ai.get('momentum'))}</b>", ""]
        if ai.get("risk_flags"):
            risks = ai.get("risk_flags")
            risk_text = "\n".join(f"• {_esc(x)}" for x in risks[:5]) if isinstance(risks, list) else _esc(risks)
            ai_lines += ["⚠️ <b>المخاطر الخبرية</b>", risk_text, ""]
        elif ai.get("risk_summary"):
            ai_lines += ["⚠️ <b>المخاطر الخبرية</b>", _esc(ai.get("risk_summary")), ""]
        if ai_lines:
            while ai_lines and ai_lines[-1] == "":
                ai_lines.pop()
            ai_lines += [
                "",
                "📌 <b>ملاحظة:</b>",
                "تحليل AI يفسر الأخبار الموثقة فقط، ولا يغيّر مستويات الرصد أو قرار SAS PRO."
            ]
            add_section(report, "🧠 <b>شرح الأخبار ببساطة</b>", ai_lines)

    fcc = tech.get("fcc_review") or {}
    if fcc.get("available"):
        fcc_lines = [f"🧠 <b>مستوى المراجعة:</b> {_esc(fcc.get('review_level') or 'محايد')}"]
        strengths = fcc.get("strengths") or []
        contradictions = fcc.get("contradictions") or []
        if strengths:
            fcc_lines += ["", "💪 <b>نقاط القوة:</b>"] + [f"• {_esc(x)}" for x in strengths[:4]]
        if contradictions:
            fcc_lines += ["", "⚠️ <b>التعارضات:</b>"] + [f"• {_esc(x)}" for x in contradictions[:4]]
        if fcc.get("note"):
            fcc_lines += ["", f"📝 <b>ملاحظة:</b> {_esc(fcc.get('note'))}"]
        fcc_lines += [
            "",
            "🛡️ <b>دور FCC:</b> مراجعة الأدلة فقط؛ لا يغيّر السعر أو الوقف أو الأهداف أو RVOL أو قرار SAS PRO."
        ]
        add_section(report, "🧠 <b>مراجعة SAS PRO AI</b>", fcc_lines)

    conclusion = []
    if momentum_label:
        conclusion.append(f"📌 <b>الوضع الحالي:</b>\nالسهم في حالة {_esc(momentum_label)} وتحت المراقبة.")
    if sas_core is True:
        conclusion.append("🟢 <b>نقطة القوة:</b>\nاجتياز SAS Core ووجود محفزات فنية وإخبارية.")
    if rr is not None and rr < 1.5:
        conclusion.append(f"🟠 <b>نقطة الانتباه:</b>\nنسبة R:R الحالية <b>{rr:.2f}</b> وهي أقل من مرجع SAS البالغ <b>1.5</b>.")
    if current_stop is not None:
        conclusion.append(f"🛑 <b>أهم مستوى للمراقبة:</b>\n<b>{_money(current_stop)}</b>")
    if targets:
        conclusion.append(f"🎯 <b>أول هدف:</b>\n<b>{_money(targets[0])}</b>")
        if len(targets) > 1:
            conclusion.append(f"📈 <b>الأهداف الأعلى:</b>\nحتى <b>{_money(targets[-1])}</b> وفق المستويات المرصودة.")
    if conclusion:
        conclusion.append("⚠️ <b>الخلاصة:</b>\nالسهم لديه إشارات فنية وإخبارية إيجابية، لكن استمرار الحركة غير مضمون، لذلك يبقى تحت المراقبة.")
        add_section(report, "🧠 <b>الخلاصة</b>", conclusion)

    report += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "⚠️ <b>هذا التقرير معلوماتي وتعليمي فقط، وليس توصية شراء أو بيع، وقرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️</b>",
        "",
        "⛔ <b>شرعية السهم مسؤوليتك — تحقق منها قبل التداول ⛔</b>",
        "",
        "📡 <b>SAS PRO</b>",
    ]
    # حماية نهائية: لا تسمح بظهور \\n كنص حرفي في رسالة Telegram.
    final_report = "\n".join(report)
    final_report = final_report.replace("\\\\r\\n", "\n").replace("\\\\n", "\n").replace("\\\\r", "\r")
    return final_report

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

    try:
        await bot_api("unbanChatMember", {
            "chat_id": settings.telegram_channel_id,
            "user_id": telegram_id,
            "only_if_banned": True,
        })
    except Exception:
        pass

    try:
        await bot_api("approveChatJoinRequest", {
            "chat_id": settings.telegram_channel_id,
            "user_id": telegram_id,
        })
        return {"ok": True, "action": "join_request_approved"}
    except Exception:
        pass

    try:
        channel_link, invite_expires = await create_user_channel_invite(telegram_id, "ACCESS", expires_at)
        await send_message(
            telegram_id,
            "✅ <b>تم تفعيل وصولك إلى SAS PRO</b>\n\n"
            "اضغط «انضمام الآن» وسيتم قبول طلب دخولك تلقائيًا لأن وصولك فعال.",
            {"inline_keyboard": [[{"text": "🚀 انضمام الآن إلى SAS PRO", "url": channel_link}]]},
        )
        return {"ok": True, "action": "join_link_sent", "invite_expires": invite_expires.isoformat()}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}

def is_active(sub):
    return bool(sub and sub.active and aware(sub.expires_at) > utcnow())

async def telegram_user(
    x_telegram_init_data: str = Header(default=""),
    authorization: str = Header(default=""),
):
    # Telegram WebApp may deliver initData through the custom header or,
    # depending on the Telegram client/WebView, through Authorization: tma <initData>.
    # Both contain the same signed payload; accepting either avoids client-specific
    # WebView header handling issues without weakening signature validation.
    init_data = (x_telegram_init_data or "").strip()
    if not init_data and authorization.lower().startswith("tma "):
        init_data = authorization[4:].strip()
    try:
        return validate_init_data(init_data)
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
    # المالك وأي مشرف SAS PRO مصرح له يستطيع فتح واجهة المستخدم
    # من لوحة الإدارة لمعاينة نفس المحطة والبيانات المتاحة للمشترك.
    if int(user["id"]) == int(settings.owner_telegram_id) or await get_admin(int(user["id"])):
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

@app.middleware("http")
async def miniapp_no_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path in {"/assets/app.js", "/assets/app.css"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.get("/")
async def home():
    response = FileResponse("web/index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response

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
    now = utcnow()
    row = (await db.execute(select(User).where(User.telegram_id == user["id"]))).scalars().first()
    if not row:
        row = User(
            telegram_id=user["id"],
            username=user.get("username"),
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
        )
        db.add(row)
        await db.flush()
    else:
        row.username = user.get("username")
        row.first_name = user.get("first_name")
        row.last_name = user.get("last_name")
    row.terms_accepted_at = now
    row.terms_version = TERMS_VERSION
    row.updated_at = now
    await db.commit()

    # أول موافقة على الشروط تمنح المستخدم شهرًا مجانيًا مرة واحدة.
    # يتم إنشاء رابط قناة أحادي الاستخدام ثم فتحه مباشرة من Mini App.
    trial = None
    if (
        int(user["id"]) != int(settings.owner_telegram_id)
        and not row.trial_used_at
        and not row.free_access
        and not (
            row.subscription_expires
            and aware(row.subscription_expires) > now
            and row.status == "active"
        )
    ):
        try:
            trial = await start_trial_for_user(user)
        except Exception as exc:
            # لا نخفي سبب فشل تفعيل التجربة؛ الواجهة تحتاج خطأ واضحًا
            # وتبقى نافذة الشروط مفتوحة حتى ينجح التفعيل.
            logging.getLogger(__name__).exception("Trial activation failed for telegram_id=%s", user["id"])
            raise HTTPException(503, f"تعذر تفعيل التجربة المجانية: {exc}") from exc

    return {
        "ok": True,
        "version": TERMS_VERSION,
        "trial_started": bool(trial),
        "trial": (
            {
                "trial_expires": trial["trial_expires"].isoformat(),
                "channel_link": trial["channel_link"],
                "invite_expires": trial["invite_expires"].isoformat(),
            }
            if trial else None
        ),
    }

@app.get("/api/me")
async def me(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    # مسار تحقق خفيف: استخدم جلسة قاعدة البيانات نفسها لكل القراءات.
    # لا نحتاج للاستعلام عن Subscription هنا لأن حالة الوصول تُحسم من User.
    existing = (await db.execute(
        select(User).where(User.telegram_id == user["id"])
    )).scalars().first()

    if not existing:
        existing = User(
            telegram_id=user["id"],
            username=user.get("username"),
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
    else:
        changed = (
            existing.username != user.get("username")
            or existing.first_name != user.get("first_name")
            or existing.last_name != user.get("last_name")
        )
        if changed:
            existing.username = user.get("username")
            existing.first_name = user.get("first_name")
            existing.last_name = user.get("last_name")
            existing.updated_at = utcnow()
            await db.commit()

    admin = False
    admin_role = None
    admin_permissions = []
    if int(user["id"]) == int(settings.owner_telegram_id):
        admin = True
        admin_role = "owner"
        admin_permissions = list(PERMISSIONS)
    else:
        admin_row = (await db.execute(
            select(AdminRole).where(AdminRole.telegram_id == int(user["id"]))
        )).scalars().first()
        if admin_row and admin_row.enabled:
            admin = True
            admin_role = admin_row.role
            try:
                admin_permissions = json.loads(admin_row.permissions or "[]")
            except Exception:
                admin_permissions = []

    now = utcnow()
    trial_active = bool(
        existing.status == "trial"
        and existing.trial_expires
        and aware(existing.trial_expires) > now
    )
    subscription_active = bool(
        not existing.free_access
        and existing.status == "active"
        and existing.subscription_expires
        and aware(existing.subscription_expires) > now
    )
    pro = admin or existing.free_access or subscription_active or trial_active
    expires = None
    if not admin:
        if trial_active:
            expires = aware(existing.trial_expires).isoformat()
        elif subscription_active:
            expires = aware(existing.subscription_expires).isoformat()

    return {
        "user": user,
        "admin": admin,
        "admin_role": admin_role,
        "admin_permissions": admin_permissions,
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
        "subscription_start": aware(existing.subscription_start).isoformat() if existing.subscription_start else None,
        "subscription_expires": aware(existing.subscription_expires).isoformat() if existing.subscription_expires else None,
        "plan": existing.plan,
        "trial_start": aware(existing.trial_start).isoformat() if existing.trial_start else None,
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
            await send_message(
                user["id"],
                "🎁 <b>بدأت تجربتك المجانية في SAS PRO</b>\n\n"
                f"⏳ المدة: <b>{settings.trial_days} أيام</b>\n"
                f"📅 تنتهي: <b>{result['trial_expires'].strftime('%d/%m/%Y %H:%M')}</b>\n\n"
                "اضغط «انضمام للقناة» وسيتم قبول طلبك تلقائيًا لأن تجربتك مفعلة.",
                {"inline_keyboard": [[{"text": "🚀 انضمام لقناة SAS PRO", "url": result["channel_link"]}]]},
            )
        except Exception:
            pass
        return {"ok": True, **result, "trial_expires": result["trial_expires"].isoformat()}
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"تعذر بدء التجربة: {exc}")

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

@app.get("/api/admin/deploy-status")
async def admin_deploy_status(user=Depends(telegram_user)):
    await require_admin_permission(user, "settings")
    url = "https://api.github.com/repos/pq070pq/sas/actions/runs?per_page=10"
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SAS-PRO-Admin"})
            r.raise_for_status()
            data = r.json()
        runs = data.get("workflow_runs") or []
        deploy = next((x for x in runs if x.get("name") == "Deploy SAS PRO to OVH"), runs[0] if runs else None)
        if not deploy:
            return {"ok": True, "status": "unknown", "message": "لا توجد عملية نشر مسجلة بعد."}
        return {
            "ok": True,
            "status": deploy.get("status") or "unknown",
            "conclusion": deploy.get("conclusion"),
            "run_number": deploy.get("run_number"),
            "attempt": deploy.get("run_attempt"),
            "sha": (deploy.get("head_sha") or "")[:7],
            "updated_at": deploy.get("updated_at"),
            "url": deploy.get("html_url"),
        }
    except Exception as exc:
        return {"ok": False, "status": "unknown", "message": f"تعذر قراءة حالة GitHub Actions: {exc}"}

@app.get("/api/admin/health")
async def admin_health(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    """Compact admin health endpoint with explicit component diagnostics."""
    admin = await get_admin(int(user["id"]))
    if not admin:
        raise HTTPException(403, "لا تملك صلاحيات الإدارة")

    out = {"ok": True, "checked_at": utcnow().isoformat(), "components": []}

    def add(name, status, message, detail=None):
        item = {"name": name, "status": status, "message": message}
        if detail:
            item["detail"] = str(detail)[:500]
        out["components"].append(item)

    try:
        add("خادم SAS PRO", "ok", "الخادم يستجيب بشكل طبيعي.")
    except Exception as exc:
        add("خادم SAS PRO", "error", "تعذر فحص الخادم.", exc)

    try:
        market = market_status()
        add(
            "حالة السوق والرادار",
            "ok" if not (market.get("open") and stock_radar_enabled() is False) else "error",
            ("السوق مفتوح والرادار مفعّل." if market.get("open") else "السوق خارج الجلسة؛ التشغيل الآلي ينتظر جلسة الرصد.")
            if not (market.get("open") and stock_radar_enabled() is False)
            else "السوق مفتوح لكن الرادار غير مفعّل."
        )
    except Exception as exc:
        add("حالة السوق والرادار", "error", "تعذر قراءة حالة السوق والرادار.", exc)

    try:
        session_date = market_status()["date"]
        rows = (await db.execute(
            select(RadarSignal)
            .where(RadarSignal.session_date == session_date)
            .order_by(RadarSignal.created_at.desc())
            .limit(100)
        )).scalars().all()
        if not rows:
            rows = (await db.execute(
                select(RadarSignal).order_by(RadarSignal.created_at.desc()).limit(1)
            )).scalars().all()
        add("لوحة الرادار", "ok", f"تمت قراءة بيانات الرادار بنجاح. السجلات المتاحة: {len(rows)}.")
    except Exception as exc:
        add("لوحة الرادار", "error", "تعذر قراءة بيانات لوحة الرادار.", exc)

    try:
        ticker_data = await ticker()
        valid = isinstance(ticker_data, list) and any(float(x.get("price") or 0) > 0 for x in ticker_data if isinstance(x, dict))
        add("أسعار السوق", "ok" if valid else "warn", "تم تحديث أسعار السوق." if valid else "مصادر الأسعار لم تُرجع أسعارًا صالحة حاليًا.")
    except Exception as exc:
        add("أسعار السوق", "warn", "تعذر تحديث شريط أسعار السوق حاليًا؛ لا يمنع تشغيل الرادار.", exc)

    try:
        run = (await db.execute(select(RadarRun).order_by(RadarRun.started_at.desc()).limit(1))).scalars().first()
        if not run:
            add("سجل دورة الرادار", "warn", "لا توجد دورة رادار مسجلة بعد.")
        elif run.status == "failed":
            add("سجل دورة الرادار", "error", f"آخر دورة فشلت: {run.error_type or 'خطأ غير محدد'} — {run.error_message or 'بدون رسالة'}.")
        elif run.status == "skipped":
            reason = {}
            try: reason = json.loads(run.diagnostics or "{}")
            except Exception: reason = {}
            why = reason.get("status") or "سبب غير مسجل"
            add("سجل دورة الرادار", "ok", f"آخر دورة تم تجاوزها طبيعيًا: {why}.")
        else:
            add("سجل دورة الرادار", "ok", f"آخر دورة حالتها: {run.status or 'غير معروفة'}.")
    except Exception as exc:
        add("سجل دورة الرادار", "error", "تعذر قراءة آخر دورة للرادار.", exc)

    if await has_permission(int(user["id"]), "settings"):
        try:
            url = "https://api.github.com/repos/pq070pq/sas/actions/runs?per_page=10"
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                response = await client.get(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SAS-PRO-Admin"})
                response.raise_for_status()
                data = response.json()
            runs = data.get("workflow_runs") or []
            deploy = next((x for x in runs if x.get("name") == "Deploy SAS PRO to OVH"), runs[0] if runs else None)
            if deploy and deploy.get("conclusion") == "failure":
                add("آخر نشر إلى OVH", "error", "آخر عملية نشر فشلت؛ راجع قسم النشر.")
            elif deploy:
                add("آخر نشر إلى OVH", "ok", f"آخر نشر: {deploy.get('status') or 'غير معروف'} — Commit {(deploy.get('head_sha') or '')[:7] or '—'}.")
            else:
                add("آخر نشر إلى OVH", "warn", "لا توجد عملية نشر مسجلة بعد.")
        except Exception as exc:
            add("آخر نشر إلى OVH", "warn", "تعذر قراءة حالة GitHub Actions؛ هذا لا يعني أن SAS PRO متوقف.", exc)

    return out

@app.get("/api/admin/radar/last")
async def admin_radar_last(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "settings")
    run = (await db.execute(
        select(RadarRun).order_by(RadarRun.started_at.desc()).limit(1)
    )).scalars().first()
    if not run:
        return {"ok": True, "found": False, "message": "لا توجد دورة رادار مسجلة بعد."}

    def loads(value, fallback):
        try:
            return json.loads(value or "")
        except Exception:
            return fallback

    return {
        "ok": True,
        "found": True,
        "id": run.id,
        "session_date": run.session_date,
        "session": run.session,
        "started_at": aware(run.started_at).isoformat(),
        "finished_at": aware(run.finished_at).isoformat() if run.finished_at else None,
        "duration_seconds": run.duration_seconds,
        "status": run.status,
        "scanner": {
            "candidates": run.candidates,
            "shortlist": run.shortlist,
            "passed": run.passed,
            "filtered": run.filtered,
            "errors": run.errors,
        },
        "channel": {
            "gate_passed": run.channel_gate_passed,
            "sent": run.channel_sent,
            "app_only": run.channel_app_only,
            "rejections": loads(run.channel_gate_rejections, {}),
        },
        "top_opportunities": loads(run.top_opportunities, []),
        "diagnostics": loads(run.diagnostics, {}),
        "error": {
            "type": run.error_type,
            "message": run.error_message,
        } if run.error_type or run.error_message else None,
    }

@app.post("/api/admin/radar/run-preview")
async def admin_radar_run_preview(user=Depends(telegram_user)):
    """Run the current Smart Levels + ICT radar without publishing to Telegram."""
    await require_admin_permission(user, "radar")
    if radar_manual_lock.locked():
        raise HTTPException(409, "توجد دورة فحص يدوية تعمل حاليًا؛ انتظر انتهائها.")
    async with radar_manual_lock:
        started = utcnow()
        try:
            from .scanner import scan_us_low_price_stocks
            result = await scan_us_low_price_stocks(force_refresh=True)
            diagnostics = result.get("diagnostics") or {}
            rows = list(result.get("stocks") or [])

            def score(row):
                return float(row.get("opening_opportunity_score") or row.get("smart_levels_score") or 0)

            rows.sort(key=score, reverse=True)
            confirmed = [
                r for r in rows
                if str((r.get("classification") or {}).get("opportunity_status") or "") == "confirmed"
            ]
            watch = [
                r for r in rows
                if str((r.get("classification") or {}).get("opportunity_status") or "") != "confirmed"
            ]

            def compact(row):
                cls = row.get("classification") or {}
                gate = cls.get("smart_levels_ict") or {}
                return {
                    "symbol": row.get("symbol"),
                    "price": row.get("live_price") or row.get("price"),
                    "change_pct": row.get("live_change_pct") if row.get("live_change_pct") is not None else row.get("change_pct"),
                    "rvol": cls.get("rvol"),
                    "smart_levels_score": cls.get("smart_levels_score"),
                    "smart_levels_status": cls.get("smart_levels_status"),
                    "status": cls.get("opportunity_status") or "watch",
                    "confirmation_ready": bool(cls.get("confirmation_ready")),
                    "gate_reasons": gate.get("reasons") or [],
                    "targets": (row.get("targets") or {}).get("targets") or [],
                    "risk_reward": (row.get("targets") or {}).get("risk_reward"),
                }

            return {
                "ok": True,
                "mode": "preview_only",
                "started_at": started.isoformat(),
                "finished_at": utcnow().isoformat(),
                "duration_seconds": round((utcnow() - started).total_seconds(), 2),
                "scanner": {
                    "candidates": int(diagnostics.get("candidates") or 0),
                    "shortlist": int(diagnostics.get("shortlist") or 0),
                    "passed": int(diagnostics.get("passed") or 0),
                    "filtered": int(diagnostics.get("filtered") or 0),
                    "errors": int(diagnostics.get("errors") or 0),
                    "confirmed": len(confirmed),
                    "watch": len(watch),
                },
                "top5": [compact(x) for x in confirmed[:5]],
                "watch_top": [compact(x) for x in watch[:10]],
                "rejections": (diagnostics.get("filtered_examples") or [])[:20],
                "filter_counts": diagnostics.get("filter_counts") or {},
                "message": "تم الفحص فقط؛ لم يتم إرسال أي رسالة إلى قناة Telegram أو تغيير قائمة القناة.",
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Admin radar preview failed: %s", exc)
            raise HTTPException(500, f"فشل فحص الرادار: {type(exc).__name__}: {exc}")


@app.get("/api/admin/overview")
async def admin_overview(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "users")
    now = utcnow()
    users = (await db.execute(select(User))).scalars().all()
    active = 0
    expired = 0
    trial = 0
    for u in users:
        trial_active = bool(u.status == "trial" and u.trial_expires and aware(u.trial_expires) > now)
        paid_active = bool(
            not u.free_access
            and u.status == "active"
            and u.subscription_expires
            and aware(u.subscription_expires) > now
        )
        is_active_user = bool(u.free_access or trial_active or paid_active)
        if is_active_user:
            active += 1
        elif (
            u.subscription_expires
            or u.trial_expires
            or u.status in {"revoked", "expired"}
        ):
            expired += 1
        if u.trial_used_at:
            trial += 1
    payments = (await db.execute(select(Payment))).scalars().all()
    owner = (await db.execute(select(User).where(User.telegram_id == int(settings.owner_telegram_id)))).scalars().first()
    # إذا كان المالك هو المستخدم الحالي، نستخدم بيانات Telegram مباشرة حتى لا
    # تظهر بطاقة OWNER فارغة عند عدم وجود سجل قديم في قاعدة البيانات.
    owner_data = {
        "telegram_id": int(settings.owner_telegram_id),
        "first_name": owner.first_name if owner else (user.get("first_name") if int(user["id"]) == int(settings.owner_telegram_id) else None),
        "last_name": owner.last_name if owner else (user.get("last_name") if int(user["id"]) == int(settings.owner_telegram_id) else None),
        "username": owner.username if owner else (user.get("username") if int(user["id"]) == int(settings.owner_telegram_id) else None),
    }
    return {
        "active": active,
        "expired": expired,
        "trial_users": trial,
        "new_users": len([u for u in users if u.created_at and (now - aware(u.created_at)).days < 30]),
        "payments": len(payments),
        "stars": sum(p.stars for p in payments),
        "owner": owner_data,
    }

@app.get("/api/admin/subscribers")
async def admin_subscribers(state: str = "active", user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "users")
    state = (state or "active").strip().lower()
    if state not in {"active", "expired"}:
        raise HTTPException(400, "الحالة يجب أن تكون active أو expired")
    now = utcnow()
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    rows = []
    for u in users:
        trial_active = bool(u.status == "trial" and u.trial_expires and aware(u.trial_expires) > now)
        paid_active = bool(
            not u.free_access
            and u.status == "active"
            and u.subscription_expires
            and aware(u.subscription_expires) > now
        )
        is_active_user = bool(u.free_access or trial_active or paid_active)
        if state == "active" and not is_active_user:
            continue
        if state == "expired" and is_active_user:
            continue
        if u.free_access:
            source = "free"
            status_label = "♾️ وصول مجاني"
            expires = None
        elif trial_active:
            source = "trial"
            status_label = "🎁 تجربة فعالة"
            expires = aware(u.trial_expires)
        elif paid_active:
            source = "subscription"
            status_label = "🟢 اشتراك فعال"
            expires = aware(u.subscription_expires)
        elif u.status == "revoked":
            source = "revoked"
            status_label = "⛔ ملغى"
            expires = aware(u.subscription_expires) if u.subscription_expires else None
        elif u.trial_expires and aware(u.trial_expires) <= now and not u.subscription_expires:
            source = "trial_expired"
            status_label = "🔴 التجربة منتهية"
            expires = aware(u.trial_expires)
        else:
            source = "expired"
            status_label = "🔴 الاشتراك منتهي"
            expires = aware(u.subscription_expires) if u.subscription_expires else None
        rows.append({
            "telegram_id": u.telegram_id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "plan": u.plan,
            "source": source,
            "status_label": status_label,
            "expires_at": expires.isoformat() if expires else None,
            "created_at": aware(u.created_at).isoformat() if u.created_at else None,
        })
    return {"state": state, "count": len(rows), "users": rows}


@app.get("/api/admin/terms-status")
async def admin_terms_status(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "users")
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    current = TERMS_VERSION
    admin_ids = set(
        (await db.execute(
            select(AdminRole.telegram_id).where(AdminRole.enabled.is_(True))
        )).scalars().all()
    )
    rows = []
    accepted = 0
    total_users = 0
    for u in users:
        # حسابات الإدارة ليست ضمن موافقات المستخدمين على الشروط.
        if int(u.telegram_id) in admin_ids:
            continue
        total_users += 1
        ok = bool(u.terms_accepted_at and u.terms_version == current)
        if not ok:
            # لوحة الإدارة تعرض الموافقات الفعلية فقط؛ لا تعرض "لم يوافق".
            continue
        accepted += 1
        rows.append({
            "telegram_id": u.telegram_id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "accepted": True,
            "accepted_at": aware(u.terms_accepted_at).isoformat() if u.terms_accepted_at else None,
            "terms_version": u.terms_version,
        })
    return {
        "ok": True,
        "terms_version": current,
        "total": total_users,
        "accepted": accepted,
        "pending": 0,
        "users": rows,
    }


@app.get("/api/admin/monthly-report")
async def admin_monthly_report(month: str = "", user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "users")
    now = utcnow()
    if not month:
        month = now.strftime("%Y-%m")
    try:
        year, mon = [int(x) for x in month.split("-", 1)]
        if mon < 1 or mon > 12:
            raise ValueError
        month_start = datetime(year, mon, 1, tzinfo=timezone.utc)
        if mon == 12:
            next_start = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_start = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(400, "صيغة الشهر يجب أن تكون YYYY-MM")

    prev_month = month_start - timedelta(days=1)
    prev_start = datetime(prev_month.year, prev_month.month, 1, tzinfo=timezone.utc)

    signals = (await db.execute(select(RadarSignal).where(
        RadarSignal.created_at >= month_start,
        RadarSignal.created_at < next_start,
    ))).scalars().all()
    outcomes = (await db.execute(select(RadarOutcome).where(
        RadarOutcome.created_at >= month_start,
        RadarOutcome.created_at < next_start,
    ))).scalars().all()
    payments = (await db.execute(select(Payment).where(
        Payment.paid_at >= month_start,
        Payment.paid_at < next_start,
    ))).scalars().all()
    users = (await db.execute(select(User).where(
        User.created_at >= month_start,
        User.created_at < next_start,
    ))).scalars().all()

    prev_signals = (await db.execute(select(RadarSignal).where(
        RadarSignal.created_at >= prev_start,
        RadarSignal.created_at < month_start,
    ))).scalars().all()
    prev_outcomes = (await db.execute(select(RadarOutcome).where(
        RadarOutcome.created_at >= prev_start,
        RadarOutcome.created_at < month_start,
    ))).scalars().all()
    prev_payments = (await db.execute(select(Payment).where(
        Payment.paid_at >= prev_start,
        Payment.paid_at < month_start,
    ))).scalars().all()

    sent = sum(1 for s in signals if s.telegram_message_id)
    target_hits = sum(1 for o in outcomes if int(o.achieved_target or 0) > 0)
    completed = sum(1 for o in outcomes if o.status == "completed")
    failed = sum(1 for o in outcomes if o.status == "failed")
    active_outcomes = sum(1 for o in outcomes if o.status == "active")

    returns = []
    for o in outcomes:
        entry = float(o.entry_price or 0)
        if entry <= 0:
            continue
        achieved = int(o.achieved_target or 0)
        if achieved > 0:
            levels = [o.target1, o.target2, o.target3, o.target4, o.target5]
            idx = min(achieved, len(levels)) - 1
            reference = levels[idx] if levels[idx] is not None else o.current_price
        elif o.status == "failed" and o.exit_level is not None:
            reference = o.exit_level
        else:
            reference = o.current_price
        if reference is not None:
            returns.append((float(reference) / entry - 1.0) * 100.0)

    avg_return = sum(returns) / len(returns) if returns else None
    best_return = max(returns) if returns else None
    worst_return = min(returns) if returns else None
    hit_rate = (target_hits / len(outcomes) * 100.0) if outcomes else None

    prev_hits = sum(1 for o in prev_outcomes if int(o.achieved_target or 0) > 0)
    prev_hit_rate = (prev_hits / len(prev_outcomes) * 100.0) if prev_outcomes else None
    prev_returns = []
    for o in prev_outcomes:
        entry = float(o.entry_price or 0)
        if entry <= 0:
            continue
        achieved = int(o.achieved_target or 0)
        if achieved > 0:
            levels = [o.target1, o.target2, o.target3, o.target4, o.target5]
            idx = min(achieved, len(levels)) - 1
            reference = levels[idx] if levels[idx] is not None else o.current_price
        elif o.status == "failed" and o.exit_level is not None:
            reference = o.exit_level
        else:
            reference = o.current_price
        if reference is not None:
            prev_returns.append((float(reference) / entry - 1.0) * 100.0)

    def pct_change(cur, prev):
        if prev == 0:
            return None
        return ((cur - prev) / prev) * 100.0

    top_symbols = {}
    for o in outcomes:
        top_symbols[o.symbol] = top_symbols.get(o.symbol, 0) + 1
    top_symbols = sorted(top_symbols.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "month": month,
        "period": {"start": month_start.isoformat(), "end": next_start.isoformat()},
        "radar": {
            "signals": len(signals),
            "sent": sent,
            "outcomes": len(outcomes),
            "target_hits": target_hits,
            "target_hit_rate": round(hit_rate, 2) if hit_rate is not None else None,
            "completed": completed,
            "failed": failed,
            "active": active_outcomes,
            "avg_return_pct": round(avg_return, 2) if avg_return is not None else None,
            "best_return_pct": round(best_return, 2) if best_return is not None else None,
            "worst_return_pct": round(worst_return, 2) if worst_return is not None else None,
            "tracked_returns": len(returns),
        },
        "subscribers": {
            "new_users": len(users),
            "trial_users": sum(1 for u in users if u.trial_start and aware(u.trial_start) >= month_start and aware(u.trial_start) < next_start),
            "paid_users": sum(1 for u in users if u.subscription_start and aware(u.subscription_start) >= month_start and aware(u.subscription_start) < next_start),
        },
        "revenue": {
            "payments": len(payments),
            "stars": sum(int(p.stars or 0) for p in payments),
            "sar": sum(int(p.sar_amount or 0) for p in payments),
        },
        "comparison": {
            "previous_month": prev_start.strftime("%Y-%m"),
            "signals": len(prev_signals),
            "outcomes": len(prev_outcomes),
            "target_hit_rate": round(prev_hit_rate, 2) if prev_hit_rate is not None else None,
            "avg_return_pct": round(sum(prev_returns) / len(prev_returns), 2) if prev_returns else None,
            "stars": sum(int(p.stars or 0) for p in prev_payments),
            "signals_change_pct": round(pct_change(len(signals), len(prev_signals)), 2) if len(prev_signals) else None,
            "stars_change_pct": round(pct_change(sum(int(p.stars or 0) for p in payments), sum(int(p.stars or 0) for p in prev_payments)), 2) if sum(int(p.stars or 0) for p in prev_payments) else None,
        },
        "top_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
        "note": "العائد متوسط مسجل من نتائج الرصد المتاحة؛ لا يشمل الإشارات التي لا تملك بيانات نتيجة كافية."
    }

@app.get("/api/admin/stars/balance")
async def admin_stars_balance(user=Depends(telegram_user)):
    await require_admin_permission(user, "payments")
    try:
        balance = await bot_api("getMyStarBalance", {})
        return {"ok": True, "balance": int((balance or {}).get("amount", 0)), "nanostar_amount": int((balance or {}).get("nanostar_amount", 0) or 0)}
    except Exception as exc:
        raise HTTPException(503, f"تعذر قراءة رصيد Telegram Stars: {exc}")

@app.get("/api/admin/stars/transactions")
async def admin_stars_transactions(user=Depends(telegram_user)):
    await require_admin_permission(user, "payments")
    try:
        result = await bot_api("getStarTransactions", {"offset": 0, "limit": 50})
        return {"ok": True, "transactions": result.get("transactions", []) if isinstance(result, dict) else []}
    except Exception as exc:
        raise HTTPException(503, f"تعذر قراءة عمليات Telegram Stars: {exc}")

@app.get("/api/admin/stars/withdrawal")
async def admin_stars_withdrawal(user=Depends(telegram_user)):
    await require_admin_permission(user, "payments")
    return {
        "ok": True,
        "automatic": False,
        "message": "السحب لا يتم مباشرة إلى محفظة TON من خلال Bot API. يبدأه مالك البوت من Telegram/Fragment، وبعدها يحدد محفظة TON في صفحة السحب.",
        "fragment_url": "https://fragment.com",
        "official_docs": "https://core.telegram.org/api/stars",
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

@app.get("/api/subscription/config")
async def subscription_config(user=Depends(telegram_user)):
    return await get_subscription_config()

@app.get("/api/admin/subscription-config")
async def admin_subscription_config(user=Depends(telegram_user)):
    await require_admin_permission(user, "settings")
    return await get_subscription_config()

@app.post("/api/admin/subscription-config")
async def admin_update_subscription_config(request: Request, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "settings")
    body = await request.json()
    paid_visible = bool(body.get("paid_plans_visible", True))
    trial_days = int(body.get("trial_days", 3))
    if trial_days < 1 or trial_days > 365:
        raise HTTPException(400, "مدة التجربة يجب أن تكون بين 1 و365 يومًا")
    await setting_set(db, "paid_plans_visible", "1" if paid_visible else "0")
    await setting_set(db, "trial_days", trial_days)
    await db.commit()
    await audit(int(user["id"]), "subscription_visibility_updated", None, {"paid_plans_visible": paid_visible, "trial_days": trial_days})
    return {"ok": True, **await get_subscription_config()}

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
            exp, channel_link = await grant_access(telegram_id, forever=True)
        else:
            try:
                n = int(days)
            except ValueError:
                raise HTTPException(400, "استخدم مدة صحيحة أو forever")
            if n <= 0 or n > 3650:
                raise HTTPException(400, "المدة يجب أن تكون بين 1 و3650 يومًا")
            exp, channel_link = await grant_access(telegram_id, days=n)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"تعذر تفعيل الاشتراك: {exc}")
    await audit(int(user["id"]), "grant_access_legacy", telegram_id, {"days": days})
    try:
        await send_message(
            telegram_id,
            "✅ <b>تم تفعيل اشتراك SAS PRO</b>\n\n"
            f"📅 تاريخ الانتهاء: <b>{exp.strftime('%d/%m/%Y')}</b>\n\n"
            "اضغط «انضمام للقناة» وسيتم قبول طلبك تلقائيًا.",
            {"inline_keyboard": [[{"text": "🚀 انضمام لقناة SAS PRO", "url": channel_link}]]},
        )
    except Exception:
        pass
    return {"ok": True, "expires_at": exp.isoformat(), "channel_link": channel_link}

@app.post("/api/admin/free-extend/{telegram_id}")
async def admin_free_extend(telegram_id: int, days: int = 3, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "subscriptions")
    if days < 1 or days > 365:
        raise HTTPException(400, "مدة التمديد المجاني يجب أن تكون بين 1 و365 يومًا")
    row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
    if not row:
        raise HTTPException(404, "المستخدم غير موجود")
    now = utcnow()
    active_paid = (await db.execute(select(Subscription).where(
        Subscription.telegram_id == telegram_id, Subscription.active == True
    ).order_by(Subscription.expires_at.desc()))).scalars().first()
    if active_paid and is_active(active_paid) and not row.free_access:
        raise HTTPException(400, "لدى المستخدم اشتراك مدفوع فعال")
    base = aware(row.trial_expires) if row.trial_expires and aware(row.trial_expires) > now else now
    row.trial_start = row.trial_start or now
    row.trial_expires = base + timedelta(days=days)
    row.trial_used_at = row.trial_used_at or now
    row.status = "trial"
    row.free_access = False
    row.updated_at = now
    await db.commit()
    try:
        await bot_api("unbanChatMember", {"chat_id": settings.telegram_channel_id, "user_id": telegram_id, "only_if_banned": True})
    except Exception:
        pass
    await audit(int(user["id"]), "free_access_extended", telegram_id, {"days": days, "expires_at": row.trial_expires.isoformat()})
    return {"ok": True, "expires_at": row.trial_expires.isoformat()}

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
            *([[{"text":"📱 فتح SAS PRO","web_app":{"url":webapp_url()}}]] if webapp_url() else [])
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

@app.get("/api/channel/access")
async def channel_access(user=Depends(require_pro)):
    try:
        link, expires = await create_user_channel_invite(int(user["id"]), "APP")
        return {"ok": True, "url": link, "invite_expires": expires.isoformat()}
    except Exception as exc:
        raise HTTPException(503, str(exc))

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
    session_date = status["date"]
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(RadarSignal)
            .where(RadarSignal.session_date == session_date)
            .order_by(RadarSignal.created_at.desc())
        )).scalars().all()

        # خارج الجلسة لا نخفي آخر رصد ناجح؛ نعرض آخر جلسة محفوظة بدل إظهار
        # شاشة فارغة وكأن الرادار لم يعمل.
        source_session = session_date
        if not rows:
            rows = (await db.execute(
                select(RadarSignal)
                .order_by(RadarSignal.created_at.desc())
                .limit(100)
            )).scalars().all()
            if rows:
                source_session = rows[0].session_date

    radar_stocks = []
    moves = []
    volumes = []
    seen = set()
    for row in rows:
        try:
            payload = json.loads(row.payload or "{}")
            payload.setdefault("symbol", row.symbol)
            payload.setdefault("created_at", row.created_at.isoformat() if row.created_at else None)
            if row.symbol not in seen:
                radar_stocks.append(payload)
                seen.add(row.symbol)
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
            "opportunities": len(radar_stocks),
            "watched": len(radar_stocks),
            "top_move_pct": max(moves) if moves else None,
            "top_volume": max(volumes) if volumes else None,
            "last_signal_at": rows[0].created_at.isoformat() if rows and rows[0].created_at else None,
            "source_session": source_session if rows else None,
            "historical": bool(rows and source_session != session_date),
            "stocks": radar_stocks[:20],
        },
        "updated_at": utcnow().isoformat(),
    }

@app.get("/api/radar/scan")
async def radar_scan(fresh: int = 0, _: dict = Depends(require_pro)):
    """إرجاع فرص الرادار المحفوظة بأمان؛ لا يسمح لفشل بيانات الرادار بإسقاط Mini App بالكامل."""
    try:
        from .scanner import scan_us_low_price_stocks
        status = market_status()
        app_limit = max(1, int(settings.radar_app_daily_limit or 15))
        session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        if not fresh:
            async with SessionLocal() as db:
                rows = (await db.execute(
                    select(RadarSignal)
                    .where(RadarSignal.session_date == session_date)
                    .order_by(RadarSignal.created_at.desc())
                )).scalars().all()

            stocks = []
            seen = set()
            for row in rows:
                try:
                    payload = json.loads(row.payload or "{}")
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                gate = payload.get("channel_gate") or {}
                if not gate.get("passed") or not payload.get("radar_active", False):
                    continue
                symbol = str(row.symbol or "").upper()
                if not symbol or symbol in seen:
                    continue
                payload.setdefault("symbol", symbol)
                stocks.append(payload)
                seen.add(symbol)

            def _num(value, default=0.0):
                try:
                    if isinstance(value, dict):
                        value = value.get("score")
                    return float(value or default)
                except (TypeError, ValueError):
                    return float(default)

            stocks.sort(
                key=lambda x: (
                    _num(x.get("quality_score")),
                    _num(x.get("opening_opportunity_score")),
                ),
                reverse=True,
            )
            latest = rows[0] if rows else None
            return {
                "enabled": stock_radar_enabled(),
                "historical": False,
                "persisted": True,
                "scan_at": latest.created_at.isoformat() if latest and latest.created_at else None,
                "session_date": session_date,
                "session": status.get("session", "unknown"),
                "stocks": stocks[:app_limit],
                "diagnostics": {
                    "candidates": 0,
                    "passed": len(stocks[:app_limit]),
                    "filtered": 0,
                    "errors": 0,
                    "daily_limit": app_limit,
                },
            }

        if not stock_radar_enabled():
            return {
                "enabled": False,
                "historical": True,
                "reason": status.get("label_ar", "السوق خارج جلسة الرصد"),
                "session": status.get("session", "unknown"),
                "scan_at": None,
                "session_date": session_date,
                "stocks": [],
                "diagnostics": {"candidates": 0, "passed": 0, "filtered": 0, "errors": 0, "daily_limit": app_limit},
            }

        result = await scan_us_low_price_stocks(force_refresh=True)
        return {
            "enabled": True,
            "historical": False,
            "persisted": False,
            "scan_at": datetime.now(timezone.utc).isoformat(),
            "range": {"min": 0.30, "max": 6.00},
            "method": "SAS PRO Radar",
            "session": status.get("session", "unknown"),
            "stocks": (result.get("stocks") or [])[:app_limit],
            "diagnostics": {**(result.get("diagnostics") or {}), "daily_limit": app_limit},
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Radar API failed")
        return {
            "enabled": False,
            "historical": False,
            "persisted": False,
            "stocks": [],
            "diagnostics": {
                "candidates": 0,
                "passed": 0,
                "filtered": 0,
                "errors": 1,
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:240],
            },
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:240],
            },
        }


@app.get("/api/stocks/{symbol}/chart")
async def stock_chart(symbol: str, _: dict = Depends(require_pro)):
    raw_symbol = symbol.upper().strip()
    if is_crypto_symbol(raw_symbol):
        try:
            pair = normalize_crypto_symbol(raw_symbol)
            data = await binance_candles(pair, interval="1h", limit=220)
            return {"symbol": pair, "candles": data[-90:], "available": len(data) >= 20, "source": "Binance Spot", "interval": "1h"}
        except Exception as exc:
            return {"symbol": normalize_crypto_symbol(raw_symbol), "candles": [], "available": False, "source": "Binance Spot", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    data = await ohlcv(raw_symbol, days=90, interval="1d")
    if isinstance(data, list):
        return {"symbol": raw_symbol, "candles": data, "available": len(data) >= 20, "source": "legacy"}
    return {"symbol": raw_symbol, **data}

@app.get("/api/stocks/{symbol}/news")
async def stock_news(symbol: str, _: dict = Depends(require_pro)):
    return await company_news(symbol.upper())

@app.get("/api/stocks/{symbol}/events")
async def stock_events(symbol: str, _: dict = Depends(require_pro)):
    return await corporate_events(symbol.upper())

@app.get("/api/stocks/{symbol}/quote")
async def stock_quote(symbol: str, _: dict = Depends(require_pro)):
    raw_symbol = symbol.upper().strip()
    if is_crypto_symbol(raw_symbol):
        try:
            return await binance_quote(raw_symbol)
        except Exception as exc:
            return {"symbol": normalize_crypto_symbol(raw_symbol), "price": None, "change_pct": None, "source": "Binance Spot", "market": "crypto_spot", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return await quote(raw_symbol)

@app.get("/api/stocks/{symbol}/shariah")
async def stock_shariah(symbol: str, _: dict = Depends(require_pro)):
    symbol = symbol.upper().strip()
    if is_crypto_symbol(symbol):
        return {
            "symbol": normalize_crypto_symbol(symbol),
            "status": "not_applicable",
            "status_ar": "غير منطبق — أصل رقمي",
            "verified": False,
            "message": "التحقق الشرعي الخاص بالأسهم لا ينطبق على الأصول الرقمية.",
            "sources": [],
        }
    try:
        return await check_shariah(symbol)
    except Exception as exc:
        return {"symbol": symbol, "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق",
                "verified": False, "message": "تعذر التحقق الآن؛ لم يتم تأليف أي نتيجة.",
                "sources": [], "error": type(exc).__name__}

@app.post("/api/stocks/{symbol}/analyze")
async def stock_analyze(symbol: str, user=Depends(require_pro), db: AsyncSession = Depends(get_session)):
    """تحليل Mini App متعدد الطبقات، مع مسار حي مستقل لـ Binance Spot للأصول الرقمية."""
    symbol = symbol.upper().strip()

    if is_crypto_symbol(symbol):
        pair = normalize_crypto_symbol(symbol)
        try:
            crypto = await asyncio.wait_for(binance_analyze(pair, interval="1h"), timeout=15.0)
            q = await asyncio.wait_for(binance_quote(pair), timeout=8.0)
            crypto["change_pct"] = q.get("change_pct")
            crypto["quote_volume"] = q.get("quote_volume")
            crypto["high_24h"] = q.get("high_24h")
            crypto["low_24h"] = q.get("low_24h")
        except Exception as exc:
            return {
                "ok": True, "symbol": pair, "partial": True,
                "quote": {"symbol": pair, "price": None, "change_pct": None, "source": "Binance Spot", "market": "crypto_spot", "error": f"{type(exc).__name__}: {str(exc)[:180]}"},
                "mini_analysis": {"direction": "غير متاح", "momentum": "غير متاح", "liquidity": "غير متاحة", "signal": "غير متاحة", "entry": None, "stop": None, "target": None, "rvol": None, "takeaway": "تعذر الحصول على بيانات Binance Spot الحية؛ لم يتم تخمين أي سعر أو مستوى."},
                "technical_analysis": {}, "sas_pro": {"targets": {"status": "error", "targets": [], "shariah": {"status": "not_applicable", "status_ar": "غير منطبق — أصل رقمي", "verified": False, "message": "مسار الشرعية الخاص بالأسهم لا يطبق على هذا الأصل.", "sources": []}}, "classification": {}, "report": None, "disclaimer": DISCLAIMER}
            }

        target_list = crypto.get("targets") or []
        entry, stop = crypto.get("entry") or crypto.get("price"), crypto.get("stop")
        target1 = target_list[0] if target_list else None
        rr = None
        try:
            if entry and stop and target1 and float(entry) > float(stop):
                rr = round((float(target1) - float(entry)) / (float(entry) - float(stop)), 2)
        except Exception:
            rr = None

        score = int(crypto.get("score") or 0)
        mini = {
            "direction": "صاعد" if crypto.get("confirmation", {}).get("trend") else "غير مؤكد",
            "momentum": "قوي" if score >= 75 else ("متوسط" if score >= 50 else "ضعيف"),
            "liquidity": "مرتفعة" if (crypto.get("rvol") or 0) >= 2 else ("طبيعية" if (crypto.get("rvol") or 0) >= 1.2 else "منخفضة"),
            "signal": crypto.get("state") or "غير متاحة", "entry": entry, "stop": stop, "target": target1,
            "rvol": crypto.get("rvol"), "takeaway": crypto.get("takeaway") or "لا توجد خلاصة كافية."
        }
        targets = {
            "status": "ok" if target_list else "watch", "price": entry, "exit": stop,
            "support": crypto.get("support"), "resistance": crypto.get("resistance"),
            "targets": target_list, "target1": target1, "risk_reward": rr,
            "risk_reward_warning": rr is None or rr < 1.5, "atr": crypto.get("atr14"),
            "source": "Binance Spot", "interval": crypto.get("interval"),
            "indicators": {k: crypto.get(k) for k in ("ema20","ema50","ema200","rsi14","adx14","macd","macd_signal","macd_hist","atr14","atr_pct")},
            "shariah": {"status": "not_applicable", "status_ar": "غير منطبق — أصل رقمي", "verified": False, "message": "هذا تحليل أصل رقمي وليس سهمًا أمريكيًا؛ لم يتم تطبيق بوابة الشرعية الخاصة بالأسهم.", "sources": []}
        }
        classification = {
            "pass": score >= 60, "score": score, "type": "crypto_spot",
            "behavior": crypto.get("state") or "غير واضح", "rvol": crypto.get("rvol"), "rsi14": crypto.get("rsi14"),
            "risk_level": "مرتفع" if (crypto.get("atr_pct") or 0) >= 5 else "متوسط",
            "breakout_confirmed": bool(crypto.get("confirmation", {}).get("breakout") and crypto.get("confirmation", {}).get("volume")),
            "reason": crypto.get("takeaway"), "score_breakdown": {}
        }
        payload = {
            "analysis": {"enabled": False, "ai_available": False, "key_takeaway": crypto.get("takeaway"), "fallback_type": "binance_spot_technical"},
            "mini_analysis": mini, "technical_analysis": crypto, "quote": q,
            "sas_pro": {"targets": targets, "classification": classification, "report": None, "disclaimer": DISCLAIMER},
            "news": [], "fundamentals": {}, "ai_status": "not_applicable", "partial": False,
            "crypto": True, "source": "Binance Spot", "symbol": pair,
            "chart": {"candles": crypto.get("candles") or [], "interval": "1h"}
        }
        _cache_put(_analysis_memory, pair, payload)
        _cache_put(_quick_scan_memory, pair, {"quote": q, "mini_analysis": mini})
        return payload

    cached = _cache_get(_analysis_memory, symbol, _ANALYSIS_CACHE_TTL)
    if cached is not None:
        try:
            live_quote = await quote(symbol)
            if isinstance(live_quote, dict) and live_quote.get("price"):
                cached["quote"] = live_quote
        except Exception:
            pass
        if True:  # تحديث نتيجة يقين في كل تحليل لمنع عرض حكم شرعي قديم من الكاش.
            try:
                cached.setdefault("sas_pro", {}).setdefault("targets", {})["shariah"] = await asyncio.wait_for(check_shariah(symbol), timeout=8.0)
            except Exception:
                cached.setdefault("sas_pro", {}).setdefault("targets", {})["shariah"] = {
                    "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق",
                    "verified": False, "message": "تعذر التحقق الآن؛ لم يتم تأليف أي نتيجة.", "sources": []
                }
        return cached

    # الشرعية طبقة مستقلة وسريعة؛ فشلها لا يعطل التحليل الفني.
    try:
        shariah_result = await asyncio.wait_for(check_shariah(symbol), timeout=10.0)
    except Exception as exc:
        shariah_result = {"symbol": symbol, "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق",
                          "verified": False, "message": "تعذر التحقق الآن؛ لم يتم تأليف أي نتيجة.",
                          "sources": [], "error": type(exc).__name__}

    # PanWatch's optional agent can take longer than the Mini App request window.
    # Keep the SAS/targets/quote path independent: a slow external agent must never
    # turn a valid technical analysis into a timeout.
    async def _optional_agent_analysis():
        try:
            return await asyncio.wait_for(analyze(symbol), timeout=6.0)
        except Exception as exc:
            return exc

    results = await asyncio.gather(
        _optional_agent_analysis(),
        technical_targets(symbol),
        quote(symbol),
        return_exceptions=True,
    )
    result, targets, q = results

    if isinstance(targets, Exception) or not isinstance(targets, dict):
        targets = {"status": "error", "targets": [], "error": (
            f"{type(targets).__name__}: {targets}" if isinstance(targets, Exception)
            else "technical_targets returned an invalid payload"
        )}
    if isinstance(q, Exception) or not isinstance(q, dict):
        q = {"symbol": symbol, "price": None, "change_pct": None, "source": "unavailable",
             "error": f"{type(q).__name__}: {q}"}

    # الأخبار والأساسيات تُجمع دائمًا؛ غيابها لا يمنع التحليل الفني.
    news, fundamentals, tipranks_data = await asyncio.gather(
        company_news(symbol, days=3),
        company_fundamentals(symbol),
        tipranks_analysis(symbol),
        return_exceptions=True,
    )
    if isinstance(news, Exception):
        news = []
    if isinstance(fundamentals, Exception):
        fundamentals = {}
    if isinstance(tipranks_data, Exception) or not isinstance(tipranks_data, dict):
        tipranks_data = {}

    # TipRanks مصدر تحليلي إضافي؛ AI يترجمه ويختصره دون تحويله إلى هدف أو قرار SAS PRO.
    if tipranks_data:
        tipranks_data = {**tipranks_data, "summary": "", "signal": "غير واضح"}
    
    # طبقة AI تفسيرية فقط. إذا لم توجد أخبار موثقة فلا نختلق تفسيرًا.
    ai_result = {}
    try:
        from .ai_radar import analyze_stock
        ai_result = await analyze_stock(
            symbol,
            company=fundamentals,
            news=news,
            fundamentals=fundamentals,
            market={"change_pct": q.get("change_pct"), "price": q.get("price")},
            tipranks=tipranks_data,
        )
    except Exception as exc:
        ai_result = {"enabled": False, "status": "provider_error", "error": str(exc)[:300]}

    # إذا كانت طبقة التحليل الفني الخارجية فارغة، نحتفظ بها كبيانات إضافية
    # ولا نجعلها شرطًا لعرض السعر/المستويات/الخلاصة الفنية.
    if isinstance(result, Exception):
        result = {}
    if not isinstance(result, dict):
        result = {}

    try:
        # استخدم نفس محرك التصنيف الموجود في التحليل الخاص حتى تكون
        # خلاصة Mini App متطابقة مع منطق SAS PRO في الخاص.
        from .scanner import classify_sas
        classification = await classify_sas(symbol, q, allow_twelve_fallback=True)
        if isinstance(classification, Exception):
            classification = {}
    except Exception:
        classification = {}

    # FCC reviewer: طبقة مراجعة اختيارية بعد اجتياز SAS، وليست بوابة للرادار.
    fcc_review = {"available": False, "status": "not_called"}
    try:
        if settings.fcc_reviewer_enabled and isinstance(classification, dict) and classification.get("pass"):
            evidence = {
                "classification": {
                    "pass": classification.get("pass"),
                    "score": classification.get("score"),
                    "type": classification.get("type"),
                    "behavior": classification.get("behavior"),
                    "reason": classification.get("reason"),
                    "rvol": classification.get("rvol"),
                    "risk_level": classification.get("risk_level"),
                },
                "technical": {
                    "status": targets.get("status"),
                    "targets": targets.get("targets") or [],
                    "exit": targets.get("exit"),
                    "support": targets.get("support"),
                    "resistance": targets.get("resistance"),
                    "atr": targets.get("atr"),
                },
                "verified_news": [
                    {
                        "headline": item.get("headline"),
                        "source": item.get("source"),
                        "url": item.get("url"),
                    }
                    for item in (news or [])[:5]
                    if isinstance(item, dict) and item.get("headline")
                ],
            }
            fcc_review = await review_stock(symbol, evidence)
    except Exception:
        fcc_review = {"available": False, "status": "provider_error"}

    targets["fcc_review"] = fcc_review

    # خلاصة فنية محلية مبنية فقط على البيانات الموجودة، بدون اختراع خبر أو سعر.
    # If the quote provider failed but OHLCV analysis produced a valid last close,
    # promote that observed close so the Mini App never shows a false "no data" state.
    if not q.get("price") and targets.get("price"):
        q["price"] = targets.get("price")
        q["source"] = q.get("source") if q.get("source") not in {None, "unavailable"} else "OHLCV analysis"
        q.pop("error", None)

    entry = targets.get("price") or q.get("price")
    stop = targets.get("exit") or targets.get("stop")
    target_list = targets.get("targets") if isinstance(targets, dict) else []
    target1 = target_list[0] if isinstance(target_list, list) and target_list else targets.get("target1")
    technical_summary = "لا توجد خلاصة فنية كافية."
    try:
        if entry and stop and target1:
            risk = float(entry) - float(stop)
            reward = float(target1) - float(entry)
            rr = round(reward / risk, 2) if risk > 0 else None
            if rr is not None:
                technical_summary = (
                    f"السعر المرجعي {float(entry):.4g}، الوقف {float(stop):.4g}، "
                    f"والهدف الأول {float(target1):.4g}. "
                    f"نسبة R:R المحسوبة للهدف الأول هي 1 : {rr:.2f}."
                )
        elif q.get("change_pct") is not None:
            technical_summary = f"التغير الحالي المسجل: {float(q.get('change_pct')):+.2f}%. راجع المستويات الفنية قبل اتخاذ أي قرار."
    except Exception:
        pass

    # الواجهة تعرض AI إن توفر، وإلا تعرض الخلاصة الفنية بدل شاشة فارغة.
    analysis_payload = dict(ai_result) if isinstance(ai_result, dict) else {}
    if not analysis_payload.get("key_takeaway"):
        analysis_payload["key_takeaway"] = technical_summary
        analysis_payload["fallback_type"] = "technical"
    analysis_payload["ai_available"] = bool(ai_result.get("enabled")) if isinstance(ai_result, dict) else False
    analysis_payload["fcc_review"] = fcc_review
    if isinstance(tipranks_data, dict) and tipranks_data:
        tipranks_data["summary"] = analysis_payload.get("tipranks_summary") or "غير متوفر"
        tipranks_data["signal"] = analysis_payload.get("tipranks_signal") or "غير واضح"
        targets["tipranks_analysis"] = tipranks_data
    targets["ai_analysis"] = analysis_payload
    targets["shariah"] = shariah_result

    try:
        report = build_report(symbol, q, targets, classification)
    except Exception:
        report = None

    # خلاصة قصيرة جدًا للواجهة؛ التفاصيل الكاملة تبقى في الخاص.
    cls = classification if isinstance(classification, dict) else {}
    target_list = targets.get("targets") if isinstance(targets, dict) else []
    target_list = target_list if isinstance(target_list, list) else []
    entry_value = targets.get("price") or q.get("price")
    stop_value = targets.get("exit") or targets.get("stop") or targets.get("support")
    target_value = target_list[0] if target_list else targets.get("target1")
    rvol_value = cls.get("rvol")
    try:
        rvol_num = float(rvol_value) if rvol_value is not None else None
    except (TypeError, ValueError):
        rvol_num = None

    behavior_value = str(cls.get("behavior") or "غير واضح")
    # هذه القيم تُشتق من نفس classify_sas المستخدم في الرادار، وليست حسابًا
    # منفصلًا من واجهة Mini App.
    if cls.get("market_structure_bearish") or cls.get("bearish_head_shoulders"):
        signal_value = "سلبية"
    elif cls.get("breakout_confirmed"):
        signal_value = "اختراق مؤكد"
    elif cls.get("strategy_pass"):
        signal_value = "إيجابية"
    elif cls.get("accumulation"):
        signal_value = "تجميع"
    else:
        signal_value = "غير متاحة" if not cls.get("rsi14") and cls.get("rvol") is None else "محايدة"

    momentum_score = (cls.get("score_breakdown") or {}).get("momentum", 0)
    volume_score = (cls.get("score_breakdown") or {}).get("volume", 0)
    if momentum_score >= 20 and volume_score >= 15:
        momentum_value = "قوي"
    elif momentum_score >= 10 or volume_score >= 15:
        momentum_value = "متوسط"
    elif cls.get("rsi14") is not None or rvol_num is not None:
        momentum_value = "ضعيف"
    else:
        momentum_value = "غير متاح"

    if rvol_num is not None and rvol_num >= 2:
        liquidity_value = "مرتفعة"
    elif rvol_num is not None and rvol_num >= 1.2:
        liquidity_value = "طبيعية"
    elif rvol_num is not None:
        liquidity_value = "منخفضة"
    else:
        liquidity_value = "غير متاحة"

    if signal_value == "اختراق مؤكد":
        mini_takeaway = "اختراق مؤكد وفق شروط SAS PRO: إغلاق + حجم + استمرار، مع عدم وجود إشارة اختراق وهمي."
    elif signal_value == "إيجابية":
        mini_takeaway = "الإشارة اجتازت شروط SAS PRO الحالية؛ المستويات المعروضة مأخوذة من محرك الرادار فقط."
    elif signal_value == "تجميع":
        mini_takeaway = "السهم في حالة تجميع وفق شروط SAS PRO؛ لا يوجد مستوى مختلق."
    elif signal_value == "سلبية":
        mini_takeaway = "البنية الفنية الحالية سلبية وفق محرك SAS PRO؛ لا يتم اختراع مستويات دخول."
    else:
        mini_takeaway = "البيانات الفنية غير مكتملة؛ لا يمكن اعتماد إشارة أو هدف أو وقف حاليًا."

    mini_analysis = {
        "direction": behavior_value,
        "momentum": momentum_value,
        "liquidity": liquidity_value,
        "signal": signal_value,
        "entry": entry_value,
        "stop": stop_value,
        "target": target_value,
        "rvol": rvol_num,
        "takeaway": mini_takeaway,
    }

    payload = {
        "analysis": analysis_payload,
        "mini_analysis": mini_analysis,
        "technical_analysis": result,
        "quote": q,
        "sas_pro": {
            "targets": targets,
            "classification": classification,
            "report": report,
            "disclaimer": DISCLAIMER,
        },
        "news": news if isinstance(news, list) else [],
        "fundamentals": fundamentals if isinstance(fundamentals, dict) else {},
        "ai_status": ai_result.get("status") if isinstance(ai_result, dict) else "unavailable",
        "partial": bool(
            (isinstance(targets, dict) and targets.get("error") and not targets.get("targets"))
            or (isinstance(q, dict) and q.get("error") and not q.get("price"))
        ),
    }

    try:
        db.add(StockAnalysis(
            telegram_id=user["id"],
            symbol=symbol,
            payload=json.dumps(payload, ensure_ascii=False),
        ))
        await db.commit()
    except Exception:
        await db.rollback()

    _cache_put(_analysis_memory, symbol, payload)
    _cache_put(_quick_scan_memory, symbol, {
        "quote": payload.get("quote") or {},
        "mini_analysis": payload.get("mini_analysis") or {},
    })
    return payload

@app.get("/api/stocks/{symbol}/mini-analysis")
async def stock_mini_analysis(symbol: str, _: dict = Depends(require_pro)):
    """تحليل SAS PRO مختصر سريع مع ذاكرة مؤقتة، ويستخدم Binance Spot للأصول الرقمية."""
    symbol = symbol.upper().strip()

    if is_crypto_symbol(symbol):
        pair = normalize_crypto_symbol(symbol)
        cached = _cache_get(_quick_scan_memory, pair, _QUICK_SCAN_CACHE_TTL)
        if cached is not None:
            try:
                q_live = await binance_quote(pair)
            except Exception:
                q_live = None
            return {"ok": True, "symbol": pair, "quote": q_live if isinstance(q_live, dict) and q_live.get("price") else cached.get("quote", {}), "mini_analysis": cached.get("mini_analysis", {}), "cached": True, "crypto": True, "source": "Binance Spot"}
        try:
            data = await asyncio.wait_for(binance_analyze(pair, interval="1h"), timeout=15.0)
            q = await asyncio.wait_for(binance_quote(pair), timeout=8.0)
            score = int(data.get("score") or 0)
            targets = data.get("targets") or []
            mini = {
                "direction": "صاعد" if data.get("confirmation", {}).get("trend") else "غير مؤكد",
                "momentum": "قوي" if score >= 75 else ("متوسط" if score >= 50 else "ضعيف"),
                "liquidity": "مرتفعة" if (data.get("rvol") or 0) >= 2 else ("طبيعية" if (data.get("rvol") or 0) >= 1.2 else "منخفضة"),
                "signal": data.get("state") or "غير متاحة", "entry": data.get("entry"), "stop": data.get("stop"),
                "target": targets[0] if targets else None, "rvol": data.get("rvol"), "takeaway": data.get("takeaway") or "لا توجد خلاصة كافية."
            }
            payload = {"ok": True, "symbol": pair, "quote": q, "mini_analysis": mini, "cached": False, "crypto": True, "source": "Binance Spot"}
            _cache_put(_quick_scan_memory, pair, {"quote": q, "mini_analysis": mini})
            return payload
        except Exception as exc:
            return {"ok": True, "symbol": pair, "quote": {"symbol": pair, "price": None, "change_pct": None, "source": "Binance Spot", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}, "mini_analysis": {"direction": "غير متاح", "momentum": "غير متاح", "liquidity": "غير متاحة", "signal": "غير متاحة", "entry": None, "stop": None, "target": None, "rvol": None, "takeaway": "تعذر الحصول على بيانات Binance Spot الحية؛ لم يتم التخمين."}, "crypto": True, "source": "Binance Spot"}

    cached = _cache_get(_quick_scan_memory, symbol, _QUICK_SCAN_CACHE_TTL)
    if cached is not None:
        try:
            q_live = await quote(symbol)
        except Exception:
            q_live = None
        return {
            "ok": True,
            "symbol": symbol,
            "quote": q_live if isinstance(q_live, dict) and q_live.get("price") else cached.get("quote", {}),
            "mini_analysis": cached.get("mini_analysis", cached),
            "cached": True,
        }

    full_cached = _cache_get(_analysis_memory, symbol, _ANALYSIS_CACHE_TTL)
    if full_cached is not None:
        mini = full_cached.get("mini_analysis") or {}
        return {
            "ok": True,
            "symbol": symbol,
            "quote": full_cached.get("quote") or {},
            "mini_analysis": mini,
            "classification": full_cached.get("sas_pro", {}).get("classification", {}),
            "targets": full_cached.get("sas_pro", {}).get("targets", {}),
            "cached": True,
        }
    try:
        q = await quote(symbol)
    except Exception:
        q = {"symbol": symbol, "price": None, "change_pct": None, "source": "unavailable"}

    try:
        from .scanner import classify_sas
        cls = await asyncio.wait_for(
            classify_sas(symbol, q, allow_twelve_fallback=True),
            timeout=25.0,
        )
    except Exception as exc:
        cls = {"pass": False, "behavior": "غير واضح", "error": str(exc)[:200]}

    if not isinstance(cls, dict):
        cls = {"pass": False, "behavior": "غير واضح"}

    try:
        targets = await asyncio.wait_for(technical_targets(symbol), timeout=15.0)
    except Exception:
        targets = {}

    target_list = targets.get("targets") if isinstance(targets, dict) else []
    target_list = target_list if isinstance(target_list, list) else []
    entry = targets.get("price") or q.get("price")
    stop = targets.get("exit") or targets.get("stop") or targets.get("support")
    target = target_list[0] if target_list else targets.get("target1")

    behavior = str(cls.get("behavior") or "غير واضح")
    if cls.get("market_structure_bearish") or cls.get("bearish_head_shoulders"):
        signal = "سلبية"
    elif cls.get("breakout_confirmed"):
        signal = "اختراق مؤكد"
    elif cls.get("strategy_pass"):
        signal = "إيجابية"
    elif cls.get("accumulation"):
        signal = "تجميع"
    else:
        signal = "غير متاحة" if not cls.get("rsi14") and cls.get("rvol") is None else "محايدة"

    breakdown = cls.get("score_breakdown") or {}
    momentum_score = breakdown.get("momentum", 0)
    volume_score = breakdown.get("volume", 0)
    rvol = cls.get("rvol")
    if momentum_score >= 20 and volume_score >= 15:
        momentum = "قوي"
    elif momentum_score >= 10 or volume_score >= 15:
        momentum = "متوسط"
    elif cls.get("rsi14") is not None or rvol is not None:
        momentum = "ضعيف"
    else:
        momentum = "غير متاح"

    try:
        rvol_num = float(rvol) if rvol is not None else None
    except (TypeError, ValueError):
        rvol_num = None
    liquidity = (
        "مرتفعة" if rvol_num is not None and rvol_num >= 2
        else "طبيعية" if rvol_num is not None and rvol_num >= 1.2
        else "منخفضة" if rvol_num is not None
        else "غير متاحة"
    )

    if signal == "اختراق مؤكد":
        takeaway = "اختراق مؤكد وفق شروط SAS PRO."
    elif signal == "إيجابية":
        takeaway = "الإشارة اجتازت شروط SAS PRO الحالية."
    elif signal == "تجميع":
        takeaway = "السهم في حالة تجميع وفق شروط SAS PRO."
    elif signal == "سلبية":
        takeaway = "البنية الفنية الحالية سلبية وفق محرك SAS PRO."
    else:
        takeaway = "البيانات الفنية غير مكتملة؛ لا يمكن اعتماد إشارة أو هدف أو وقف حاليًا."

    response = {
        "ok": True,
        "symbol": symbol,
        "quote": q,
        "mini_analysis": {
            "direction": behavior,
            "momentum": momentum,
            "liquidity": liquidity,
            "signal": signal,
            "entry": entry,
            "stop": stop,
            "target": target,
            "rvol": rvol_num,
            "takeaway": takeaway,
        },
        "classification": cls,
        "targets": targets,
        "cached": False,
    }
    _cache_put(_quick_scan_memory, symbol, response)
    return response

@app.get("/api/stocks/{symbol}/private-link")
async def stock_private_link(symbol: str, _: dict = Depends(require_pro)):
    """رابط التحليل الكامل في الخاص مع تمرير رمز السهم تلقائيًا."""
    symbol = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", symbol):
        raise HTTPException(400, "رمز سهم غير صالح")
    try:
        bot = await bot_api("getMe", {})
        username = str(bot.get("username") or "").strip().lstrip("@")
    except Exception as exc:
        raise HTTPException(503, f"تعذر تجهيز رابط التحليل الخاص: {exc}")
    if not username:
        raise HTTPException(503, "لم يتم العثور على اسم مستخدم البوت")
    return {
        "ok": True,
        "symbol": symbol,
        "url": f"https://t.me/{username}?start=analysis_{symbol}",
    }


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

async def process_telegram_update(data: dict):
    """Process one Telegram update from either webhook or long polling."""
    message_probe = data.get("message") or {}
    probe_text = str(message_probe.get("text") or "").strip()
    probe_sender = message_probe.get("from") or {}
    probe_chat = message_probe.get("chat") or {}
    logger.warning(
        "Telegram update received: update_id=%s chat_id=%s chat_type=%s user_id=%s text=%r",
        data.get("update_id"),
        probe_chat.get("id"),
        probe_chat.get("type"),
        probe_sender.get("id"),
        probe_text[:80],
    )

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
                active = bool(row and (row.free_access or (row.trial_expires and aware(row.trial_expires) > now) or is_active(sub)))
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

    # Deep link من Mini App: /start analysis_SYMBOL
    # نحوله إلى نفس مسار التحليل الخاص، مع الحفاظ على الاشتراك والصلاحيات.
    deep_analysis = re.fullmatch(
        r"/start(?:@\w+)?\s+analysis_([A-Za-z]{1,5}(?:\.[A-Za-z])?)",
        text,
        re.IGNORECASE,
    )
    if deep_analysis:
        text = deep_analysis.group(1).upper()

    # تحليل الأسهم الخاص: أرسل رمزًا واضحًا مثل AAPL في الخاص فقط.
    # لا نعترض الأوامر أو الرسائل العامة أو الرموز غير الصالحة.
    chat_type = str((message.get("chat") or {}).get("type") or "")
    symbol_match = re.fullmatch(r"\$?([A-Za-z]{1,5}(?:\.[A-Za-z])?)", text)
    if chat_type == "private" and symbol_match and not text.startswith("/"):
        symbol = symbol_match.group(1).upper()
        async with SessionLocal() as db:
            row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
            now = utcnow()
            active_sub = (await db.execute(select(Subscription).where(
                Subscription.telegram_id == telegram_id,
                Subscription.active == True,
            ).order_by(Subscription.expires_at.desc()))).scalars().first()
            pro_active = bool(
                telegram_id == settings.owner_telegram_id
                or (row and row.free_access)
                or (row and row.status == "trial" and row.trial_expires and aware(row.trial_expires) > now)
                or (row and row.status == "active" and row.subscription_expires and aware(row.subscription_expires) > now and active_sub and is_active(active_sub))
            )
        if not pro_active:
            await send_message(chat_id,
                "🔒 <b>تحليل الأسهم الخاص متاح لمشتركي SAS PRO.</b>\n\n"
                "افتح Mini App لتفعيل التجربة أو الاشتراك، ثم أرسل رمز السهم مثل <code>AAPL</code> هنا.")
            return {"ok": True}
        # لا ننفذ التحليل الثقيل داخل طلب Telegram نفسه؛ يجب أن نعيد 200 بسرعة
        # حتى لا يعيد Telegram إرسال نفس الرسالة بسبب تأخر مزودي البيانات.
        async def _safe_send(text_to_send: str):
            try:
                await send_message(chat_id, text_to_send)
                logger.info("Telegram private response sent: chat_id=%s symbol=%s", chat_id, symbol)
            except Exception as exc:
                logger.exception("Telegram sendMessage failed: chat_id=%s symbol=%s error=%s", chat_id, symbol, exc)

        # Return from the webhook immediately; Telegram must not wait for
        # external APIs, database work, or the private-analysis pipeline.
        await _safe_send(
            f"🔎 <b>تم استلام {html.escape(symbol)}</b>\nجارٍ تجهيز التحليل الكامل وإرساله هنا..."
        )

        async def _run_private_analysis():
            from .private_analysis import build_private_analysis
            last_exc = None
            for attempt in range(3):
                try:
                    logger.warning("Private analysis started: symbol=%s attempt=%s", symbol, attempt + 1)
                    report = await asyncio.wait_for(build_private_analysis(symbol), timeout=75)
                    await _safe_send(report)
                    logger.warning("Private analysis completed: symbol=%s attempt=%s", symbol, attempt + 1)
                    return
                except asyncio.TimeoutError as exc:
                    last_exc = exc
                    logger.error("Private stock analysis timeout: symbol=%s attempt=%s", symbol, attempt + 1)
                except Exception as exc:
                    last_exc = exc
                    logger.exception("Private stock analysis failed: symbol=%s attempt=%s error=%s", symbol, attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
            try:
                raise last_exc
            except Exception:
                await _safe_send(
                    "❌ تعذر إكمال التحليل الكامل حاليًا بعد 3 محاولات.\n"
                    "تم تسجيل الخطأ تلقائيًا؛ أرسل رمز السهم مرة أخرى بعد قليل."
                )

        global private_analysis_task
        private_analysis_task = asyncio.create_task(
            _run_private_analysis(),
            name=f"saspro-private-analysis-{symbol}",
        )
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
                    await send_message(target, "🚀 <b>تم تفعيل SAS PRO</b>\n\nرابط الدخول:", {"inline_keyboard":[[{"text":"🚀 دخول إلى SAS PRO","url":link}]]})
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
        if webapp_url():
            kb = {"inline_keyboard": [[{"text": "🔐 فتح SAS PRO وطلب إذن الدخول", "web_app": {"url": webapp_url()}}]]}
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
                {"inline_keyboard": [[{"text": "🛠️ فتح لوحة الإدارة", "web_app": {"url": webapp_url()}}]]},
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
                "🚀 <b>SAS PRO — 🇺🇲 سوق الأسهم الأمريكية</b>"
            )
        await send_message(
            chat_id,
            msg,
            {"inline_keyboard": [[{"text": "🚀 دخول إلى SAS PRO", "web_app": {"url": webapp_url()}}]]},
        )
        return {"ok": True}

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    expected = settings.telegram_webhook_secret
    if expected and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
        raise HTTPException(403, "Invalid Telegram webhook secret")
    data = await request.json()
    return await process_telegram_update(data)


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
            "🚀 اضغط «انضمام للقناة» وسيتم قبول طلبك تلقائيًا لأن اشتراكك فعال.",
            {"inline_keyboard": [[{"text": "🚀 انضمام لقناة SAS PRO", "url": result["channel_link"]}], [{"text": "📱 فتح SAS PRO", "web_app": {"url": webapp_url()}}]]},
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
    return {"ok": True, "expires_at": exp.isoformat(), "channel_link": result["channel_link"]}


@app.post("/api/admin/plan-visibility/{plan}")
async def admin_plan_visibility(plan: str, request: Request, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "settings")
    if plan not in ("monthly", "3month", "6month", "yearly"):
        raise HTTPException(400, "الباقة غير معروفة")
    body = await request.json()
    await setting_set(db, f"plan_{plan}_visible", "1" if bool(body.get("visible")) else "0")
    await db.commit()
    return {"ok": True, "plan": plan, "visible": bool(body.get("visible"))}