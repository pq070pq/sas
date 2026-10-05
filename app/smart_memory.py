"""Bounded, deduplicating in-process memory for SAS PRO.

Keeps only useful recent state, removes expired entries, limits memory growth,
and provides stable list deduplication so repeated provider/news data does not
propagate into reports.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import OrderedDict
from typing import Any


class SmartMemory:
    def __init__(self, ttl_seconds: int, max_items: int = 256):
        self.ttl = max(1, int(ttl_seconds))
        self.max_items = max(1, int(max_items))
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [k for k, (stamp, _) in self._items.items() if now - stamp >= self.ttl]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def get(self, key: str):
        self._purge()
        item = self._items.get(str(key).upper())
        if item is None:
            return None
        stamp, value = item
        if time.monotonic() - stamp >= self.ttl:
            self._items.pop(str(key).upper(), None)
            return None
        self._items.move_to_end(str(key).upper())
        return copy.deepcopy(value)

    def put(self, key: str, value: Any) -> None:
        self._purge()
        normalized = str(key).upper()
        self._items[normalized] = (time.monotonic(), copy.deepcopy(value))
        self._items.move_to_end(normalized)
        self._purge()

    def clear(self) -> None:
        self._items.clear()

    def stats(self) -> dict:
        self._purge()
        return {"items": len(self._items), "max_items": self.max_items, "ttl_seconds": self.ttl}


def dedupe_records(rows: list[Any] | None, keys: tuple[str, ...] = ("symbol", "headline", "url")) -> list[Any]:
    """Stable de-duplication: preserves order and keeps the first useful record."""
    result = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            fingerprint = hashlib.sha1(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        else:
            parts = []
            for key in keys:
                value = row.get(key)
                if value not in (None, ""):
                    parts.append(str(value).strip().lower())
            if not parts:
                parts = [json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)]
            fingerprint = hashlib.sha1("|".join(parts).encode()).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)
    return result
