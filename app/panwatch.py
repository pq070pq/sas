import httpx
from .config import settings

async def analyze(symbol: str):
    base = settings.panwatch_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
        candidates = [
            f"{base}/api/stocks/{symbol}/agents/TradingAgentsAgent/trigger",
            f"{base}/api/stocks/{symbol}/agents/tradingagents/trigger",
        ]
        last = None
        for url in candidates:
            last = await client.post(url)
            if last.status_code < 400:
                return last.json()
        last.raise_for_status()
        return last.json()
