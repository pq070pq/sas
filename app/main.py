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
from .db import SessionLocal, User, Subscription, Payment, StockAnalysis, RadarSignal, RadarRun, AccessRequest, Setting, Invite, AdminRole, get_session, init_db
from .telegram import validate_init_data, send_message, bot_api
from .market import quote, ticker
from .panwatch import analyze, technical_targets, ohlcv
from .news import company_news, corporate_events, tipranks_analysis
from .news_translation import translate_stock_news
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
    prefer_extended = session in {"premarket", "afterhours"}
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

    report = ["🚀 <b>SAS PRO RADAR | تنبيه زخم</b>", ""]
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
        report.append("🎯 الأهداف: " + " | ".join(_money(x) for x in targets[:3]))
    else:
        report.append("🎯 الأهداف: غير مؤكدة — لا يوجد هدف فني معتمد")
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
