from datetime import date, timedelta
import httpx
from .config import settings

async def company_news(symbol: str, days: int = 14):
    if not settings.finnhub_api_key:
        return []
    end = date.today()
    start = end - timedelta(days=days)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://finnhub.io/api/v1/company-news", params={
            "symbol": symbol.upper(), "from": start.isoformat(), "to": end.isoformat(),
            "token": settings.finnhub_api_key
        })
        r.raise_for_status()
        return r.json()

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
