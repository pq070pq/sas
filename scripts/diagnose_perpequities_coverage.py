#!/usr/bin/env python3
"""Read-only live coverage diagnostic for PerpEquities vs SAS PRO's price universe.

The exchange screener comparison is a proxy, not the exact final scanner shortlist:
the real radar also combines PanWatch/OpenTerminal/other feeds and applies
additional evidence filters. This script never feeds the scanner or publishes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PERP_URL = "https://perpequities.com/api/v1/assets"
NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
MIN_PRICE = 0.30
MAX_PRICE = 15.00
MAX_AGE_SECONDS = 600
SAMPLE_SYMBOLS = ("OSS", "NOK", "VEEA")
HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
}


def get_json(url: str, params: dict | None = None, headers: dict | None = None):
    full_url = url + ("?" + urlencode(params) if params else "")
    request = Request(full_url, headers=headers or {"Accept": "application/json", "User-Agent": "SAS-PRO-coverage-diagnostic/1.0"})
    with urlopen(request, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def num(value):
    try:
        return float(str(value or "").replace("$", "").replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def exchange_proxy_symbols():
    """Approximate SAS $0.30-$15 price-filtered exchange universe."""
    symbols = set()
    errors = []
    excluded_words = ("WARRANT", "RIGHT", "UNIT", "PREFERRED", "ETF", "NOTE", "DEPOSITARY", "TRUST", "FUND")
    for exchange in ("NASDAQ", "NYSE", "AMEX"):
        try:
            status, payload = get_json(
                NASDAQ_URL,
                {"tableonly": "true", "limit": 5000, "offset": 0, "exchange": exchange, "download": "true"},
                HEADERS,
            )
            rows = ((payload.get("data") or {}).get("rows") or [])
            if status != 200 or not isinstance(rows, list):
                errors.append(exchange + ":invalid-response")
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                name = str(row.get("name") or "").strip().upper()
                price = num(row.get("lastsale"))
                volume = num(row.get("volume"))
                if not symbol or not name or volume <= 0 or not MIN_PRICE <= price <= MAX_PRICE:
                    continue
                if any(word in name for word in excluded_words):
                    continue
                if any(symbol.endswith(suffix) for suffix in (".WS", ".WT", ".W", ".U")):
                    continue
                symbols.add(symbol)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            errors.append(exchange + ":" + type(exc).__name__)
    return symbols, errors


def main() -> int:
    print("PerpEquities coverage diagnostic — read-only; no scanner integration")
    try:
        status, payload = get_json(PERP_URL, {"assetClass": "equity", "live": "1", "limit": 2000})
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"RESULT=PERPEQUITIES_API_ERROR DETAIL={type(exc).__name__}: {exc}")
        return 0
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        print(f"RESULT=INVALID_PERPEQUITIES_RESPONSE HTTP_STATUS={status}")
        return 0

    now = datetime.now(timezone.utc)
    generated = parse_time(payload.get("generatedAt"))
    market_at = parse_time(payload.get("marketDataAt"))
    age = (now - market_at).total_seconds() if market_at else None
    rows = payload["data"]
    by_ticker = {str(row.get("ticker") or "").strip().upper(): row for row in rows if isinstance(row, dict) and row.get("ticker")}
    fresh = age is not None and -60 <= age <= MAX_AGE_SECONDS and payload.get("stale") is not True
    print(f"HTTP_STATUS={status}")
    print(f"GENERATED_AT={generated.isoformat() if generated else 'unknown'}")
    print(f"MARKET_DATA_AT={market_at.isoformat() if market_at else 'unknown'}")
    print(f"MARKET_DATA_AGE_SECONDS={round(age) if age is not None else 'unknown'}")
    print(f"SNAPSHOT_FRESH={fresh}")
    print(f"PERPEQUITIES_RETURNED_ROWS={len(rows)}")
    print(f"API_TOTAL={payload.get('total', 'unknown')}")

    for symbol in SAMPLE_SYMBOLS:
        row = by_ticker.get(symbol)
        if row is None:
            print(f"SYMBOL={symbol} COVERED=false")
        else:
            venues = row.get("venues") if isinstance(row.get("venues"), list) else []
            live = sum(1 for v in venues if isinstance(v, dict) and v.get("live") is True and v.get("status") == "trading")
            print(f"SYMBOL={symbol} COVERED=true LIVE_MARKETS={live} URL={row.get('url', 'unknown')}")

    universe, errors = exchange_proxy_symbols()
    if errors:
        print("EXCHANGE_UNIVERSE_ERRORS=" + ",".join(errors))
    print(f"EXCHANGE_PRICE_VOLUME_PROXY_SYMBOLS={len(universe)}")
    matched = sorted(universe.intersection(by_ticker.keys()))
    missing = sorted(universe.difference(by_ticker.keys()))
    pct = (len(matched) / len(universe) * 100) if universe else 0.0
    print(f"PROXY_SYMBOLS_WITH_PERPEQUITIES={len(matched)}")
    print(f"PROXY_COVERAGE_PCT={pct:.2f}")
    print(f"PROXY_SYMBOLS_WITHOUT_PERPEQUITIES={len(missing)}")
    print("PROXY_MISSING_SAMPLE=" + (",".join(missing[:100]) if missing else "none"))
    print("IMPORTANT=This is an exchange-screener proxy, not the exact final SAS scanner candidate set.")
    print("IMPORTANT=Perpetual contract metrics are not spot-stock liquidity or proof of whale activity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
