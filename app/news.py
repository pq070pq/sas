import asyncio
import time
import re
import logging

logger = logging.getLogger(__name__)

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
from zoneinfo import ZoneInfo
import time
import httpx
from .config import settings
from .key_pool import KeyPool, parse_keys

_finnhub_pool = KeyPool(parse_keys(settings.finnhub_api_keys, settings.finnhub_api_key), settings.api_key_cooldown_seconds)
_marketaux_pool = KeyPool(parse_keys(getattr(settings, "marketaux_api_keys", ""), settings.marketaux_api_key), settings.api_key_cooldown_seconds)
_fmp_pool = KeyPool(parse_keys(settings.fmp_api_keys, settings.fmp_api_key), settings.api_key_cooldown_seconds)
_news_cache = {}

_FUNDAMENTALS_CACHE_TTL = 3600
_fundamentals_cache = {}
_earnings_calendar_cache = None
_tipranks_cache = {}
_yahoo_news_cache = {}
_YAHOO_NEWS_TTL = 15 * 60

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


def _yahoo_date(value):
    """Convert Yahoo RSS publication dates to Unix seconds without failing the feed."""
    from email.utils import parsedate_to_datetime
    try:
        return int(parsedate_to_datetime(str(value)).timestamp())
    except Exception:
        return 0


async def yahoo_finance_news(symbol: str, limit: int = 20):
    """Fetch symbol news from Yahoo Finance's RSS feed; news-only, never a quote source."""
    key = str(symbol or "").upper().strip()
    if not key or not key.replace(".", "").isalnum():
        return []
    now = time.monotonic()
    cached = _yahoo_news_cache.get(key)
    if cached and now - cached[0] < _YAHOO_NEWS_TTL:
        return cached[1][:max(1, int(limit))]
    url = f"https://finance.yahoo.com/rss/headline?s={key}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SASPRO/1.0; +https://github.com/pq070pq/sas)"
        }) as client:
            response = await client.get(url)
        response.raise_for_status()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)
        rows = []
        for item in root.findall(".//item"):
            headline = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not headline or not link or not link.startswith(("https://", "http://")):
                continue
            source = (item.findtext("source") or "").strip() or "Yahoo Finance"
            description = (item.findtext("description") or "").strip()
            rows.append({
                "headline": headline,
                "source": source,
                "url": link,
                "datetime": _yahoo_date(item.findtext("pubDate")),
                "summary": re.sub(r"<[^>]+>", " ", description).strip()[:800],
                "symbol": key,
                "provider": "Yahoo Finance",
            })
        # Deduplicate exact and near-identical headlines across syndicated publishers.
        unique = []
        seen = set()
        for row in rows:
            normal = re.sub(r"[^a-z0-9]+", " ", row["headline"].lower()).strip()
            if not normal or normal in seen:
                continue
            seen.add(normal)
            unique.append(row)
        _yahoo_news_cache[key] = (now, unique)
        return unique[:max(1, int(limit))]
    except Exception:
        return []


async def yahoo_corporate_actions(symbol: str):
    """Fetch Yahoo chart corporate-action events (splits/dividends), not price quotes."""
    key = str(symbol or "").upper().strip()
    if not key or not key.replace(".", "").isalnum():
        return {"splits": [], "dividends": [], "source": "Yahoo Finance", "status": "unavailable"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{key}?range=10y&interval=1d&events=div%2Csplits"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SASPRO/1.0; +https://github.com/pq070pq/sas)"
        }) as client:
            response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        result = (((payload.get("chart") or {}).get("result") or [None])[0]) or {}
        events = result.get("events") or {}
        splits = []
        for stamp, event in (events.get("splits") or {}).items():
            try:
                date_text = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat()
                numerator = int(event.get("numerator") or 0)
                denominator = int(event.get("denominator") or 0)
                if numerator and denominator:
                    ratio = f"{numerator}:{denominator}"
                    label = "تقسيم عكسي" if numerator < denominator else ("تقسيم عادي" if numerator > denominator else "تغيير نسبة ADS")
                    splits.append({"date": date_text, "ratio": ratio, "label_ar": label, "source": "Yahoo Finance"})
            except Exception:
                continue
        dividends = []
        for stamp, event in (events.get("dividends") or {}).items():
            try:
                dividends.append({
                    "date": datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat(),
                    "amount": event.get("amount"),
                    "currency": "USD",
                    "source": "Yahoo Finance",
                })
            except Exception:
                continue
        splits.sort(key=lambda x: x["date"], reverse=True)
        dividends.sort(key=lambda x: x["date"], reverse=True)
        return {"splits": splits, "dividends": dividends, "source": "Yahoo Finance", "status": "ok"}
    except Exception:
        return {"splits": [], "dividends": [], "source": "Yahoo Finance", "status": "unavailable"}


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


