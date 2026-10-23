"""Independent publisher for verified speculative-radar candidates with analyst-target confirmation.

A symbol may be published at most once per New York trading session. This
module deliberately requires observed float and session-open data; it does not
reuse daily percentage change as a substitute.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import RadarSignal, ScheduledReport, SessionLocal
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
        analyst_target=_number(candidate.get("analyst_target")),
        analyst_count=int(candidate.get("analyst_count")) if str(candidate.get("analyst_count") or "").isdigit() else None,
    )
    if not result["eligible"]:
        return None, result

    report = (
        "🔥 <b>SAS PRO | المضاربي اللحظي + أهداف المحللين</b>\n"
        "🏷️ <b>نوع الرصد:</b> مضاربي لحظي مؤكد + هدف محللين ≥ 50%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 السهم: <b>${symbol}</b>\n"
        f"💵 السعر الحالي: <b>${float(candidate['price']):,.4f}</b>\n"
        f"📈 الارتفاع من افتتاح الجلسة: <b>+{result['change_from_open_pct']:.2f}%</b>\n"
        f"📊 حجم التداول: <b>{int(float(candidate['volume'])):,}</b> سهم\n"
        f"🪙 الأسهم الحرة: <b>{int(float(candidate['float_shares'])):,}</b>\n"
        f"🎯 متوسط هدف المحللين: <b>${result['analyst_target']:,.2f}</b>\n"
        f"📈 الارتفاع المحتمل حسب الهدف: <b>+{result['analyst_upside_pct']:.1f}%</b>\n"
        f"👥 عدد المحللين: <b>{result['analyst_count']}</b>\n"
        "🎯 <b>فلتر الهدف:</b> صعود محتمل 50% أو أكثر وفق متوسط هدف المحللين\n"
        "👥 <b>تغطية المحللين:</b> 3 محللين على الأقل\n"
        "✅ اجتاز فلتر المضاربي اللحظي وأهداف المحللين\n"
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
        # Cross-radar dedupe: do not publish a symbol already sent by the main radar during this session.
        main_signal = (await db.execute(select(RadarSignal).where(RadarSignal.symbol == symbol, RadarSignal.session_date == session_date, RadarSignal.telegram_message_id.is_not(None)))).scalars().first()
        if main_signal is not None:
            return {"status": "duplicate_main_radar_skipped", "symbol": symbol, "session_date": session_date}
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


# Float is slow-moving, so cache verified FMP values across scan cycles.
_FLOAT_CACHE: dict[str, tuple[float, float]] = {}
_FLOAT_CACHE_TTL_SECONDS = 6 * 60 * 60
_SPECULATIVE_SCAN_LIMIT = 35


def _configured_keys(primary: str, plural: str) -> list[str]:
    values = str(getattr(settings, plural, "") or "").replace("\\n", ",").replace(";", ",").split(",")
    keys = [item.strip() for item in values if item.strip()]
    first = str(getattr(settings, primary, "") or "").strip()
    if first:
        keys.insert(0, first)
    return list(dict.fromkeys(keys))


async def _finnhub_session_quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Use Finnhub's current quote, including today's regular-session open."""
    keys = _configured_keys("finnhub_api_key", "finnhub_api_keys")
    for key in keys:
        try:
            response = await client.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": key},
                timeout=8,
            )
            if response.status_code in (401, 403, 429):
                continue
            response.raise_for_status()
            payload = response.json()
            price = _number(payload.get("c"))
            session_open = _number(payload.get("o"))
            if price and session_open:
                return {"price": price, "session_open": session_open, "source": "Finnhub"}
        except Exception as exc:
            logger.debug("SPECULATIVE_FINNHUB_QUOTE_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
    return None


async def _fmp_analyst_target(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Read the most recent FMP price-target summary; missing evidence fails closed."""
    for key in _configured_keys("fmp_api_key", "fmp_api_keys"):
        try:
            response = await client.get("https://financialmodelingprep.com/stable/price-target-summary", params={"symbol": symbol, "apikey": key}, timeout=8)
            if response.status_code in (401, 403, 404, 429):
                continue
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else (payload.get("data") or [])
            for row in rows:
                if not isinstance(row, dict) or str(row.get("symbol") or symbol).upper() != symbol:
                    continue
                for tk, ck, period in (("lastMonthAvgPriceTarget", "lastMonthCount", "شهر"), ("lastQuarterAvgPriceTarget", "lastQuarterCount", "ربع سنة")):
                    target = _number(row.get(tk))
                    try:
                        count = int(row.get(ck) or 0)
                    except (TypeError, ValueError):
                        count = 0
                    if target and count >= 3:
                        return {"target": target, "count": count, "period": period}
        except Exception as exc:
            logger.debug("SPECULATIVE_FMP_TARGET_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
    return None

async def _fmp_float_shares(client: httpx.AsyncClient, symbol: str) -> float | None:
    """Fetch actual float shares from FMP; never substitute shares outstanding."""
    cached = _FLOAT_CACHE.get(symbol)
    now = time.monotonic()
    if cached and now - cached[0] < _FLOAT_CACHE_TTL_SECONDS:
        return cached[1]

    keys = _configured_keys("fmp_api_key", "fmp_api_keys")
    for key in keys:
        try:
            response = await client.get(
                "https://financialmodelingprep.com/stable/shares-float",
                params={"symbol": symbol, "apikey": key},
                timeout=8,
            )
            if response.status_code in (401, 403, 404, 429):
                continue
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else (payload.get("data") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_symbol = str(row.get("symbol") or symbol).upper()
                if row_symbol != symbol:
                    continue
                value = _number(
                    row.get("floatShares")
                    or row.get("sharesFloat")
                    or row.get("float_shares")
                    or row.get("floatSharesOutstanding")
                    or row.get("freeFloat")
                    or row.get("float")
                )
                if value:
                    _FLOAT_CACHE[symbol] = (now, value)
                    return value
        except Exception as exc:
            logger.debug("SPECULATIVE_FMP_FLOAT_FAILED symbol=%s error=%s", symbol, type(exc).__name__)
    return None


async def speculative_radar_cycle() -> dict:
    """Discover $1-$10 US stocks through existing providers and publish verified matches.

    Existing discovery feeds provide session-volume candidates; Finnhub supplies
    current price/session open, and FMP supplies actual float shares. Missing or
    unavailable observations fail closed. The main radar's thresholds are untouched.
    """
    from .market_calendar import market_status
    from .scanner import discover_low_price_stocks

    status = market_status()
    if status.get("session") != "regular":
        logger.info("SPECULATIVE_RADAR_SKIPPED session=%s", status.get("session"))
        return {"status": "outside_regular_session", "sent": 0}

    try:
        discovered = await discover_low_price_stocks(price_min=0.30, price_max=6.00)
    except Exception:
        logger.exception("SPECULATIVE_RADAR_DISCOVERY_FAILED")
        return {"status": "discovery_failed", "sent": 0}

    # Volume is a cheap first-stage screen. Keep the request budget below Finnhub's
    # free-tier per-minute limit; stronger volume candidates are checked first.
    shortlist = [
        row for row in discovered
        if isinstance(row, dict)
        and 0.30 <= (_number(row.get("price")) or 0) <= 6.00
        and (_number(row.get("volume")) or 0) > 500_000
        and str(row.get("symbol") or "").strip()
    ]
    shortlist.sort(key=lambda row: _number(row.get("volume")) or 0, reverse=True)
    shortlist = shortlist[:_SPECULATIVE_SCAN_LIMIT]
    if not shortlist:
        logger.info("SPECULATIVE_RADAR_EMPTY_DISCOVERY")
        return {"status": "no_candidates", "sent": 0, "discovered": len(discovered)}

    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=9, follow_redirects=True) as client:
        async def check(row: dict):
            symbol = str(row.get("symbol") or "").upper().strip()
            async with semaphore:
                quote = await _finnhub_session_quote(client, symbol)
                if not quote:
                    return {"status": "quote_unavailable", "symbol": symbol}
                price = quote["price"]
                session_open = quote["session_open"]
                change = ((price - session_open) / session_open) * 100
                if not (0.30 <= price <= 6.00) or change < 10.0:
                    return {"status": "price_or_momentum_filtered", "symbol": symbol}
                float_shares, analyst_target = await asyncio.gather(_fmp_float_shares(client, symbol), _fmp_analyst_target(client, symbol))
                if float_shares is None:
                    return {"status": "float_unavailable", "symbol": symbol}
                if analyst_target is None:
                    return {"status": "analyst_target_unavailable", "symbol": symbol}
                return await publish_speculative_candidate({
                    "symbol": symbol,
                    "price": price,
                    "float_shares": float_shares,
                    "volume": _number(row.get("volume")),
                    "session_open": session_open,
                    "analyst_target": analyst_target["target"],
                    "analyst_count": analyst_target["count"],
                })

        results = await asyncio.gather(*(check(row) for row in shortlist), return_exceptions=True)

    sent = 0
    counts: dict[str, int] = {}
    for result in results:
        if isinstance(result, Exception):
            counts["error"] = counts.get("error", 0) + 1
            logger.warning("SPECULATIVE_RADAR_CANDIDATE_FAILED error=%s", type(result).__name__)
            continue
        state = str(result.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        if state == "sent":
            sent += 1

    summary = {
        "status": "completed",
        "discovered": len(discovered),
        "shortlist": len(shortlist),
        "sent": sent,
        "results": counts,
    }
    logger.info("SPECULATIVE_RADAR_SUMMARY %s", summary)
    return summary
