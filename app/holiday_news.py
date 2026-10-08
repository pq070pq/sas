import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import httpx

from .config import settings
from .telegram import send_message
from .market_calendar import market_status
from .ai_radar import _providers, _extract_json

logger = logging.getLogger(__name__)
HOLIDAY_NEWS_INTERVAL_MINUTES = 180
_last_news_run_at = None
_sent_news_keys = set()

_KEYWORDS = (
    "federal reserve", "fed ", "interest rate", "inflation", "cpi", "ppi",
    "jobs", "payroll", "unemployment", "treasury", "yield", "nasdaq",
    "s&p", "dow", "stocks", "stock market", "wall street", "oil", "crude",
    "gold", "bitcoin", "crypto", "tariff", "trade", "sanctions", "war",
    "iran", "israel", "ukraine", "recession", "gdp", "china", "economy",
    "bank", "banking", "earnings", "guidance", "ipo", "merger", "acquisition",
)

async def _get_general_news():
    rows = []
    try:
        from .news import _finnhub_get, _fmp_get
        if settings.finnhub_api_key or settings.finnhub_api_keys:
            data = await _finnhub_get("news", {"category": "general"}, timeout=12)
            if isinstance(data, list):
                rows.extend(data)
        if not rows and (settings.fmp_api_key or settings.fmp_api_keys) and settings.fmp_news_enabled:
            data = await _fmp_get("news/general-latest", {"page": 0, "limit": 30}, timeout=12)
            if isinstance(data, list):
                for x in data:
                    rows.append({
                        "headline": x.get("title"),
                        "url": x.get("url"),
                        "source": x.get("publisher") or x.get("site"),
                        "datetime": x.get("publishedDate"),
                    })
    except Exception:
        logger.exception("Holiday news: news provider fetch failed.")
    return rows

def _normalise(item):
    if not isinstance(item, dict):
        return None
    headline = str(item.get("headline") or item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    source = str(item.get("source") or item.get("publisher") or item.get("site") or "").strip()
    stamp = item.get("datetime") or item.get("publishedDate") or 0
    try:
        if isinstance(stamp, str):
            stamp = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
        else:
            stamp = int(stamp or 0)
    except Exception:
        stamp = 0
    if not headline or not url or not source:
        return None
    return {"headline": headline, "url": url, "source": source, "datetime": stamp}

def _is_relevant(item):
    text = item["headline"].lower()
    return any(k in text for k in _KEYWORDS)

async def _translate_batch(items):
    providers = _providers()
    if not providers:
        return []
    evidence = [{"id": f"N{i+1}", "headline": x["headline"], "source": x["source"], "url": x["url"]} for i, x in enumerate(items)]
    prompt = """أنت محرر موجز أخبار SAS PRO.
ترجم واختصر الأخبار المرفقة إلى العربية اعتمادًا على العناوين الموثقة فقط.
ممنوع اختراع أي معلومة أو رقم أو سبب لحركة السوق.
ممنوع إعطاء توصية شراء أو بيع.
لا تذكر أسعار أسهم أو أهدافًا أو نقاط دخول/وقف.
إذا كان تأثير الخبر غير واضح، اكتب «الأثر غير واضح».
أعد JSON فقط:
{"items":[{"id":"N1","title_ar":"عنوان عربي مختصر","summary_ar":"ماذا حدث باختصار","market_impact":"الأثر المحتمل على السوق أو القطاع، أو الأثر غير واضح"}]}
لا تضف أخبارًا من خارج القائمة ولا تغير id.

الأخبار:
""" + json.dumps(evidence, ensure_ascii=False)
    for provider in providers:
        key = provider.get("key")
        pool = provider.get("pool")
        try:
            if pool is not None:
                key = await pool.acquire()
                if not key:
                    continue
            body = {
                "model": provider["model"],
                "messages": [
                    {"role": "system", "content": "أنت مترجم ومحرر أخبار مالي مقيد بالأدلة. أعد JSON فقط."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 1200,
            }
            if provider["name"] == "Groq":
                body["response_format"] = {"type": "json_object"}
            async with httpx.AsyncClient(timeout=settings.ai_radar_timeout_seconds) as client:
                response = await client.post(f'{provider["base_url"]}/chat/completions',
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
            if response.status_code in (401, 403, 429) and pool is not None:
                await pool.mark_failure(key)
                continue
            response.raise_for_status()
            if pool is not None:
                await pool.mark_success(key)
            payload = response.json()
            raw = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            parsed = _extract_json(raw)
            translated = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(translated, list):
                continue
            valid_ids = {x["id"] for x in evidence}
            return [x for x in translated if isinstance(x, dict) and str(x.get("id")) in valid_ids]
        except Exception as exc:
            logger.warning("Holiday news translation failed via %s: %s", provider.get("name"), exc)
    return []

async def publish_holiday_news():
    """موجز أخبار عربي مستقل أثناء عطلة/نهاية أسبوع سوق الأسهم."""
    global _last_news_run_at
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return {"sent": False, "reason": "Telegram not configured"}
    status = market_status()
    if not status["holiday"] and status["session"] != "weekend":
        return {"sent": False, "reason": "stock market is open"}
    now = time.monotonic()
    if _last_news_run_at is not None and now - _last_news_run_at < HOLIDAY_NEWS_INTERVAL_MINUTES * 60:
        return {"sent": False, "reason": "news interval not reached"}
    _last_news_run_at = now

    raw = await _get_general_news()
    candidates, seen = [], set()
    current_ts = int(datetime.now(timezone.utc).timestamp())
    for raw_item in raw:
        item = _normalise(raw_item)
        if not item or not _is_relevant(item):
            continue
        if item["datetime"] and current_ts - item["datetime"] > 24 * 3600:
            continue
        key = item["url"] or item["headline"].lower()
        if key in seen or key in _sent_news_keys:
            continue
        seen.add(key)
        candidates.append(item)
        if len(candidates) >= 5:
            break
    if not candidates:
        return {"sent": False, "reason": "no new relevant news"}

    translated = await _translate_batch(candidates)
    by_id = {str(x.get("id")): x for x in translated}
    lines = [
        "📰 <b>SAS PRO | موجز أخبار السوق</b>", "",
        "🌙 <b>السوق الأمريكي في إجازة</b>",
        "أهم المستجدات المؤثرة في الأسواق خلال الساعات الأخيرة:", "",
    ]
    added = 0
    for i, item in enumerate(candidates, 1):
        tr = by_id.get(f"N{i}")
        if not tr:
            continue
        title = str(tr.get("title_ar") or "").strip()
        summary = str(tr.get("summary_ar") or "").strip()
        impact = str(tr.get("market_impact") or "الأثر غير واضح").strip()
        if not title or not summary:
            continue
        lines += [f"🔹 <b>{title}</b>", summary, f"📊 <b>الأثر المحتمل:</b> {impact}",
                  f"📰 المصدر: {item['source']}", ""]
        _sent_news_keys.add(item["url"] or item["headline"].lower())
        added += 1
    if not added:
        return {"sent": False, "reason": "translation unavailable"}
    lines += [
        "━━━━━━━━━━━━━━━━━━",
        f"🕐 {datetime.now(ZoneInfo('Asia/Riyadh')).strftime('%Y-%m-%d %H:%M')} بتوقيت السعودية",
        "",
        "⚡ <b>SAS PRO</b>",
    ]
    await send_message(settings.telegram_channel_id, "\n".join(lines))
    if len(_sent_news_keys) > 500:
        _sent_news_keys.clear()
    return {"sent": True, "news": added}
