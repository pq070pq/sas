"""Best-effort enrichment for stock-alert fields that the scanner does not calculate.

Only accepts values returned by configured/public data providers. Missing values stay
missing; borrow rates are not inferred from short interest or availability counts.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 6 * 60 * 60
_TIMEOUT = 8.0


def _keys() -> list[str]:
    raw = [settings.fmp_api_key, *(settings.fmp_api_keys or "").replace(";", ",").split(",")]
    result: list[str] = []
    for value in raw:
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _first(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else {}
    return data if isinstance(data, dict) else {}


def _pick(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", "N/A", "n/a", "—", "-"):
            return value
    return None


async def _fmp_json(client: httpx.AsyncClient, path: str, key: str) -> Any:
    response = await client.get(
        "https://financialmodelingprep.com" + path,
        params={"apikey": key},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


async def _fmp_enrichment(symbol: str) -> dict[str, Any]:
    keys = _keys()
    if not keys:
        return {}
    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        for key in keys:
            try:
                profile, filings = await asyncio.gather(
                    _fmp_json(client, f"/stable/shares-float?symbol={symbol}", key),
                    _fmp_json(client, f"/stable/sec-filings-search?symbol={symbol}", key),
                    return_exceptions=True,
                )
                profile_row = _first(profile) if not isinstance(profile, Exception) else {}
                filing_rows = filings if isinstance(filings, list) else []
                filing_rows = [row for row in filing_rows if isinstance(row, dict)]
                filing_rows.sort(
                    key=lambda row: str(_pick(row, "filingDate", "date", "acceptedDate") or ""),
                    reverse=True,
                )
                filing = filing_rows[0] if filing_rows else {}
                result: dict[str, Any] = {}
                float_shares = _pick(profile_row, "floatShares", "float_shares", "float")
                if isinstance(float_shares, (int, float)) and float_shares > 0:
                    result["float_shares"] = float_shares
                    result["float_source"] = "Financial Modeling Prep"
                elif isinstance(float_shares, str):
                    try:
                        number = float(float_shares.replace(",", ""))
                        if number > 0:
                            result["float_shares"] = number
                            result["float_source"] = "Financial Modeling Prep"
                    except ValueError:
                        pass
                if filing:
                    filing_type = _pick(filing, "type", "form", "filingType")
                    filing_date = _pick(filing, "filingDate", "date", "acceptedDate")
                    filing_url = _pick(filing, "finalLink", "link", "url", "filingUrl")
                    bits = [str(v) for v in (filing_type, filing_date) if v]
                    if bits:
                        result["filing"] = " — ".join(bits)
                    if filing_url:
                        result["filing_url"] = str(filing_url)
                    result["filing_source"] = "SEC filings via Financial Modeling Prep"
                if result:
                    return result
            except Exception as exc:
                logger.debug("Stock report enrichment provider failed for %s: %s", symbol, type(exc).__name__)
    return {}


async def _licensed_enrichment(symbol: str) -> dict[str, Any]:
    """Optional deployment-specific licensed endpoint; only recognized explicit fields are used."""
    base = str(settings.licensed_market_data_url or "").strip().rstrip("/")
    if not base:
        return {}
    headers = {"Accept": "application/json"}
    if settings.licensed_market_data_api_key:
        headers["Authorization"] = f"Bearer {settings.licensed_market_data_api_key}"
        headers["X-API-Key"] = settings.licensed_market_data_api_key
    try:
        async with httpx.AsyncClient(headers=headers, timeout=float(settings.licensed_market_data_timeout_seconds or 10)) as client:
            response = await client.get(f"{base}/stocks/{symbol}")
            response.raise_for_status()
            payload = response.json()
        row = _first(payload)
        result: dict[str, Any] = {}
        ctb = _pick(row, "ctb", "cost_to_borrow", "costToBorrow", "borrow_fee", "borrowFeePct")
        available = _pick(row, "shares_available_to_borrow", "sharesAvailableToBorrow", "short_shares_available")
        float_shares = _pick(row, "float_shares", "floatShares", "float")
        if ctb is not None:
            result["ctb"] = ctb
            result["ctb_source"] = str(_pick(row, "source", "provider") or "Licensed market-data provider")
        if available is not None:
            result["shares_available_to_borrow"] = available
            result["borrow_availability_source"] = str(_pick(row, "source", "provider") or "Licensed market-data provider")
        if float_shares is not None and "float_shares" not in result:
            result["float_shares"] = float_shares
            result["float_source"] = str(_pick(row, "source", "provider") or "Licensed market-data provider")
        return result
    except Exception as exc:
        logger.debug("Licensed stock enrichment failed for %s: %s", symbol, type(exc).__name__)
        return {}


async def enrich_stock_report_data(symbol: str) -> dict[str, Any]:
    """Return cached provider-backed float, borrow, and latest filing fields."""
    key = str(symbol or "").strip().upper()
    if not key:
        return {}
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return dict(cached[1])
    fmp, licensed = await asyncio.gather(
        _fmp_enrichment(key),
        _licensed_enrichment(key),
        return_exceptions=True,
    )
    result: dict[str, Any] = {}
    for payload in (fmp, licensed):
        if isinstance(payload, dict):
            for field, value in payload.items():
                if value not in (None, "") and (field not in result or result[field] in (None, "")):
                    result[field] = value
    result["enrichment_checked_at"] = datetime.now(timezone.utc).isoformat()
    _CACHE[key] = (now, result)
    return result
