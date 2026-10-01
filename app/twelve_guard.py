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
from .key_pool import KeyPool, parse_keys

_pool = KeyPool(parse_keys(settings.twelve_data_api_keys, settings.twelve_data_api_key), settings.api_key_cooldown_seconds)

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
        if _pool.size == 0 or _blocked:
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
    """Guard one Twelve Data request and rotate configured keys on throttling."""
    global _blocked, _block_reason
    if not await allowed():
        raise RuntimeError(f"Twelve Data circuit breaker open: {_block_reason or 'protected'}")

    key = await _pool.acquire()
    if not key:
        raise RuntimeError("Twelve Data credential pool is cooling down")

    params = kwargs.get("params")
    if isinstance(params, dict):
        params = dict(params)
        params["apikey"] = key
        kwargs["params"] = params

    try:
        response = await getter(*args, **kwargs)
    except Exception:
        await _pool.mark_failure(key)
        raise

    await record_response(response)

    if getattr(response, "status_code", None) in (401, 403, 429):
        retry = response.headers.get("Retry-After")
        await _pool.mark_failure(key, retry_after=int(retry) if retry and retry.isdigit() else None)
        snap = await _pool.snapshot()
        if snap["available"] == 0:
            async with _lock:
                _blocked = True
                _block_reason = f"all {snap['keys']} Twelve Data credentials are cooling down"
    else:
        await _pool.mark_success(key)

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
