from datetime import datetime, date, timedelta, timezone
import time
import httpx
from .config import settings

_FUNDAMENTALS_CACHE_TTL = 3600
_fundamentals_cache = {}


def _safe_timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


async def company_news(symbol: str, days: int = 2):
    """Return recent Finnhub company news without allowing news failure to break the radar."""
    if not settings.finnhub_api_key:
        return []
    end = date.today()
    start = end - timedelta(days=max(1, days))
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get("https://finnhub.io/api/v1/company-news", params={
                "symbol": symbol.upper(), "from": start.isoformat(), "to": end.isoformat(),
                "token": settings.finnhub_api_key,
            })
            r.raise_for_status()
            rows = r.json()
            return rows if isinstance(rows, list) else []
    except Exception:
        return []


def select_catalyst(news, max_age_hours: int = 48):
    """Select one recent, source-linked headline; never infer that it caused price movement."""
    now = datetime.now(timezone.utc)
    candidates = []
    for item in news or []:
        headline = str(item.get("headline") or "").strip()
        url = str(item.get("url") or "").strip()
        source = str(item.get("source") or "").strip()
        published = _safe_timestamp(item.get("datetime"))
        if not headline or not url or not published:
            continue
        age = (now - published).total_seconds() / 3600
        if age < -1 or age > max_age_hours:
            continue
        candidates.append((published, {
            "headline": headline,
            "source": source or "Finnhub",
            "url": url,
            "published_at": published.isoformat(),
            "age_hours": round(max(0, age), 1),
        }))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


async def corporate_events(symbol: str):
    if not settings.finnhub_api_key:
        return {"earnings": [], "dividends": [], "splits": []}
    async with httpx.AsyncClient(timeout=20) as c:
        results = {}
        for name, endpoint in [
            ("earnings", "calendar/earnings"),
            ("dividends", "stock/dividend"),
            ("splits", "stock/split"),
        ]:
            params = {"symbol": symbol.upper(), "token": settings.finnhub_api_key}
            if name == "earnings":
                params.update({"from": date.today().isoformat(), "to": (date.today()+timedelta(days=90)).isoformat()})
            else:
                params.update({"from": (date.today()-timedelta(days=365)).isoformat(), "to": date.today().isoformat()})
            try:
                r = await c.get("https://finnhub.io/api/v1/" + endpoint, params=params)
                r.raise_for_status()
                results[name] = r.json()
            except Exception:
                results[name] = []
        return results


async def company_fundamentals(symbol: str):
    """Small cached Finnhub fundamentals snapshot for the AI layer."""
    key = symbol.upper().strip()
    if not key or not settings.finnhub_api_key:
        return {}

    now = time.monotonic()
    cached = _fundamentals_cache.get(key)
    if cached and now - cached[0] < _FUNDAMENTALS_CACHE_TTL:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=12) as c:
            profile_task = c.get(
                "https://finnhub.io/api/v1/stock/profile2",
                params={"symbol": key, "token": settings.finnhub_api_key},
            )
            metric_task = c.get(
                "https://finnhub.io/api/v1/stock/metric",
                params={"symbol": key, "metric": "all", "token": settings.finnhub_api_key},
            )
            profile_response, metric_response = await __import__("asyncio").gather(profile_task, metric_task)
            profile_response.raise_for_status()
            metric_response.raise_for_status()
            profile = profile_response.json() if profile_response.content else {}
            metric = metric_response.json() if metric_response.content else {}
            data = {
                "name": profile.get("name"),
                "ticker": profile.get("ticker") or key,
                "exchange": profile.get("exchange"),
                "industry": profile.get("finnhubIndustry"),
                "market_cap_m": profile.get("marketCapitalization"),
                "shares_outstanding_m": profile.get("shareOutstanding"),
                "pe_ttm": (metric.get("metric") or {}).get("peBasicExclExtraTTM"),
                "eps_ttm": (metric.get("metric") or {}).get("epsBasicExclExtraItemsTTM"),
                "revenue_growth_3y": (metric.get("metric") or {}).get("revenueGrowth3Y"),
                "net_margin": (metric.get("metric") or {}).get("netMarginTTM"),
                "roe_ttm": (metric.get("metric") or {}).get("roeTTM"),
                "debt_to_equity": (metric.get("metric") or {}).get("totalDebtToEquityQuarterly"),
                "source": "Finnhub",
            }
            _fundamentals_cache[key] = (now, data)
            return data
    except Exception:
        return {}
