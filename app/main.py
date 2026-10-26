import asyncio
import copy
import math
import logging
import time
import re
import json
import base64
import hashlib
import hmac
import html
import os
from pathlib import Path
import httpx
from urllib.parse import urlsplit, urljoin
from html.parser import HTMLParser
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import SessionLocal, User, Subscription, Payment, StockAnalysis, RadarSignal, RadarRun, RadarOutcome, AccessRequest, Setting, Invite, AdminRole, get_session, init_db
from .telegram import validate_init_data, send_message, bot_api
from .market import quote, ticker
from .panwatch import analyze, technical_targets, ohlcv
from .news import company_news, company_fundamentals, corporate_events, tipranks_analysis, market_news, _is_relevant_stock_market_news, _newsfilter_market_news
from .news_translation import translate_stock_news, _mymemory_translate
from .scheduler import scheduler
from .market_calendar import market_status, us_market_holidays
from .holiday_radar import stock_radar_enabled
from .radar_health import radar_health_monitor
from .radar_lifecycle import is_current_active_opportunity
from .timeutil import utcnow, aware
from .subscriptions import TERMS_VERSION, TERMS_TEXT, get_plans, get_subscription_config, setting_set, setting_get, start_trial_for_user, create_invoice_for_user, apply_successful_payment, grant_access, active_subscription, ensure_subscription_settings, create_user_channel_invite
from .admin import PERMISSIONS, ROLE_DEFAULTS, get_admin, has_permission, audit
from .fcc_reviewer import review_stock
from .shariah import check_shariah
from .smart_memory import SmartMemory
from .binance_spot import is_crypto_symbol, normalize_symbol as normalize_crypto_symbol, quote as binance_quote, analyze as binance_analyze, candles as binance_candles

# Short quote cache plus single-flight locks: concurrent users requesting
# the same symbol share one provider refresh instead of multiplying API calls.
_SESSION_QUOTE_CACHE = {}
_SESSION_QUOTE_CACHE_TTL = 10
_SESSION_QUOTE_LOCKS = {}

async def _quote_for_current_session(symbol: str):
    status = market_status()
    session = str(status.get("session") or "closed")
    prefer_extended = session in {"premarket", "afterhours", "night"}
    key = (str(symbol or "").upper().strip(), session)

    def cached_quote():
        cached = _SESSION_QUOTE_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _SESSION_QUOTE_CACHE_TTL:
            return dict(cached[1])
        return None

    cached = cached_quote()
    if cached is not None:
        return cached

    lock = _SESSION_QUOTE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_QUOTE_LOCKS[key] = lock

    async with lock:
        # Another request may have refreshed the cache while we waited.
        cached = cached_quote()
        if cached is not None:
            return cached

        result = None
        # Keep the existing provider fallback/retry behavior unchanged.
        for attempt in range(2):
            try:
                candidate = await quote(key[0], prefer_extended=prefer_extended)
                if isinstance(candidate, dict) and candidate.get("price"):
                    result = candidate
                    break
                result = candidate
                logger.warning(
                    "STOCK_QUOTE_RESCAN symbol=%s session=%s attempt=%s reason=no_valid_price",
                    key[0], session, attempt + 1,
                )
            except Exception as exc:
                logger.warning(
                    "STOCK_QUOTE_RESCAN symbol=%s session=%s attempt=%s error=%s",
                    key[0], session, attempt + 1, type(exc).__name__,
                )
            if attempt == 0:
                await asyncio.sleep(0.35)

        if isinstance(result, dict) and result.get("price"):
            _SESSION_QUOTE_CACHE[key] = (time.monotonic(), dict(result))
            if len(_SESSION_QUOTE_CACHE) > 256:
                for old_key in list(_SESSION_QUOTE_CACHE)[:128]:
                    _SESSION_QUOTE_CACHE.pop(old_key, None)
        # Keep lock memory bounded too.
        if len(_SESSION_QUOTE_LOCKS) > 256:
            for old_key in list(_SESSION_QUOTE_LOCKS)[:128]:
                if old_key != key and not _SESSION_QUOTE_LOCKS[old_key].locked():
                    _SESSION_QUOTE_LOCKS.pop(old_key, None)
        return result


logger = logging.getLogger(__name__)

scheduler_task = None
telegram_polling_task = None
telegram_config_task = None
private_analysis_task = None
radar_health_task = None
radar_manual_lock = asyncio.Lock()

# Manual Mini App scans run in the background so the HTTP request never has to
# stay open for the full multi-minute radar cycle. Keep a bounded in-process
# history so multiple authorized clients can poll the same active job.
_radar_app_scan_lock = asyncio.Lock()
_radar_app_active_scan_job_id = None
_radar_app_scan_jobs = {}
_radar_app_scan_tasks = {}
_RADAR_APP_SCAN_JOB_HISTORY_LIMIT = 6

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

