#!/usr/bin/env python3
"""Historical, non-lookahead backtest for the SAS PRO ICT-style liquidity sweep.

Downloads daily OHLCV from Stooq. This is a research harness, not a trading
recommendation. Symbols must be supplied explicitly to make universe selection
visible and reproducible.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def fetch_daily(symbol: str, timeout: int = 15) -> list[dict]:
    """Try API-keyed historical providers first; Stooq is last-resort only."""
    import os
    import json as _json
    import urllib.error

    def request_json(url: str, params: dict) -> dict:
        full_url = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(full_url, headers={"User-Agent": "SAS-PRO-ICT-Backtest/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return _json.loads(response.read().decode("utf-8", errors="replace"))

    errors = []
    td_key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if td_key:
        try:
            data = request_json("https://api.twelvedata.com/time_series", {
                "symbol": symbol.upper(), "interval": "1day", "outputsize": 365, "apikey": td_key
            })
            values = data.get("values") or []
            if values:
                rows = []
                for x in reversed(values):
                    try:
                        rows.append({"date": x.get("datetime", ""), "open": float(x["open"]),
                                     "high": float(x["high"]), "low": float(x["low"]),
                                     "close": float(x["close"]), "volume": float(x.get("volume") or 0)})
                    except (ValueError, TypeError, KeyError):
                        continue
                if rows:
                    return rows
            errors.append("Twelve Data: " + str(data.get("message") or data.get("code") or "no values")[:150])
        except Exception as exc:
            errors.append("Twelve Data: " + type(exc).__name__)

    fh_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if fh_key:
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            data = request_json("https://finnhub.io/api/v1/stock/candle", {
                "symbol": symbol.upper(), "resolution": "D",
                "from": int((now - timedelta(days=730)).timestamp()),
                "to": int(now.timestamp()), "token": fh_key,
            })
            if data.get("s") == "ok" and data.get("c"):
                return [{
                    "date": datetime.fromtimestamp(data["t"][i], timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(data["o"][i]), "high": float(data["h"][i]),
                    "low": float(data["l"][i]), "close": float(data["c"][i]),
                    "volume": float(data["v"][i]),
                } for i in range(len(data["c"]))]
            errors.append("Finnhub: " + str(data.get("s") or data.get("error") or "no data")[:150])
        except Exception as exc:
            errors.append("Finnhub: " + type(exc).__name__)

    # Stooq currently may return a browser-verification HTML page in CI.
    # Keep it as a fallback, but fail transparently rather than treating HTML as CSV.
    try:
        query = urllib.parse.urlencode({"s": symbol.lower() + ".us", "i": "d"})
        req = urllib.request.Request("https://stooq.com/q/d/l/?" + query,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(payload))
        required = {"Date", "Open", "High", "Low", "Close", "Volume"}
        if reader.fieldnames and required.issubset(set(reader.fieldnames)):
            rows = []
            for row in reader:
                try:
                    rows.append({"date": row["Date"], "open": float(row["Open"]),
                                 "high": float(row["High"]), "low": float(row["Low"]),
                                 "close": float(row["Close"]), "volume": float(row["Volume"])})
                except (ValueError, TypeError, KeyError):
                    continue
            if rows:
                return rows
        preview = payload.strip().replace("\\n", " ")[:100]
        errors.append("Stooq blocked/non-CSV: " + (preview or "empty response"))
    except Exception as exc:
        errors.append("Stooq: " + type(exc).__name__)
    raise RuntimeError(" | ".join(errors) or "No historical data provider configured")


def sweep_at(rows: list[dict], index: int, lookback: int, min_penetration_pct: float) -> dict | None:
    # Only candles strictly before the signal candle define the liquidity level.
    if index < lookback:
        return None
    prior = rows[index - lookback:index]
    candle = rows[index]
    support = min(x["low"] for x in prior)
    low, high = candle["low"], candle["high"]
    if support <= 0 or high <= low:
        return None
    penetration = (support - low) / support * 100.0
    close_position = (candle["close"] - low) / (high - low)
    bullish = (
        low < support
        and penetration >= min_penetration_pct
        and candle["close"] > support
        and candle["close"] > candle["open"]
        and close_position >= 0.60
    )
    if not bullish:
        return None
    return {"support": support, "penetration_pct": penetration}


def evaluate(rows: list[dict], symbol: str, lookback: int, min_price: float,
             max_price: float, min_dollar_volume: float, min_penetration_pct: float,
             horizons: tuple[int, ...]) -> list[dict]:
    signals = []
    for i in range(lookback, len(rows) - max(horizons, default=0)):
        candle = rows[i]
        price = candle["close"]
        if not min_price <= price <= max_price:
            continue
        if price * candle["volume"] < min_dollar_volume:
            continue
        detected = sweep_at(rows, i, lookback, min_penetration_pct)
        if not detected:
            continue
        record = {
            "symbol": symbol.upper(),
            "date": candle["date"],
            "entry_close": round(price, 6),
            "support_level": round(detected["support"], 6),
            "penetration_pct": round(detected["penetration_pct"], 4),
            "dollar_volume": round(price * candle["volume"], 2),
        }
        for horizon in horizons:
            future = rows[i + 1:i + 1 + horizon]
            if len(future) < horizon:
                continue
            record[f"return_{horizon}d_pct"] = round((future[-1]["close"] / price - 1) * 100, 3)
            record[f"max_favorable_{horizon}d_pct"] = round((max(x["high"] for x in future) / price - 1) * 100, 3)
            record[f"max_adverse_{horizon}d_pct"] = round((min(x["low"] for x in future) / price - 1) * 100, 3)
        signals.append(record)
    return signals


def summarize(signals: list[dict], horizon: int) -> dict:
    key = f"return_{horizon}d_pct"
    rows = [s for s in signals if key in s]
    returns = [s[key] for s in rows]
    return {
        "horizon_sessions": horizon,
        "signals": len(rows),
        "positive_close_rate_pct": round(100 * sum(x > 0 for x in returns) / len(returns), 2) if returns else None,
        "median_close_return_pct": round(statistics.median(returns), 3) if returns else None,
        "mean_close_return_pct": round(statistics.mean(returns), 3) if returns else None,
        "median_max_favorable_pct": round(statistics.median(s[f"max_favorable_{horizon}d_pct"] for s in rows), 3) if rows else None,
        "median_max_adverse_pct": round(statistics.median(s[f"max_adverse_{horizon}d_pct"] for s in rows), 3) if rows else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True, help="Comma-separated US ticker symbols; universe is explicit.")
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--min-price", type=float, default=0.30)
    parser.add_argument("--max-price", type=float, default=15.0)
    parser.add_argument("--min-dollar-volume", type=float, default=1_000_000.0)
    parser.add_argument("--min-penetration-pct", type=float, default=0.10)
    parser.add_argument("--horizons", default="5,10")
    parser.add_argument("--output", default="ict_backtest_results.json")
    args = parser.parse_args()
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    horizons = tuple(sorted({int(x) for x in args.horizons.split(",") if int(x) > 0}))
    if not symbols or not horizons or args.lookback < 3:
        parser.error("Provide symbols, positive horizons, and lookback >= 3")

    all_signals, diagnostics = [], []
    for symbol in symbols:
        try:
            rows = fetch_daily(symbol)
            if len(rows) < args.lookback + max(horizons, default=0) + 1:
                diagnostics.append({"symbol": symbol, "bars": len(rows), "status": "insufficient_or_unavailable"})
                continue
            signals = evaluate(rows, symbol, args.lookback, args.min_price, args.max_price,
                               args.min_dollar_volume, args.min_penetration_pct, horizons)
            all_signals.extend(signals)
            diagnostics.append({"symbol": symbol, "bars": len(rows), "signals": len(signals), "status": "ok"})
        except Exception as exc:
            diagnostics.append({"symbol": symbol, "status": "error", "error": type(exc).__name__, "detail": str(exc)[:220]})
        time.sleep(0.15)

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Stooq daily OHLCV",
        "method": "completed daily candle sweeps; levels from prior candles only; no look-ahead",
        "universe": symbols,
        "filters": {
            "min_price": args.min_price, "max_price": args.max_price,
            "min_dollar_volume": args.min_dollar_volume, "lookback": args.lookback,
            "min_penetration_pct": args.min_penetration_pct,
        },
        "diagnostics": diagnostics,
        "summary": [summarize(all_signals, h) for h in horizons],
        "signals": all_signals,
        "limitations": [
            "Explicit symbol universe may have survivorship/selection bias.",
            "Daily close-to-close and excursion statistics do not model spread, slippage, fees, halts, or executable fills.",
            "Positive return rate is not proof of profitability; compare with a baseline and out-of-sample period.",
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ("created_utc", "source", "universe", "filters", "diagnostics", "summary", "limitations")}, ensure_ascii=False, indent=2))
    print(f"Full signal details written to {args.output}")
    return 0 if any(d.get("status") == "ok" for d in diagnostics) else 2


if __name__ == "__main__":
    raise SystemExit(main())
