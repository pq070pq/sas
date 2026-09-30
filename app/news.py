from datetime import datetime, date, timedelta, timezone
import httpx
from .config import settings


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
