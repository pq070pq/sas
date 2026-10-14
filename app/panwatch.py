import csv
import io
import httpx
from .config import settings
from .twelve_guard import call as twelve_call



async def _stooq_ohlcv(symbol: str, days: int = 90):
    """Keyless daily OHLCV fallback."""
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"}) as client:
            stooq_symbol = symbol.lower() + ".us"
            r = await client.get("https://stooq.com/q/d/l/", params={"s": stooq_symbol, "i": "d"})
            if r.status_code >= 400:
                return []
            # Stooq CSV headers are capitalized (Date, Open, High, Low, Close, Volume),
            # while the analysis pipeline consumes lowercase keys. Normalize here so
            # valid fallback candles are not silently discarded by OHLCV parsing.
            rows = []
            for raw in csv.DictReader(io.StringIO(r.text)):
                row = {str(k or "").strip().lower(): str(v or "").strip() for k, v in raw.items()}
                if not all(row.get(key) for key in ("date", "open", "high", "low", "close")):
                    continue
                try:
                    for key in ("open", "high", "low", "close"):
                        row[key] = float(row[key])
                    row["volume"] = float(row.get("volume") or 0)
                except (TypeError, ValueError):
                    continue
                rows.append(row)
            return rows[-max(20, min(int(days), 365)):]
    except Exception:
        return []

async def analyze(symbol: str):
    base = settings.panwatch_base_url.rstrip("/")
    params = {
        "allow_unbound": "true",
        "bypass_market_hours": "true",
        "wait": "true",
        "force_refresh": "true",
        "symbol": symbol.upper(),
        "market": "US",
        "name": symbol.upper(),
    }
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        r = await client.post(
            f"{base}/api/stocks/0/agents/tradingagents/trigger",
            params=params,
        )
        r.raise_for_status()
        return r.json()


