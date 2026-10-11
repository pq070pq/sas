"""Forward validation of published radar signals against completed daily candles.

Returns are measured from the saved publication price, not from a later mutable quote.
Missing candles are left pending; they are never treated as wins or losses.
"""
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .db import SessionLocal, RadarSignal, RadarForwardCheck, ScheduledReport
from .timeutil import utcnow

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")


def _close_map(result):
    candles = (result or {}).get("candles") or []
    out = {}
    for candle in candles:
        try:
            day = str(candle.get("time") or candle.get("date") or "")[:10]
            close = float(candle["close"])
            if day and close > 0:
                out[day] = close
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _pct(entry, close):
    if entry is None or close is None or float(entry) <= 0:
        return None
    return (float(close) / float(entry) - 1.0) * 100.0


async def evaluate_forward_validation(max_signals: int = 40):
    """Update 3/5/10-session returns once per NY trading date.

    Uses the existing PanWatch OHLCV adapter and existing daily signal snapshots.
    This is an observational scorecard only; it does not alter live radar gates.
    """
    today = datetime.now(NY).date().isoformat()
    key = f"radar_forward_validation:{today}"
    async with SessionLocal() as db:
        already = (await db.execute(select(ScheduledReport).where(ScheduledReport.report_key == key))).scalars().first()
        if already:
            return {"status": "already_run", "updated": 0}
        cutoff = (datetime.now(NY).date() - timedelta(days=45)).isoformat()
        signals = (await db.execute(
            select(RadarSignal).where(
                RadarSignal.telegram_message_id.is_not(None),
                RadarSignal.session_date >= cutoff,
            ).order_by(RadarSignal.session_date.desc()).limit(max(1, min(int(max_signals), 100)))
        )).scalars().all()
        work = []
        for signal in signals:
            payload = json.loads(signal.payload or "{}")
            price = payload.get("live_price") or payload.get("price")
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            row = (await db.execute(select(RadarForwardCheck).where(
                RadarForwardCheck.radar_signal_id == signal.id
            ))).scalars().first()
            if row is None:
                row = RadarForwardCheck(
                    radar_signal_id=signal.id, symbol=signal.symbol,
                    signal_date=signal.session_date, entry_price=price, status="pending",
                )
                db.add(row)
                work.append((signal, row))
            elif row.status != "complete":
                work.append((signal, row))
        await db.flush()

        if not work:
            db.add(ScheduledReport(report_key=key))
            await db.commit()
            return {"status": "no_pending_signals", "updated": 0}

        # Fetch outside the transaction loop, with the existing OHLCV adapter.
        from .panwatch import ohlcv
        symbols = sorted({s.symbol.upper() for s, row in work} | {"SPY"})
        histories = {}
        for symbol in symbols:
            try:
                result = await ohlcv(symbol, days=90, interval="1d")
                histories[symbol] = (_close_map(result), str((result or {}).get("source") or "unknown"))
            except Exception:
                logger.exception("Forward validation OHLCV unavailable for %s", symbol)
                histories[symbol] = ({}, "unavailable")

        updated = 0
        for signal, row in work:
            stock, source = histories.get(signal.symbol.upper(), ({}, "unavailable"))
            spy, _ = histories.get("SPY", ({}, "unavailable"))
            signal_day = str(signal.session_date)[:10]
            stock_days = sorted(day for day in stock if day > signal_day)
            spy_days = sorted(day for day in spy if day > signal_day)
            if row.entry_spy_close is None:
                row.entry_spy_close = spy.get(signal_day)
            row.data_source = source
            for horizon in (3, 5, 10):
                close_field = f"close_{horizon}"
                ret_field = f"return_{horizon}_pct"
                spy_ret_field = f"spy_return_{horizon}_pct"
                excess_field = f"excess_{horizon}_pct"
                # Count only actual completed trading dates after the signal date.
                if getattr(row, close_field) is not None or len(stock_days) < horizon or len(spy_days) < horizon:
                    continue
                stock_close = stock.get(stock_days[horizon - 1])
                spy_close = spy.get(spy_days[horizon - 1])
                stock_ret = _pct(row.entry_price, stock_close)
                spy_ret = _pct(row.entry_spy_close, spy_close)
                if stock_ret is None or spy_ret is None:
                    continue
                setattr(row, close_field, stock_close)
                setattr(row, ret_field, round(stock_ret, 4))
                setattr(row, spy_ret_field, round(spy_ret, 4))
                setattr(row, excess_field, round(stock_ret - spy_ret, 4))
            row.status = "complete" if row.return_10_pct is not None else "pending"
            row.updated_at = utcnow()
            updated += 1

        db.add(ScheduledReport(report_key=key))
        await db.commit()
        return {"status": "ok", "updated": updated, "signals_considered": len(work)}
