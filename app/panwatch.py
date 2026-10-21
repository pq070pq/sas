import csv
import math
import io
import httpx
from .config import settings
from .twelve_guard import call as twelve_call



async def _stooq_ohlcv(symbol: str, days: int = 90):
    """Keyless daily OHLCV fallback."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 SAS-PRO/2.1"}) as client:
            stooq_symbol = symbol.lower() + ".us"
            r = await client.get("https://stooq.com/q/d/l/", params={"s": stooq_symbol, "i": "d"})
            if r.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"Stooq returned HTTP {r.status_code}",
                    request=r.request,
                    response=r,
                )
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
        raise

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


def _normalize_ohlcv_rows(rows):
    """Normalize provider-specific OHLCV records to the shape consumed by the chart."""
    if not isinstance(rows, (list, tuple)):
        return []
    normalized = []
    for item in rows:
        try:
            if isinstance(item, dict):
                lower = {str(k).strip().lower(): v for k, v in item.items()}
                def pick(*keys):
                    for key in keys:
                        value = lower.get(key)
                        if value not in (None, ""):
                            return value
                    return None
                stamp = pick("datetime", "date", "time", "timestamp", "t")
                op, hi, lo, cl = pick("open", "o"), pick("high", "h"), pick("low", "l"), pick("close", "c")
                vol = pick("volume", "vol", "v")
            elif isinstance(item, (list, tuple)) and len(item) >= 5:
                # Common exchange kline layout: [time, open, high, low, close, volume].
                stamp, op, hi, lo, cl = item[:5]
                vol = item[5] if len(item) > 5 else 0
            else:
                continue
            op, hi, lo, cl = float(op), float(hi), float(lo), float(cl)
            if not all(math.isfinite(v) and v > 0 for v in (op, hi, lo, cl)):
                continue
            tolerance = max(abs(op), abs(hi), abs(lo), abs(cl), 1.0) * 1e-5
            if hi + tolerance < max(op, cl) or lo - tolerance > min(op, cl):
                continue
            normalized.append({
                "datetime": str(stamp or ""),
                "open": op, "high": max(hi, op, cl), "low": min(lo, op, cl),
                "close": cl, "volume": float(vol or 0),
            })
        except (TypeError, ValueError, OverflowError):
            continue
    normalized.sort(key=lambda row: row["datetime"])
    return normalized


async def ohlcv(symbol: str, days: int = 90, interval: str = "1d", min_candles: int = 20, allow_twelve_data: bool = True, timeout_seconds: float | None = None):
    """Return observed OHLCV candles with explicit source diagnostics.
    
    Yahoo is intentionally excluded. Historical fallback order is PanWatch,
    Twelve Data, Finnhub, FMP, then Stooq.

    min_candles lets indicator consumers require enough history without treating
    a short response as a successful source. allow_twelve_data protects the
    scanner quota while preserving Twelve Data for explicitly permitted calls.
    """
    # Keep the default 20-bar requirement for indicators/scanners, but allow chart-only callers to request a smaller real OHLC history.
    minimum = max(4, min(int(min_candles), 260))
    base = settings.panwatch_base_url.rstrip("/")
    request_timeout = settings.panwatch_timeout_seconds if timeout_seconds is None else httpx.Timeout(max(2.0, min(float(timeout_seconds), 15.0)), connect=min(3.0, max(1.5, float(timeout_seconds) / 2)))
    async with httpx.AsyncClient(timeout=request_timeout) as client:
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
            candidate_rows = data.get("klines", data.get("candles", data.get("values", []))) if isinstance(data, dict) else data
            candidate_rows = _normalize_ohlcv_rows(candidate_rows)
            if len(candidate_rows) >= minimum:
                rows, source = candidate_rows, "PanWatch"
            elif candidate_rows:
                rows, source = candidate_rows, "PanWatch (partial)"
                attempts.append("PanWatch:insufficient")
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            attempts.append(f"PanWatch:HTTP{status_code}" if status_code else f"PanWatch:{type(exc).__name__}")

        if len(rows) < minimum and allow_twelve_data and (settings.twelve_data_api_key or settings.twelve_data_api_keys):
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
                candidate_rows = _normalize_ohlcv_rows(list(reversed((r.json()).get("values") or [])))
                if len(candidate_rows) > len(rows):
                    rows, source = candidate_rows, "Twelve Data (partial)" if len(candidate_rows) < minimum else "Twelve Data"
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                message = f"TwelveData:HTTP{status_code}" if status_code else f"TwelveData:{type(exc).__name__}"
                if isinstance(exc, RuntimeError) and "Twelve Data" in str(exc):
                    message = "TwelveData:" + str(exc)[:120]
                attempts.append(message)
            if len(rows) >= minimum: source = "Twelve Data"
            elif len(rows): attempts.append("TwelveData:insufficient")
        if len(rows) < minimum:
            # Finnhub candle endpoint is an additional historical-data fallback
            # for cases where PanWatch/Twelve Data do not return enough bars.
            finnhub_keys = [
                item.strip()
                for item in str(getattr(settings, "finnhub_api_keys", "") or "").replace("\\n", ",").replace(";", ",").split(",")
                if item.strip()
            ]
            if getattr(settings, "finnhub_api_key", ""):
                finnhub_keys.insert(0, str(settings.finnhub_api_key).strip())
            finnhub_keys = list(dict.fromkeys(key for key in finnhub_keys if key))
            for finnhub_key in finnhub_keys:
                try:
                    from datetime import datetime, timedelta, timezone
                    end_ts = int(datetime.now(timezone.utc).timestamp())
                    start_ts = int((datetime.now(timezone.utc) - timedelta(days=max(45, min(int(days) * 2, 730)))).timestamp())
                    r = await client.get(
                        "https://finnhub.io/api/v1/stock/candle",
                        params={"symbol": symbol.upper(), "resolution": "D", "from": start_ts, "to": end_ts, "token": finnhub_key},
                    )
                    if r.status_code in (401, 403):
                        attempts.append(f"Finnhub:HTTP{r.status_code}")
                        continue
                    if r.status_code == 429:
                        attempts.append("Finnhub:HTTP429 rate-limited; key rotation stopped")
                        break
                    r.raise_for_status()
                    payload = r.json()
                    if payload.get("s") == "ok" and payload.get("c"):
                        candidate_rows = [{
                            "datetime": datetime.fromtimestamp(payload["t"][i], timezone.utc).strftime("%Y-%m-%d"),
                            "open": payload["o"][i], "high": payload["h"][i],
                            "low": payload["l"][i], "close": payload["c"][i],
                            "volume": payload["v"][i],
                        } for i in range(len(payload["c"]))]
                        if len(candidate_rows) > len(rows):
                            rows = candidate_rows
                            source = "Finnhub Candles" if len(candidate_rows) >= minimum else "Finnhub Candles (partial)"
                        if len(candidate_rows) < minimum:
                            attempts.append("Finnhub:insufficient")
                    else:
                        attempts.append(f"Finnhub:{payload.get('s', 'no_data')}")
                    break
                except Exception as exc:
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    attempts.append(f"Finnhub:HTTP{status_code}" if status_code else f"Finnhub:{type(exc).__name__}")
                    if status_code not in (401, 403):
                        break
        if len(rows) < minimum:
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
                    if r.status_code in (401, 403):
                        attempts.append(f"FMP:HTTP{r.status_code}")
                        continue
                    if r.status_code == 429:
                        attempts.append("FMP:HTTP429 rate-limited; key rotation stopped")
                        break
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
                    if len(normalized) > len(rows):
                        rows = normalized[-max(20, min(int(days), 365)):]
                        source = "Financial Modeling Prep" if len(normalized) >= minimum else "Financial Modeling Prep (partial)"
                    if len(normalized) >= minimum:
                        break
                    attempts.append("FMP:insufficient")
                except Exception as exc:
                    attempts.append(f"FMP:{type(exc).__name__}")
        if len(rows) < minimum:
            try:
                stooq_rows = await _stooq_ohlcv(symbol, days)
                if len(stooq_rows) > len(rows):
                    rows = stooq_rows
                    source = "Stooq" if len(stooq_rows) >= minimum else "Stooq (partial)"
                if len(stooq_rows) < minimum:
                    attempts.append("Stooq:insufficient")
            except Exception as exc:
                # Preserve provider failure detail for the chart diagnostic without turning it into HTTP 500.
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                attempts.append(f"Stooq:HTTP{status_code}" if status_code else f"Stooq:{type(exc).__name__}")
    candles = []
    for row in rows:
        try:
            normalized = _normalize_ohlcv_rows([row])
            if normalized:
                candle = normalized[0]
                candles.append({**candle, "time": candle.get("datetime")})
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
