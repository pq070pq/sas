import asyncio
"""SAS PRO scheduler orchestration.

This module owns timing/orchestration only. Business operations remain in their
respective modules and are imported lazily from app.jobs to preserve backwards
compatibility during the incremental refactor.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import settings
from .holiday_radar import publish_holiday_radar
from .market_calendar import market_status
from .maintenance import cleanup_old_data
from .timeutil import utcnow
from .market_brief import publish_market_brief
from .radar_learning import learn_radar_profile

logger = logging.getLogger(__name__)


async def scheduler():
    """Run the recurring SAS PRO orchestration loop."""
    # Lazy import avoids a module cycle: jobs keeps the compatibility export
    # while the scheduler owns the orchestration boundary.
    from .jobs import (
        expiry_cycle,
        evaluate_radar_outcomes,
        weekly_radar_report,
        stock_radar_cycle,
    )

    logger.info("SAS PRO scheduler started.")
    await asyncio.sleep(60)
    logger.info("SAS PRO initial scheduler grace period completed.")
    last_cleanup_at = None
    while True:
        cycle_started = utcnow()
        try:
            logger.info("Scheduler cycle started.")
            if settings.cleanup_enabled and (
                last_cleanup_at is None
                or (utcnow() - last_cleanup_at).total_seconds()
                >= max(1, int(settings.cleanup_interval_hours)) * 3600
            ):
                try:
                    cleanup_stats = await cleanup_old_data()
                    last_cleanup_at = utcnow()
                    logger.info("Scheduler: intelligent cleanup completed: %s", cleanup_stats)
                except Exception:
                    logger.exception("Scheduler: intelligent cleanup failed; continuing normally.")

            await expiry_cycle()
            logger.info("Scheduler: expiry cycle completed.")
            await evaluate_radar_outcomes()

            try:
                learning_profile = await learn_radar_profile()
                logger.info(
                    "Radar self-learning: samples=%s win_rate=%.1f%% rvol_floor=%.2f change_floor=%.2f",
                    learning_profile.get("samples", 0),
                    float(learning_profile.get("win_rate", 0.0)) * 100,
                    float(learning_profile.get("rvol_floor", 0.50)),
                    float(learning_profile.get("change_floor", 0.0)),
                )
            except Exception:
                logger.exception("Radar self-learning cycle failed; keeping safe defaults.")

            logger.info("Scheduler: radar outcome evaluation completed.")
            await weekly_radar_report()
            await publish_market_brief()
            logger.info("Scheduler: market brief cycle completed.")

            status = market_status()
            if status["holiday"] or status["session"] == "weekend":
                logger.info("Scheduler mode: holiday/weekend radar.")
                await publish_holiday_radar()
            else:
                logger.info("Scheduler mode: stock radar.")
                await stock_radar_cycle()

            await weekly_radar_report()
            logger.info(
                "Scheduler cycle completed in %.1fs.",
                (utcnow() - cycle_started).total_seconds(),
            )
        except Exception:
            logger.exception("Scheduler cycle failed.")

        configured_interval = max(1, int(settings.radar_interval_minutes))
        current_session = str((market_status() or {}).get("session") or "")
        if current_session in {"afterhours", "night"}:
            sleep_seconds = 3600
        else:
            sleep_seconds = max(600, configured_interval * 60)

        try:
            next_status = market_status()
            if next_status.get("session") == "premarket":
                et = ZoneInfo("America/New_York")
                now_et = datetime.now(et)
                open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                until_open = (open_et - now_et).total_seconds()
                if until_open > 0:
                    sleep_seconds = min(sleep_seconds, max(1, int(until_open)))
        except Exception:
            logger.exception("Scheduler transition timing check failed.")

        logger.info("Scheduler sleeping for %.0fs before next cycle.", sleep_seconds)
        await asyncio.sleep(sleep_seconds)
