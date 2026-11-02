"""Shared confirmation rules for the SAS PRO scanner and channel gate.

Targets are informational when resistance is not well-defined. A protective
stop, a passing SAS strategy, and a trustworthy live price remain mandatory.
"""


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def confirmation_state(*, strategy_pass, live_price, live_source, targets):
    """Return explicit confirmation and target states without fabricating levels."""
    price = _number(live_price)
    source = str(live_source or "").strip().lower()
    live_ok = price > 0 and source not in {"", "unavailable", "scan data"}

    target_data = targets if isinstance(targets, dict) else {}
    stop = _number(target_data.get("exit") or target_data.get("stop"))
    stop_distance_pct = ((price - stop) / price * 100.0) if price > 0 and stop > 0 else 0.0
    stop_ok = bool(price > stop > 0 and stop_distance_pct >= 0.75)

    levels = target_data.get("targets")
    levels = levels if isinstance(levels, list) else []
    first_target = _number(levels[0]) if levels else 0.0
    target_confirmed = bool(target_data.get("status") == "ok" and first_target > price > stop > 0)

    ready = bool(strategy_pass and live_ok and stop_ok)
    return {
        "confirmation_ready": ready,
        "opportunity_status": "confirmed" if ready else "watch",
        "live_price_ready": live_ok,
        "stop_ready": stop_ok,
        "target_confirmed": target_confirmed,
        "stop_distance_pct": round(stop_distance_pct, 2),
    }
