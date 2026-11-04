"""Pure, conservative primitives for the SAS PRO paper-trading engine.

This module deliberately has no database, provider, scheduler, Telegram, or UI
dependencies. It does not submit real orders and never assumes a fill unless a
caller supplies observed market data.
"""
from __future__ import annotations

from math import floor
from typing import Iterable


def calculate_position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_fraction: float = 0.01,
    max_position_fraction: float = 0.20,
) -> dict:
    """Size a long paper position from a fixed risk budget and notional cap."""
    values = (equity, entry_price, stop_price, risk_fraction, max_position_fraction)
    if any(float(value) <= 0 for value in values):
        raise ValueError("All sizing inputs must be positive.")
    if stop_price >= entry_price:
        raise ValueError("For a long position, stop_price must be below entry_price.")
    if risk_fraction > 1 or max_position_fraction > 1:
        raise ValueError("Fractions must not exceed 1.")

    risk_budget = float(equity) * float(risk_fraction)
    risk_per_share = float(entry_price) - float(stop_price)
    shares_by_risk = floor(risk_budget / risk_per_share)
    notional_cap = float(equity) * float(max_position_fraction)
    shares_by_cap = floor(notional_cap / float(entry_price))
    shares = max(0, min(shares_by_risk, shares_by_cap))
    notional = shares * float(entry_price)
    planned_risk = shares * risk_per_share
    return {
        "shares": shares,
        "risk_budget": round(risk_budget, 4),
        "risk_per_share": round(risk_per_share, 6),
        "planned_risk": round(planned_risk, 4),
        "notional": round(notional, 4),
        "position_fraction": round(notional / float(equity), 6),
        "sizing_status": "sized" if shares > 0 else "below_minimum_size",
    }


def resolve_long_barrier(
    *,
    open_price: float,
    high_price: float,
    low_price: float,
    stop_price: float,
    target_price: float,
) -> dict | None:
    """Resolve one OHLC bar conservatively for a long trade.

    If stop and target are both touched in the same bar, stop wins because OHLC
    data cannot reveal the intrabar path. A gap through the stop is filled at
    the observed open, which is worse than the stop. Target fills are capped at
    the target to avoid claiming price improvement without execution evidence.
    """
    values = (open_price, high_price, low_price, stop_price, target_price)
    if any(float(value) <= 0 for value in values):
        raise ValueError("Prices must be positive.")
    if low_price > high_price:
        raise ValueError("low_price cannot exceed high_price.")
    if not (stop_price < target_price):
        raise ValueError("stop_price must be below target_price.")

    stop_touched = low_price <= stop_price
    target_touched = high_price >= target_price
    if not stop_touched and not target_touched:
        return None

    if stop_touched:
        fill = min(float(open_price), float(stop_price)) if open_price <= stop_price else float(stop_price)
        return {"event": "stop", "fill_price": round(fill, 6), "resolution": "stop_first_conservative"}

    return {"event": "target", "fill_price": round(float(target_price), 6), "resolution": "target_touched"}


def summarize_closed_trades(trades: Iterable[dict], *, starting_equity: float = 10000.0) -> dict:
    """Calculate auditable metrics from closed trades with realized P&L.

    Each trade must provide numeric pnl. Open/unresolved trades must not be
    passed here; callers should report them separately.
    """
    if float(starting_equity) <= 0:
        raise ValueError("starting_equity must be positive.")

    rows = list(trades)
    pnls = []
    for trade in rows:
        if trade.get("status") not in {"closed", "won", "lost"}:
            continue
        try:
            pnl = float(trade["pnl"])
        except (KeyError, TypeError, ValueError):
            continue
        pnls.append(pnl)

    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = float(starting_equity)
    peak = equity
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    count = len(pnls)
    net_pnl = sum(pnls)
    return {
        "closed_trades": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / count * 100, 2) if count else None,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "net_pnl": round(net_pnl, 4),
        "ending_equity": round(float(starting_equity) + net_pnl, 4),
        "return_pct": round(net_pnl / float(starting_equity) * 100, 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "infinite"),
        "average_trade_pnl": round(net_pnl / count, 4) if count else None,
        "expectancy_per_trade": round(net_pnl / count, 4) if count else None,
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "method": "realized_pnl_only; fees_and_slippage_must_be_included_by_caller",
    }
