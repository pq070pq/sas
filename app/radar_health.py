import asyncio
import logging
from datetime import datetime, timezone

from .config import settings
from .market_calendar import market_status
from .telegram import send_message

logger = logging.getLogger(__name__)

_health_alert_active = False
_last_signature = None

CHECK_INTERVAL_SECONDS = 300
RADAR_STUCK_SECONDS = 20 * 60


def _task_snapshot(task):
    if task is None:
        return {"state": "missing", "age": None, "in_radar": False}

    if task.done():
        return {"state": "done", "age": None, "in_radar": False}

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
                issue = "مهمة المجدول توقفت/انهارت."
                severity = "critical"
            elif active_session and snapshot["in_radar"] and snapshot["age"] is not None:
                if snapshot["age"] >= RADAR_STUCK_SECONDS:
                    minutes = int(snapshot["age"] // 60)
                    issue = f"دورة الرادار عالقة منذ {minutes} دقيقة."
                    severity = "critical"

            signature = f"{severity}:{issue}" if issue else None

            if issue and signature != _last_signature:
                logger.error("RADAR HEALTH ALERT: %s", issue)
                sent = await _notify_admin(
                    "🚨 <b>SAS PRO | تنبيه صحة الرادار</b>\n\n"
                    f"❌ <b>الحالة:</b> {issue}\n"
                    f"📡 <b>الجلسة:</b> {status.get('label_ar') or session}\n"
                    "🛠️ النظام سيستمر في المراقبة، ويجب فحص الرادار/المزودات إذا استمر الخلل."
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