# Uploaded branding files live in /app/data, which is backed by the saspro_data
# Docker volume, so they survive container rebuilds and redeployments.
SITE_MEDIA_DIR = Path(os.getenv("SAS_MEDIA_DIR", "data/site_media"))
SITE_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
SITE_MEDIA_CONFIG = SITE_MEDIA_DIR / "media.json"
SITE_MEDIA_SLOTS = {
    "hero": {"label": "صورة الواجهة الرئيسية", "kind": "image"},
    "icon-radar": {"label": "أيقونة زر الرادار", "kind": "icon"},
    "icon-channel": {"label": "أيقونة زر القناة", "kind": "icon"},
    "icon-home": {"label": "أيقونة الرئيسية", "kind": "icon"},
    "icon-tab-radar": {"label": "أيقونة تبويب الرادار", "kind": "icon"},
    "icon-search": {"label": "أيقونة البحث", "kind": "icon"},
    "icon-watch": {"label": "أيقونة المحفوظات", "kind": "icon"},
    "icon-account": {"label": "أيقونة الحساب", "kind": "icon"},
    "icon-profile": {"label": "أيقونة ملف التعريف الشخصي", "kind": "icon"},
    "icon-profile": {"label": "أيقونة ملف المستخدم الشخصي", "kind": "icon"},
    "icon-live-movers": {"label": "أيقونة أعلى الأسهم حركة", "kind": "icon"},
    "icon-perp-movers": {"label": "أيقونة الأكثر تحركًا خلال 24 ساعة", "kind": "icon"},
    "icon-bitcoin": {"label": "أيقونة البتكوين في مؤشرات السوق", "kind": "icon"},
    "icon-gold": {"label": "أيقونة الذهب في مؤشرات السوق", "kind": "icon"},
    "icon-sp500": {"label": "أيقونة مؤشر S&P 500", "kind": "icon"},
    "icon-nasdaq": {"label": "أيقونة مؤشر ناسداك", "kind": "icon"},
    "icon-dow-jones": {"label": "أيقونة مؤشر داو جونز", "kind": "icon"},
    "icon-vix": {"label": "أيقونة مؤشر VIX", "kind": "icon"},
    "icon-market-indicators": {"label": "أيقونة عنوان مؤشرات السوق", "kind": "icon"},
    "icon-shariah": {"label": "أيقونة الشرعية في بطاقة السهم", "kind": "icon"},
    "icon-disclaimer-alert": {"label": "أيقونة التنبيه القانوني أسفل الصفحة", "kind": "icon"},
}
SITE_MEDIA_MIMES = {
    "image/webp": (".webp", lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    "image/png": (".png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": (".jpg", lambda b: b.startswith(b"\xff\xd8\xff")),
}
app.mount("/media", StaticFiles(directory=str(SITE_MEDIA_DIR)), name="site-media")

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
    global scheduler_task, telegram_polling_task, telegram_config_task, private_analysis_task, radar_health_task
    telegram_config_task = asyncio.create_task(_configure_telegram(), name="saspro-telegram-config")
    scheduler_task = asyncio.create_task(scheduler(), name="saspro-scheduler")

    def _scheduler_done(task):
        try:
            if task.cancelled():
                logging.getLogger(__name__).warning(
                    "SAS PRO scheduler task cancelled unexpectedly."
                )
                return
            exc = task.exception()
            if exc is not None:
                logging.getLogger(__name__).error(
                    "SAS PRO scheduler task terminated with exception: %s: %s",
                    type(exc).__name__,
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            else:
                logging.getLogger(__name__).error(
                    "SAS PRO scheduler task stopped unexpectedly without exception."
                )
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to inspect SAS PRO scheduler task termination."
            )

    scheduler_task.add_done_callback(_scheduler_done)

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
        "Background tasks started: scheduler=%s radar_health=%s telegram=%s",
        scheduler_task.get_name(), radar_health_task.get_name(), telegram_polling_task.get_name()
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

def _safe_external_http_url(value):
    raw = str(value or "").strip()
    if not raw or any(char.isspace() for char in raw) or chr(92) in raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ""
    return raw


def build_report(symbol: str, q: dict, tech: dict, classification: dict | None = None, outcome=None) -> str:
    """Build a concise SAS PRO alert with a verified, source-linked catalyst when available."""
    q = q or {}
    tech = tech or {}
    classification = classification or {}
    intraday = tech.get("intraday") or {}
    checks = tech.get("radar_checks") or {}
    quality = tech.get("quality_score") or {}
    shariah = tech.get("shariah") or {}
    news_items = tech.get("news_items") or []
    events = tech.get("corporate_events") or tech.get("events") or []
    split_analysis = tech.get("reverse_split_analysis") or tech.get("split_analysis") or {}
    corporate_actions = tech.get("corporate_actions") or {}

    def esc(value):
        return html.escape(str(value or "").replace("\\\\n", "\n").strip())

    def num(value):
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def first(*values):
        return next((v for v in values if v is not None and v != ""), None)

    price = num(q.get("price"))
    if price is not None and price <= 0:
        price = None
    change = num(q.get("change_pct"))
    score = num(quality.get("score"))
    if score is None:
        score = num(classification.get("score"))
    rvol = num(first(intraday.get("rvol"), intraday.get("intraday_rvol"),
                     checks.get("momentum_rvol"), classification.get("rvol"),
                     tech.get("volume_ratio")))
    entry = num(first(tech.get("entry"), tech.get("entry_price"), tech.get("price"), price))
    stop = num(first(getattr(outcome, "current_stop", None) if outcome is not None else None,
                     tech.get("exit"), tech.get("stop"), tech.get("stop_loss")))
    targets = sorted(set(x for x in (num(v) for v in (tech.get("targets") or []))
                         if x is not None and x > 0))
    target_ok = checks.get("target")
    if target_ok is False:
        targets = []
    rr = None
    if entry is not None and stop is not None and targets:
        risk, reward = entry - stop, targets[0] - entry
        if risk > 0 and reward > 0:
            rr = reward / risk

    state = first(tech.get("radar_candidate_status"), tech.get("channel_gate", {}).get("reason"))
    confirmed = bool(checks.get("advanced_confirmation")) and bool(checks.get("target", False))
    if price is None:
        status = "السعر الحي غير متاح — لا تعتمد الرصد كإشارة دخول"
    elif confirmed:
        status = "تأكيد فني مكتمل — راقب السعر والسيولة"
    else:
        status = "مراقبة — التأكيد غير مكتمل"

    # Pick only a recent, dated, source-linked headline. A headline is a possible
    # catalyst, not proof that it caused the price move.
    now = datetime.now(timezone.utc)
    catalysts = []
    for item in news_items if isinstance(news_items, list) else []:
        if not isinstance(item, dict):
            continue
        headline = str(item.get("headline") or "").strip()
        url = _safe_external_http_url(item.get("url"))
        source = str(item.get("source") or "مصدر إخباري").strip()
        stamp = item.get("datetime")
        try:
            if isinstance(stamp, (int, float)):
                published = datetime.fromtimestamp(stamp, tz=timezone.utc)
            elif isinstance(stamp, str) and stamp.strip():
                published = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            else:
                continue
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        age_hours = (now - published.astimezone(timezone.utc)).total_seconds() / 3600
        if headline and url and -1 <= age_hours <= 48:
            catalysts.append((published, headline, source, url))
    catalysts.sort(key=lambda row: row[0], reverse=True)

    radar_type = str(
        tech.get("radar_type")
        or classification.get("radar_type")
        or "intraday_speculative"
    ).strip().lower()
    radar_type_labels = {
        "intraday_speculative": "مضاربي لحظي",
        "analyst_target": "أهداف المحللين",
        "technical_breakout": "اختراق فني",
        "accumulation": "تجميع مبكر",
    }
    report = [f"🚀 <b>SAS PRO RADAR | {esc(radar_type_labels.get(radar_type, 'تقرير الرادار'))}</b>", ""]
    report += [
        f"💎 <b>السهم:</b> {esc(symbol.upper())}",
        f"💵 <b>السعر:</b> {_money(price) if price is not None else 'غير متاح'}"
        + (f"  |  {'🟢' if change >= 0 else '🔴'} <b>{change:+.2f}%</b>" if change is not None else ""),
        f"🌊 <b>RVOL:</b> {rvol:.2f}×" if rvol is not None else "",
        f"🧭 <b>الحالة:</b> {esc(status)}",
    ]
    report = [line for line in report if line != ""]

    report += ["", "━━━━━━━━━━━━━━━━━━", "", "📰 <b>لماذا يتحرك السهم؟</b>"]
    if catalysts:
        published, headline, source, url = catalysts[0]
        report.append(f"🔎 <a href=\"{esc(url)}\">{esc(headline)}</a>")
        report.append(f"📚 المصدر: {esc(source)} | {published.strftime('%Y-%m-%d %H:%M UTC')}")
        report.append("📌 محفز محتمل وفق الخبر المنشور؛ لا يمكن الجزم بأنه سبب الحركة وحده.")
    else:
        event = None
        if isinstance(events, list):
            for row in events:
                if isinstance(row, dict):
                    event = first(row.get("headline"), row.get("description"), row.get("title"))
                elif isinstance(row, str) and row.strip():
                    event = row.strip()
                if event:
                    break
        if event:
            report.append(f"🔎 {esc(event)}")
            report.append("📌 حدث وارد في بيانات الرادار؛ تحقق من المصدر والتوقيت قبل ربطه بحركة السعر.")
        else:
            report.append("⚪ لا يوجد خبر حديث موثق في بيانات الرادار يفسّر الحركة.")
            report.append("لن ننسب الارتفاع إلى أرباح أو خبر من دون مصدر وتاريخ قابلين للتحقق.")

    # Show reverse-split classification only when source data actually confirms an event.
    # Missing data is not a reason to print an uncertainty warning in every radar report.
    split_events = []
    if isinstance(events, list):
        for item in events:
            if isinstance(item, dict):
                text_value = " ".join(str(item.get(k) or "") for k in ("headline", "description", "title", "type", "label_ar"))
                event_date = item.get("date") or item.get("datetime") or item.get("published_at")
                if any(term in text_value.lower() for term in ("reverse split", "reverse stock split", "تقسيم عكسي", "تجزئة عكسية", "دمج الأسهم")):
                    split_events.append((text_value.strip(), event_date))
            elif isinstance(item, str) and any(term in item.lower() for term in ("reverse split", "reverse stock split", "تقسيم عكسي", "تجزئة عكسية", "دمج الأسهم")):
                split_events.append((item.strip(), None))

    split_detected = bool(
        split_analysis.get("detected") is True
        or split_analysis.get("is_reverse_split") is True
        or corporate_actions.get("reverse_split") is True
        or split_events
    )
    # If the data provider has no verified split event, omit this entire section.
    if split_detected:
        split_date = first(split_analysis.get("date"), split_analysis.get("last_reverse_split_date"),
                           corporate_actions.get("reverse_split_date"),
                           split_events[0][1] if split_events else None)
        split_ratio = first(split_analysis.get("ratio"), corporate_actions.get("reverse_split_ratio"))
        split_verified = split_analysis.get("verified") is True or corporate_actions.get("verified") is True
        report += ["", "━━━━━━━━━━━━━━━━━━", "", "⚠️ <b>نوع السهم ومخاطر التقسيم العكسي</b>"]
        report.append("🏷️ <b>التصنيف:</b> سهم مرتبط بتقسيم عكسي — قد يكون ضمن أسهم عائدة من تقسيم")
        if split_date:
            report.append(f"📅 <b>تاريخ الحدث:</b> {esc(split_date)}")
        if split_ratio:
            report.append(f"🔀 <b>نسبة التقسيم المعلنة:</b> {esc(split_ratio)}")
        report.append("🔴 <b>مستوى المخاطر: مرتفع</b> — التقسيم العكسي لا يرفع القيمة الاقتصادية للشركة بحد ذاته.")
        report.append("• قد يستمر الهبوط بعد التقسيم، مع تذبذب وسيولة أضعف وفروقات سعرية أكبر.")
        report.append("• افحص التخفيف بإصدار أسهم جديدة، والتمويل، والضمانات، ومتطلبات الإدراج قبل تقييم الارتداد.")
        if split_verified:
            report.append("✅ حدث التقسيم معلّم بأنه متحقق في بيانات المصدر.")
        else:
            report.append("🟠 تاريخ/تفاصيل التقسيم تحتاج تحققًا من إفصاح الشركة أو البورصة.")
        if split_analysis.get("cycle_target_hit_after_split") is True:
            report.append("⛔ دورة الارتداد: هدف الدورة بعد آخر تقسيم عكسي تحقق سابقًا؛ لا تُعرض كدورة جديدة.")
        elif split_analysis.get("cycle_target_hit_after_split") is False:
            report.append("🔎 دورة الارتداد: لا توجد إشارة مسجلة إلى تحقق الهدف بعد التقسيم؛ هذا لا يضمن تكرار الدورة.")
        else:
            report.append("🔎 دورة الارتداد: لم يتم التحقق من استهلاك الهدف بعد التقسيم؛ لا تفترض أنها دورة جديدة.")

    report += ["", "━━━━━━━━━━━━━━━━━━", "", "🎯 <b>خطة الرصد</b>"]
    report.append(f"🔵 السعر المرجعي: {_money(entry if entry is not None else price)}"
                  if entry is not None or price is not None else "🔵 السعر المرجعي: غير متاح")
    report.append(f"🛑 الوقف الفني: {_money(stop)}" if stop is not None else "🛑 الوقف الفني: غير متوفر")
    if targets:
        report.append("🎯 <b>الأهداف الفنية:</b> " + " | ".join(_money(x) for x in targets[:3]))
    else:
        report.append("🎯 <b>الأهداف الفنية:</b> غير مؤكدة — لا يوجد هدف فني معتمد")

    # Analyst consensus is separate from technical levels. Show only explicit
    # source-backed fields supplied by the caller; never infer or fabricate them.
    analyst_target = num(first(
        tech.get("analyst_target_price"),
        tech.get("mean_analyst_target"),
        tech.get("price_target_mean"),
    ))
    analyst_count = num(first(tech.get("analyst_count"), tech.get("number_of_analysts")))
    analyst_asof = first(tech.get("analyst_target_asof"), tech.get("analyst_updated_at"))
    analyst_source = first(tech.get("analyst_target_source"), tech.get("analyst_source"))
    if analyst_target is not None and analyst_target > 0:
        upside = ((analyst_target / price) - 1) * 100 if price else None
        report.append("🧑‍💼 <b>هدف المحللين (منفصل عن الهدف الفني):</b> " + _money(analyst_target))
        if upside is not None:
            report.append(f"📈 الارتفاع النظري إلى متوسط الهدف: {upside:+.1f}%")
        if analyst_count is not None:
            report.append(f"👥 عدد المحللين: {int(analyst_count)}")
        if analyst_asof:
            report.append("🕒 تاريخ بيانات الهدف: " + esc(analyst_asof))
        if analyst_source:
            report.append("📚 مصدر هدف المحللين: " + esc(analyst_source))
        report.append("⚠️ هدف المحللين تقدير بعيد/متوسط المدى، وليس إشارة دخول لحظية.")
    if rr is not None:
        report.append(f"⚖️ العائد/المخاطرة إلى الهدف الأول: {rr:.2f} : 1")
    report.append("⚠️ لا تطارد السعر؛ انتظر اكتمال التأكيد الفني والسيولة.")

    report += ["", "━━━━━━━━━━━━━━━━━━", "", "📊 <b>تقييم الرادار</b>"]
    if score is not None:
        label = first(quality.get("label"), classification.get("label"), "مراقبة")
        report.append(f"⭐ جودة الفرصة: {score:.0f}/100 — {esc(label)}")
    checks_short = [
        ("momentum", "الزخم"),
        ("liquidity", "السيولة"),
        ("rvol", "الحجم النسبي"),
        ("target", "الهدف الفني"),
        ("no_chase", "عدم مطاردة السعر"),
        ("advanced_confirmation", "التأكيد الفني"),
    ]
    passed = [label for key, label in checks_short if checks.get(key) is True]
    failed = [label for key, label in checks_short if checks.get(key) is False]
    if passed or failed:
        report.append("🧾 <b>فحص الشروط</b>")
    if passed:
        report.append("🟢 تحقق: " + "، ".join(passed[:4]))
    if failed:
        report.append("🔴 لم يتحقق: " + "، ".join(failed[:3]))
    momentum_rvol = num(checks.get("momentum_rvol"))
    rvol_threshold = num(checks.get("momentum_rvol_threshold"))
    if momentum_rvol is not None:
        threshold_text = f" (المطلوب {rvol_threshold:.2f}×)" if rvol_threshold is not None else ""
        report.append(f"📊 RVOL: {momentum_rvol:.2f}×{threshold_text}")
    gate = tech.get("channel_gate") or {}
    if gate and not gate.get("passed"):
        report.append("👀 سبب الإرسال للمراقبة: " + esc(gate.get("reason") or "لم تكتمل شروط التأكيد"))
    if tech.get("radar_candidate_status"):
        report.append("📍 حالة المرشح: " + esc(tech.get("radar_candidate_status")))

    if shariah:
        report += [
            "",
            f"🕌 <b>الشرعية:</b> {esc(shariah.get('status_ar') or 'غير واضح / يحتاج تحقق')}",
            "⛔ شرعية السهم مسؤوليتك — تحقق منها قبل التداول.",
        ]

    report += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "⚠️ هذا التقرير معلوماتي وتعليمي فقط، وليس توصية شراء أو بيع. قرار التداول وإدارة المخاطر مسؤولية المتداول.",
        "📡 <b>SAS PRO</b>",
    ]
    return "\n".join(report)

def _money(value):
    if value is None:
        return "غير واضح"
    return f"${value:,.4f}".rstrip("0").rstrip(".")

async def set_channel_access(telegram_id: int, allow: bool, expires_at=None):
    """Lock/unlock channel access for SAS PRO users."""
    if not settings.telegram_channel_id:
        return {"ok": False, "reason": "telegram_channel_id_not_configured"}

    if not allow:
        # Telegram has no standalone "kick" method: briefly ban, then immediately
        # unban. This removes the member from the channel without leaving a ban,
        # so they can rejoin after renewing their subscription.
        try:
            await bot_api("banChatMember", {
                "chat_id": settings.telegram_channel_id,
                "user_id": telegram_id,
                "revoke_messages": False,
            })
            await bot_api("unbanChatMember", {
                "chat_id": settings.telegram_channel_id,
                "user_id": telegram_id,
                "only_if_banned": True,
            })
            return {"ok": True, "action": "kicked_unbanned"}
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
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
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
            if isinstance(detail, BaseException):
                item["exception_type"] = type(detail).__name__
                frames = __import__("traceback").extract_tb(detail.__traceback__)
                app_frames = []
                for frame in frames:
                    filename = frame.filename.replace("\\", "/")
                    if filename.startswith("/app/"):
                        filename = "app/" + filename[len("/app/"):]
                    elif "/app/" in filename:
                        filename = "app/" + filename.split("/app/", 1)[1]
                    if filename.startswith("app/") or filename.startswith("web/"):
                        app_frames.append((filename, frame.lineno, frame.name))
                if app_frames:
                    filename, line, function = app_frames[-1]
                    item["location"] = f"{filename}:{line}"
                    item["function"] = function
                error_name = type(detail).__name__
                diagnosis = {
                    "TimeoutError": "انتهت مهلة الاتصال؛ افحص استجابة مزود البيانات أو الخدمة الخارجية.",
                    "ConnectError": "تعذر الاتصال بالخدمة الخارجية؛ تحقق من الشبكة والعنوان والمنفذ.",
                    "HTTPStatusError": "الخدمة الخارجية أعادت حالة HTTP غير ناجحة؛ افحص المفتاح والصلاحيات وحدود الطلبات.",
                    "KeyError": "هناك حقل متوقع غير موجود في البيانات؛ راجع بنية الاستجابة أو أسماء المفاتيح.",
                    "AttributeError": "تم استدعاء خاصية أو دالة غير موجودة؛ راجع نوع البيانات والسطر المحدد.",
                    "TypeError": "تعارض في نوع البيانات أو عدد الوسائط؛ راجع الاستدعاء والسطر المحدد.",
                    "OperationalError": "تعذر تنفيذ عملية قاعدة البيانات؛ افحص الاتصال والمخطط وقفل قاعدة البيانات.",
                    "IntegrityError": "رفضت قاعدة البيانات البيانات بسبب قيد أو تكرار؛ راجع السجل والقيد المتأثر.",
                }.get(error_name, "افحص السطر المحدد وسجل الخطأ؛ لم يُصنّف السبب تلقائيًا بشكل أدق.")
                item["diagnosis"] = diagnosis
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
        # Health checks must not call the network-backed ticker refresh: its
        # provider chain can take much longer than this endpoint's UI timeout.
        # Start/continue the normal ticker refresh in the background and inspect
        # only the in-process cache so diagnostics stay fast and do not cancel
        # the market-data fallback path.
        from . import market as market_data
        refresh_task = getattr(market_data, "_TICKER_REFRESH_TASK", None)
        if refresh_task is None or refresh_task.done():
            refresh_task = asyncio.create_task(market_data._refresh_ticker_cache())
            market_data._TICKER_REFRESH_TASK = refresh_task
        cache = getattr(market_data, "_TICKER_CACHE", {})
        symbols = [symbol for symbol, _label in getattr(market_data, "_TICKER_SYMBOLS", [])]
        valid_cached = [
            symbol for symbol in symbols
            if isinstance(cache.get(symbol), dict)
            and float(cache[symbol].get("price") or 0) > 0
        ]
        missing = len(symbols) - len(valid_cached)
        # A partial cache is normal while the provider refresh is still running.
        # Report "updating" instead of a warning until that refresh has completed.
        if not refresh_task.done():
            add(
                "أسعار السوق",
                "ok",
                f"تحديث أسعار السوق جارٍ بالخلفية؛ الأسعار المتاحة حاليًا {len(valid_cached)} من {len(symbols)} مؤشرات."
            )
        elif valid_cached and not missing:
            add("أسعار السوق", "ok", "تتوفر أسعار محفوظة صالحة؛ يجري تحديثها بالخلفية.")
        elif valid_cached:
            missing_symbols = [symbol for symbol in symbols if symbol not in valid_cached]
            add(
                "أسعار السوق",
                "warn",
                f"اكتمل التحديث مع نقص أسعار {missing} من {len(symbols)} مؤشرات.",
                "المؤشرات غير المتاحة: " + ", ".join(missing_symbols)
            )
        else:
            add(
                "أسعار السوق",
                "warn",
                "اكتمل التحديث دون أسعار صالحة؛ افحص سجلات مزودي البيانات.",
                "لا توجد أسعار صالحة لأي من المؤشرات: " + ", ".join(symbols)
            )
    except Exception as exc:
        add("أسعار السوق", "warn", "تعذر فحص حالة أسعار السوق؛ لا يمنع ذلك تشغيل الرادار.", exc)

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
    subscription_rows = (await db.execute(
        select(Subscription).order_by(Subscription.expires_at.desc())
    )).scalars().all()
    subscriptions_by_user = {}
    for sub in subscription_rows:
        subscriptions_by_user.setdefault(int(sub.telegram_id), []).append(sub)
    active = 0
    expired = 0
    trial = 0
    for u in users:
        trial_active = bool(u.status == "trial" and u.trial_expires and aware(u.trial_expires) > now)
        user_subscriptions = subscriptions_by_user.get(int(u.telegram_id), [])
        current_subscription = next(
            (sub for sub in user_subscriptions if sub.active and aware(sub.expires_at) > now),
            None,
        )
        # Prefer a currently active, unexpired Subscription row.
        # If none exists, preserve valid legacy/admin-activated User access even when
        # historical inactive Subscription rows remain in the database.
        paid_active = bool(
            not u.free_access
            and (
                current_subscription is not None
                or (
                    current_subscription is None
                    and u.status == "active"
                    and u.subscription_expires
                    and aware(u.subscription_expires) > now
                )
            )
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
    subscription_rows = (await db.execute(
        select(Subscription).order_by(Subscription.expires_at.desc())
    )).scalars().all()
    subscriptions_by_user = {}
    for sub in subscription_rows:
        subscriptions_by_user.setdefault(int(sub.telegram_id), []).append(sub)
    rows = []
    for u in users:
        trial_active = bool(u.status == "trial" and u.trial_expires and aware(u.trial_expires) > now)
        user_subscriptions = subscriptions_by_user.get(int(u.telegram_id), [])
        current_subscription = next(
            (sub for sub in user_subscriptions if sub.active and aware(sub.expires_at) > now),
            None,
        )
        # Do not let stale Subscription history hide a valid admin/legacy activation.
        # A current Subscription row wins; otherwise honor active User fields with a future expiry.
        paid_active = bool(
            not u.free_access
            and (
                current_subscription is not None
                or (
                    not user_subscriptions
                    and u.status == "active"
                    and u.subscription_expires
                    and aware(u.subscription_expires) > now
                )
            )
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
            expires = aware(current_subscription.expires_at) if current_subscription else aware(u.subscription_expires)
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
        "plan": u.plan, "free_access": u.free_access, "status": u.status,
        "trial_used_at": aware(u.trial_used_at).isoformat() if u.trial_used_at else None,
        "terms_accepted_at": aware(u.terms_accepted_at).isoformat() if u.terms_accepted_at else None,
    } for u in rows]

@app.post("/api/admin/users/{telegram_id}/reset-for-test")
async def admin_reset_user_for_test(telegram_id: int, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    """Reset one selected account to the subscription entry screen for owner testing."""
    await require_admin_permission(user, "subscriptions")
    row = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalars().first()
    if not row:
        raise HTTPException(404, "لم يتم العثور على المستخدم")
    await db.execute(update(Subscription).where(
        Subscription.telegram_id == telegram_id, Subscription.active == True
    ).values(active=False))
    now = utcnow()
    row.status = "new"
    row.free_access = False
    row.trial_start = None
    row.trial_expires = None
    row.trial_used_at = None
    row.subscription_start = None
    row.subscription_expires = None
    row.plan = None
    row.warning_sent_at = None
    row.channel_join_requested_at = None
    row.updated_at = now
    await db.commit()
    try:
        await set_channel_access(telegram_id, allow=False)
    except Exception:
        logger.exception("Could not revoke channel access during test reset for %s", telegram_id)
    await audit(int(user["id"]), "reset_user_for_subscription_test", telegram_id, {"reset_at": now.isoformat()})
    return {"ok": True, "telegram_id": telegram_id, "status": "new"}


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

@app.get("/api/admin/gift-candidates")
async def admin_gift_candidates(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "subscriptions")
    now = utcnow()
    rows = (await db.execute(select(User).order_by(User.first_name, User.username))).scalars().all()
    candidates = []
    for row in rows:
        if row.free_access:
            continue
        paid = (await db.execute(select(Subscription).where(
            Subscription.telegram_id == row.telegram_id, Subscription.active == True
        ).order_by(Subscription.expires_at.desc()))).scalars().first()
        expiry = None
        kind = None
        if paid and aware(paid.expires_at) > now:
            expiry, kind = aware(paid.expires_at), "مدفوع"
        elif row.trial_expires and aware(row.trial_expires) > now and row.status == "trial":
            expiry, kind = aware(row.trial_expires), "تجربة مجانية"
        if expiry:
            candidates.append({
                "telegram_id": row.telegram_id,
                "name": " ".join(part for part in [row.first_name, row.last_name] if part).strip() or row.username or str(row.telegram_id),
                "username": row.username,
                "kind": kind,
                "expires_at": expiry.isoformat(),
            })
    return candidates


@app.get("/api/admin/gift-message")
async def admin_get_gift_message(user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "subscriptions")
    default_template = (
        "🎁 هدية اشتراك من {brand}\\n\\n"
        "أهلًا بك! 🎉\\n"
        "تم تمديد اشتراكك كهدية لمدة {days} يومًا.\\n"
        "تاريخ انتهاء اشتراكك الجديد: {expiry_date} 📅\\n\\n"
        "شكرًا لثقتك بنا، ونتمنى لك تجربة رائعة مع {brand} 💎"
    )
    template = await setting_get(db, "gift_message_template", default_template)
    template = str(template).replace("\\\\n", "\\n")
    template = re.sub(r"</?(?:b|i|u|code)>", "", template, flags=re.IGNORECASE)
    return {"template": template}


@app.post("/api/admin/gift-message")
async def admin_save_gift_message(request: Request, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "subscriptions")
    body = await request.json()
    template = str(body.get("template") or "").replace("\\\\n", "\\n").strip()
    template = re.sub(r"</?(?:b|i|u|code)>", "", template, flags=re.IGNORECASE)
    if not template:
        raise HTTPException(400, "اكتب نص رسالة الهدية")
    if len(template) > 2500:
        raise HTTPException(400, "الحد الأقصى لرسالة الهدية 2500 حرف")
    allowed = {"days", "expiry_date", "brand"}
    placeholders = set(re.findall(r"\{([a-zA-Z_]+)\}", template))
    if placeholders - allowed:
        raise HTTPException(400, "متغير غير معروف: " + ", ".join(sorted(placeholders - allowed)))
    await setting_set(db, "gift_message_template", template)
    await db.commit()
    await audit(int(user["id"]), "gift_message_template_updated", None, {"length": len(template)})
    return {"ok": True, "template": template}


@app.post("/api/admin/gift-extend-all")
async def admin_gift_extend_all(request: Request, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    await require_admin_permission(user, "subscriptions")
    body = await request.json()
    try:
        days = int(body.get("days", 7))
        telegram_id = int(body.get("telegram_id", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "تحقق من رقم المستخدم وعدد أيام الهدية")
    if days < 1 or days > 365:
        raise HTTPException(400, "مدة الهدية يجب أن تكون بين يوم واحد و365 يومًا")
    now = utcnow()
    query = select(User).order_by(User.telegram_id)
    if telegram_id:
        query = select(User).where(User.telegram_id == telegram_id)
    users = (await db.execute(query)).scalars().all()
    if telegram_id and not users:
        raise HTTPException(404, "لم يتم العثور على مستخدم بهذا الرقم")
    extended = []
    skipped = 0
    for row in users:
        if row.free_access:
            skipped += 1
            continue
        paid = (await db.execute(select(Subscription).where(
            Subscription.telegram_id == row.telegram_id, Subscription.active == True
        ).order_by(Subscription.expires_at.desc()))).scalars().first()
        if paid and aware(paid.expires_at) > now:
            paid.expires_at = aware(paid.expires_at) + timedelta(days=days)
            row.subscription_expires = paid.expires_at
            row.updated_at = now
            extended.append((row, paid.expires_at))
            continue
        if row.trial_expires and aware(row.trial_expires) > now and row.status == "trial":
            row.trial_expires = aware(row.trial_expires) + timedelta(days=days)
            row.updated_at = now
            extended.append((row, row.trial_expires))
            continue
        skipped += 1
    if not extended:
        raise HTTPException(400, "لا يوجد اشتراك فعّال قابل للتمديد لهذا الاختيار")
    await db.commit()
    sent = 0
    failed = 0
    for row, expires_at in extended:
        try:
            message = (
                "🎁 هدية اشتراك من SAS PRO\n"
                "أهلًا بك! 🎉\n"
                f"تم تمديد اشتراكك كهدية لمدة {days} أيام.\n"
                f"📅 تاريخ انتهاء اشتراكك الجديد: {aware(expires_at).strftime('%d/%m/%Y')}\n"
                "شكرًا لثقتك بنا، ونتمنى لك تجربة رائعة مع SAS PRO 💎"
            )
            await send_message(row.telegram_id, message)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("Gift extension notification failed for %s: %s", row.telegram_id, exc)
    action = "gift_extend_single_subscription" if telegram_id else "gift_extend_all_subscriptions"
    await audit(int(user["id"]), action, telegram_id or None, {
        "days": days, "extended": len(extended), "skipped": skipped,
        "notifications_sent": sent, "notifications_failed": failed
    })
    return {"ok": True, "days": days, "target": "single" if telegram_id else "all",
            "extended": len(extended), "skipped": skipped,
            "notifications_sent": sent, "notifications_failed": failed}


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



def _read_site_media_config():
    try:
        value = json.loads(SITE_MEDIA_CONFIG.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


@app.get("/api/site-media")
async def site_media_config():
    configured = _read_site_media_config()
    items = {}
    for slot, meta in SITE_MEDIA_SLOTS.items():
        filename = configured.get(slot)
        path = SITE_MEDIA_DIR / filename if isinstance(filename, str) else None
        if path and path.parent == SITE_MEDIA_DIR and path.is_file():
            items[slot] = {
                "url": f"/media/{path.name}?v={path.stat().st_mtime_ns}",
                "label": meta["label"],
                "kind": meta["kind"],
            }
    return {"ok": True, "items": items}


@app.post("/api/admin/site-media/{slot}")
async def admin_upload_site_media(slot: str, request: Request, user=Depends(telegram_user)):
    await require_admin_permission(user, "settings")
    if slot not in SITE_MEDIA_SLOTS:
        raise HTTPException(404, "موضع الصورة غير معروف")
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    spec = SITE_MEDIA_MIMES.get(content_type)
    if not spec:
        raise HTTPException(415, "ارفع صورة بصيغة WebP أو PNG أو JPEG فقط")
    payload = await request.body()
    if not payload:
        raise HTTPException(400, "ملف الصورة فارغ")
    if len(payload) > 5 * 1024 * 1024:
        raise HTTPException(413, "الحد الأقصى لحجم الصورة 5 ميغابايت")
    extension, signature_check = spec
    if not signature_check(payload):
        raise HTTPException(415, "محتوى الملف لا يطابق نوع الصورة")
    filename = f"{slot}{extension}"
    target = SITE_MEDIA_DIR / filename
    try:
        for old in SITE_MEDIA_DIR.glob(f"{slot}.*"):
            if old.is_file() and old.name != filename:
                old.unlink()
        target.write_bytes(payload)
        configured = _read_site_media_config()
        configured[slot] = filename
        SITE_MEDIA_CONFIG.write_text(json.dumps(configured, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("Could not persist admin-uploaded site media for slot %s", slot)
        raise HTTPException(500, "تعذر حفظ الصورة على مساحة التخزين الدائمة")
    return {
        "ok": True,
        "slot": slot,
        "url": f"/media/{filename}?v={target.stat().st_mtime_ns}",
        "label": SITE_MEDIA_SLOTS[slot]["label"],
    }


@app.delete("/api/admin/site-media/{slot}")
async def admin_delete_site_media(slot: str, user=Depends(telegram_user)):
    await require_admin_permission(user, "settings")
    if slot not in SITE_MEDIA_SLOTS:
        raise HTTPException(404, "موضع الصورة غير معروف")
    configured = _read_site_media_config()
    filename = configured.get(slot)
    try:
        # Only remove files belonging to this known slot; never accept a client path.
        if isinstance(filename, str) and Path(filename).name == filename:
            target = SITE_MEDIA_DIR / filename
            if target.parent == SITE_MEDIA_DIR and target.is_file():
                target.unlink()
        configured.pop(slot, None)
        SITE_MEDIA_CONFIG.write_text(json.dumps(configured, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("Could not delete admin-managed site media for slot %s", slot)
        raise HTTPException(500, "تعذر حذف الصورة من مساحة التخزين الدائمة")
    return {"ok": True, "slot": slot, "deleted": bool(filename)}


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


@app.get("/api/admin/radar/daily-summary")
async def admin_radar_daily_summary(session_date: str | None = None, user=Depends(telegram_user), db: AsyncSession = Depends(get_session)):
    """Read-only end-of-day radar summary. It never starts a scan or contacts market-data providers."""
    await require_admin_permission(user, "radar")
    from .db import RadarOutcome
    if session_date:
        try:
            datetime.strptime(session_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "صيغة التاريخ يجب أن تكون YYYY-MM-DD")

    dates = (await db.execute(
        select(RadarRun.session_date).where(RadarRun.status != "running")
        .distinct().order_by(RadarRun.session_date.desc()).limit(10)
    )).scalars().all()
    chosen_date = session_date or (dates[0] if dates else None)
    if not chosen_date:
        return {"ok": True, "found": False, "dates": [], "message": "لا توجد إحصائيات رادار محفوظة بعد."}

    runs = (await db.execute(
        select(RadarRun).where(RadarRun.session_date == chosen_date)
        .order_by(RadarRun.started_at.asc())
    )).scalars().all()
    signals = (await db.execute(
        select(RadarSignal).where(RadarSignal.session_date == chosen_date)
        .order_by(RadarSignal.created_at.desc())
    )).scalars().all()
    outcomes = (await db.execute(
        select(RadarOutcome).where(RadarOutcome.session_date == chosen_date)
    )).scalars().all()

    def loads(value, fallback):
        try:
            result = json.loads(value or "")
            return result if isinstance(result, type(fallback)) else fallback
        except Exception:
            return fallback

    rejection_totals = {}
    for run in runs:
        for key, value in loads(run.channel_gate_rejections, {}).items():
            try:
                rejection_totals[str(key)] = rejection_totals.get(str(key), 0) + int(value or 0)
            except (TypeError, ValueError):
                continue

    latest = next((r for r in reversed(runs) if r.status != "running"), runs[-1] if runs else None)
    latest_ny = datetime.now(ZoneInfo("America/New_York"))
    report_ready = chosen_date != latest_ny.strftime("%Y-%m-%d") or (latest_ny.hour >= 16)
    completed_outcomes = sum(1 for o in outcomes if o.status == "completed")
    failed_outcomes = sum(1 for o in outcomes if o.status == "failed")
    active_outcomes = sum(1 for o in outcomes if o.status == "active")
    target_hits = sum(1 for o in outcomes if int(o.achieved_target or 0) > 0)
    return {
        "ok": True, "found": bool(runs or signals), "session_date": chosen_date,
        "dates": dates, "report_ready": report_ready,
        "market_state": "مكتمل" if report_ready else "بانتظار إغلاق السوق الأمريكي",
        "summary": {
            "runs": len(runs),
            "completed_runs": sum(1 for r in runs if r.status == "completed"),
            "failed_runs": sum(1 for r in runs if r.status == "failed"),
            "candidates": sum(int(r.candidates or 0) for r in runs),
            "shortlist": sum(int(r.shortlist or 0) for r in runs),
            "passed": sum(int(r.passed or 0) for r in runs),
            "filtered": sum(int(r.filtered or 0) for r in runs),
            "errors": sum(int(r.errors or 0) for r in runs),
            "channel_sent": sum(int(r.channel_sent or 0) for r in runs),
            "app_only": sum(int(r.channel_app_only or 0) for r in runs),
            "unique_signals": len({str(s.symbol or "").upper() for s in signals if s.symbol}),
            "target_hits": target_hits, "completed_outcomes": completed_outcomes,
            "failed_outcomes": failed_outcomes, "active_outcomes": active_outcomes,
            "duration_seconds": round(sum(float(r.duration_seconds or 0) for r in runs), 1),
        },
        "rejections": rejection_totals,
        "top_opportunities": loads(latest.top_opportunities, []) if latest else [],
        "last_finished_at": aware(latest.finished_at).isoformat() if latest and latest.finished_at else None,
        "last_status": latest.status if latest else None,
    }


@app.get("/api/radar/learning-profile")
async def radar_learning_profile_api(user=Depends(telegram_user)):
    """Admin-only read of the persisted learning profile; never triggers a scan or provider request."""
    await require_admin_permission(user, "radar")
    from .radar_learning import get_radar_profile
    return await get_radar_profile()


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
        "closed_market_macro_radar": not status.get("radar_active", status["open"]),
    }


# PerpEquities 24-hour movers are kept separate from SAS technical radar signals.
_PERPEQUITIES_URL = "https://perpequities.com/ar"
_PERPEQUITIES_CACHE_TTL = 300
_PERPEQUITIES_MOVERS_CACHE = {"fetched_at": 0.0, "payload": None}


class _PerpEquitiesMoversParser(HTMLParser):
    """Extract only the page's explicit 'most moved in 24 hours' section."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.offset = 0
        self.anchors = []
        self.headings = []
        self.anchor = None
        self.heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchor = {"href": attrs.get("href", ""), "start": self.offset, "parts": []}
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading = {"tag": tag, "level": int(tag[1]), "start": self.offset, "parts": []}

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor["parts"].append(data)
        if self.heading is not None:
            self.heading["parts"].append(data)
        self.offset += len(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            item = self.anchor
            item["end"] = self.offset
            item["text"] = " ".join("".join(item.pop("parts")).split())
            self.anchors.append(item)
            self.anchor = None
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.heading is not None and tag == self.heading["tag"]:
            item = self.heading
            item["end"] = self.offset
            item["text"] = " ".join("".join(item.pop("parts")).split())
            self.headings.append(item)
            self.heading = None


def _parse_perpequities_movers(document: str):
    parser = _PerpEquitiesMoversParser()
    parser.feed(document)
    clean = lambda value: re.sub(r"[\u064B-\u065F\u0670\u200e\u200f\u202a-\u202e]", "", value or "").strip()
    target = next((h for h in parser.headings if "الأكثر تحركًا خلال 24 ساعة" in clean(h["text"]) or "الأكثر تحركا خلال 24 ساعة" in clean(h["text"])), None)
    if not target:
        return None
    section_end = min(
        (h["start"] for h in parser.headings if h["start"] > target["end"] and h["level"] <= target["level"] and not any(word in clean(h["text"]) for word in ("الرابحون", "الخاسرون"))),
        default=parser.offset + 1,
    )
    changes = []
    seen = set()
    for anchor in parser.anchors:
        if not (target["end"] <= anchor["start"] < section_end):
            continue
        label = clean(anchor.get("text", "")).replace("−", "-").replace("–", "-")
        match_change = re.search(r"([+\-]?\s*\d+(?:[.,]\d+)?)\s*[%٪]", label)
        match_symbol = re.match(r"\$?([A-Z][A-Z0-9.-]{0,11})", label)
        if not match_change or not match_symbol:
            continue
        symbol = match_symbol.group(1).upper()
        if symbol in seen:
            continue
        try:
            change = float(match_change.group(1).replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            continue
        href = urljoin(_PERPEQUITIES_URL, anchor.get("href") or "")
        changes.append({"symbol": symbol, "change_pct": change, "url": href})
        seen.add(symbol)
    if not changes:
        return None
    gainers = sorted((x for x in changes if x["change_pct"] > 0), key=lambda x: x["change_pct"], reverse=True)[:8]
    losers = sorted((x for x in changes if x["change_pct"] < 0), key=lambda x: x["change_pct"])[:8]
    return {"available": bool(gainers or losers), "gainers": gainers, "losers": losers}


_PERPEQUITIES_API_URL = "https://perpequities.com/api/v1/assets?assetClass=equity&live=1&limit=2000"
_PERPEQUITIES_API_CACHE_TTL = 300
_PERPEQUITIES_ASSETS_CACHE = {"fetched_at": 0.0, "payload": None}


def _perpequities_asset_row(row):
    if not isinstance(row, dict):
        return None
    ticker = str(row.get("ticker") or "").strip().upper()
    if not ticker or not re.fullmatch(r"[A-Z0-9.\-]{1,16}", ticker):
        return None
    venues = []
    for venue in row.get("venues") or []:
        if not isinstance(venue, dict) or not venue.get("live") or venue.get("status") != "trading":
            continue
        venues.append({
            "venue": venue.get("venueName") or venue.get("venue"),
            "price": venue.get("price") if venue.get("price") is not None else venue.get("mark"),
            "volume24hUsd": venue.get("volume24hUsd"),
            "openInterestUsd": venue.get("openInterestUsd"),
            "spreadBps": venue.get("spreadBps"),
            "depth1PctUsd": venue.get("depth1PctUsd"),
            "fundingApr": venue.get("fundingApr"),
            "updatedAt": venue.get("updatedAt"),
        })
    return {
        "symbol": ticker,
        "name": row.get("name"),
        "price": row.get("price"),
        "change24h": row.get("change24h"),
        "change7d": row.get("change7d"),
        "volume24hUsd": row.get("volume24hUsd"),
        "qualityFilteredVolume24hUsd": row.get("qualityFilteredVolume24hUsd"),
        "openInterestUsd": row.get("openInterestUsd"),
        "venueCount": row.get("venueCount"),
        "tradableMarkets": row.get("tradableMarkets"),
        "liveMarkets": row.get("liveMarkets"),
        "url": row.get("url") or "https://perpequities.com/ar",
        "venues": venues[:8],
    }


async def _fetch_perpequities_assets():
    now = time.monotonic()
    cached = _PERPEQUITIES_ASSETS_CACHE.get("payload")
    if cached and now - _PERPEQUITIES_ASSETS_CACHE.get("fetched_at", 0.0) < _PERPEQUITIES_API_CACHE_TTL:
        return cached
    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            headers={"User-Agent": "SASPRO/2.1 (+https://perpequities.com/data)", "Accept": "application/json"},
        ) as client:
            response = await client.get(_PERPEQUITIES_API_URL)
            response.raise_for_status()
        body = response.json()
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise ValueError("PerpEquities API returned an unexpected data envelope.")
        assets = []
        seen = set()
        for row in rows:
            item = _perpequities_asset_row(row)
            if not item or item["symbol"] in seen:
                continue
            seen.add(item["symbol"])
            assets.append(item)
        if not assets:
            raise ValueError("PerpEquities API returned no usable live equity assets.")
        market_at = response.headers.get("X-Market-Data-At") or body.get("marketDataAt") or body.get("generatedAt")
        stale_header = str(response.headers.get("X-Market-Data-Stale") or body.get("stale") or "").lower()
        payload = {
            "available": True,
            "assets": assets,
            "source": "PerpEquities",
            "source_url": "https://perpequities.com/ar",
            "generated_at": body.get("generatedAt"),
            "market_data_at": market_at,
            "updated_at": market_at or body.get("generatedAt"),
            "stale": stale_header in ("1", "true", "yes"),
            "message_ar": None,
        }
        _PERPEQUITIES_ASSETS_CACHE.update({"fetched_at": time.monotonic(), "payload": payload})
        return payload
    except Exception as exc:
        logger.warning("PerpEquities assets API refresh failed: %s", exc)
        if cached:
            return {**cached, "stale": True, "message_ar": "تعذر تحديث بيانات PerpEquities؛ نعرض آخر نسخة محفوظة."}
        return {
            "available": False, "assets": [], "source": "PerpEquities",
            "source_url": "https://perpequities.com/ar", "generated_at": None,
            "market_data_at": None, "updated_at": None, "stale": False,
            "message_ar": "بيانات PerpEquities غير متاحة مؤقتًا.",
        }


def _movers_price_in_range(item, minimum=0.30, maximum=20.00):
    """Only include assets whose published reference price is inside the requested band."""
    if not isinstance(item, dict):
        return False
    try:
        price = float(item.get("price"))
    except (TypeError, ValueError):
        return False
    return price >= minimum and price <= maximum


def _rank_perpequities_movers(assets):
    """Return positive movers only; exclude crypto-like assets and require the regular US session."""
    status = market_status()
    if not stock_radar_enabled():
        return [], [], []

    crypto_name_terms = (
        "crypto", "cryptocurrency", "stablecoin", "bitcoin", "ethereum",
        "token", "coinbase wrapped", "wrapped bitcoin", "usd coin",
    )
    eligible = []
    for raw in assets if isinstance(assets, list) else []:
        if not _movers_price_in_range(raw):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        name = str(raw.get("name") or "").strip().lower()
        if not symbol or is_crypto_symbol(symbol) or any(term in name for term in crypto_name_terms):
            continue
        try:
            change = float(raw.get("change24h"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(change) or change == 0:
            continue
        item = dict(raw)
        item["price"] = float(item["price"])
        item["change24h"] = change
        item["change_pct"] = round(change * 100, 4)
        eligible.append(item)
    gainers = sorted((x for x in eligible if x["change24h"] > 0), key=lambda x: x["change24h"], reverse=True)[:8]
    losers = sorted((x for x in eligible if x["change24h"] < 0), key=lambda x: x["change24h"])[:8]
    ranked = gainers + losers
    return gainers, losers, ranked


@app.get("/api/market/movers")
async def perpequities_market_movers(_: dict = Depends(require_pro)):
    """Independent NASDAQ cash-equity gainers/losers for the current radar session."""
    status = market_status()
    if not stock_radar_enabled():
        return {
            "available": False, "assets": [], "gainers": [], "losers": [],
            "price_min": 0.30, "price_max": 15.00,
            "market_session": status.get("session"),
            "message_ar": "تظهر حركة الأسهم عند توفر جلسة رصد وبيانات سوق موثوقة.",
        }
    try:
        from .scanner import discover_market_movers
        payload = await asyncio.wait_for(discover_market_movers(), timeout=25)
        return {
            **payload,
            "source": payload.get("source") or "NASDAQ live market data",
            "price_min": 0.30, "price_max": 15.00,
            "market_session": status.get("session"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.warning("NASDAQ market movers failed: %s", type(exc).__name__)
        return {
            "available": False, "assets": [], "gainers": [], "losers": [],
            "price_min": 0.30, "price_max": 15.00,
            "market_session": status.get("session"),
            "message_ar": "تعذر جلب بيانات حركة ناسداك مؤقتًا؛ ستتم المحاولة في التحديث التالي.",
        }


@app.get("/api/market/ticker")
async def market_ticker(_: dict = Depends(require_pro)):
    return await ticker()


_MARKET_NEWS_RESPONSE_CACHE = {"at": 0.0, "session_key": "", "payload": None}


@app.get("/api/market/news")
async def market_news_endpoint(_: dict = Depends(require_pro)):
    """Cache news on the Riyadh 15:00–15:00 cycle; refresh frequently during the regular US session."""
    status = market_status()
    now_riyadh = datetime.now(ZoneInfo("Asia/Riyadh"))
    cycle_start = now_riyadh.replace(hour=15, minute=0, second=0, microsecond=0)
    if now_riyadh < cycle_start:
        cycle_start -= timedelta(days=1)
    session = str(status.get("session") or "closed")
    cycle_key = cycle_start.isoformat()
    # During the official US session, keep headlines fresh; outside it, reuse the
    # same translated payload for the current Saudi 3 PM–3 PM cycle.
    refresh_ttl = 60 if session == "regular" else 24 * 60 * 60
    session_key = cycle_key + ":" + session
    now_mono = time.monotonic()
    cached = _MARKET_NEWS_RESPONSE_CACHE
    cached_payload = cached.get("payload") if isinstance(cached, dict) else None
    cached_items = cached_payload.get("items", []) if isinstance(cached_payload, dict) else []
    cached_translated = int(cached_payload.get("translated_count") or 0) if isinstance(cached_payload, dict) else 0
    # Retry translation promptly when the cached feed contains untranslated headlines.
    # A 24-hour news cache must not freeze a temporary translation-provider failure for the whole day.
    translation_retry_due = (
        isinstance(cached_items, list)
        and bool(cached_items)
        and cached_translated < min(16, len(cached_items))
        and now_mono - float(cached.get("at") or 0) >= 2 * 60
    )
    if (cached.get("payload") and cached.get("session_key") == session_key
            and now_mono - cached["at"] < refresh_ttl
            and (cached["payload"].get("available") or refresh_ttl < 86400)
            and not translation_retry_due):
        return copy.deepcopy(cached["payload"])

    try:
        rows = await asyncio.wait_for(market_news(limit=24), timeout=10.0)
    except Exception as exc:
        logger.warning("Market news provider failed: %s", type(exc).__name__)
        rows = []
    if not rows:
        # If providers temporarily fail or return no matching stories, keep the
        # last known non-empty feed visible instead of replacing the ribbon with
        # an empty response. The frontend can still retry on the next refresh.
        previous = cached.get("payload") if isinstance(cached, dict) else None
        previous_items = previous.get("items", []) if isinstance(previous, dict) else []
        # Re-validate cached items before fallback: old cache entries may predate
        # the US-market relevance filter and must never leak unrelated headlines.
        if isinstance(previous_items, list) and previous_items:
            safe_items = [
                item for item in previous_items
                if isinstance(item, dict) and _is_relevant_stock_market_news(item)
            ]
            if safe_items:
                fallback = copy.deepcopy(previous)
                fallback["items"] = safe_items
                fallback["available"] = True
                fallback["stale"] = True
                fallback["message_ar"] = "تعذر تحديث الأخبار مؤقتًا؛ المعروض آخر أخبار السوق الأمريكي المطابقة."
                return fallback
        payload = {"items": [], "updated_at": datetime.now(timezone.utc).isoformat(), "available": False, "translated_count": 0}
        _MARKET_NEWS_RESPONSE_CACHE.update({"at": now_mono, "session_key": session_key, "payload": payload})
        return payload

    # The ribbon displays up to 16 cards. Translate all visible headlines and
    # keep the whole endpoint below the frontend request timeout.
    try:
        translated = await asyncio.wait_for(translate_stock_news("MARKET", rows[:16]), timeout=22.0)
    except Exception as exc:
        logger.info("Market news Arabic translation unavailable: %s", type(exc).__name__)
        translated = []
    # Retry missing headlines through the lightweight Arabic fallback before building the feed.
    translated_urls = {
        str(item.get("url") or "").strip()
        for item in translated if isinstance(item, dict) and item.get("translated_headline")
    }
    missing_headlines = [
        row for row in rows[:16]
        if str(row.get("url") or "").strip() not in translated_urls
        and str(row.get("headline") or row.get("title") or "").strip()
    ]
    if missing_headlines:
        try:
            fallback_headlines = await asyncio.wait_for(
                asyncio.gather(
                    *(_mymemory_translate(str(row.get("headline") or row.get("title") or ""))
                      for row in missing_headlines),
                    return_exceptions=True,
                ),
                timeout=7.0,
            )
            for row, fallback_headline in zip(missing_headlines, fallback_headlines):
                if isinstance(fallback_headline, str) and any(0x0600 <= ord(ch) <= 0x06FF for ch in fallback_headline):
                    translated.append({
                        "url": str(row.get("url") or ""),
                        "headline": str(row.get("headline") or row.get("title") or ""),
                        "translated_headline": fallback_headline,
                        "summary": "",
                        "basis": "headline_only",
                        "source": str(row.get("source") or ""),
                        "translation_provider": "MyMemory fallback retry",
                    })
        except Exception as exc:
            logger.info("Market news fallback translation unavailable: %s", type(exc).__name__)

    def normalized_news_url(value):
        # Match translation to source if tracking parameters or trailing slash differ.
        try:
            parts = urlsplit(str(value or "").strip())
            if not parts.scheme or not parts.netloc:
                return str(value or "").strip().rstrip("/")
            return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"
        except Exception:
            return str(value or "").strip().rstrip("/")

    by_url = {
        normalized_news_url(item.get("url")): item
        for item in translated if isinstance(item, dict) and item.get("url")
    }
    by_headline = {
        str(item.get("headline") or "").strip().casefold(): item
        for item in translated if isinstance(item, dict) and item.get("headline")
    }
    result = []
    translated_count = 0
    for row in rows:
        url = str(row.get("url") or "").strip()
        headline = str(row.get("headline") or row.get("title") or "").strip()
        translation = by_url.get(normalized_news_url(url)) or by_headline.get(headline.casefold()) or {}
        translated_headline = str(translation.get("translated_headline") or "").strip()
        if translated_headline:
            translated_count += 1
        result.append({
            **row,
            "translated_headline": translated_headline,
            "translated_summary": str(translation.get("summary") or ""),
            "translation_basis": str(translation.get("basis") or ""),
            "translation_provider": str(translation.get("translation_provider") or ""),
        })
    payload = {
        "items": result,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "available": True,
        "translated_count": translated_count,
    }
    _MARKET_NEWS_RESPONSE_CACHE.update({"at": time.monotonic(), "session_key": session_key, "payload": payload})
    return payload


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

        # Dashboard cards are for the current session only. Historical records remain
        # in the database for analytics, but must not masquerade as current opportunities.

    radar_stocks = []
    moves = []
    volumes = []
    seen = set()
    for row in rows:
        try:
            payload = json.loads(row.payload or "{}")
            payload.setdefault("symbol", row.symbol)
            payload.setdefault("created_at", row.created_at.isoformat() if row.created_at else None)
            payload.setdefault("session_date", row.session_date)
            # اعرض فقط الفرص التي لا تزال نشطة ومؤكدة واجتازت بوابة النشر.
            # لا تسمح لحالة watch أو لسجل من جلسة اليوم بإعادة إظهار فرصة قديمة.
            if not is_current_active_opportunity(payload):
                continue
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
            "last_signal_at": radar_stocks[0].get("created_at") if radar_stocks else None,
            "source_session": radar_stocks[0].get("session_date") if radar_stocks else None,
            "historical": False,
            "stocks": radar_stocks[:20],
        },
        "updated_at": utcnow().isoformat(),
    }

def _update_radar_app_scan_progress(job_id: str, progress: dict):
    job = _radar_app_scan_jobs.get(job_id)
    if job and job.get("status") == "running" and isinstance(progress, dict):
        job["progress"] = dict(progress)


async def _execute_radar_app_scan(job_id: str):
    """Run a manual Mini App scan asynchronously and retain its result for pollers."""
    started_monotonic = time.monotonic()
    job = _radar_app_scan_jobs.get(job_id)
    if not job:
        return

    try:
        from .scanner import scan_us_low_price_stocks

        status = market_status()
        app_limit = max(1, int(settings.radar_app_daily_limit or 15))
        session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        logger.info("RADAR_APP_SCAN_STARTED job_id=%s session=%s", job_id, status.get("session", "unknown"))

        if not stock_radar_enabled():
            result = {
                "enabled": False,
                "historical": True,
                "reason": status.get("label_ar", "السوق خارج جلسة الرصد"),
                "session": status.get("session", "unknown"),
                "scan_at": None,
                "session_date": session_date,
                "stocks": [],
                "diagnostics": {
                    "candidates": 0, "passed": 0, "filtered": 0, "errors": 0,
                    "daily_limit": app_limit,
                },
            }
        else:
            _update_radar_app_scan_progress(job_id, {
                "stage": "discovering",
                "message_ar": "نجمع الأسهم الأمريكية النشطة من مصادر السوق…",
                "done": 0,
                "total": 0,
            })

            def progress_callback(progress):
                _update_radar_app_scan_progress(job_id, progress)

            scanned = await scan_us_low_price_stocks(
                force_refresh=True,
                progress_callback=progress_callback,
            )
            # A fresh scanner result is not yet a published opportunity: it may
            # not have the saved-signal lifecycle fields (radar_active/channel_gate).
            # Keep confirmed opportunities separate from preliminary candidates so
            # the UI can honestly show scan activity without weakening publish rules.
            scanned_stocks = scanned.get("stocks") or []
            active_stocks = [
                stock for stock in scanned_stocks
                if is_current_active_opportunity(stock)
            ]
            preliminary_stocks = [
                stock for stock in scanned_stocks
                if isinstance(stock, dict)
                and str(stock.get("symbol") or "").strip()
            ]
            result = {
                "enabled": True,
                "historical": False,
                "persisted": False,
                "scan_at": datetime.now(timezone.utc).isoformat(),
                "range": {"min": 0.30, "max": 6.00},
                "method": "SAS PRO Radar",
                "session": status.get("session", "unknown"),
                "stocks": active_stocks[:app_limit],
                "preliminary_stocks": preliminary_stocks[:app_limit],
                "diagnostics": {
                    **(scanned.get("diagnostics") or {}),
                    "daily_limit": app_limit,
                    "preliminary_count": len(preliminary_stocks),
                    "confirmed_count": len(active_stocks),
                    "lifecycle_rejected": len(scanned_stocks) - len(active_stocks),
                },
            }

            # Persist preliminary candidates separately from published RadarSignal rows.
            # This allows the Mini App to display candidates after reopening without
            # triggering an expensive new scan or weakening publication requirements.
            try:
                async with SessionLocal() as db:
                    await setting_set(db, "radar_preliminary_snapshot", json.dumps({
                        "session_date": session_date,
                        "scan_at": result["scan_at"],
                        "stocks": preliminary_stocks[:app_limit],
                    }, ensure_ascii=False))
                    await db.commit()
            except Exception:
                logger.exception("Failed to persist preliminary radar snapshot")

        job["result"] = result
        job["status"] = "success"
        job["progress"] = {
            "stage": "completed",
            "message_ar": "اكتمل الرصد وتم تجهيز النتائج.",
            "done": 1,
            "total": 1,
        }
        elapsed = round(time.monotonic() - started_monotonic, 2)
        diag = result.get("diagnostics") or {}
        logger.info(
            "RADAR_APP_SCAN_FINISHED job_id=%s status=success duration_seconds=%s candidates=%s passed=%s errors=%s",
            job_id, elapsed, diag.get("candidates", 0), diag.get("passed", 0), diag.get("errors", 0),
        )
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = {"type": type(exc).__name__, "message": str(exc)[:240]}
        job["progress"] = {
            "stage": "failed",
            "message_ar": "تعذر إكمال الرصد؛ احتفظنا بالنتائج السابقة.",
            "done": 0,
            "total": 0,
        }
        logger.exception("RADAR_APP_SCAN_FAILED job_id=%s", job_id)
    finally:
        job["finished_at"] = utcnow().isoformat()
        job["duration_seconds"] = round(time.monotonic() - started_monotonic, 2)
        _radar_app_scan_tasks.pop(job_id, None)


@app.post("/api/radar/scan/start")
async def radar_scan_start(_: dict = Depends(require_pro)):
    """Queue one shared manual scan and return immediately."""
    global _radar_app_active_scan_job_id

    async with _radar_app_scan_lock:
        active = _radar_app_scan_jobs.get(_radar_app_active_scan_job_id or "")
        if active and active.get("status") == "running":
            return {
                "job_id": _radar_app_active_scan_job_id,
                "status": "running",
                "started_at": active.get("started_at"),
                "progress": active.get("progress") or {},
            }

        job_id = str(time.time_ns())
        _radar_app_scan_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "started_at": utcnow().isoformat(),
            "finished_at": None,
            "duration_seconds": None,
            "progress": {
                "stage": "starting",
                "message_ar": "نجهز دورة الرصد…",
                "done": 0,
                "total": 0,
            },
            "result": None,
            "error": None,
        }
        _radar_app_active_scan_job_id = job_id
        task = asyncio.create_task(_execute_radar_app_scan(job_id), name=f"saspro-radar-app-scan-{job_id}")
        _radar_app_scan_tasks[job_id] = task

        # Discard only old, completed job snapshots; never remove the active one.
        for old_id in list(_radar_app_scan_jobs):
            if len(_radar_app_scan_jobs) <= _RADAR_APP_SCAN_JOB_HISTORY_LIMIT:
                break
            old_job = _radar_app_scan_jobs.get(old_id) or {}
            if old_id != job_id and old_job.get("status") != "running":
                _radar_app_scan_jobs.pop(old_id, None)
                _radar_app_scan_tasks.pop(old_id, None)

        return {
            "job_id": job_id,
            "status": "running",
            "started_at": _radar_app_scan_jobs[job_id]["started_at"],
            "progress": _radar_app_scan_jobs[job_id]["progress"],
        }


@app.get("/api/radar/scan/status")
async def radar_scan_status(job_id: str, _: dict = Depends(require_pro)):
    """Return the current stage or completed payload for an authorized scan job."""
    if not job_id or len(job_id) > 32 or not job_id.isdigit():
        raise HTTPException(400, "معرّف مهمة الرصد غير صالح.")

    job = _radar_app_scan_jobs.get(job_id)
    if not job:
        return {"job_id": job_id, "status": "not_found"}

    response = {
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "duration_seconds": job.get("duration_seconds"),
        "progress": job.get("progress") or {},
    }
    if job.get("status") == "success":
        response["result"] = job.get("result")
    elif job.get("status") == "failed":
        response["error"] = job.get("error") or {"type": "UnknownError", "message": "تعذر إكمال مسح الأسهم."}
    return response


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
                # لا تظهر في المحفوظ إلا الفرصة المؤكدة التي ما زالت نشطة واجتازت بوابة النشر.
                if not is_current_active_opportunity(payload):
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
            preliminary_stocks = []
            preliminary_scan_at = None
            try:
                async with SessionLocal() as db:
                    snapshot = await db.get(Setting, "radar_preliminary_snapshot")
                    saved = json.loads(snapshot.value or "{}") if snapshot else {}
                if isinstance(saved, dict) and saved.get("session_date") == session_date:
                    preliminary_stocks = [
                        {**stock, "_radar_preliminary_candidate": True}
                        for stock in (saved.get("stocks") or [])
                        if isinstance(stock, dict) and str(stock.get("symbol") or stock.get("ticker") or "").strip()
                    ][:app_limit]
                    preliminary_scan_at = saved.get("scan_at")
            except Exception:
                logger.exception("Failed to load preliminary radar snapshot")
            return {
                "enabled": stock_radar_enabled(),
                "historical": False,
                "persisted": True,
                "scan_at": latest.created_at.isoformat() if latest and latest.created_at else preliminary_scan_at,
                "session_date": session_date,
                "session": status.get("session", "unknown"),
                "stocks": stocks[:app_limit],
                "preliminary_stocks": preliminary_stocks,
                "diagnostics": {
                    "candidates": 0,
                    "passed": len(stocks[:app_limit]),
                    "filtered": 0,
                    "errors": 0,
                    "preliminary_count": len(preliminary_stocks),
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
    # A chart can render a short run of genuine OHLC candles; do not require the 20 bars used by indicators.
    data = await ohlcv(raw_symbol, days=90, interval="1d", min_candles=4, timeout_seconds=15.0)
    if isinstance(data, list):
        return {"symbol": raw_symbol, "candles": data, "available": len(data) > 0, "enough_data": len(data) >= 20, "source": "legacy"}
    candles = data.get("candles") or []
    # The chart can draw real candles even when there are fewer than 20;
    # technical indicators still require enough bars and must not be fabricated.
    return {"symbol": raw_symbol, **data, "available": len(candles) > 0, "enough_data": len(candles) >= 20}

@app.get("/api/stocks/{symbol}/perpequities")
async def stock_perpequities_analysis(symbol: str, _: dict = Depends(require_pro)):
    """Return PerpEquities contract context and canonical chart/research page."""
    raw_symbol = symbol.upper().strip()
    if is_crypto_symbol(raw_symbol):
        return {"symbol": raw_symbol, "available": False, "source": "PerpEquities",
                "message_ar": "مصدر PerpEquities مخصص لعقود الأسهم وليس لهذا الأصل الرقمي."}
    try:
        from .perpequities_provider import get_equity_perpetual
        context = await asyncio.wait_for(get_equity_perpetual(raw_symbol), timeout=9.0)
        if not context:
            return {"symbol": raw_symbol, "available": False, "source": "PerpEquities",
                    "source_url": "https://perpequities.com/ar",
                    "message_ar": "لا تتوفر حاليًا بيانات حديثة لهذا السهم في PerpEquities."}
        return {"symbol": raw_symbol, "available": True, **context,
                "chart_url": context.get("source_url") or "https://perpequities.com/ar"}
    except Exception as exc:
        logger.info("PerpEquities analysis unavailable for %s: %s", raw_symbol, type(exc).__name__)
        return {"symbol": raw_symbol, "available": False, "source": "PerpEquities",
                "source_url": "https://perpequities.com/ar",
                "message_ar": "تعذر تحميل تحليل PerpEquities الآن؛ يمكنك فتح المصدر مباشرة."}

@app.get("/api/stocks/{symbol}/cusp")
async def stock_cusp_analysis(symbol: str, _: dict = Depends(require_pro)):
    """Public CUSP Wealth asset-page data for supplementary stock fundamentals."""
    raw_symbol = symbol.upper().strip()
    if is_crypto_symbol(raw_symbol):
        return {"symbol": raw_symbol, "available": False, "source": "CUSP Wealth",
                "message_ar": "مصدر CUSP مخصص لصفحات الأسهم وليس للأصول الرقمية."}
    try:
        from .cuspwealth_provider import get_cusp_asset
        result = await asyncio.wait_for(get_cusp_asset(raw_symbol), timeout=12.0)
        # Keep the source explicit and preserve nulls for data CUSP does not publish.
        result.setdefault("source", "CUSP Wealth")
        result.setdefault("source_url", f"https://cuspwealth.com/ar/asset/{raw_symbol}")
        if result.get("available"):
            result["message_ar"] = "بيانات مالية مستخرجة من صفحة السهم في CUSP Wealth؛ القيم غير المنشورة تبقى غير متاحة."
        return result
    except Exception as exc:
        logger.info("CUSP Wealth page unavailable for %s: %s", raw_symbol, type(exc).__name__)
        return {"symbol": raw_symbol, "available": False, "source": "CUSP Wealth",
                "source_url": f"https://cuspwealth.com/ar/asset/{raw_symbol}",
                "message_ar": "تعذر تحميل بيانات CUSP الآن.", "error": type(exc).__name__}

@app.get("/api/market/institutional-recommendations")
async def institutional_recommendations_endpoint(_: dict = Depends(require_pro)):
    """News-style feed for current headlines about analyst actions and price targets.
    Uses configured news providers; does not invent ratings or scrape analyst pages.
    """
    cache = getattr(institutional_recommendations_endpoint, "_cache", {})
    now_mono = time.monotonic()
    if cache.get("payload") and now_mono - float(cache.get("at") or 0) < 300:
        return copy.deepcopy(cache["payload"])
    # Query the dedicated analyst-action feed independently of the general-market
    # relevance gate. That gate intentionally excludes many analyst-only headlines.
    market_rows, analyst_rows = await asyncio.gather(
        asyncio.wait_for(market_news(limit=40), timeout=12.0),
        asyncio.wait_for(_newsfilter_market_news(limit=40), timeout=12.0),
        return_exceptions=True,
    )
    rows = []
    for result in (market_rows, analyst_rows):
        if isinstance(result, list):
            rows.extend(item for item in result if isinstance(item, dict))
        elif isinstance(result, Exception):
            logger.warning("Institutional recommendation provider failed: %s", type(result).__name__)
    action_terms = (
        "analyst", "price target", "price-target", "upgrade", "upgraded", "downgrade",
        "downgraded", "rating", "overweight", "underweight", "buy rating", "sell rating",
        "initiated", "reiterated", "raised to", "cut to", "price objective",
        "goldman sachs", "jpmorgan", "jp morgan", "morgan stanley", "bank of america",
        "bofa", "citigroup", "citi", "barclays", "ubs", "wells fargo", "jefferies",
        "raymond james", "needham", "benchmark", "wedbush", "piper sandler",
    )
    selected, seen = [], set()
    for row in sorted((x for x in rows if isinstance(x, dict)), key=lambda x: int(x.get("datetime") or 0), reverse=True):
        headline = str(row.get("headline") or "").strip()
        text_blob = (headline + " " + str(row.get("summary") or "")).casefold()
        url = str(row.get("url") or "").strip()
        if not headline or not url.startswith(("https://", "http://")) or not any(term in text_blob for term in action_terms):
            continue
        dedupe = re.sub(r"[^a-z0-9]+", " ", headline.casefold()).strip()
        if not dedupe or dedupe in seen:
            continue
        seen.add(dedupe)
        selected.append({**row, "category": "institutional_recommendation"})
        if len(selected) >= 16:
            break
    translated = []
    if selected:
        try:
            translated = await asyncio.wait_for(translate_stock_news("INSTITUTIONAL", selected), timeout=18.0)
        except Exception as exc:
            logger.info("Institutional recommendation translation unavailable: %s", type(exc).__name__)
    translated_by_url = {str(item.get("url") or "").rstrip("/"): item for item in translated if isinstance(item, dict) and item.get("url")}
    items = []
    for row in selected:
        item = dict(row)
        tr = translated_by_url.get(str(row.get("url") or "").rstrip("/"), {})
        item["translated_headline"] = str(tr.get("translated_headline") or "").strip()
        item["translated_summary"] = str(tr.get("summary") or "").strip()
        item["source"] = str(row.get("source") or "مصدر إخباري")
        items.append(item)
    payload = {
        "items": items, "updated_at": datetime.now(timezone.utc).isoformat(),
        "available": bool(items),
        "message_ar": "" if items else "لا توجد حاليًا أخبار حديثة مطابقة عن ترقيات المحللين أو الأسعار المستهدفة من المصادر المتاحة.",
        "mode": "news_headlines",
    }
    institutional_recommendations_endpoint._cache = {"at": now_mono, "payload": payload}
    return payload



@app.get("/api/stocks/{symbol}/news")
async def stock_news(symbol: str, _: dict = Depends(require_pro)):
    """Return original company-news evidence plus best-effort Arabic translation fields."""
    key = symbol.upper().strip()
    try:
        rows = await asyncio.wait_for(
            company_news(key, include_marketaux=True),
            timeout=12.0,
        )
    except Exception as exc:
        logger.warning("Company news fetch failed for %s: %s", key, type(exc).__name__)
        rows = []

    if not isinstance(rows, list) or not rows:
        return []

    # Translation must never replace the original headline, source, or URL.
    try:
        translated = await asyncio.wait_for(
            translate_stock_news(key, rows[:16]),
            timeout=22.0,
        )
    except Exception as exc:
        logger.info("Company news Arabic translation unavailable for %s: %s", key, type(exc).__name__)
        translated = []

    by_url = {
        str(item.get("url") or "").strip(): item
        for item in translated if isinstance(item, dict) and item.get("url")
    }
    by_headline = {
        str(item.get("headline") or "").strip().casefold(): item
        for item in translated if isinstance(item, dict) and item.get("headline")
    }

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        headline = str(row.get("headline") or row.get("title") or "").strip()
        translation = by_url.get(url) or by_headline.get(headline.casefold()) or {}
        result.append({
            **row,
            "headline": headline,
            "translated_headline": str(translation.get("translated_headline") or ""),
            "translated_summary": str(translation.get("summary") or ""),
            "translation_basis": str(translation.get("basis") or ""),
            "translation_provider": str(translation.get("translation_provider") or ""),
        })
    return result

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
    result = await _quote_for_current_session(raw_symbol)
    # Only attach already-cached PerpEquities context: do not delay live spot quotes.
    try:
        from .perpequities_provider import get_cached_equity_perpetual
        context = get_cached_equity_perpetual(raw_symbol)
        if context and isinstance(result, dict):
            result["perpequities_context"] = context
    except Exception:
        pass
    return result

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
            live_quote = await _quote_for_current_session(symbol)
            if isinstance(live_quote, dict) and live_quote.get("price"):
                cached["quote"] = live_quote
        except Exception:
            pass
        # ترقية النتائج القديمة بعد إضافة محرك الجودة: لا نعيد كاشًا بلا نجوم أو خطة مشروطة.
        try:
            cached_sas = cached.setdefault("sas_pro", {})
            cached_classification = cached_sas.get("classification")
            if not isinstance(cached_classification, dict) or not all(
                key in cached_classification for key in ("quality_score", "stars", "conditional_plan", "quality_checks", "trading_style")
            ):
                from .scanner import classify_sas
                refreshed_classification = await asyncio.wait_for(
                    classify_sas(symbol, cached.get("quote") or {}, allow_twelve_fallback=True),
                    timeout=25.0,
                )
                if isinstance(refreshed_classification, dict):
                    cached_sas["classification"] = refreshed_classification
                    try:
                        cached_sas["report"] = build_report(symbol, cached.get("quote") or {}, cached_sas.get("targets") or {}, refreshed_classification)
                    except Exception:
                        pass
                    _cache_put(_analysis_memory, symbol, cached)
        except Exception as exc:
            logger.exception("Could not refresh cached SAS quality classification for %s", symbol)
            cached_sas = cached.setdefault("sas_pro", {})
            old_cls = cached_sas.get("classification") if isinstance(cached_sas.get("classification"), dict) else {}
            cached_sas["classification"] = {
                **old_cls,
                "quality_score": None,
                "stars": None,
                "conditional_plan": {},
                "quality_checks": {},
                "trading_style": None,
                "quality_error": f"{type(exc).__name__}: {str(exc)[:180]}",
                "quality_data_available": False,
                "recommendation": "غير قابل للتقييم — تعذر تحديث بيانات المؤشرات؛ لا تعتمد على النجوم أو المستويات القديمة.",
            }
            _cache_put(_analysis_memory, symbol, cached)
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
        _quote_for_current_session(symbol),
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
        company_news(symbol, days=3, include_marketaux=True),
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
            exc = classification
            logger.error(
                "SAS quality classification returned exception for %s: %s",
                symbol, type(exc).__name__, exc_info=(type(exc), exc, exc.__traceback__),
            )
            classification = {
                "type": "غير محدد", "behavior": "غير قابل للتقييم",
                "score": None, "quality_score": None, "stars": None,
                "quality_checks": {}, "conditional_plan": {}, "trading_style": None,
                "risk_level": "غير محدد", "risk_score": None, "risk_emoji": "⚠️",
                "risk_reasons": ["تعذر جلب بيانات المؤشرات"],
                "quality_data_available": False,
                "quality_error": f"{type(exc).__name__}: {str(exc)[:180]}",
                "reason": "تعذر حساب المؤشرات من المصادر الحالية؛ لم يتم تخمين التقييم.",
                "recommendation": "غير قابل للتقييم — تعذر حساب المؤشرات من البيانات المتاحة.",
                "pass": False,
            }
    except Exception as exc:
        logger.exception("SAS quality classification failed for %s", symbol)
        classification = {
            "type": "غير محدد", "behavior": "غير قابل للتقييم",
            "score": None, "quality_score": None, "stars": None,
            "quality_checks": {}, "conditional_plan": {}, "trading_style": None,
            "risk_level": "غير محدد", "risk_score": None, "risk_emoji": "⚠️",
            "risk_reasons": ["تعذر جلب بيانات المؤشرات"],
            "quality_data_available": False,
            "quality_error": f"{type(exc).__name__}: {str(exc)[:180]}",
            "reason": "تعذر حساب المؤشرات من المصادر الحالية؛ لم يتم تخمين التقييم.",
            "recommendation": "غير قابل للتقييم — تعذر حساب المؤشرات من البيانات المتاحة.",
            "pass": False,
        }

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

    summary_by_story = {}
    for summary_item in analysis_payload.get("news_summaries") or []:
        if not isinstance(summary_item, dict):
            continue
        story_key = (str(summary_item.get("url") or "").strip(), str(summary_item.get("headline") or "").strip())
        if story_key[0] and story_key[1] and summary_item.get("summary"):
            summary_by_story[story_key] = summary_item
    news_for_display = []
    for item in news if isinstance(news, list) else []:
        if not isinstance(item, dict):
            news_for_display.append(item)
            continue
        display_item = dict(item)
        story_key = (str(item.get("url") or "").strip(), str(item.get("headline") or "").strip())
        summary_item = summary_by_story.get(story_key)
        if summary_item:
            display_item["ai_summary"] = summary_item.get("summary")
            display_item["ai_summary_basis"] = summary_item.get("basis")
        news_for_display.append(display_item)
    # Translate verified headlines/summaries into Arabic without changing source URLs.
    try:
        translated_news = await translate_stock_news(symbol, news_for_display)
        analysis_payload["news_summaries"] = translated_news

        # The Mini App renders targets["news_items"], not ai_analysis.news_summaries.
        # Merge translations into the actual display payload while retaining the
        # original headline and source URL for traceability.
        translations_by_story = {
            (str(row.get("url") or "").strip(), str(row.get("headline") or "").strip()): row
            for row in translated_news
            if isinstance(row, dict) and row.get("url") and row.get("headline")
        }
        for display_item in news_for_display:
            if not isinstance(display_item, dict):
                continue
            original_headline = str(display_item.get("headline") or "").strip()
            story_key = (str(display_item.get("url") or "").strip(), original_headline)
            translated = translations_by_story.get(story_key)
            if not translated:
                continue
            arabic_headline = str(translated.get("translated_headline") or "").strip()
            arabic_summary = str(translated.get("summary") or "").strip()
            if arabic_headline:
                display_item["original_headline"] = original_headline
                display_item["translated_headline"] = arabic_headline
                display_item["headline"] = arabic_headline
            if arabic_summary:
                display_item["original_summary"] = str(display_item.get("ai_summary") or display_item.get("summary") or "")
                display_item["summary"] = arabic_summary
                display_item["ai_summary"] = arabic_summary
            display_item["translation_basis"] = translated.get("basis") or "article_or_headline"
    except Exception:
        logger.exception("Failed to attach Arabic stock-news translations for %s", symbol)
        analysis_payload["news_summaries"] = []
    targets["news_items"] = news_for_display
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
        "news": news_for_display,
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
        cached_quote = q_live if isinstance(q_live, dict) and q_live.get("price") else cached.get("quote", {})
        cached_classification = cached.get("classification")
        cached_targets = cached.get("targets")
        # Quick-cache entries created before the quality panel may lack all classification fields.
        if not isinstance(cached_classification, dict) or not all(
            key in cached_classification for key in ("quality_score", "stars", "conditional_plan", "quality_checks", "trading_style")
        ):
            try:
                from .scanner import classify_sas
                refreshed_classification = await asyncio.wait_for(
                    classify_sas(symbol, cached_quote or {}, allow_twelve_fallback=True),
                    timeout=25.0,
                )
                if isinstance(refreshed_classification, dict):
                    cached_classification = refreshed_classification
                    cached["classification"] = refreshed_classification
                    _cache_put(_quick_scan_memory, symbol, cached)
            except Exception:
                logger.exception("Could not refresh quick-cache SAS quality classification for %s", symbol)
        return {
            "ok": True,
            "symbol": symbol,
            "quote": cached_quote,
            "mini_analysis": cached.get("mini_analysis", cached),
            "classification": cached_classification or {},
            "targets": cached_targets or {},
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

    # If the primary classifier cannot provide indicators, calculate a conservative
    # fallback from observed historical OHLCV candles (never from the quote alone).
    if cls.get("rsi14") is None or cls.get("rvol") is None:
        try:
            history = await asyncio.wait_for(ohlcv(symbol, days=90, interval="1d"), timeout=18.0)
            candles = history.get("candles", []) if isinstance(history, dict) else []
            valid = [x for x in candles if all(x.get(k) is not None for k in ("close", "high", "low"))]
            if len(valid) >= 20:
                closes = [float(x["close"]) for x in valid]
                highs = [float(x["high"]) for x in valid]
                lows = [float(x["low"]) for x in valid]
                volumes = [float(x.get("volume") or 0) for x in valid]
                last = closes[-1]
                sma20 = sum(closes[-20:]) / 20
                sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
                gains, losses = [], []
                for a, b in zip(closes[-15:-1], closes[-14:]):
                    delta = b - a
                    gains.append(max(delta, 0))
                    losses.append(max(-delta, 0))
                avg_gain = sum(gains) / max(len(gains), 1)
                avg_loss = sum(losses) / max(len(losses), 1)
                rsi = 100.0 if avg_loss == 0 and avg_gain > 0 else (50.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
                avg_vol = sum(volumes[-21:-1]) / max(len(volumes[-21:-1]), 1)
                rvol_fallback = volumes[-1] / avg_vol if avg_vol > 0 else None
                trend = "صاعد" if last > sma20 and (sma50 is None or sma20 >= sma50) else ("هابط" if last < sma20 and (sma50 is None or sma20 <= sma50) else "عرضي")
                if not cls.get("behavior") or cls.get("behavior") == "غير واضح":
                    cls["behavior"] = trend
                if cls.get("rsi14") is None:
                    cls["rsi14"] = rsi
                if cls.get("rvol") is None:
                    cls["rvol"] = rvol_fallback
                cls.setdefault("score_breakdown", {})
                if isinstance(cls["score_breakdown"], dict):
                    if not cls["score_breakdown"].get("momentum"):
                        cls["score_breakdown"]["momentum"] = 20 if rsi >= 60 else (10 if rsi >= 50 else 0)
                    if not cls["score_breakdown"].get("volume"):
                        cls["score_breakdown"]["volume"] = 15 if rvol_fallback is not None and rvol_fallback >= 1.5 else 0
                cls["fallback_source"] = history.get("source", "OHLCV")
                cls["fallback_candles"] = len(valid)
                # Mark the fallback as descriptive, not as a confirmed breakout signal.
                cls.setdefault("strategy_pass", False)
                cls.setdefault("breakout_confirmed", False)
                cls.setdefault("accumulation", False)
        except Exception:
            pass

    try:
        targets = await asyncio.wait_for(technical_targets(symbol), timeout=15.0)
    except Exception:
        targets = {}

    target_list = targets.get("targets") if isinstance(targets, dict) else []
    target_list = target_list if isinstance(target_list, list) else []
    entry = targets.get("price") or q.get("price")
    stop = targets.get("exit") or targets.get("stop") or targets.get("support")
    target = target_list[0] if target_list else targets.get("target1")
    # Only calculate illustrative ATR levels when real candles exist and the
    # primary target service returned no levels; never derive levels from price alone.
    if entry and (stop is None or target is None):
        try:
            if len(valid) >= 20:
                atr = sum((float(x["high"]) - float(x["low"])) for x in valid[-14:]) / 14
                if atr > 0:
                    if stop is None: stop = round(float(entry) - 1.5 * atr, 4)
                    if target is None: target = round(float(entry) + 2.0 * atr, 4)
                    cls["levels_source"] = "ATR from historical OHLCV"
        except (NameError, TypeError, ValueError, KeyError):
            pass

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
        # Private messages no longer run or send fallback stock analysis.
        # Direct the subscriber to the Analysis icon in the SAS PRO app instead.
        await send_message(
            chat_id,
            f"📊 <b>SAS PRO | تحليل السهم {html.escape(symbol)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "للحصول على تحليل السهم ومؤشراته بشكل واضح ومحدّث،\n"
            "📱 افتح تطبيق <b>SAS PRO</b> واضغط أيقونة <b>تحليل السهم</b>، ثم ابحث عن الرمز.\n"
            f"🔎 رمز السهم: <code>{html.escape(symbol)}</code>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ ستظهر البيانات المتاحة داخل التطبيق مع مصدرها، دون عرض أهداف أو مستويات غير موثوقة.\n"
            "⚠️ المعلومات تعليمية فقط وليست توصية استثمارية."
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


class _SECTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "ix:header"}:
            self.skip_depth += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style", "ix:header"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "br", "tr", "td", "h1", "h2", "h3", "section"}:
            self.parts.append(" ")
    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())
    def text(self):
        value = " ".join(self.parts)
        value = re.sub(r"\\[nrt]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()


_SEC_FILING_TEXT_CACHE = {}
_SEC_FILING_TEXT_TTL = 1800

def _clean_sec_filing_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\\[nrt]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    # Remove common SEC cover-page boilerplate so the summary does not repeat it.
    value = re.sub(r"UNITED STATES SECURITIES AND EXCHANGE COMMISSION.*?FORM\s+(?:8-K|10-Q|10-K)\b", " ", value, flags=re.I)
    value = re.sub(r"WASHINGTON,?\s+D\.?C\.?\s+\d{5}", " ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip()
    return value

async def _sec_fetch_filing_text(client, url):
    now = time.time()
    cached = _SEC_FILING_TEXT_CACHE.get(url)
    if cached and now - cached[0] < _SEC_FILING_TEXT_TTL:
        return cached[1]
    try:
        response = await client.get(url, headers={"Accept": "text/html,application/xhtml+xml,text/plain"})
        response.raise_for_status()
        extractor = _SECTextExtractor()
        extractor.feed(response.text[:2_000_000])
        text = _clean_sec_filing_text(extractor.text())[:12000]
        _SEC_FILING_TEXT_CACHE[url] = (now, text)
        return text
    except (httpx.HTTPError, ValueError):
        return ""

def _classify_sec_filing_text(form, description, items, text):
    clean = _clean_sec_filing_text(text)
    source = (str(description or "") + " " + str(items or "") + " " + clean).lower()
    item_codes = re.findall(r"\b\d{1,2}\.\d{2}\b", str(items or ""))
    item_map = {
        "1.01": "توقيع اتفاقية مهمة",
        "1.02": "إنهاء اتفاقية مهمة",
        "1.03": "إفلاس أو إعادة هيكلة",
        "2.01": "إتمام استحواذ أو بيع أصل",
        "2.02": "إعلان نتائج مالية",
        "2.03": "التزام مالي أو اقتراض كبير",
        "2.04": "تغيّر في التزامات مالية",
        "2.05": "إعادة هيكلة أو إغلاق أعمال",
        "2.06": "خسارة أو انخفاض قيمة أصل",
        "3.01": "مشكلة في شروط الإدراج",
        "5.02": "تغيير في الإدارة",
        "5.07": "نتائج تصويت المساهمين",
        "7.01": "معلومات معلنة للمستثمرين",
        "8.01": "تطور آخر مهم",
        "9.01": "مرفقات داعمة للإفصاح",
    }
    matched = []
    evidence = ""
    impact = "غير واضح — لا توجد أدلة كافية لتحديد اتجاه الأثر"
    impact_class = "unknown"
    rules = [
        (r"bankruptcy|chapter 11|going concern|default under|material weakness in internal control|received a delisting notice|delisting", "إشارة سلبية في النص", "negative"),
        (r"revenue increased by|net income increased by|raises its full.year guidance|record quarterly revenue|awarded .* contract|completed the acquisition", "إشارة إيجابية في النص", "positive"),
        (r"terminated .* agreement|withdraws its guidance|lowers its full.year guidance|revenue declined by|net loss increased by|material impairment charge", "إشارة سلبية في النص", "negative"),
    ]
    for pattern, label, cls in rules:
        match = re.search(pattern, source, re.I)
        if match:
            matched.append((label, cls))
            if not evidence:
                evidence = source[max(0, match.start()-100):match.end()+160]
    if matched:
        classes = {x[1] for x in matched}
        if len(classes) == 1:
            impact_class = matched[0][1]
            impact = matched[0][0] + " (بحسب العبارة الظاهرة)"
        else:
            impact_class = "unknown"
            impact = "مختلط — النص يحتوي مؤشرات متعارضة"
            evidence = "توجد إشارات إيجابية وسلبية في النص؛ يلزم تقييم التفاصيل معًا."
    if clean:
        # Skip the filing cover text; use the first informative sentence/section.
        event = clean
        for marker in ("Item 1.01", "Item 2.02", "Item 2.03", "Item 5.02", "Item 8.01", "Management's Discussion", "Results of Operations"):
            pos = event.lower().find(marker.lower())
            if pos >= 0:
                event = event[pos:]
                break
        event = event[:650].strip()
    elif item_codes:
        descriptions = [item_map[c] for c in item_codes if c in item_map]
        event = "موضوع الإفصاح: " + "، ".join(dict.fromkeys(descriptions)) if descriptions else "بنود الإفصاح: " + ", ".join(item_codes)
    elif description and not re.search(r"current report|primary document", description, re.I):
        event = str(description)
    else:
        event = "ما قدرنا نستخرج نص واضح يشرح الحدث من المستند."
    if not evidence:
        evidence = "ما ظهرت عبارة صريحة كافية لتصنيف الأثر."
    return {
        "eventSummary": event,
        "impactLabel": impact,
        "impactClass": impact_class,
        "impactEvidence": evidence[:300],
        "analysisBasis": "نص المستند" if clean else ("بنود الإفصاح فقط" if item_codes else "بيانات وصفية فقط"),
        "textAvailable": bool(clean),
    }

# SEC EDGAR disclosure search. Public filing data only; cached to respect SEC fair-access limits.
_SEC_TICKERS_CACHE = {"expires": 0.0, "items": None}
_SEC_SUBMISSIONS_CACHE = {}
_SEC_CACHE_TTL = 600

@app.get("/disclosures")
async def disclosures_page():
    return FileResponse(Path("web/disclosures.html"))

@app.get("/api/disclosures/search")
async def search_sec_disclosures(
    q: str,
    form: str = "all",
    days: str = "all",
    limit: int = 100,
):
    query = (q or "").strip()[:100]
    if not query:
        raise HTTPException(400, "أدخل رمز السهم أو اسم الشركة.")
    allowed_forms = {"all", "8-K", "10-K", "10-Q", "S-1", "S-3", "424B5", "DEF 14A", "4", "SC 13D", "SC 13G"}
    if form not in allowed_forms:
        raise HTTPException(400, "نوع الإفصاح غير مدعوم.")
    if days not in {"all", "30", "90", "365"}:
        raise HTTPException(400, "الفترة الزمنية غير صحيحة.")
    limit = max(1, min(int(limit or 100), 100))
    sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if (
        not sec_user_agent
        or "@" not in sec_user_agent
        or "example.com" in sec_user_agent.casefold()
        or "configure sec_user_agent" in sec_user_agent.casefold()
        or "your_email" in sec_user_agent.casefold()
    ):
        raise HTTPException(
            503,
            "البحث متوقف حتى ضبط SEC_USER_AGENT باسم التطبيق وبريد تواصل حقيقي في إعدادات الخادم.",
        )
    headers = {
        "User-Agent": sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    }
    now = time.time()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            if not _SEC_TICKERS_CACHE["items"] or now >= _SEC_TICKERS_CACHE["expires"]:
                response = await client.get("https://www.sec.gov/files/company_tickers.json")
                response.raise_for_status()
                raw = response.json()
                _SEC_TICKERS_CACHE["items"] = list(raw.values())
                _SEC_TICKERS_CACHE["expires"] = now + 21600
            companies = _SEC_TICKERS_CACHE["items"]
            normalized = query.casefold()
            exact = [c for c in companies if str(c.get("ticker", "")).casefold() == normalized]
            exact_name = [c for c in companies if str(c.get("title", "")).casefold() == normalized]
            prefix = [c for c in companies if str(c.get("ticker", "")).casefold().startswith(normalized)]
            name_matches = [c for c in companies if normalized in str(c.get("title", "")).casefold()]
            matches = exact or exact_name or prefix or name_matches
            if not matches:
                raise HTTPException(404, "لم يتم العثور على الشركة في قائمة SEC. جرّب رمز السهم الرسمي.")
            match = matches[0]
            cik_int = int(match["cik_str"])
            cik = str(cik_int).zfill(10)
            cache_key = cik
            cached = _SEC_SUBMISSIONS_CACHE.get(cache_key)
            if cached and now - cached[0] < _SEC_CACHE_TTL:
                submission = cached[1]
            else:
                response = await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
                response.raise_for_status()
                submission = response.json()
                _SEC_SUBMISSIONS_CACHE[cache_key] = (now, submission)
            recent = (submission.get("filings") or {}).get("recent") or {}
            forms = recent.get("form") or []
            filing_dates = recent.get("filingDate") or []
            accessions = recent.get("accessionNumber") or []
            docs = recent.get("primaryDocument") or []
            descriptions = recent.get("primaryDocDescription") or []
            report_dates = recent.get("reportDate") or []
            items_list = recent.get("items") or []
            cutoff = (date.today() - timedelta(days=int(days))).isoformat() if days != "all" else None
            filings = []
            analyzed_count = 0
            for i, filing_form in enumerate(forms):
                filing_date = filing_dates[i] if i < len(filing_dates) else ""
                if cutoff and filing_date and filing_date < cutoff:
                    continue
                if form != "all" and filing_form != form:
                    continue
                accession = accessions[i] if i < len(accessions) else ""
                doc = docs[i] if i < len(docs) else ""
                accession_path = accession.replace("-", "")
                cik_path = str(cik_int)
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{accession_path}/"
                document_url = filing_url + doc if doc else f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{accession_path}/"
                filing_text = ""
                if doc and analyzed_count < 12 and filing_form in {"8-K", "10-K", "10-Q", "S-1", "S-3", "424B5"}:
                    filing_text = await _sec_fetch_filing_text(client, document_url)
                    analyzed_count += 1
                analysis = _classify_sec_filing_text(
                    filing_form,
                    descriptions[i] if i < len(descriptions) else "",
                    items_list[i] if i < len(items_list) else "",
                    filing_text,
                )
                filings.append({
                    "form": filing_form,
                    "filingDate": filing_date,
                    "accessionNumber": accession,
                    "description": descriptions[i] if i < len(descriptions) else "",
                    "reportDate": report_dates[i] if i < len(report_dates) else "",
                    "items": items_list[i] if i < len(items_list) else "",
                    "textAvailable": bool(filing_text),
                    "analysis": analysis,
                    "filingUrl": filing_url,
                    "documentUrl": document_url,
                })
                if len(filings) >= limit:
                    break
            return {
                "company": {
                    "name": match.get("title") or submission.get("name") or query,
                    "ticker": match.get("ticker") or query.upper(),
                    "cik": cik,
                },
                "asOf": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "SEC EDGAR",
                "filings": filings,
                "count": len(filings),
            }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning("SEC disclosure provider HTTP error status=%s", exc.response.status_code)
        raise HTTPException(502, "مصدر SEC غير متاح حاليًا. أعد المحاولة لاحقًا.") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("SEC disclosure search failed: %s", type(exc).__name__)
        raise HTTPException(502, "تعذر جلب بيانات SEC حاليًا. تحقق من الاتصال وإعداد SEC_USER_AGENT.") from exc
