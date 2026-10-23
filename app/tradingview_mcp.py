"""Optional, read-only TradingView Unified MCP discovery adapter.

Disabled by default. The adapter is intended for shadow-mode discovery only:
SAS PRO still applies its own price, exchange, liquidity, and technical gates.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

_COLUMNS = ["name", "close", "change", "volume", "market_cap_basic", "type", "subtype", "description"]


def _endpoint_and_token():
    if os.getenv("TV_MCP_RADAR_ENABLED", "").strip() != "1":
        return None, None
    endpoint = os.getenv("TV_MCP_URL", "http://127.0.0.1:3811/mcp").strip()
    token = os.getenv("TV_HTTP_TOKEN", "").strip()
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.path != "/mcp"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("TV_MCP_URL must be an explicit loopback http://127.0.0.1:PORT/mcp URL")
    if len(token) < 32:
        raise ValueError("TV_HTTP_TOKEN must contain at least 32 characters")
    return endpoint, token


def _decode_tool_result(payload):
    if not isinstance(payload, dict):
        return []
    if payload.get("error"):
        raise RuntimeError("TradingView MCP returned a JSON-RPC error")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise RuntimeError("TradingView MCP tool call failed")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _rows_from_data(structured)
    for block in result.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            decoded = json.loads(block.get("text", ""))
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            rows = _rows_from_data(decoded)
            if rows:
                return rows
    return []


def _rows_from_data(data):
    if isinstance(data, list):
        raw_rows = data
    elif isinstance(data, dict):
        raw_rows = data.get("rows") or data.get("results") or data.get("data") or []
    else:
        return []
    normalized = []
    for item in raw_rows:
        if isinstance(item, dict) and isinstance(item.get("d"), list):
            symbol = str(item.get("s") or "").strip().upper()
            values = item["d"]
            row = dict(zip(_COLUMNS, values))
            row["symbol"] = symbol.split(":", 1)[-1] if ":" in symbol else symbol
            row["exchange"] = symbol.split(":", 1)[0] if ":" in symbol else ""
        elif isinstance(item, dict):
            row = dict(item.get("values") or {})
            row.update({key: value for key, value in item.items() if key != "values"})
            symbol = str(row.get("symbol") or row.get("ticker") or row.get("s") or "").strip().upper()
            if ":" in symbol:
                row.setdefault("exchange", symbol.split(":", 1)[0])
                symbol = symbol.split(":", 1)[1]
            row["symbol"] = symbol
        else:
            continue
        try:
            price = float(row.get("price", row.get("close")))
            volume = float(row.get("volume") or 0)
            change = float(row.get("change_pct", row.get("change")) or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or price <= 0:
            continue
        row.update({
            "symbol": str(row["symbol"]).upper(),
            "price": price,
            "volume": volume,
            "change_pct": change,
            "market_cap": row.get("market_cap", row.get("market_cap_basic", 0)) or 0,
            "source": "TradingView MCP",
        })
        normalized.append(row)
    return normalized


async def discover_tradingview_mcp(price_min=0.30, price_max=6.00):
    """Fetch bounded US stock candidates from MCP; return [] when disabled."""
    endpoint, token = _endpoint_and_token()
    if not endpoint:
        return []
    candidates = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0), follow_redirects=False) as client:
        for offset in range(0, 300, 100):
            request = {
                "jsonrpc": "2.0",
                "id": offset + 1,
                "method": "tools/call",
                "params": {
                    "name": "screener_scan",
                    "arguments": {
                        "market": "america",
                        "columns": _COLUMNS,
                        "filters": [
                            {"field": "close", "operation": "egreater", "value": price_min},
                            {"field": "close", "operation": "eless", "value": price_max},
                        ],
                        "sort_by": "volume",
                        "sort_order": "desc",
                        "limit": 100,
                        "offset": offset,
                    },
                },
            }
            response = await client.post(
                endpoint,
                json=request,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )
            response.raise_for_status()
            rows = _decode_tool_result(response.json())
            if not rows:
                break
            for row in rows:
                price = row.get("price", 0)
                if price_min <= price <= price_max:
                    candidates.append(row)
            if len(rows) < 100:
                break
    # Deduplicate without overriding a candidate with malformed or stale values.
    unique = {}
    for row in candidates:
        symbol = row["symbol"]
        current = unique.get(symbol)
        if current is None or row["price"] * row["volume"] > current["price"] * current["volume"]:
            unique[symbol] = row
    return list(unique.values())
