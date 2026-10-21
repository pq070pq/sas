"""Independent publisher for verified speculative-radar candidates.

A symbol may be published at most once per New York trading session. This
module deliberately requires observed float and session-open data; it does not
reuse daily percentage change as a substitute.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import ScheduledReport, SessionLocal
from .finviz_intraday_filter import evaluate_intraday_candidate
from .telegram import send_message

logger = logging.getLogger(__name__)


def speculative_report_key(symbol: str, session_date: str) -> str:
    """Stable idempotency key: one symbol per NY session date."""
    normalized = "".join(ch for ch in str(symbol or "").upper().strip() if ch.isalnum() or ch in ".-")
    return f"spec:{session_date}:{normalized}"[:64]


def build_speculative_report(candidate: dict) -> tuple[str | None, dict]:
    """Build a channel report only when every configured threshold is verified."""
    symbol = str(candidate.get("symbol") or "").upper().strip()
    if not symbol:
        return None, {"eligible": False, "reasons": ["رمز السهم غير متوفر"]}

    result = evaluate_intraday_candidate(
        price=_number(candidate.get("price")),
        float_shares=_number(candidate.get("float_shares")),
        volume=_number(candidate.get("volume")),
        session_open=_number(candidate.get("session_open")),
    )
    if not result["eligible"]:
        return None, result

    report = (
        "🔥 <b>SAS PRO | رصد الأسهم المضاربية</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 السهم: <b>${symbol}</b>\n"
        f"💵 السعر الحالي: <b>${float(candidate['price']):,.4f}</b>\n"
        f"📈 الارتفاع من افتتاح الجلسة: <b>+{result['change_from_open_pct']:.2f}%</b>\n"
        f"📊 حجم التداول: <b>{int(float(candidate['volume'])):,}</b> سهم\n"
        f"🪙 الأسهم الحرة: <b>{int(float(candidate['float_shares'])):,}</b>\n"
        "✅ اجتاز شروط فلتر الأسهم المضاربية\n"
        "⚠️ رصد آلي تعليمي وليس توصية شراء أو بيع. الأسهم المضاربية عالية المخاطر."
    )
    return report, result


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def publish_speculative_candidate(candidate: dict) -> dict:
    """Publish once per symbol per NY session, with DB-backed deduplication.

    Call only with fresh observations from a trusted market-data provider.
    A failed Telegram send releases the reservation so a later cycle can retry.
    """
    symbol = str(candidate.get("symbol") or "").upper().strip()
    report, evaluation = build_speculative_report(candidate)
    if not report:
        return {"status": "not_eligible", "symbol": symbol, "evaluation": evaluation}
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"status": "telegram_not_configured", "symbol": symbol}

    session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    key = speculative_report_key(symbol, session_date)
    async with SessionLocal() as db:
        if (await db.execute(select(ScheduledReport).where(ScheduledReport.report_key == key))).scalars().first():
            return {"status": "duplicate_skipped", "symbol": symbol, "session_date": session_date}
        db.add(ScheduledReport(report_key=key))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return {"status": "duplicate_skipped", "symbol": symbol, "session_date": session_date}

    try:
        await send_message(settings.telegram_channel_id, report)
    except Exception:
        logger.exception("Speculative radar Telegram delivery failed for %s", symbol)
        async with SessionLocal() as db:
            reservation = (await db.execute(select(ScheduledReport).where(ScheduledReport.report_key == key))).scalars().first()
            if reservation is not None:
                await db.delete(reservation)
                await db.commit()
        return {"status": "send_failed", "symbol": symbol, "session_date": session_date}

    logger.info("Speculative radar report sent symbol=%s session=%s", symbol, session_date)
    return {"status": "sent", "symbol": symbol, "session_date": session_date, "evaluation": evaluation}
