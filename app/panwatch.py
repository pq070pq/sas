import httpx
from .config import settings

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
