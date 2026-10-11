#!/usr/bin/env python3
"""Live, read-only PerpEquities coverage diagnostic for representative SAS symbols.

This is a coverage check only: it does not feed the scanner or publish signals.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

URL = "https://perpequities.com/api/v1/assets"
SAMPLE_SYMBOLS = ("OSS", "NOK", "VEEA")
MAX_AGE_SECONDS = 600


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def main() -> int:
    url = URL + "?" + urlencode({"assetClass": "equity", "live": "1", "limit": 2000})
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "SAS-PRO-coverage-diagnostic/1.0"})
    try:
        with urlopen(request, timeout=12) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"RESULT=API_ERROR\nDETAIL={type(exc).__name__}: {exc}")
        return 0  # A diagnostic failure must not block unrelated PR validation.

    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        print(f"RESULT=INVALID_RESPONSE\nHTTP_STATUS={status}\nTOP_LEVEL_TYPE={type(payload).__name__}")
        return 0

    now = datetime.now(timezone.utc)
    generated = parse_time(payload.get("generatedAt"))
    market_data_at = parse_time(payload.get("marketDataAt"))
    age = (now - market_data_at).total_seconds() if market_data_at else None
    rows = payload["data"]
    by_ticker = {}
    for row in rows:
        if isinstance(row, dict):
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                by_ticker[ticker] = row

    stale_flag = payload.get("stale") is True
    fresh = age is not None and -60 <= age <= MAX_AGE_SECONDS and not stale_flag
    print("PerpEquities live coverage diagnostic — sample only, not the full SAS radar universe")
    print(f"HTTP_STATUS={status}")
    print(f"GENERATED_AT={generated.isoformat() if generated else 'missing/invalid'}")
    print(f"MARKET_DATA_AT={market_data_at.isoformat() if market_data_at else 'missing/invalid'}")
    print(f"MARKET_DATA_AGE_SECONDS={round(age) if age is not None else 'unknown'}")
    print(f"STALE_FLAG={stale_flag}")
    print(f"SNAPSHOT_FRESH={fresh}")
    print(f"RETURNED_ROWS={len(rows)}")
    print(f"API_TOTAL={payload.get('total', 'unknown')}")
    found = 0
    for symbol in SAMPLE_SYMBOLS:
        row = by_ticker.get(symbol)
        if row is None:
            print(f"SYMBOL={symbol} COVERED=false")
            continue
        found += 1
        venues = row.get("venues") if isinstance(row.get("venues"), list) else []
        tradable = [v for v in venues if isinstance(v, dict) and v.get("live") is True and v.get("status") == "trading"]
        print(
            f"SYMBOL={symbol} COVERED=true PRICE={row.get('price', 'unknown')} "
            f"LIVE_MARKETS={len(tradable)} URL={row.get('url', 'unknown')}"
        )
    print(f"SAMPLE_COVERAGE={found}/{len(SAMPLE_SYMBOLS)}")
    print("NOTE=Perpetual-contract metrics are not spot-stock liquidity and are not used as proof of whale activity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
