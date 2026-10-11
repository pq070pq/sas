"""Optional PerpEquities enrichment provider.

This module is intentionally isolated from stock discovery and signal decisions.
Perp-equity contract metrics are context only; they are not spot-stock liquidity
or proof of institutional/whale accumulation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)
BASE_URL = "https://perpequities.com/api/v1"
CACHE_TTL_SECONDS = 300
MAX_DATA_AGE_SECONDS = 600
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_cache_lock = asyncio.Lock()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 and number < float("inf") else None


def normalize_asset(payload: Any, requested_symbol: str, now: datetime | None = None) -> dict[str, Any] | None:
    """Validate and normalize one asset response; reject stale/incomplete snapshots."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    ticker = str(data.get("ticker") or "").strip().upper()
    if not ticker or ticker != requested_symbol.strip().upper():
        return None

    generated_at = _parse_time(payload.get("generatedAt"))
    market_data_at = _parse_time(data.get("marketDataAt") or payload.get("marketDataAt"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    # Snapshot timestamp must be present and recent. A republished old snapshot
    # must not be treated as live.
    if payload.get("stale") is True or data.get("stale") is True:
        return None
    if market_data_at is None:
        # Some endpoint versions expose marketDataAt only in headers. We cannot
        # validate headers after normalization, so fail closed if the body omits it.
        return None
    age = (current - market_data_at).total_seconds()
    if age < -60 or age > MAX_DATA_AGE_SECONDS:
        return None

    venues = data.get("venues")
    if not isinstance(venues, list):
        venues = []
    live_venues = []
    for venue in venues:
        if not isinstance(venue, dict) or venue.get("live") is not True:
            continue
        if venue.get("status") != "trading":
            continue
        live_venues.append({
            "venue": str(venue.get("venue") or ""),
            "volume_24h_usd": _number(venue.get("volume24hUsd")),
            "open_interest_usd": _number(venue.get("openInterestUsd")),
            "spread_bps": _number(venue.get("spreadBps")),
            "depth_1pct_usd": _number(venue.get("depth1PctUsd")),
            "funding_apr": _signed_number(venue.get("fundingApr")),
            "updated_at": venue.get("updatedAt"),
        })

    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    volume_quality = quality.get("volume") if isinstance(quality.get("volume"), dict) else {}
    oi_quality = quality.get("openInterest") if isinstance(quality.get("openInterest"), dict) else {}
    return {
        "symbol": ticker,
        "reference_price": _number(data.get("price")),
        "change_24h_fraction": _signed_number(data.get("change24h")),
        "change_7d_fraction": _signed_number(data.get("change7d")),
        "volume_24h_usd": _number(data.get("qualityFilteredVolume24hUsd")),
        "open_interest_usd": _number(data.get("openInterestUsd")),
        "live_markets": len(live_venues),
        "venues": live_venues,
        "volume_observed_markets": volume_quality.get("observedMarkets"),
        "volume_total_markets": volume_quality.get("totalMarkets"),
        "oi_observed_markets": oi_quality.get("observedMarkets"),
        "oi_total_markets": oi_quality.get("totalMarkets"),
        "market_data_at": market_data_at.isoformat(),
        "generated_at": generated_at.isoformat() if generated_at else None,
        "source": "PerpEquities",
        "source_url": str(data.get("url") or "https://perpequities.com"),
        "attribution": "Data: PerpEquities (perpequities.com)",
        "is_contract_context_only": True,
    }


def _signed_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if abs(number) < float("inf") else None


async def get_equity_perpetual(symbol: str) -> dict[str, Any] | None:
    """Return optional contract context for a ticker, or None on any unavailable data."""
    ticker = str(symbol or "").strip().upper()
    if not ticker or len(ticker) > 12 or not ticker.replace(".", "").replace("-", "").isalnum():
        return None

    now = time.monotonic()
    async with _cache_lock:
        cached = _cache.get(ticker)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return dict(cached[1]) if cached[1] is not None else None

    result = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.get(f"{BASE_URL}/assets/{ticker}")
            if response.status_code == 404:
                result = None
            else:
                response.raise_for_status()
                result = normalize_asset(response.json(), ticker)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.info("PerpEquities enrichment unavailable for %s: %s", ticker, type(exc).__name__)
    except Exception:
        logger.exception("Unexpected PerpEquities enrichment failure for %s", ticker)

    async with _cache_lock:
        _cache[ticker] = (time.monotonic(), result)
    return dict(result) if result is not None else None


def clear_cache() -> None:
    """Clear the in-process cache; intended for tests and controlled diagnostics."""
    _cache.clear()