async def _marketaux_company_news(symbol: str, days: int = 2, limit: int = 3):
    """Fetch a small, recent, symbol-specific Marketaux batch for on-demand analysis."""
    key = str(symbol or "").upper().strip()
    if _marketaux_pool.size == 0 or not key:
        return []
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    response = None
    for _ in range(_marketaux_pool.size):
        api_key = await _marketaux_pool.acquire()
        if not api_key:
            break
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                response = await client.get(
                    "https://api.marketaux.com/v1/news/all",
                    params={
                        "api_token": api_key,
                    "symbols": key,
                    "language": "en",
                    "filter_entities": "true",
                    "published_after": cutoff_dt.isoformat(),
                        "limit": max(1, min(3, int(limit))),
                    },
                )
            if response.status_code in (401, 403, 429):
                retry = response.headers.get("Retry-After")
                await _marketaux_pool.mark_failure(api_key, retry_after=int(retry) if retry and retry.isdigit() else None)
                continue
            response.raise_for_status()
            await _marketaux_pool.mark_success(api_key)
            payload = response.json()
            break
        except Exception as exc:
            await _marketaux_pool.mark_failure(api_key)
            logger.info("Marketaux company-news request failed for %s: %s", key, type(exc).__name__)
            continue
    else:
        return []
    if response is None or response.status_code in (401, 403, 429):
        return []

    now_epoch = int(time.time())
    cutoff = int(cutoff_dt.timestamp())
    rows, seen = [], set()
    articles = payload.get("data", []) if isinstance(payload, dict) else []
    for item in articles if isinstance(articles, list) else []:
        if not isinstance(item, dict):
            continue
        headline = re.sub(r"\\s+", " ", str(item.get("title") or "")).strip()
        url = str(item.get("url") or "").strip()
        published = str(item.get("published_at") or "").strip()
        try:
            stamp = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError, OverflowError):
            stamp = 0
        if not headline or not url.startswith("https://") or stamp < cutoff or stamp > now_epoch + 3600:
            continue
        entities = item.get("entities") or []
        symbols = [str(entity.get("symbol") or "").upper() for entity in entities
                   if isinstance(entity, dict) and entity.get("symbol")]
        # Reject broad-market articles that do not actually identify this stock.
        if symbols and key not in symbols:
            continue
        normal = re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()
        if not normal or normal in seen:
            continue
        seen.add(normal)
        description = re.sub(r"<[^>]+>", " ", str(item.get("description") or item.get("snippet") or "")).strip()
        rows.append({
            "headline": headline,
            "source": str(item.get("source") or "Marketaux"),
            "url": url,
            "datetime": stamp,
            "summary": " ".join(description.split())[:800],
            "symbol": key,
            "provider": "Marketaux",
        })
    rows.sort(key=lambda row: int(row.get("datetime") or 0), reverse=True)
    return rows[:max(1, min(3, int(limit)))]


