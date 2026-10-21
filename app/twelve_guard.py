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


async def call(getter: Callable[..., Awaitable[Any]], *args, **kwargs):
    """Guard Twelve Data requests and rotate configured credentials on per-key failures.

    Each credential must be independently provisioned and used within its own
    provider terms. Shared account-credit exhaustion and local daily caps still
    stop requests; key rotation is not used to bypass account-level quotas.
    """
    global _blocked, _block_reason
    if not await allowed():
        raise RuntimeError(f"Twelve Data circuit breaker open: {_block_reason or 'protected'}")

    params_original = kwargs.get("params")
    base_params = dict(params_original) if isinstance(params_original, dict) else None
    attempts = max(1, _pool.size)
    last_response = None
    last_error = None

    for _ in range(attempts):
        if not await allowed():
            break
        key = await _pool.acquire()
        if not key:
            break
        call_kwargs = dict(kwargs)
        if base_params is not None:
            call_kwargs["params"] = {**base_params, "apikey": key}
        try:
            response = await getter(*args, **call_kwargs)
        except Exception as exc:
            last_error = exc
            await _pool.mark_failure(key)
            continue

        last_response = response
        await record_response(response)
        code = getattr(response, "status_code", None)
        if code in (401, 403):
            # Invalid/unauthorized credentials are key-specific: cool this key
            # down and try another independently provisioned credential.
            await _pool.mark_failure(key)
            if not await allowed():
                break
            continue
        if code == 429:
            # Treat rate limiting as a provider/account-level limit. Do not
            # rotate keys to evade it; pause Twelve Data until the UTC day resets.
            async with _lock:
                _blocked = True
                _block_reason = "Twelve Data HTTP429 rate limit"
            return response

        await _pool.mark_success(key)
        return response

    snap = await _pool.snapshot()
    async with _lock:
        if snap["keys"] and snap["available"] == 0:
            # Do not latch the global daily breaker for per-key cooldowns;
            # acquire() will become available again after the backoff expires.
            _block_reason = f"all {snap['keys']} Twelve Data credentials are cooling down"
        elif last_response is not None and getattr(last_response, "status_code", None) == 429:
            _block_reason = "Twelve Data rate limit; credentials are cooling down"
    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Twelve Data credential pool unavailable: {_block_reason or 'cooling down'}")


async def status() -> dict:
    async with _lock:
        return {
            "blocked": _blocked,
            "reason": _block_reason,
            "utc_day": _day or _utc_day(),
            "requests_seen": _used,
            "credits_left": _remaining,
        }
