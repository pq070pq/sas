"""Central Twelve Data circuit breaker.

Twelve Data is strictly optional in SAS PRO. If its remaining quota becomes low,
or a request returns 429, all further Twelve Data calls are blocked for the
current UTC day. The rest of the radar continues using primary/fallback sources.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .config import settings

_lock = asyncio.Lock()
_blocked = False
_block_reason = ""
_day = ""
_used = 0
_remaining: int | None = None


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def allowed() -> bool:
    global _day, _blocked, _block_reason, _used
    async with _lock:
        today = _utc_day()
        if today != _day:
            _day = today
            _blocked = False
            _block_reason = ""
            _used = 0
            # remaining is refreshed only from real API response headers.
        if not settings.twelve_data_api_key or _blocked:
            return False
        cap = int(getattr(settings, "twelve_data_daily_request_cap", 0) or 0)
        if cap > 0 and _used >= cap:
            _blocked = True
            _block_reason = f"daily request cap {cap} reached"
            return False
        reserve = int(getattr(settings, "twelve_data_reserve_credits", 0) or 0)
        if _remaining is not None and reserve > 0 and _remaining <= reserve:
            _blocked = True
            _block_reason = f"remaining credits {_remaining} <= reserve {reserve}"
            return False
        return True


async def record_response(response: Any) -> None:
    global _used, _remaining, _blocked, _block_reason
    async with _lock:
        _used += 1
        left = response.headers.get("api-credits-left")
        if left is not None:
            try:
                _remaining = int(left)
            except (TypeError, ValueError):
                pass
        reserve = int(getattr(settings, "twelve_data_reserve_credits", 0) or 0)
        if _remaining is not None and reserve > 0 and _remaining <= reserve:
            _blocked = True
            _block_reason = f"remaining credits {_remaining} <= reserve {reserve}"
        if getattr(response, "status_code", None) == 429:
            _blocked = True
            _block_reason = "Twelve Data returned HTTP 429"


async def call(getter: Callable[..., Awaitable[Any]], *args, **kwargs):
    """Guard one Twelve Data request. Raises RuntimeError before making a call when blocked."""
    if not await allowed():
        raise RuntimeError(f"Twelve Data circuit breaker open: {_block_reason or 'protected'}")
    response = await getter(*args, **kwargs)
    await record_response(response)
    return response


async def status() -> dict:
    async with _lock:
        return {
            "blocked": _blocked,
            "reason": _block_reason,
            "utc_day": _day or _utc_day(),
            "requests_seen": _used,
            "credits_left": _remaining,
        }
