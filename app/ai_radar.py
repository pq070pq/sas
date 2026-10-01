import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL = 20 * 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_semaphore = None


def _providers():
    providers = []
    if settings.groq_api_key:
        providers.append({"name": "Groq", "key": settings.groq_api_key, "base_url": settings.groq_base_url.rstrip("/"), "model": settings.groq_model})
    if settings.gemini_api_key:
        providers.append({"name": "Gemini", "key": settings.gemini_api_key, "base_url": settings.gemini_base_url.rstrip("/"), "model": settings.gemini_model})
    if settings.openrouter_api_key:
        providers.append({"name": "OpenRouter", "key": settings.openrouter_api_key, "base_url": settings.openrouter_base_url.rstrip("/"), "model": settings.openrouter_model})
    return providers


def _cache_key(symbol: str, news: list[dict], fundamentals: dict) -> str:
    payload = json.dumps(
        {"symbol": symbol, "news": news[:5], "fundamentals": fundamentals},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            return value if isinstance(value, dict) else {}
        except Exception:
            pass
    return {}


def _normalise(value, fallback="غير متوفر"):
    text = str(value or "").strip()
    return text[:900] if text else fallback


def _safe_news(news: list[dict]) -> list[dict]:
    safe = []
    for idx, item in enumerate(news[:5], 1):
        if not isinstance(item, dict):
            continue
        headline = str(item.get("headline") or "").strip()
        url = str(item.get("url") or "").strip()
        source = str(item.get("source") or "").strip()
        if not headline or not url or not source:
            continue
        safe.append({
            "id": f"N{idx}",
            "headline": headline,
            "source": source,
            "url": url,
            "datetime": item.get("datetime"),
            "summary": str(item.get("summary") or "").strip()[:800],
        })
    return safe


def _contains_market_price_claim(value: Any) -> bool:
    text = str(value or "")
    # AI must never create or repeat a market price. Financial ratios/percentages
    # are allowed; currency-like price strings are not.
    return bool(re.search(r"(?i)(?:\$|USD\\s*\\d|\\d+(?:\\.\\d+)?\\s*(?:دولار|USD)\\b)", text))


def _prompt(symbol: str, news: list[dict], fundamentals: dict) -> str:
    evidence = {
        "symbol": symbol,
        "verified_news_sources": news,
        "verified_financial_data": fundamentals,
    }
    return f"""
أنت طبقة تحليل أخبار داخل SAS PRO، ولست مصدر بيانات أسعار.
التزم حرفيًا بالأدلة المرفقة فقط.

قواعد إلزامية:
- لا تنشئ أي خبر أو عنوان أو مصدر أو رابط.
- لا تستخدم معرفة خارجية.
- لا تخترع أي رقم.
- لا تنشئ أو تعدل أو تقترح أي سعر سوق، هدف، وقف، دخول، اختراق، دعم، مقاومة، نسبة عائد أو مخاطرة.
- لا تذكر سعر السهم الحالي أو سعرًا تاريخيًا.
- إذا لم تكفِ الأدلة، اكتب «غير واضح» أو «غير متوفر».
- لا تقل إن خبرًا سبب الارتفاع بشكل مؤكد إلا إذا كان محتوى الخبر وتوقيته يدعمان ذلك؛ استخدم «مرتبط بالخبر» أو «ارتباط محتمل» أو «غير واضح».
- أي رقم مالي تذكره يجب أن يكون موجودًا حرفيًا في verified_financial_data.
- أي خبر تذكره يجب أن يكون مأخوذًا من verified_news_sources.
- لا تقدم توصية شراء أو بيع.

أعد JSON فقط:
{{
  "primary_source_id": "N1 أو N2 أو غير واضح",
  "supporting_source_ids": ["N2"],
  "headline_summary": "تلخيص للخبر الموجود في المصدر فقط",
  "why_rising": "تفسير مبني على محتوى وتوقيت المصادر فقط، أو غير واضح",
  "news_assessment": "مرتبط بالخبر | ارتباط محتمل | غير واضح",
  "financial_summary": "تحليل مالي مبسط من verified_financial_data فقط، أو нет",
  "risk_flags": ["مخاطر موجودة صراحة في الأدلة فقط"],
  "key_takeaway": "خلاصة قصيرة مبنية على الأدلة فقط"
}}

الأدلة الموثقة:
{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)}
""".strip()


def _validate(parsed: dict, news: list[dict], fundamentals: dict) -> dict:
    valid_ids = {item["id"] for item in news}
    primary = str(parsed.get("primary_source_id") or "").strip()
    if primary not in valid_ids:
        primary = "غير واضح"

    supporting = [
        x for x in (parsed.get("supporting_source_ids") or [])
        if str(x) in valid_ids
    ][:4]

    fields = {
        "headline_summary": _normalise(parsed.get("headline_summary"), "غير واضح"),
        "why_rising": _normalise(parsed.get("why_rising"), "غير واضح"),
        "news_assessment": _normalise(parsed.get("news_assessment"), "غير واضح"),
        "financial_summary": _normalise(parsed.get("financial_summary"), "غير متوفر"),
        "key_takeaway": _normalise(parsed.get("key_takeaway"), "غير واضح"),
    }
    # Never publish an AI response that contains a market-price claim.
    for key, value in fields.items():
        if _contains_market_price_claim(value):
            fields[key] = "غير متوفر — تم حجب قيمة سعرية غير مسموحة من طبقة الذكاء الاصطناعي."

    risks = []
    for value in parsed.get("risk_flags") or []:
        text = str(value or "").strip()
        if text and not _contains_market_price_claim(text):
            risks.append(text[:400])
    fields["risk_flags"] = risks[:4]

    return {
        "enabled": True,
        "status": "ok",
        "primary_source_id": primary,
        "supporting_source_ids": supporting,
        **fields,
    }


async def analyze_stock(
    symbol: str,
    company: dict | None = None,
    news: list[dict] | None = None,
    fundamentals: dict | None = None,
    market: dict | None = None,
) -> dict:
    if not settings.ai_radar_enabled:
        return {"enabled": False, "status": "disabled"}

    safe_news = _safe_news(news or [])
    safe_fundamentals = dict(fundamentals or {})
    # Remove any price-bearing fields before they ever reach the LLM.
    for key in ("price", "current_price", "52w_high", "52w_low"):
        safe_fundamentals.pop(key, None)

    providers = _providers()
    if not providers:
        return {
            "enabled": False,
            "status": "no_api_key",
            "key_takeaway": "تحليل الذكاء الاصطناعي غير مفعّل — أضف مفتاح مزود LLM في .env.",
        }

    key = _cache_key(symbol, safe_news, safe_fundamentals)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    global _semaphore
    if _semaphore is None:
        import asyncio
        _semaphore = asyncio.Semaphore(max(1, settings.ai_radar_concurrency))

    async with _semaphore:
        prompt = _prompt(symbol, safe_news, safe_fundamentals)
        last_error = None
        async with httpx.AsyncClient(timeout=settings.ai_radar_timeout_seconds) as client:
            for provider in providers:
                try:
                    headers = {
                        "Authorization": f"Bearer {provider['key']}",
                        "Content-Type": "application/json",
                    }
                    body = {
                        "model": provider["model"],
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "أنت محلل أخبار مقيد بالأدلة. لا تكتب أي معلومة "
                                    "غير موجودة في الأدلة ولا أي سعر سوق. أعد JSON فقط."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 900,
                    }
                    if provider["name"] == "Groq":
                        body["response_format"] = {"type": "json_object"}

                    response = await client.post(
                        f"{provider['base_url']}/chat/completions",
                        headers=headers,
                        json=body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                    parsed = _extract_json(content)
                    if not parsed:
                        raise ValueError("LLM returned invalid JSON")

                    result = _validate(parsed, safe_news, safe_fundamentals)
                    result["provider"] = provider["name"]
                    result["model"] = provider["model"]
                    result["evidence_count"] = len(safe_news)
                    _cache[key] = (now, result)
                    return result
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "AI news analysis failed for %s (%s): %s",
                        symbol, provider["name"], exc,
                    )

        return {
            "enabled": False,
            "status": "provider_error",
            "error": str(last_error)[:300] if last_error else "unknown",
        }
