import hashlib
import json
import logging
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
    payload = json.dumps({"symbol": symbol, "news": news[:5], "fundamentals": fundamentals}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _normalise(value, fallback="غير متوفر"):
    text = str(value or "").strip()
    return text[:900] if text else fallback


def _prompt(symbol: str, company: dict, news: list[dict], fundamentals: dict, market: dict) -> str:
    evidence = {"symbol": symbol, "company": company, "latest_news": news[:5], "fundamentals": fundamentals, "market_context": market}
    return f"""
أنت محلل أخبار وأسهم داخل نظام SAS PRO.
حلل السهم اعتمادًا على الأدلة المرفقة فقط. لا تستخدم معرفة خارجية ولا تخترع أرقامًا أو أخبارًا.

المطلوب:
1) قراءة أحدث الأخبار المرتبطة بالسهم وتحديد الخبر الأهم والأحدث.
2) تفسير سبب الارتفاع فقط إذا كان توقيت الخبر ومحتواه يدعمان هذا الارتباط.
3) إذا لم توجد أدلة كافية، اكتب بوضوح: غير واضح.
4) تقديم تحليل مالي مبسط من البيانات المالية المتاحة فقط: الإيرادات، الربحية، النقد، الدين، النمو، التخفيف/الإصدارات إن ظهرت. إذا لم تتوفر البيانات، اذكر ذلك صراحة.
5) ذكر المخاطر الواضحة في الأخبار أو البيانات.
6) لا تقدم توصية شراء/بيع، ولا احتمالات مئوية، ولا هدفًا سعريًا جديدًا.
7) لا تعتبر حركة السعر وحدها خبرًا أو سببًا مؤكدًا.

أعد JSON فقط بهذه المفاتيح:
{{
  "headline_summary": "ملخص الخبر الأهم في جملة أو جملتين",
  "why_rising": "لماذا ارتفع السهم وفق الأدلة، أو غير واضح",
  "news_assessment": "مرتبط بالخبر | ارتباط محتمل | غير واضح",
  "financial_summary": "تحليل مالي مبسط، أو لا توجد بيانات مالية كافية",
  "risk_flags": ["خطر 1", "خطر 2"],
  "key_takeaway": "الخلاصة للمستخدم في سطر واحد"
}}

الأدلة:
{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)}
""".strip()


async def analyze_stock(symbol: str, company: dict | None = None, news: list[dict] | None = None, fundamentals: dict | None = None, market: dict | None = None) -> dict:
    if not settings.ai_radar_enabled:
        return {"enabled": False, "status": "disabled"}

    providers = _providers()
    if not providers:
        return {"enabled": False, "status": "no_api_key", "key_takeaway": "تحليل الذكاء الاصطناعي غير مفعّل — أضف مفتاح مزود LLM في .env."}

    news = news or []
    fundamentals = fundamentals or {}
    company = company or {}
    market = market or {}

    key = _cache_key(symbol, news, fundamentals)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    global _semaphore
    if _semaphore is None:
        import asyncio
        _semaphore = asyncio.Semaphore(max(1, settings.ai_radar_concurrency))

    async with _semaphore:
        prompt = _prompt(symbol, company, news, fundamentals, market)
        last_error = None
        async with httpx.AsyncClient(timeout=settings.ai_radar_timeout_seconds) as client:
            for provider in providers:
                try:
                    headers = {"Authorization": f"Bearer {provider['key']}", "Content-Type": "application/json"}
                    body = {
                        "model": provider["model"],
                        "messages": [
                            {"role": "system", "content": "أنت جزء من رادار معلوماتي. التزم بالأدلة فقط. لا تخترع معلومات ولا تقدم توصية استثمارية."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 900,
                    }
                    if provider["name"] == "Groq":
                        body["response_format"] = {"type": "json_object"}

                    response = await client.post(f"{provider['base_url']}/chat/completions", headers=headers, json=body)
                    response.raise_for_status()
                    payload = response.json()
                    content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                    parsed = _extract_json(content)
                    if not parsed:
                        raise ValueError("LLM returned invalid JSON")

                    result = {
                        "enabled": True,
                        "status": "ok",
                        "provider": provider["name"],
                        "model": provider["model"],
                        "headline_summary": _normalise(parsed.get("headline_summary")),
                        "why_rising": _normalise(parsed.get("why_rising")),
                        "news_assessment": _normalise(parsed.get("news_assessment"), "غير واضح"),
                        "financial_summary": _normalise(parsed.get("financial_summary")),
                        "risk_flags": [_normalise(x, "") for x in (parsed.get("risk_flags") or []) if str(x).strip()][:4],
                        "key_takeaway": _normalise(parsed.get("key_takeaway")),
                    }
                    _cache[key] = (now, result)
                    return result
                except Exception as exc:
                    last_error = exc
                    logger.warning("AI radar provider failed for %s (%s): %s", symbol, provider["name"], exc)

        return {"enabled": False, "status": "provider_error", "error": str(last_error)[:300] if last_error else "unknown"}
