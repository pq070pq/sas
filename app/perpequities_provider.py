"""Optional PerpEquities enrichment provider.

Uses one cached bulk snapshot instead of one HTTP request per stock. Contract
metrics are context only, never spot-stock liquidity or proof of whale activity.
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
_snapshot: tuple[float, dict[str, Any]] | None = None
_snapshot_lock = asyncio.Lock()


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


def _signed_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if abs(number) < float("inf") else None


def _freshness(value: Any, current: datetime) -> tuple[str | None, float | None, bool]:
    parsed = _parse_time(value)
    if parsed is None:
        return None, None, False
    age = (current - parsed).total_seconds()
    fresh = -60 <= age <= MAX_DATA_AGE_SECONDS
    return parsed.isoformat(), round(age, 2), fresh


def normalize_asset(payload: Any, requested_symbol: str, now: datetime | None = None) -> dict[str, Any] | None:
    """Validate and normalize an asset from either a single-asset or bulk response."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        wanted = requested_symbol.strip().upper()
        data = next((item for item in data if isinstance(item, dict) and str(item.get("ticker") or "").strip().upper() == wanted), None)
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

    if payload.get("stale") is True or data.get("stale") is True:
        return None
    if market_data_at is None:
        return None
    age = (current - market_data_at).total_seconds()
    if age < -60 or age > MAX_DATA_AGE_SECONDS:
        return None

    venues = data.get("venues")
    if not isinstance(venues, list):
        venues = []
    live_venues = []
    fresh_live_markets = 0
    for venue in venues:
        if not isinstance(venue, dict) or venue.get("live") is not True or venue.get("status") != "trading":
            continue
        updated_at, venue_age, venue_fresh = _freshness(venue.get("updatedAt"), current)
        if venue_fresh:
            fresh_live_markets += 1
        live_venues.append({
            "venue": str(venue.get("venue") or ""),
            "volume_24h_usd": _number(venue.get("volume24hUsd")),
            "open_interest_usd": _number(venue.get("openInterestUsd")),
            "spread_bps": _number(venue.get("spreadBps")),
            "depth_1pct_usd": _number(venue.get("depth1PctUsd")),
            "funding_apr": _signed_number(venue.get("fundingApr")),
            "updated_at": updated_at,
            "updated_age_seconds": venue_age,
            "fresh": venue_fresh,
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
        "fresh_live_markets": fresh_live_markets,
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


async def _get_snapshot() -> dict[str, Any] | None:
    global _snapshot
    now = time.monotonic()
    if _snapshot is not None and now - _snapshot[0] < CACHE_TTL_SECONDS:
        return _snapshot[1]

    async with _snapshot_lock:
        now = time.monotonic()
        if _snapshot is not None and now - _snapshot[0] < CACHE_TTL_SECONDS:
            return _snapshot[1]
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
                response = await client.get(
                    f"{BASE_URL}/assets",
                    params={"assetClass": "equity", "live": "1", "limit": "2000"},
                )
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                return None
            # Do not cache stale snapshots; never reset data age based on publication time.
            if payload.get("stale") is True or _parse_time(payload.get("marketDataAt")) is None:
                return None
            _snapshot = (time.monotonic(), payload)
            return payload
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.info("PerpEquities bulk snapshot unavailable: %s", type(exc).__name__)
            return None
        except Exception:
            logger.exception("Unexpected PerpEquities bulk snapshot failure")
            return None


async def get_equity_perpetual(symbol: str) -> dict[str, Any] | None:
    """Return optional contract context from one cached bulk snapshot, or None."""
    ticker = str(symbol or "").strip().upper()
    if not ticker or len(ticker) > 12 or not ticker.replace(".", "").replace("-", "").isalnum():
        return None
    payload = await _get_snapshot()
    if payload is None:
        return None
    return normalize_asset(payload, ticker)


def clear_cache() -> None:
    """Clear the in-process snapshot cache; intended for tests and diagnostics."""
    global _snapshot
    _snapshot = None



async def get_equity_perpetuals(symbols: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Return optional PerpEquities context for matching symbols from one cached snapshot."""
    payload = await _get_snapshot()
    if payload is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol or symbol in result:
            continue
        item = normalize_asset(payload, symbol)
        if item is not None:
            result[symbol] = item
    return result


def get_cached_equity_perpetual(symbol: str) -> dict[str, Any] | None:
    """Read a still-valid in-process snapshot without making a network request."""
    if _snapshot is None or time.monotonic() - _snapshot[0] >= CACHE_TTL_SECONDS:
        return None
    return normalize_asset(_snapshot[1], str(symbol or "").strip().upper())