async def company_news(symbol: str, days: int = 2, include_marketaux: bool = False):
    """Recent company news with caching and provider failover; Marketaux is opt-in to protect API quota."""
    key = symbol.upper().strip()
    if not key:
        return []
    cache_key = f"{key}:{days}:marketaux={int(bool(include_marketaux))}"
    now = time.monotonic()
    cached = _news_cache.get(cache_key)
    if cached and now - cached[0] < max(60, settings.news_cache_minutes * 60):
        return cached[1]

    end = date.today()
    start = end - timedelta(days=max(1, days))
    result = []
    if include_marketaux:
        result = await _marketaux_company_news(key, days=days, limit=3)

    # Fall back to existing providers if Marketaux is not configured or has no matching stories.
    if not result:
        yahoo_rows = await yahoo_finance_news(key, limit=30)
        cutoff = int(datetime.now(timezone.utc).timestamp()) - max(1, days) * 86400
        yahoo_recent = [
            item for item in yahoo_rows
            if not item.get("datetime") or int(item.get("datetime") or 0) >= cutoff
        ]
        if yahoo_recent:
            result = yahoo_recent
        else:
            rows = await _finnhub_get(
                "company-news",
                {"symbol": key, "from": start.isoformat(), "to": end.isoformat()},
            )
            if isinstance(rows, list) and rows:
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
    yahoo_actions = await yahoo_corporate_actions(symbol)
    if yahoo_actions.get("status") == "ok":
        results["splits"] = yahoo_actions.get("splits") or []
        results["dividends"] = yahoo_actions.get("dividends") or []
        results["source"] = "Yahoo Finance"
        results["status"] = "ok"

    if _finnhub_pool.size:
        for name, endpoint in [
                ("earnings", "calendar/earnings"),
                ("dividends", "stock/dividend"),
                ("splits", "stock/split"),
            ]:
                # Keep Yahoo as the primary source for splits/dividends; Finnhub
                # supplies only missing event types or acts as fallback.
                if name in ("splits", "dividends") and yahoo_actions.get("status") == "ok":
                    continue
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
        if results["status"] != "ok":
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


_MARKET_NEWS_CACHE = {"at": 0.0, "items": [], "session_key": ""}
_MARKET_NEWS_TTL = 300
_US_EQUITY_TERMS = (
    "stock market", "stocks", "shares", "equities", "s&p 500", "s&p", "nasdaq", "dow jones",
    "wall street", "stock futures", "earnings", "ipo", "listed company", "share price",
    "market rally", "stocks fall", "stocks rise", "tech stocks", "small-cap", "large-cap",
    "bull market", "bear market", "selloff", "sell-off", "trading session", "investors",
    "federal reserve", "fomc", "interest rate", "rate cut", "rate hike", "inflation", "cpi", "ppi", "powell", "treasury", "bond yield", "jobs report", "nonfarm payroll", "unemployment", "gdp", "tariff", "oil prices",
    "equity markets", "market close", "market open", "stock exchange", "us stocks", "u.s. stocks",
)

_MARKET_IMPACT_TERMS = (
    "federal reserve", "federal open market", "fomc", "fed", "interest rate",
    "rate cut", "rate hike", "inflation", "cpi", "ppi", "powell", "treasury",
    "bond yield", "jobs report", "nonfarm payroll", "unemployment", "gdp",
    "recession", "tariff", "trade war", "sanctions", "government shutdown",
    "debt ceiling", "s&p", "nasdaq", "dow jones", "wall street", "stock futures",
    "earnings", "market rally", "stocks fall", "stocks rise", "stock market",
    "stock", "stocks", "shares", "equities", "investors", "market", "markets",
    "oil prices", "crude oil", "banking crisis", "liquidity", "economic data",
    "economic growth", "economic outlook", "tech stocks", "selloff", "sell-off",
)

