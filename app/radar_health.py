import asyncio
import logging
from datetime import datetime, timezone

from .config import settings
from .market_calendar import market_status
from .telegram import send_message

logger = logging.getLogger(__name__)

_health_alert_active = False
_last_signature = None
_last_restart_at = None

CHECK_INTERVAL_SECONDS = 300
RADAR_STUCK_SECONDS = 20 * 60
RADAR_RESTART_COOLDOWN_SECONDS = 30 * 60


def _task_snapshot(task):
    if task is None:
        return {"state": "missing", "age": None, "in_radar": False}

    if task.done():
        if task.cancelled():
            return {
                "state": "done",
                "age": None,
                "in_radar": False,
                "error_type": "CancelledError",
                "error": "مهمة المجدول أُلغيت.",
            }
        try:
            exc = task.exception()
        except Exception as exc:
            return {
                "state": "done",
                "age": None,
                "in_radar": False,
                "error_type": type(exc).__name__,
                "error": str(exc) or repr(exc),
            }
        if exc is None:
            return {"state": "done", "age": None, "in_radar": False, "error_type": None, "error": None}
        return {
            "state": "done",
            "age": None,
            "in_radar": False,
            "error_type": type(exc).__name__,
            "error": str(exc) or repr(exc),
        }

    stack = task.get_stack(limit=20)
    in_radar = False
    cycle_started = None

    for frame in stack:
        if frame.f_code.co_name == "stock_radar_cycle":
            in_radar = True
        value = frame.f_locals.get("cycle_started")
        if isinstance(value, datetime):
            cycle_started = value

    age = None
    if cycle_started is not None:
        try:
            age = max(0.0, (datetime.now(timezone.utc) - cycle_started).total_seconds())
        except Exception:
            age = None

    return {"state": "running", "age": age, "in_radar": in_radar}


async def _notify_admin(text):
    admin_id = int(settings.owner_telegram_id or 0)
    if not admin_id:
        logger.error("Radar health alert skipped: OWNER_TELEGRAM_ID is not configured.")
        return False
    try:
        await send_message(admin_id, text)
        return True
    except Exception:
        logger.exception("Radar health alert delivery failed.")
        return False


async def _restart_scheduler(reason):
    """Restart the background scheduler when it has stopped or is stuck in radar."""
    global _last_restart_at
    now = datetime.now(timezone.utc)
    if _last_restart_at is not None:
        elapsed = (now - _last_restart_at).total_seconds()
        if elapsed < RADAR_RESTART_COOLDOWN_SECONDS:
            logger.warning(
                "Radar scheduler restart suppressed by cooldown: %.0fs remaining.",
                RADAR_RESTART_COOLDOWN_SECONDS - elapsed,
            )
            return False

    try:
        from . import main as main_module

        old_task = getattr(main_module, "scheduler_task", None)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            try:
                await asyncio.wait_for(old_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        main_module.scheduler_task = asyncio.create_task(
            main_module.scheduler(),
            name="saspro-scheduler",
        )
        _last_restart_at = now
        logger.warning("RADAR WATCHDOG: scheduler restarted | reason=%s", reason)
        return True
    except Exception:
        logger.exception("RADAR WATCHDOG: scheduler restart failed | reason=%s", reason)
        return False


async def radar_health_monitor():
    global _health_alert_active, _last_signature

    logger.info("Radar health monitor started.")

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

            from . import main as main_module
            task = getattr(main_module, "scheduler_task", None)
            snapshot = _task_snapshot(task)
            status = market_status()
            session = str(status.get("session") or "")
            active_session = session in {"premarket", "regular", "afterhours", "night"}

            issue = None
            severity = "warning"

            if task is None:
                issue = "مهمة المجدول غير موجودة."
                severity = "critical"
            elif snapshot["state"] == "done":
                error_type = snapshot.get("error_type")
                error = str(snapshot.get("error") or "").strip()
                if error_type and error:
                    issue = f"مهمة المجدول توقفت/انهارت: {error_type}: {error}"
                elif error_type:
                    issue = f"مهمة المجدول توقفت/انهارت: {error_type}"
                else:
                    issue = "مهمة المجدول توقفت/انهارت بدون استثناء مسجل."
                severity = "critical"
            elif active_session and snapshot["in_radar"] and snapshot["age"] is not None:
                if snapshot["age"] >= RADAR_STUCK_SECONDS:
                    minutes = int(snapshot["age"] // 60)
                    issue = f"دورة الرادار عالقة منذ {minutes} دقيقة."
                    severity = "critical"

            signature = f"{severity}:{issue}" if issue else None

            if issue and signature != _last_signature:
                logger.error("RADAR HEALTH ALERT: %s", issue)
                restarted = False
                if severity == "critical":
                    restarted = await _restart_scheduler(issue)
                restart_text = (
                    "♻️ <b>تمت محاولة إعادة تشغيل المجدول تلقائيًا.</b>"
                    if restarted
                    else "⚠️ <b>تعذرت إعادة تشغيل المجدول تلقائيًا أو كانت ضمن مهلة الحماية.</b>"
                )
                sent = await _notify_admin(
                    "🚨 <b>SAS PRO | تنبيه صحة الرادار</b>\n\n"
                    f"❌ <b>الحالة:</b> {issue}\n"
                    f"📡 <b>الجلسة:</b> {status.get('label_ar') or session}\n"
                    f"{restart_text}\n"
                    "🔎 تم تضمين نوع الاستثناء ورسالة الخطأ في التنبيه لتحديد السبب الحقيقي بدل الاكتفاء برسالة عامة."
                )
                if sent:
                    _health_alert_active = True
                    _last_signature = signature

            elif not issue and _health_alert_active:
                sent = await _notify_admin(
                    "✅ <b>SAS PRO | تعافي الرادار</b>\n\n"
                    "عاد مجدول الرادار للعمل بصورة طبيعية، ولم يعد الخلل المرصود قائمًا."
                )
                if sent:
                    _health_alert_active = False
                    _last_signature = None

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Radar health monitor cycle failed.")
