"""Optional ScrapeGraphAI enrichment for already-discovered stock news.

SEC filings continue to come from the official SEC integration. This module only
extracts structured context from a small number of existing, source-linked news
URLs; it never discovers tickers or gates technical radar signals.
"""
import asyncio
import ipaddress
import logging
import time
from urllib.parse import urlsplit

from .config import settings

logger = logging.getLogger(__name__)
_CACHE = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60
_MAX_ITEMS = 3
_TIMEOUT_SECONDS = 25
_client_class = None
try:
    from scrapegraph_py import AsyncScrapeGraphAI as _client_class
except Exception:  # optional integration; the core radar must keep working
    _client_class = None

_SCHEMA = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": [
            "contract", "partnership", "earnings", "acquisition", "regulatory",
            "financing", "product", "litigation", "guidance", "other", "unclear"
        ]},
        "summary_ar": {"type": "string"},
        "event_direction": {"type": "string", "enum": ["positive", "negative", "neutral", "unclear"]},
        "financial_figures": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "value": {"type": "string"},
            "currency": {"type": "string"}, "context": {"type": "string"}
        }, "required": ["label", "value", "currency", "context"]}},
        "duration": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["event_type", "summary_ar", "event_direction", "financial_figures", "duration", "evidence_quote"],
}


def _safe_public_news_url(value: str) -> bool:
    """Accept ordinary public HTTP(S) news pages, never local URLs or SEC scraping."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")):
            return False
        if host == "sec.gov" or host.endswith(".sec.gov"):
            return False  # SEC data must use the official SEC API integration.
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


def _extract_payload(result):
    if getattr(result, "status", None) != "success":
        return None
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    # SDK response envelopes can wrap extracted JSON under a result/data key.
    for key in ("result", "data", "extracted_data", "output"):
        nested = data.get(key)
        if isinstance(nested, dict) and ("event_type" in nested or "summary_ar" in nested):
            return nested
    if "event_type" in data or "summary_ar" in data:
        return data
    results = data.get("results")
    if isinstance(results, dict):
        for value in results.values():
            if isinstance(value, dict) and ("event_type" in value or "summary_ar" in value):
                return value
    return None


def _normalise(payload, item, symbol):
    if not isinstance(payload, dict):
        return None
    allowed_types = {"contract", "partnership", "earnings", "acquisition", "regulatory",
                     "financing", "product", "litigation", "guidance", "other", "unclear"}
    allowed_directions = {"positive", "negative", "neutral", "unclear"}
    event_type = str(payload.get("event_type") or "unclear").lower()
    direction = str(payload.get("event_direction") or "unclear").lower()
    if event_type not in allowed_types:
        event_type = "unclear"
    if direction not in allowed_directions:
        direction = "unclear"
    figures = []
    for figure in payload.get("financial_figures") or []:
        if not isinstance(figure, dict):
            continue
        value = str(figure.get("value") or "").strip()[:100]
        if value:
            figures.append({
                "label": str(figure.get("label") or "رقم معلن")[:100],
                "value": value,
                "currency": str(figure.get("currency") or "")[:20],
                "context": str(figure.get("context") or "")[:240],
            })
    return {
        "symbol": symbol,
        "event_type": event_type,
        "summary_ar": str(payload.get("summary_ar") or "").strip()[:900],
        "event_direction": direction,
        "financial_figures": figures[:5],
        "duration": str(payload.get("duration") or "").strip()[:160],
        "evidence_quote": str(payload.get("evidence_quote") or "").strip()[:600],
        "source_url": str(item.get("url") or ""),
        "source_headline": str(item.get("headline") or item.get("title") or "")[:500],
        "verification_status": "extracted_unverified",
        "disclaimer": "استخراج آلي يحتاج مراجعة المصدر الأصلي؛ لا يثبت تأثير الخبر على سعر السهم.",
    }


async def enrich_catalyst_news(symbol: str, items: list[dict]) -> list[dict]:
    """Add optional structured catalyst context to at most three existing news items."""
    if not settings.scrapegraphai_enabled or not settings.scrapegraphai_api_key or _client_class is None:
        return items or []
    selected = [item for item in (items or [])
                if isinstance(item, dict) and item.get("url") and
                (item.get("headline") or item.get("title")) and
                _safe_public_news_url(item.get("url"))][:_MAX_ITEMS]
    if not selected:
        return items or []

    async def enrich_one(item, client):
        url = str(item.get("url") or "")
        cache_key = (str(symbol or "").upper(), url)
        cached = _CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        prompt = (
            "استخرج معلومات الحدث من صفحة المصدر فقط. رمز السهم المرشح: " + str(symbol).upper() + ". "
            "عنوان الخبر من مزود الأخبار: " + str(item.get("headline") or item.get("title") or "")[:500] + ". "
            "حدد نوع الحدث واتجاهه من حيث مضمون الإعلان، وليس توقع حركة السهم. "
            "لخص بالعربية دون إضافة معلومات غير موجودة. استخرج فقط الأرقام المالية المذكورة حرفيًا، "
            "مع العملة والسياق، واترك المصفوفة فارغة إن لم توجد أرقام. اقتبس جملة قصيرة داعمة حرفيًا. "
            "لا تعتبر الخبر متعلقًا بالسهم إلا إذا ربطت الصفحة الشركة أو رمزها بوضوح. "
            "إذا كانت الصفحة لا تقدم أدلة كافية، استخدم unclear. لا تستنتج أثرًا سعريًا."
        )
        try:
            result = await asyncio.wait_for(
                client.extract(prompt=prompt, url=url, schema=_SCHEMA, mode="reader"),
                timeout=_TIMEOUT_SECONDS,
            )
            payload = _normalise(_extract_payload(result), item, str(symbol or "").upper())
            if payload and (payload["summary_ar"] or payload["evidence_quote"] or payload["financial_figures"]):
                _CACHE[cache_key] = (time.monotonic(), payload)
                return payload
        except Exception as exc:
            logger.info("ScrapeGraphAI catalyst enrichment skipped for %s: %s", symbol, type(exc).__name__)
        return None

    try:
        async with _client_class(api_key=settings.scrapegraphai_api_key) as client:
            enriched = await asyncio.gather(*(enrich_one(item, client) for item in selected))
        by_url = {str(row.get("source_url")): row for row in enriched if row}
        return [
            {**item, "catalyst_intelligence": by_url[str(item.get("url") or "")]}
            if isinstance(item, dict) and str(item.get("url") or "") in by_url else item
            for item in (items or [])
        ]
    except Exception as exc:
        logger.info("ScrapeGraphAI unavailable; continuing without catalyst enrichment: %s", type(exc).__name__)
        return items or []
