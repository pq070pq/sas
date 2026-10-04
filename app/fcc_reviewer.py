import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_failure_count = 0
_disabled_until = 0.0
_lock = asyncio.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 15 * 60
_FAILURE_THRESHOLD = 3


def _base_url() -> str:
    return (settings.fcc_reviewer_base_url or "").rstrip("/")


def _endpoint() -> str:
    base = _base_url()
    if not base:
        return ""
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _cache_key(symbol: str, evidence: dict) -> str:
    raw = json.dumps({"symbol": symbol, "evidence": evidence}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalise_list(value: Any, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip()[:300] for x in value if str(x).strip()][:limit]


def _extract_json(text: str) -> dict:
    try:
        value = json.loads((text or "").strip())
        return value if isinstance(value, dict) else {}
    except Exception:
        start, end = (text or "").find("{"), (text or "").rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start:end + 1])
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
    return {}


def _normalise(parsed: dict) -> dict:
    level = str(parsed.get("review_level") or "محايد").strip()
    allowed = {"تأكيد قوي", "تأكيد جيد", "محايد", "تعارض"}
    if level not in allowed:
        level = "محايد"
    return {
        "available": True,
        "status": "ok",
        "review_level": level,
        "strengths": _normalise_list(parsed.get("strengths")),
        "contradictions": _normalise_list(parsed.get("contradictions")),
        "note": str(parsed.get("note") or "لا توجد ملاحظة إضافية.").strip()[:700],
        "model": settings.fcc_reviewer_model,
    }


def _prompt(symbol: str, evidence: dict) -> str:
    return f"""أنت مراجع AI اختياري داخل SAS PRO.
راجع الأدلة التي أعطاها لك SAS PRO فقط، ولا تستخدم معرفة خارجية.

قواعد إلزامية:
- لا تضف خبرًا أو رقمًا أو سعرًا أو معلومة غير موجودة في الأدلة.
- لا تغيّر السعر أو الوقف أو الأهداف أو RVOL أو R:R أو أي مستوى فني.
- لا تقبل أو ترفض السهم نيابة عن الرادار؛ أنت مراجع فقط.
- لا تقدم توصية شراء أو بيع.
- إذا وجدت تعارضًا، اذكره بوضوح.
- أعد JSON فقط وبالعربية.

JSON:
{{
  "review_level": "تأكيد قوي | تأكيد جيد | محايد | تعارض",
  "strengths": ["نقاط قوة مثبتة من الأدلة"],
  "contradictions": ["تعارضات مثبتة من الأدلة"],
  "note": "خلاصة قصيرة جدًا"
}}

السهم: {symbol}
الأدلة:
{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)}
""".strip()


async def review_stock(symbol: str, evidence: dict) -> dict:
    global _failure_count, _disabled_until
    if not settings.fcc_reviewer_enabled:
        return {"available": False, "status": "disabled"}
    endpoint = _endpoint()
    if not endpoint or not settings.fcc_reviewer_model:
        return {"available": False, "status": "not_configured"}

    now = time.monotonic()
    if now < _disabled_until:
        return {"available": False, "status": "circuit_open"}

    key = _cache_key(symbol, evidence)
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    payload = {
        "model": settings.fcc_reviewer_model,
        "messages": [
            {"role": "system", "content": "أنت مراجع أدلة فقط. لا تغيّر قرار SAS PRO ولا تخترع معلومات. أعد JSON فقط."},
            {"role": "user", "content": _prompt(symbol, evidence)},
        ],
        "temperature": 0.0,
        "max_tokens": 500,
    }

    try:
        async with _lock:
            async with httpx.AsyncClient(timeout=settings.fcc_reviewer_timeout_seconds) as client:
                response = await client.post(
                    endpoint,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
                content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                parsed = _extract_json(content)
                if not parsed:
                    raise ValueError("FCC reviewer returned invalid JSON")
                result = _normalise(parsed)
            _failure_count = 0
            _disabled_until = 0.0
            _cache[key] = (time.monotonic(), result)
            return result
    except Exception as exc:
        _failure_count += 1
        if _failure_count >= _FAILURE_THRESHOLD:
            _disabled_until = time.monotonic() + max(60, settings.fcc_reviewer_failure_cooldown_seconds)
            logger.error("FCC reviewer circuit opened for %ss after repeated failures", settings.fcc_reviewer_failure_cooldown_seconds)
        else:
            logger.warning("FCC reviewer failed for %s: %s", symbol, exc)
        return {"available": False, "status": "provider_error"}
