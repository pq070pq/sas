"""Arabic translation of stock-news headlines and summaries using configured AI providers."""
import json
import logging
import httpx
from .config import settings

logger = logging.getLogger(__name__)


async def translate_stock_news(symbol: str, items: list[dict]) -> list[dict]:
    """Translate a small set of verified headlines/summaries; preserve original evidence."""
    selected = [x for x in items if isinstance(x, dict) and x.get("headline")][:5]
    if not selected:
        return []
    providers = [
        (settings.gemini_api_key, settings.gemini_base_url, settings.gemini_model),
        (settings.groq_api_key, settings.groq_base_url, settings.groq_model),
        (settings.openrouter_api_key, settings.openrouter_base_url, settings.openrouter_model),
    ]
    provider = next((p for p in providers if p[0] and p[1]), None)
    if not provider:
        return []
    api_key, base_url, model = provider
    payload = [
        {"url": str(item.get("url") or ""), "headline": str(item.get("headline") or "")[:500],
         "summary": str(item.get("ai_summary") or item.get("summary") or "")[:1800],
         "source": str(item.get("source") or "")[:100]}
        for item in selected
    ]
    prompt = (
        "أنت مترجم أخبار مالية. أعد JSON فقط بالشكل "
        '{"items":[{"url":"نفس الرابط حرفيًا","translated_headline":"عنوان عربي دقيق",'
        '"summary":"ملخص عربي واضح من جملتين كحد أقصى","basis":"article_or_headline_only"}]}. '
        "ترجم دون إضافة معلومات أو استنتاجات غير موجودة. إذا لم يوجد ملخص، لخّص العنوان فقط وضع "
        "basis=headline_only. لا تدّع أن الخبر يؤثر على السهم ما لم يذكر ذلك صراحة. "
        f"رمز السهم المعروض: {symbol}. لا تنسب خبرًا للشركة إلا إذا دل النص على ذلك. الأخبار: "
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.1,
                      "messages": [{"role": "system", "content": "Return valid JSON only. Arabic output."},
                                   {"role": "user", "content": prompt}],
                      "response_format": {"type": "json_object"}},
            )
            response.raise_for_status()
            parsed = json.loads(response.json()["choices"][0]["message"]["content"])
            translations = parsed.get("items", []) if isinstance(parsed, dict) else []
            by_url = {str(item.get("url") or ""): item for item in translations
                      if isinstance(item, dict) and item.get("url")}
            result = []
            for item in selected:
                translated = by_url.get(str(item.get("url") or ""))
                if not translated or not translated.get("translated_headline"):
                    continue
                result.append({
                    "url": str(item.get("url") or ""),
                    "headline": str(item.get("headline") or ""),
                    "translated_headline": str(translated.get("translated_headline") or "")[:500],
                    "summary": str(translated.get("summary") or "")[:1200],
                    "basis": "headline_only" if translated.get("basis") == "headline_only" else "article_or_headline",
                    "source": str(item.get("source") or ""),
                })
            return result
    except Exception as exc:
        logger.warning("Stock news Arabic translation unavailable for %s: %s", symbol, type(exc).__name__)
        return []
