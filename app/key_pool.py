"""Small, quota-safe credential pools for independently provisioned API keys.

Keys are rotated for fault tolerance and load spreading only. They are not a
mechanism for bypassing a provider's account/organization quota or terms.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class _KeyState:
    key: str
    failures: int = 0
    blocked_until: float = 0.0


class KeyPool:
    def __init__(self, keys: Iterable[str] = (), cooldown_seconds: int = 60):
        unique = []
        seen = set()
        for raw in keys:
            key = str(raw or "").strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        self._states = [_KeyState(key=x) for x in unique]
        self._cursor = 0
        self._cooldown = max(10, int(cooldown_seconds))
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._states)

    async def acquire(self) -> str | None:
        async with self._lock:
            if not self._states:
                return None
            now = time.monotonic()
            for offset in range(len(self._states)):
                idx = (self._cursor + offset) % len(self._states)
                state = self._states[idx]
                if state.blocked_until <= now:
                    self._cursor = (idx + 1) % len(self._states)
                    logger.debug("API_KEY_POOL_ACQUIRED slot=%s pool_size=%s", idx + 1, len(self._states))
                    return state.key
            logger.info("API_KEY_POOL_EXHAUSTED pool_size=%s available=0", len(self._states))
            return None

    async def mark_success(self, key: str) -> None:
        async with self._lock:
            for idx, state in enumerate(self._states):
                if state.key == key:
                    state.failures = 0
                    state.blocked_until = 0.0
                    logger.debug("API_KEY_POOL_SUCCESS slot=%s", idx + 1)
                    return

    async def mark_failure(self, key: str, *, retry_after: int | None = None) -> None:
        async with self._lock:
            for idx, state in enumerate(self._states):
                if state.key != key:
                    continue
                state.failures += 1
                delay = retry_after if retry_after and retry_after > 0 else min(
                    900, self._cooldown * (2 ** min(state.failures - 1, 4))
                )
                state.blocked_until = time.monotonic() + delay
                available = sum(1 for item in self._states if item.blocked_until <= time.monotonic())
                logger.warning(
                    "API_KEY_POOL_FAILURE slot=%s failures=%s cooldown_seconds=%s available=%s pool_size=%s",
                    idx + 1, state.failures, delay, available, len(self._states),
                )
                return

    async def snapshot(self) -> dict:
        async with self._lock:
            now = time.monotonic()
            return {
                "keys": len(self._states),
                "available": sum(1 for x in self._states if x.blocked_until <= now),
            }


def parse_keys(*values: str | None) -> list[str]:
    """Accept comma/newline/semicolon separated env values plus legacy singles."""
    out = []
    seen = set()
    for value in values:
        if not value:
            continue
        normalized = str(value).replace("\\n", ",").replace("\n", ",").replace(";", ",")
        for item in normalized.split(","):
            key = item.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out
