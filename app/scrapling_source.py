import asyncio
import time

try:
    from scrapling.fetchers import Fetcher
except Exception:
    Fetcher = None

_cache = {}
_TTL = 20 * 60

async def enrich_news_items(items, limit=3):
    """Fetch article text as optional evidence; never replace verified headline/source/url."""
    if Fetcher is None:
        return items or []
    selected = [x for x in (items or []) if isinstance(x, dict) and x.get("url")][:max(1, int(limit))]
    async def one(item):
        url = str(item.get("url") or "").strip()
        cached = _cache.get(url)
        now = time.monotonic()
        if cached and now - cached[0] < _TTL:
            return {**item, "summary": cached[1]}
        try:
            page = await asyncio.to_thread(Fetcher.get, url, stealthy_headers=True, timeout=8, retries=1)
            try:
                text = str(page.markdown() or "")
            except Exception:
                text = str(page.get_text() or "")
            summary = " ".join(text.split())[:5000]
            if summary:
                _cache[url] = (now, summary)
                return {**item, "summary": summary}
        except Exception:
            pass
        return item
    return await asyncio.gather(*(one(item) for item in selected))
