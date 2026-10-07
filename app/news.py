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
from datetime import datetime, date, timedelta, timezone
import time
import httpx
from .config import settings
from .key_pool import KeyPool, parse_keys

_finnhub_pool = KeyPool(parse_keys(settings.finnhub_api_keys, settings.finnhub_api_key), settings.api_key_cooldown_seconds)
_fmp_pool = KeyPool(parse_keys(settings.fmp_api_keys, settings.fmp_api_key), settings.api_key_cooldown_seconds)
_news_cache = {}

_FUNDAMENTALS_CACHE_TTL = 3600
_fundamentals_cache = {}
_earnings_calendar_cache = None
_tipranks_cache = {}

async def tipranks_analysis(symbol: str):
    """Fetch TipRanks stock-analysis page as external AI evidence; never used for SAS price/targets."""
    key = str(symbol or "").upper().strip()
    if not key or Fetcher is None:
        return {}
    now = time.monotonic()
    cached = _tipranks_cache.get(key)
    if cached and now - cached[0] < 3600:
        return cached[1]
    url = f"https://www.tipranks.com/stocks/{key.lower()}"
    try:
        page = await asyncio.to_thread(Fetcher.get, url, stealthy_headers=True, timeout=10, retries=1)
        try:
            raw = str(page.markdown() or "")
        except Exception:
            raw = str(page.get_text() or "")
        text = " ".join(raw.split())
        if not text:
            return {}
        # Keep the payload compact; AI receives the site's own text and translates it.
        data = {
            "source": "TipRanks",
            "url": url,
            "symbol": key,
            "page_text": text[:12000],
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
        _tipranks_cache[key] = (now, data)
        return data
    except Exception:
        return {}


def _safe_timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


async def _finnhub_get(endpoint: str, params: dict, timeout: int = 12):
    for _ in range(max(1, _finnhub_pool.size)):
        key = await _finnhub_pool.acquire()
        if not key:
            return None
        try:
            request_params = dict(params)
            request_params["token"] = key
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get("https://finnhub.io/api/v1/" + endpoint, params=request_params)
            if response.status_code in (401, 403, 429):
                retry = response.headers.get("Retry-After")
                await _finnhub_pool.mark_failure(key, retry_after=int(retry) if retry and retry.isdigit() else None)
                continue
            response.raise_for_status()
            await _finnhub_pool.mark_success(key)
            return response.json()
        except Exception:
            await _finnhub_pool.mark_failure(key)
    return None


async def _fmp_get(endpoint: str, params: dict, timeout: int = 12):
    if not settings.fmp_news_enabled:
        return None
    for _ in range(max(1, _fmp_pool.size)):
        key = await _fmp_pool.acquire()
        if not key:
            return None
        try:
            request_params = dict(params)
            request_params["apikey"] = key
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get("https://financialmodelingprep.com/stable/" + endpoint, params=request_params)
            if response.status_code in (401, 403, 429):
                retry = response.headers.get("Retry-After")
                await _fmp_pool.mark_failure(key, retry_after=int(retry) if retry and retry.isdigit() else None)
                continue
            response.raise_for_status()
            await _fmp_pool.mark_success(key)
            return response.json()
        except Exception:
            await _fmp_pool.mark_failure(key)
    return None


def _normalise_fmp_news(rows):
    out = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        published = item.get("publishedDate")
        try:
            dt = int(datetime.fromisoformat(str(published).replace("Z", "+00:00")).timestamp()) if published else 0
        except Exception:
            dt = 0
        out.append({
            "headline": str(item.get("title") or "").strip(),
            "source": str(item.get("publisher") or item.get("site") or "FMP").strip(),
            "url": str(item.get("url") or "").strip(),
            "datetime": dt,
            "summary": str(item.get("text") or "").strip()[:800],
            "symbol": str(item.get("symbol") or "").upper(),
        })
    return [x for x in out if x["headline"] and x["url"]]


async def company_news(symbol: str, days: int = 2):
    """Recent company news with caching and provider failover."""
    key = symbol.upper().strip()
    if not key:
        return []
    cache_key = f"{key}:{days}"
    now = time.monotonic()
    cached = _news_cache.get(cache_key)
    if cached and now - cached[0] < max(60, settings.news_cache_minutes * 60):
        return cached[1]

    end = date.today()
    start = end - timedelta(days=max(1, days))
    rows = await _finnhub_get(
        "company-news",
        {"symbol": key, "from": start.isoformat(), "to": end.isoformat()},
    )
    if isinstance(rows, list):
        result = rows
    else:
        fmp_rows = await _fmp_get(
            "news/stock",
            {"symbols": key, "from": start.isoformat(), "to": end.isoformat(), "limit": 20},
        )
        result = _normalise_fmp_news(fmp_rows if isinstance(fmp_rows, list) else [])

    _news_cache[cache_key] = (now, result)
    return result


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



_CATALYST_RULES = {
    "استحواذ/اندماج": (30, ("acquisition", "acquire", "acquired", "merger", "merges", "buyout")),
    "عقد/شراكة": (24, ("contract", "agreement", "partnership", "strategic partnership", "deal")),
    "FDA/تنظيم": (28, ("fda", "approval", "approved", "clearance", "clinical trial", "regulatory")),
    "نتائج/توجيهات": (20, ("earnings", "revenue", "guidance", "financial results", "profit")),
    "تمويل/طرح": (-12, ("offering", "public offering", "private placement", "registered direct", "dilution")),
    "إدراج/ناسداك": (16, ("nasdaq compliance", "minimum bid", "listing", "delisting")),
    "منتج/تقنية": (18, ("launches", "launch", "new product", "technology", "ai", "patent")),
    "تقسيم أسهم": (8, ("stock split", "reverse stock split", "share consolidation")),
}

def score_catalyst(news, max_age_hours: int = 48):
    """Rank recent headlines by materiality, recency and directional language.
    This is an evidence score, not a claim that the headline caused the move.
    """
    now = datetime.now(timezone.utc)
    best = None
    for item in news or []:
        headline = str(item.get("headline") or "").strip()
        if not headline:
            continue
        published = _safe_timestamp(item.get("datetime"))
        if not published:
            continue
        age = (now - published).total_seconds() / 3600
        if age < -1 or age > max_age_hours:
            continue
        text = headline.lower()
        points = 0
        categories = []
        for label, (weight, terms) in _CATALYST_RULES.items():
            if any(term in text for term in terms):
                points += weight
                categories.append(label)
        if not categories:
            points = 8
        bullish_terms = ("beats", "raises", "record", "wins", "approved", "approval", "strong", "surges", "growth", "contract", "partnership")
        bearish_terms = ("miss", "cuts", "warning", "lawsuit", "investigation", "offering", "dilution", "delist", "bankruptcy")
        if any(term in text for term in bullish_terms):
            points += 8
        if any(term in text for term in bearish_terms):
            points -= 10
        recency_bonus = max(0, 15 - int(age / 4))
        score = max(0, min(100, 40 + points + recency_bonus))
        candidate = {
            "score": score,
            "direction": "إيجابي" if points > 5 else "سلبي" if points < -5 else "محايد",
            "impact": "قوي" if score >= 75 else "متوسط" if score >= 55 else "ضعيف",
            "categories": categories[:3],
            "headline": headline,
            "source": str(item.get("source") or "News"),
            "url": str(item.get("url") or ""),
            "published_at": published.isoformat(),
            "age_hours": round(max(0, age), 1),
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    return best

async def corporate_events(symbol: str):
    """Structured corporate events plus recent material-event headlines."""
    results = {
        "earnings": [], "dividends": [], "splits": [],
        "material_events": [], "status": "partial",
    }
    if _finnhub_pool.size:
        for name, endpoint in [
                ("earnings", "calendar/earnings"),
                ("dividends", "stock/dividend"),
                ("splits", "stock/split"),
            ]:
                params = {"symbol": symbol.upper()}
                if name == "earnings":
                    params.update({"from": date.today().isoformat(), "to": (date.today()+timedelta(days=90)).isoformat()})
                else:
                    params.update({"from": (date.today()-timedelta(days=365)).isoformat(), "to": date.today().isoformat()})
                try:
                    payload = await _finnhub_get(endpoint, params, timeout=20)
                    results[name] = payload if payload is not None else []
                except Exception:
                    results[name] = []
        results["status"] = "ok"

    # Headline evidence catches material events without dedicated endpoints.
    try:
        rows = await company_news(symbol, days=14)
        keywords = {
            "استحواذ/دمج": ("acquisition", "acquire", "acquired", "merger", "merges", "combination"),
            "تمويل/طرح أسهم": ("offering", "public offering", "private placement", "registered direct"),
            "ضمانات/وارنت": ("warrant", "exercise price", "warrants"),
            "ناسداك/إدراج": ("nasdaq", "delist", "delisting", "listing", "compliance", "minimum bid"),
            "عقد/شراكة": ("contract", "agreement", "partnership", "distribution", "exclusive rights"),
            "نتائج/توجيهات": ("earnings", "revenue", "guidance", "financial results"),
            "تقسيم/دمج أسهم": ("reverse stock split", "stock split", "share consolidation", "split"),
            "توزيعات": ("dividend", "distribution"),
        }
        seen = set()
        for item in rows or []:
            headline = str(item.get("headline") or "").strip()
            lower = headline.lower()
            if not headline or headline in seen:
                continue
            categories = [label for label, terms in keywords.items() if any(term in lower for term in terms)]
            if categories:
                seen.add(headline)
                stamp = _safe_timestamp(item.get("datetime"))
                results["material_events"].append({
                    "category": categories[:3], "headline": headline,
                    "date": stamp.isoformat() if stamp else None,
                    "url": item.get("url"), "source": item.get("source"),
                })
            if len(results["material_events"]) >= 12:
                break
    except Exception:
        pass
    return results

async def earnings_calendar_window(days: int = 5):
    """Return upcoming US earnings events from today through today + days."""
    global _earnings_calendar_cache
    if _finnhub_pool.size == 0:
        return []
    now = time.monotonic()
    if _earnings_calendar_cache and now - _earnings_calendar_cache[0] < 1800:
        return _earnings_calendar_cache[1]
    start = date.today()
    end = start + timedelta(days=max(0, int(days)))
    rows = await _finnhub_get(
        "calendar/earnings",
        {"from": start.isoformat(), "to": end.isoformat()},
        timeout=20,
    )
    result = rows.get("earningsCalendar", []) if isinstance(rows, dict) else []
    _earnings_calendar_cache = (now, result)
    return result


async def company_fundamentals(symbol: str):
    """Small cached Finnhub fundamentals snapshot for the AI layer."""
    key = symbol.upper().strip()
    if not key or _finnhub_pool.size == 0:
        return {}

    now = time.monotonic()
    cached = _fundamentals_cache.get(key)
    if cached and now - cached[0] < _FUNDAMENTALS_CACHE_TTL:
        return cached[1]

    try:
        import asyncio
        profile, metric = await asyncio.gather(
            _finnhub_get("stock/profile2", {"symbol": key}),
            _finnhub_get("stock/metric", {"symbol": key, "metric": "all"}),
        )
        profile = profile if isinstance(profile, dict) else {}
        metric = metric if isinstance(metric, dict) else {}
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
