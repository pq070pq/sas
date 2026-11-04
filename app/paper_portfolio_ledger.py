"""Auditable paper-trade ledger helpers.

This module is deliberately separate from live order execution and Telegram.
Callers must pass normalized, source-backed signal levels; this module never
fetches prices or invents missing entry/stop/target values.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import PaperTrade, RadarSignal
from .paper_portfolio import calculate_position_size


def prepare_paper_trade(
    *,
    radar_signal_id: int,
    symbol: str,
    session_date: str,
    channel_gate_passed: bool,
    confirmed: bool,
    entry_price: float | None,
    stop_price: float | None,
    targets: list[float | None],
    equity: float,
    risk_fraction: float = 0.01,
    max_position_fraction: float = 0.20,
) -> dict:
    """Validate an eligible signal and return a deterministic virtual trade row."""
    if not channel_gate_passed or not confirmed:
        raise ValueError("Only confirmed signals that passed the channel gate are eligible.")
    if not radar_signal_id or not symbol or not session_date:
        raise ValueError("Signal identity fields are required.")
    if entry_price is None or stop_price is None:
        raise ValueError("A source-backed entry and stop are required.")
    valid_targets = [float(value) for value in targets if value is not None]
    if not valid_targets:
        raise ValueError("At least one source-backed target is required.")
    entry = float(entry_price)
    stop = float(stop_price)
    if any(value <= entry for value in valid_targets):
        raise ValueError("Long targets must be above entry.")
    sizing = calculate_position_size(
        equity=equity,
        entry_price=entry,
        stop_price=stop,
        risk_fraction=risk_fraction,
        max_position_fraction=max_position_fraction,
    )
    if sizing["shares"] < 1:
        raise ValueError("Position size is below one share.")
    return {
        "radar_signal_id": int(radar_signal_id),
        "symbol": str(symbol).upper(),
        "session_date": str(session_date),
        "status": "open",
        "entry_price": entry,
        "shares": sizing["shares"],
        "stop_price": stop,
        "target1": valid_targets[0] if len(valid_targets) > 0 else None,
        "target2": valid_targets[1] if len(valid_targets) > 1 else None,
        "target3": valid_targets[2] if len(valid_targets) > 2 else None,
        "risk_budget": sizing["risk_budget"],
        "planned_risk": sizing["planned_risk"],
        "notional": sizing["notional"],
    }


async def create_paper_trade_once(
    db: AsyncSession,
    *,
    signal: RadarSignal,
    trade_data: dict,
) -> tuple[PaperTrade, bool]:
    """Persist one paper trade per radar signal; safe to retry."""
    existing = (await db.execute(
        select(PaperTrade).where(PaperTrade.radar_signal_id == signal.id)
    )).scalars().first()
    if existing is not None:
        return existing, False
    if int(trade_data.get("radar_signal_id") or 0) != signal.id:
        raise ValueError("Trade data does not match the persisted radar signal.")
    trade = PaperTrade(**trade_data)
    db.add(trade)
    await db.flush()
    return trade, True