async def _rss_market_news(limit: int = 24):
    """Fetch recent US-market headlines from independent RSS feeds concurrently."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    feeds = (
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    )
    now_epoch = int(time.time())
    cutoff = now_epoch - 7 * 86400

    async def read_feed(client, source, feed_url):
        try:
            response = await client.get(feed_url, timeout=5)
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception as exc:
            logger.info("Market news RSS feed unavailable (%s): %s", source, type(exc).__name__)
            return []

        rows = []
        for item in root.findall(".//item"):
            headline = re.sub(r"\s+", " ", (item.findtext("title") or "")).strip()
            url = (item.findtext("link") or "").strip()
            if not headline or not url.startswith(("https://", "http://")):
                continue
            normal = headline.lower()
            if not any(term in normal for term in _US_EQUITY_TERMS) and not any(term in normal for term in _MARKET_IMPACT_TERMS):
                continue
            published_text = item.findtext("pubDate") or ""
            try:
                stamp = int(parsedate_to_datetime(published_text).timestamp()) if published_text else 0
            except Exception:
                stamp = 0
            if stamp <= 0 or stamp < cutoff or stamp > now_epoch + 3600:
                continue
            description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            rows.append({
                "headline": headline, "source": source, "url": url,
                "datetime": stamp, "summary": " ".join(description.split())[:500],
                "category": "general", "related": "", "provider": source,
            })
        return rows

    async with httpx.AsyncClient(follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SASPRO-MarketNews/1.0)"
    }) as client:
        feed_rows = await asyncio.gather(
            *(read_feed(client, source, url) for source, url in feeds),
            return_exceptions=True,
        )

    rows = []
    seen = set()
    for result in feed_rows:
        if not isinstance(result, list):
            continue
        for row in result:
            key = re.sub(r"[^a-z0-9]+", " ", row["headline"].lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: int(row.get("datetime") or 0), reverse=True)
    return rows[:max(1, min(40, int(limit)))]

async def _marketaux_market_news(limit: int = 24):
    """Fetch recent US financial news from Marketaux; keep API token server-side."""
    if _marketaux_pool.size == 0:
        return []
    payload = None
    for _ in range(_marketaux_pool.size):
        api_key = await _marketaux_pool.acquire()
        if not api_key:
            break
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                response = await client.get(
                    "https://api.marketaux.com/v1/news/all",
                    params={"api_token": api_key, "countries": "us", "language": "en",
                            "filter_entities": "true", "limit": max(1, min(50, int(limit) * 2))},
                )
            if response.status_code in (401, 403, 429):
                retry = response.headers.get("Retry-After")
                await _marketaux_pool.mark_failure(api_key, retry_after=int(retry) if retry and retry.isdigit() else None)
                continue
            response.raise_for_status()
            await _marketaux_pool.mark_success(api_key)
            payload = response.json()
            break
        except Exception as exc:
            await _marketaux_pool.mark_failure(api_key)
            logger.warning("Marketaux market-news request failed: %s", type(exc).__name__)
    if payload is None:
        return []
    articles = payload.get("data", []) if isinstance(payload, dict) else []
    now_epoch = int(time.time())
    cutoff = now_epoch - 7 * 86400
    rows, seen = [], set()
    for item in articles if isinstance(articles, list) else []:
        if not isinstance(item, dict):
            continue
        headline = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        url = str(item.get("url") or "").strip()
        published = str(item.get("published_at") or "").strip()
        try:
            stamp = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError, OverflowError):
            stamp = 0
        if not headline or not url.startswith("https://") or stamp < cutoff or stamp > now_epoch + 3600:
            continue
        normal = re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()
        if not normal or normal in seen:
            continue
        entities = item.get("entities") or []
        symbols = [str(x.get("symbol") or "").upper() for x in entities if isinstance(x, dict) and x.get("symbol")]
        if not symbols and not any(term in normal for term in _US_EQUITY_TERMS) and not any(
            term in normal for term in ("federal reserve", "interest rate", "inflation", "treasury", "economy", "nasdaq", "s&p", "dow jones", "stock market")
        ):
            continue
        seen.add(normal)
        rows.append({"headline": headline, "source": str(item.get("source") or "Marketaux"),
                     "url": url, "datetime": stamp,
                     "summary": re.sub(r"<[^>]+>", " ", str(item.get("description") or "")).strip()[:500],
                     "category": "general", "related": ", ".join(symbols), "provider": "Marketaux"})
    rows.sort(key=lambda row: int(row.get("datetime") or 0), reverse=True)
    return rows[:max(1, min(40, int(limit)))]

async def _newsfilter_market_news(limit: int = 24):
    """Fetch source-linked market headlines from Newsfilter when an API key is configured."""
    api_key = str(getattr(settings, "newsfilter_api_key", "") or "").strip()
    if not api_key:
        return []
    query = (
        'title:("Federal Reserve" OR "interest rates" OR inflation OR stocks OR shares '
        'OR earnings OR Nasdaq OR Dow OR "S&P 500" OR tariffs OR economy OR "stock market") '
        'OR description:("Federal Reserve" OR "interest rates" OR inflation OR stocks '
        'OR earnings OR Nasdaq OR "S&P 500" OR tariffs)'
    )
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.post(
                "https://api.newsfilter.io/search",
                headers={"Authorization": api_key, "Accept": "application/json"},
                json={"queryString": query, "from": 0, "size": max(10, min(40, int(limit) * 2))},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Newsfilter market-news request failed: %s", type(exc).__name__)
        return []
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    if not isinstance(articles, list):
        return []
    now_epoch = int(time.time())
    cutoff = now_epoch - 7 * 86400
    rows, seen = [], set()
    for item in articles:
        if not isinstance(item, dict):
            continue
        headline = re.sub(r"\s+", " ", str(item.get("title") or item.get("headline") or "")).strip()
        url = str(item.get("url") or "").strip()
        published = item.get("publishedAt") or item.get("published_at") or item.get("datetime")
        try:
            if isinstance(published, (int, float)):
                stamp = int(published)
            else:
                published_text = str(published or "").strip()
                if published_text.isdigit():
                    stamp = int(published_text)
                else:
                    stamp = int(datetime.fromisoformat(published_text.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError, OverflowError):
            stamp = 0
        if not headline or not url.startswith("https://") or stamp < cutoff or stamp > now_epoch + 3600:
            continue
        normal = re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()
        if not normal or normal in seen:
            continue
        if not any(term in normal for term in _US_EQUITY_TERMS) and not any(term in normal for term in (
            "federal reserve", "interest rate", "inflation", "tariff", "treasury", "economic", "economy",
            "nasdaq", "s&p", "dow jones", "earnings", "stocks", "stock market", "shares",
        )):
            continue
        seen.add(normal)
        source = item.get("source") or {}
        if isinstance(source, dict):
            source_name = source.get("name") or source.get("id") or "Newsfilter"
        else:
            source_name = str(source or "Newsfilter")
        description = re.sub(r"<[^>]+>", " ", str(item.get("description") or item.get("summary") or "")).strip()
        rows.append({
            "headline": headline,
            "source": str(source_name),
            "url": url,
            "datetime": stamp,
            "summary": " ".join(description.split())[:500],
            "category": "general",
            "related": ", ".join(item.get("symbols", [])) if isinstance(item.get("symbols"), list) else "",
            "provider": "Newsfilter",
        })
    rows.sort(key=lambda row: int(row.get("datetime") or 0), reverse=True)
    return rows[:max(1, min(40, int(limit)))]

def _market_news_session_window():
    """Return the active US trading-day news window; preserve the legacy feed on holidays/weekends."""
    try:
        from .market_calendar import market_status, us_market_holidays
        status = market_status()
        session_key = str(status.get("date") or date.today().isoformat())
        if status.get("holiday") or status.get("session") == "weekend":
            return None, "legacy:" + session_key

        ny = ZoneInfo("America/New_York")
        now = datetime.now(ny)
        session_day = now.date()
        # Between midnight and 04:00 ET, retain the prior trading session's feed.
        if now.hour < 4:
            session_day -= timedelta(days=1)
        while session_day.weekday() >= 5 or session_day in us_market_holidays(session_day.year):
            session_day -= timedelta(days=1)
        start = datetime(session_day.year, session_day.month, session_day.day, 4, 0, tzinfo=ny)
        return int(start.timestamp()), session_day.isoformat()
    except Exception as exc:
        logger.info("Market-news session window unavailable: %s", type(exc).__name__)
        return None, "legacy:" + date.today().isoformat()


def _is_relevant_stock_market_news(row):
    """Strict relevance gate for the public market-news ribbon.

    Keep US equities, listed-company catalysts, earnings/filings, market indexes,
    central-bank decisions and macro data that can move stocks. Reject sports,
    entertainment and unrelated viral/social-media stories even if they contain
    generic words such as 'record', 'views', 'week' or 'market'.
    """
    if not isinstance(row, dict):
        return False
    headline = re.sub(r"\s+", " ", str(row.get("headline") or "")).strip().lower()
    summary = re.sub(r"\s+", " ", str(row.get("summary") or "")).strip().lower()
    related = str(row.get("related") or "").strip()
    if not headline:
        return False
    text = headline + " " + summary[:1200]

    # Reject clearly unrelated verticals unless the same story explicitly reports
    # a financial/market catalyst. This prevents sports-viewership headlines.
    off_topic = (
        "super league", "football", "soccer", "cricket", "ipl ", "nba ", "nfl ",
        "mlb ", "nhl ", "premier league", "champions league", "world cup",
        "matchday", "goalkeeper", "striker", "coach sacked", "transfer window",
        "youtube views", "streaming record", "box office", "celebrity", "reality show",
        "music video", "album release", "gaming tournament", "esports",
        # Exclude non-US market stories from the US-market ribbon unless the same
        # article explicitly mentions a US index, Federal Reserve, or US-listed stock.
        "indian derivatives", "india's derivatives", "india derivatives", "nifty",
        "sensex", "sebi", "national stock exchange of india", "bombay stock exchange",
        "nse india", "bse india", "indian stock market",
    )
    finance_context = (
        "stock", "stocks", "share price", "shares", "equity", "equities",
        "earnings", "revenue", "profit", "loss", "guidance", "forecast",
        "ipo", "listing", "acquisition", "merger", "buyback", "dividend",
        "sec filing", "10-k", "10-q", "8-k", "analyst", "price target",
        "dow jones", "nasdaq", "s&p 500", "s&p500", "wall street",
        "federal reserve", "fomc", "interest rate", "inflation", "cpi",
        "jobs report", "treasury yield", "bond yield", "tariff", "gdp",
        "recession", "stock futures", "market rally", "sell-off", "selloff",
        "trading", "investor", "investors", "market cap", "shares rose",
        "shares fell", "shares jump", "shares drop", "stock rises", "stock falls",
    )
    if any(term in text for term in off_topic):
        us_market_context = (
            "s&p 500", "s&p500", "nasdaq", "dow jones", "wall street",
            "federal reserve", "fomc", "u.s. stocks", "us stocks",
            "nyse", "nyse-listed", "nasdaq-listed", "american stocks",
            "u.s. listed", "us-listed", "sec filing",
        )
        if not any(term in text for term in us_market_context):
            return False

    # Entity-linked company headlines may not use generic market terminology;
    # allow them only when the headline/summary still describes a business event.
    business_context = (
        "company", "corp", "inc.", "plc", "announces", "announced", "launches",
        "launched", "appoints", "appointed", "recalls", "recall", "settlement",
        "lawsuit", "regulator", "regulatory", "approval", "contract", "orders",
        "partnership", "outlook", "results", "quarter", "sales", "revenue",
        "earnings", "profit", "loss", "shares", "stock", "investigation",
    )
    has_symbol = bool(related and re.search(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b", related))
    if has_symbol and any(term in text for term in business_context):
        return True
    # Generic finance keywords alone are not enough: they can match foreign
    # exchanges, crypto-only stories, or broad global business news. For macro
    # headlines, require an explicit US-equities anchor; company-specific items
    # with a related ticker and a business catalyst are handled above.
    us_equities_context = (
        "u.s. stocks", "us stocks", "u.s. stock market", "us stock market",
        "american stocks", "u.s. equities", "us equities", "u.s.-listed",
        "us-listed", "nyse", "nasdaq", "s&p 500", "s&p500", "dow jones",
        "wall street", "russell 2000", "nyse composite", "nasdaq composite",
        "federal reserve", "fomc", "u.s. treasury", "us treasury",
        "u.s. inflation", "us inflation", "u.s. jobs", "us jobs report",
        "u.s. economy", "us economy", "u.s. gdp", "us gdp",
        "u.s. tariffs", "us tariffs", "u.s. market", "us market",
    )
    return (
        any(term in text for term in finance_context)
        and any(term in text for term in us_equities_context)
    )


async def market_news(limit: int = 24):
    """Aggregate current-session US-market headlines across configured providers."""
    now = time.monotonic()
    cutoff, session_key = _market_news_session_window()
    cached = _MARKET_NEWS_CACHE
    requested_limit = max(1, min(40, int(limit)))
    if (cached["items"] and cached.get("session_key") == session_key
            and now - cached["at"] < _MARKET_NEWS_TTL):
        return cached["items"][:requested_limit]

    # Do not stop at the first provider that returns a single story. Collect
    # from every configured source so the ribbon can show a useful multi-story feed.
    marketaux_rows, newsfilter_rows, finnhub_payload, rss_rows = await asyncio.gather(
        _marketaux_market_news(requested_limit),
        _newsfilter_market_news(requested_limit),
        _finnhub_get("news", {"category": "general"}, timeout=10),
        _rss_market_news(requested_limit),
        return_exceptions=True,
    )

    rows = []
    for provider_rows in (marketaux_rows, newsfilter_rows, rss_rows):
        if isinstance(provider_rows, list):
            rows.extend(row for row in provider_rows if isinstance(row, dict))

    if isinstance(finnhub_payload, list):
        cutoff_7d = int(time.time()) - 7 * 86400
        now_epoch = int(time.time())
        for item in finnhub_payload:
            if not isinstance(item, dict):
                continue
            headline = re.sub(r"\\s+", " ", str(item.get("headline") or "")).strip()
            url = str(item.get("url") or "").strip()
            try:
                stamp = int(item.get("datetime") or 0)
            except (TypeError, ValueError):
                stamp = 0
            if not headline or not url.startswith(("https://", "http://")) or stamp < cutoff_7d or stamp > now_epoch + 3600:
                continue
            rows.append({
                "headline": headline,
                "source": str(item.get("source") or "Finnhub"),
                "url": url,
                "datetime": stamp,
                "summary": re.sub(r"<[^>]+>", " ", str(item.get("summary") or "")).strip()[:500],
                "category": str(item.get("category") or "general"),
                "related": str(item.get("related") or ""),
                "provider": "Finnhub",
            })

    # Apply the same time window and strict relevance gate to every provider.
    if cutoff is not None:
        rows = [row for row in rows if int(row.get("datetime") or 0) >= cutoff]
    rows = [row for row in rows if _is_relevant_stock_market_news(row)]

    # Deduplicate syndicated copies by normalized headline, then prefer the
    # newest items. Keep URLs source-linked for the original-article action.
    unique = []
    seen = set()
    for row in sorted(rows, key=lambda item: int(item.get("datetime") or 0), reverse=True):
        headline = re.sub(r"\\s+", " ", str(row.get("headline") or "")).strip()
        key = re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)

    result = unique[:requested_limit]
    _MARKET_NEWS_CACHE.update({"at": now, "items": result, "session_key": session_key})
    return result
