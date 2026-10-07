"""Intelligent SAS PRO retention and cache cleanup.

Deletes only disposable historical records while preserving users, subscriptions,
payments, active radar outcomes, and settings. Runtime provider caches are pruned
by TTL/size so repeated scans cannot grow memory indefinitely.
"""
from __future__ import annotations

import time
from datetime import timedelta
from sqlalchemy import delete, select

from .config import settings
from .db import (
    SessionLocal, RadarRun, RadarSignal, RadarOutcome, StockAnalysis,
    AuditLog, AccessRequest, Invite, ScheduledReport,
)
from .timeutil import utcnow


def _prune_timestamp_cache(cache: dict, ttl_seconds: int, max_items: int) -> int:
    now = time.monotonic()
    removed = 0
    for key, value in list(cache.items()):
        stamp = value[0] if isinstance(value, tuple) and value else None
        if isinstance(stamp, (int, float)) and now - stamp >= ttl_seconds:
            cache.pop(key, None)
            removed += 1
    if len(cache) > max_items:
        ordered = sorted(
            cache.items(),
            key=lambda item: item[1][0] if isinstance(item[1], tuple) and isinstance(item[1][0], (int, float)) else float("-inf"),
        )
        overflow = max(0, len(cache) - max_items)
        for key, _ in ordered[:overflow]:
            cache.pop(key, None)
            removed += 1
    return removed


def _prune_runtime_caches() -> dict:
    removed = {}
    try:
        from . import ai_radar
        removed["ai"] = _prune_timestamp_cache(ai_radar._cache, 20 * 60, settings.runtime_cache_max_items)
    except Exception:
        removed["ai"] = 0

    try:
        from . import news
        removed["news_summary"] = _prune_timestamp_cache(news._cache, 20 * 60, settings.runtime_cache_max_items)
        removed["news"] = _prune_timestamp_cache(news._news_cache, max(60, settings.news_cache_minutes * 60), settings.runtime_cache_max_items)
        removed["fundamentals"] = _prune_timestamp_cache(news._fundamentals_cache, 3600, settings.runtime_cache_max_items)
        removed["tipranks"] = _prune_timestamp_cache(news._tipranks_cache, 3600, settings.runtime_cache_max_items)
        if news._earnings_calendar_cache:
            stamp = news._earnings_calendar_cache[0]
            if isinstance(stamp, (int, float)) and time.time() - stamp >= 1800:
                news._earnings_calendar_cache = None
                removed["earnings"] = 1
            else:
                removed["earnings"] = 0
    except Exception:
        removed.setdefault("news_summary", 0)

    try:
        from . import market
        removed["fred"] = _prune_timestamp_cache(market._FRED_CACHE, 15 * 60, settings.runtime_cache_max_items)
        removed["ticker"] = 0
        if len(market._TICKER_CACHE) > 32:
            overflow = len(market._TICKER_CACHE) - 32
            for key in list(market._TICKER_CACHE)[:overflow]:
                market._TICKER_CACHE.pop(key, None)
            removed["ticker"] = overflow
    except Exception:
        removed.setdefault("fred", 0)

    try:
        from .main import _analysis_memory, _quick_scan_memory
        removed["analysis"] = 0
        removed["quick_scan"] = 0
        if _analysis_memory.stats()["items"] > settings.runtime_cache_max_items:
            _analysis_memory.clear()
            removed["analysis"] = 1
        if _quick_scan_memory.stats()["items"] > settings.runtime_cache_max_items:
            _quick_scan_memory.clear()
            removed["quick_scan"] = 1
    except Exception:
        removed.setdefault("analysis", 0)

    return removed


async def cleanup_old_data() -> dict:
    """Run once per day (or configured interval) and return deletion counts."""
    now = utcnow()
    stats = {"runtime": _prune_runtime_caches()}

    async with SessionLocal() as db:
        outcome_cutoff = now - timedelta(days=max(1, settings.radar_signal_retention_days))
        signal_cutoff = outcome_cutoff
        run_cutoff = now - timedelta(days=max(1, settings.radar_run_retention_days))
        analysis_cutoff = now - timedelta(days=max(1, settings.radar_analysis_retention_days))
        audit_cutoff = now - timedelta(days=max(1, settings.audit_log_retention_days))
        access_cutoff = now - timedelta(days=max(1, settings.access_request_retention_days))
        invite_cutoff = now - timedelta(days=max(1, settings.invite_retention_days))
        report_cutoff = now - timedelta(days=max(1, settings.scheduled_report_retention_days))

        result = await db.execute(delete(RadarOutcome).where(
            RadarOutcome.created_at < outcome_cutoff,
            RadarOutcome.status != "active",
        ))
        stats["radar_outcomes"] = result.rowcount or 0

        active_signal_ids = select(RadarOutcome.radar_signal_id).where(RadarOutcome.status == "active")
        result = await db.execute(delete(RadarSignal).where(
            RadarSignal.created_at < signal_cutoff,
            ~RadarSignal.id.in_(active_signal_ids),
        ))
        stats["radar_signals"] = result.rowcount or 0

        result = await db.execute(delete(RadarRun).where(RadarRun.started_at < run_cutoff))
        stats["radar_runs"] = result.rowcount or 0

        result = await db.execute(delete(StockAnalysis).where(StockAnalysis.created_at < analysis_cutoff))
        stats["stock_analyses"] = result.rowcount or 0

        result = await db.execute(delete(AuditLog).where(AuditLog.created_at < audit_cutoff))
        stats["audit_logs"] = result.rowcount or 0

        result = await db.execute(delete(AccessRequest).where(
            AccessRequest.requested_at < access_cutoff,
            AccessRequest.status != "pending",
        ))
        stats["access_requests"] = result.rowcount or 0

        result = await db.execute(delete(Invite).where(
            Invite.created_at < invite_cutoff,
            Invite.expires_at < now,
        ))
        stats["invites"] = result.rowcount or 0

        result = await db.execute(delete(ScheduledReport).where(
            ScheduledReport.created_at < report_cutoff
        ))
        stats["scheduled_reports"] = result.rowcount or 0

        await db.commit()

    stats["total_db"] = sum(v for k, v in stats.items() if k != "runtime" and isinstance(v, int))
    return stats