async def ohlcv(symbol: str, days: int = 90, interval: str = "1d", min_candles: int = 20, allow_twelve_data: bool = True):
    """Return observed OHLCV candles with explicit source diagnostics.

    min_candles lets indicator consumers require enough history without treating
    a short response as a successful source. allow_twelve_data protects the
    scanner quota while preserving Twelve Data for explicitly permitted calls.
    """
    minimum = max(20, min(int(min_candles), 260))
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        rows = []
        source = "unavailable"
        attempts = []
        try:
            r = await client.get(
                f"{base}/api/klines/{symbol.upper()}",
                params={"market": "US", "days": max(20, min(int(days), 365)), "interval": interval},
            )
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") or payload
            rows = data.get("klines", []) if isinstance(data, dict) else []
            if len(rows) >= minimum: source = "PanWatch"
            else: attempts.append("PanWatch:insufficient")
        except Exception as exc:
            attempts.append(f"PanWatch:{type(exc).__name__}")

        if len(rows) < minimum and allow_twelve_data and settings.twelve_data_api_key:
            try:
                r = await twelve_call(
                    client.get,
                    "https://api.twelvedata.com/time_series",
                    params={
                        "symbol": symbol.upper(),
                        # Twelve Data expects "1day", not the app's internal "1d".
                        "interval": {"1d": "1day", "1h": "1h", "1wk": "1week"}.get(interval, interval),
                        "outputsize": max(20, min(int(days), 365)),
                        "apikey": settings.twelve_data_api_key,
                    },
                )
                r.raise_for_status()
                rows = list(reversed((r.json()).get("values") or []))
            except Exception as exc:
                attempts.append(f"TwelveData:{type(exc).__name__}")
            if len(rows) >= 20: source = "Twelve Data"
            elif len(rows): attempts.append("TwelveData:insufficient")
        if len(rows) < 20:
            # Finnhub candle endpoint is an additional historical-data fallback
            # for cases where PanWatch/Twelve Data do not return enough bars.
            finnhub_key = str(getattr(settings, "finnhub_api_key", "") or "").strip()
            if finnhub_key:
                try:
                    from datetime import datetime, timedelta, timezone
                    end_ts = int(datetime.now(timezone.utc).timestamp())
                    start_ts = int((datetime.now(timezone.utc) - timedelta(days=max(45, min(int(days) * 2, 730)))).timestamp())
                    r = await client.get(
                        "https://finnhub.io/api/v1/stock/candle",
                        params={"symbol": symbol.upper(), "resolution": "D", "from": start_ts, "to": end_ts, "token": finnhub_key},
                    )
                    r.raise_for_status()
                    payload = r.json()
                    if payload.get("s") == "ok" and payload.get("c"):
                        rows = [{
                            "datetime": datetime.fromtimestamp(payload["t"][i], timezone.utc).strftime("%Y-%m-%d"),
                            "open": payload["o"][i], "high": payload["h"][i],
                            "low": payload["l"][i], "close": payload["c"][i],
                            "volume": payload["v"][i],
                        } for i in range(len(payload["c"]))]
                        if len(rows) >= 20:
                            source = "Finnhub Candles"
                        else:
                            attempts.append("Finnhub:insufficient")
                    else:
                        attempts.append(f"Finnhub:{payload.get('s', 'no_data')}")
                except Exception as exc:
                    attempts.append(f"Finnhub:{type(exc).__name__}")
        if len(rows) < 20:
            # Financial Modeling Prep provides an independent daily OHLCV source.
            # It is optional and only queried when a key is configured.
            fmp_keys = [
                item.strip()
                for item in str(getattr(settings, "fmp_api_keys", "") or "").replace("\\n", ",").replace(";", ",").split(",")
                if item.strip()
            ]
            if getattr(settings, "fmp_api_key", ""):
                fmp_keys.insert(0, str(settings.fmp_api_key).strip())
            fmp_keys = list(dict.fromkeys(key for key in fmp_keys if key))
            for fmp_key in fmp_keys:
                try:
                    from datetime import datetime, timedelta, timezone
                    end_date = datetime.now(timezone.utc).date()
                    start_date = end_date - timedelta(days=max(45, min(int(days) * 3, 1095)))
                    r = await client.get(
                        "https://financialmodelingprep.com/stable/historical-price-eod/full",
                        params={
                            "symbol": symbol.upper(),
                            "from": start_date.isoformat(),
                            "to": end_date.isoformat(),
                            "apikey": fmp_key,
                        },
                    )
                    if r.status_code in (401, 403, 429):
                        attempts.append(f"FMP:HTTP{r.status_code}")
                        continue
                    r.raise_for_status()
                    payload = r.json()
                    fmp_rows = payload if isinstance(payload, list) else (payload.get("historical") or [])
                    normalized = []
                    for item in fmp_rows:
                        if not isinstance(item, dict):
                            continue
                        try:
                            normalized.append({
                                "datetime": str(item.get("date") or item.get("datetime") or ""),
                                "open": float(item["open"]),
                                "high": float(item["high"]),
                                "low": float(item["low"]),
                                "close": float(item["close"]),
                                "volume": float(item.get("volume") or 0),
                            })
                        except (KeyError, TypeError, ValueError):
                            continue
                    normalized.sort(key=lambda item: item["datetime"])
                    if len(normalized) >= minimum:
                        rows, source = normalized[-max(20, min(int(days), 365)):], "Financial Modeling Prep"
                        break
                    attempts.append("FMP:insufficient")
                except Exception as exc:
                    attempts.append(f"FMP:{type(exc).__name__}")
        if len(rows) < 20:
            # Yahoo chart endpoint is a last-resort historical OHLCV source for
            # symbols not covered by PanWatch, Twelve Data, or Finnhub.
            try:
                from datetime import datetime, timedelta, timezone
                end_ts = int(datetime.now(timezone.utc).timestamp())
                start_ts = int((datetime.now(timezone.utc) - timedelta(days=max(180, min(int(days) * 3, 730)))).timestamp())
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}",
                    params={"period1": start_ts, "period2": end_ts, "interval": "1d", "events": "history"},
                    headers={"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"},
                )
                r.raise_for_status()
                payload = r.json().get("chart", {})
                result = (payload.get("result") or [None])[0]
                if result:
                    stamps = result.get("timestamp") or []
                    quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                    opens = quote_rows.get("open") or []
                    highs = quote_rows.get("high") or []
                    lows = quote_rows.get("low") or []
                    closes = quote_rows.get("close") or []
                    volumes = quote_rows.get("volume") or []
                    yahoo_rows = []
                    for i, stamp in enumerate(stamps):
                        try:
                            o, h, lo, cl = opens[i], highs[i], lows[i], closes[i]
                            if any(v is None for v in (o, h, lo, cl)):
                                continue
                            yahoo_rows.append({
                                "datetime": datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d"),
                                "open": o, "high": h, "low": lo, "close": cl,
                                "volume": volumes[i] if i < len(volumes) and volumes[i] is not None else 0,
                            })
                        except (IndexError, TypeError, ValueError):
                            continue
                    if len(yahoo_rows) >= minimum:
                        rows, source = yahoo_rows, "Yahoo Finance"
                    else:
                        attempts.append("YahooFinance:insufficient")
                else:
                    attempts.append("YahooFinance:no_data")
            except Exception as exc:
                attempts.append(f"YahooFinance:{type(exc).__name__}")
        if len(rows) < 20:
            rows = await _stooq_ohlcv(symbol, days)
            if len(rows) >= 20: source = "Stooq"
            else: attempts.append("Stooq:insufficient")
    candles = []
    for row in rows:
        try:
            candles.append({
                "time": row.get("date") or row.get("datetime"),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
            })
        except (TypeError, ValueError, KeyError):
            continue
    return {"candles": candles, "source": source, "available": len(candles) >= minimum, "diagnostic": " | ".join(attempts) if attempts else None}


async def technical_targets(symbol: str):
    """Build targets from the same multi-provider OHLCV fallback used by the chart."""
    # Reuse the complete history pipeline (PanWatch -> Twelve Data -> Finnhub -> Stooq)
    # instead of querying only PanWatch/Stooq and leaving analysis empty when either fails.
    history = await ohlcv(symbol, days=90, interval="1d")
    rows = history.get("candles", []) if isinstance(history, dict) else []
    candles = []
    for row in rows:
        try:
            candles.append({
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
                "date": row.get("date"),
            })
        except (TypeError, ValueError, KeyError):
            continue

    if len(candles) < 20:
        return {"status": "insufficient_data", "targets": [], "exit": None}

    last = candles[-1]
    price = last["close"]
    recent = candles[-20:-1]

    # Confirmed resistance/support: local swing levels with two candles on each side.
    resistances = []
    supports = []
    for i in range(2, len(candles) - 2):
        h = candles[i]["high"]
        l = candles[i]["low"]
        if h >= candles[i-1]["high"] and h >= candles[i-2]["high"] and h >= candles[i+1]["high"] and h >= candles[i+2]["high"]:
            if h > price * 1.003:
                resistances.append(h)
        if l <= candles[i-1]["low"] and l <= candles[i-2]["low"] and l <= candles[i+1]["low"] and l <= candles[i+2]["low"]:
            if l < price * 0.997:
                supports.append(l)

    def unique_levels(levels):
        out = []
        for level in sorted(levels):
            if not out or abs(level - out[-1]) / out[-1] > 0.01:
                out.append(level)
        return out

    resistances = unique_levels(resistances)
    supports = unique_levels(supports)

    # ATR(14) is used as a sanity check so targets are not placed unrealistically close.
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
    atr = sum(trs[-14:]) / min(14, len(trs[-14:]))
    min_distance = max(atr * 0.35, price * 0.01)

    targets = []
    for level in resistances:
        if level - price >= min_distance:
            targets.append(round(level, 4))
        if len(targets) == 5:
            break

    avg_volume = sum(c["volume"] for c in recent) / max(1, len(recent))
    current_volume = last["volume"]
    volume_ratio = current_volume / avg_volume if avg_volume else None

    # Keep only confirmed resistance levels. Do not fabricate targets or force a
    # minimum count; fewer than five real levels is valid and is reported as such.

    # Stop is shown only when a confirmed support exists. ATR is a diagnostic
    # measurement, never a fabricated target/stop for the Telegram report.
    below = [s for s in supports if s < price]
    exit_level = max(below) if below else None

    return {
        "status": "ok",
        "price": round(price, 4),
        "atr": round(atr, 4),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "resistances": targets,
        "support": round(exit_level, 4) if exit_level is not None else None,
        "targets": targets,
        "exit": round(exit_level, 4) if exit_level is not None else None,
        "method": "swing-resistance + ATR + volume confirmation",
    }
