import json
import re
import asyncio
from datetime import datetime
import httpx

from .config import settings

YAAQEEN_URL = "https://yaaqen.com/stocks/{symbol}"

def _normalize(value):
    if not value:
        return None
    v = str(value).strip().lower()
    if v in {"halal", "compliant", "shariah_compliant", "شرعي", "متوافق"}:
        return "compliant"
    if v in {"not_halal", "non_compliant", "غير شرعي", "غير متوافق"}:
        return "non_compliant"
    if v in {"doubtful", "questionable", "محل نظر", "مشبوه"}:
        return "doubtful"
    return None

async def _yaaqeen(symbol):
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(YAAQEEN_URL.format(symbol=symbol.upper()))
            r.raise_for_status()
            html = r.text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        m = re.search(r"توافق الشريعة.*?(شرعي|غير شرعي|محل نظر)", text)
        status = _normalize(m.group(1) if m else None)
        date_m = re.search(r"تم التحديث بتاريخ\s*([0-9]{2}-[0-9]{2}-[0-9]{4})", text)
        return {"source":"يقين","methodology":"معايير منسوبة للجنة الراجحي","status":status,
                "status_ar":{"compliant":"شرعي","non_compliant":"غير شرعي","doubtful":"محل نظر"}.get(status,"غير واضح"),
                "updated_at":date_m.group(1) if date_m else None}
    except Exception as e:
        return {"source":"يقين","status":None,"status_ar":"غير متاح","error":str(e)}

def _parse_yaaqeen_html(html, symbol):
    """Parse the directly published Yaqeen verdict robustly; never infer a missing verdict."""
    # Preserve spacing between tags and decode HTML entities before matching.
    from html import unescape
    text = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    text = " ".join(text.split())

    # Prefer the small section following the methodology heading. Yaqeen can
    # change wrappers/markup, so use a fallback search for its exact verdict
    # labels rather than requiring a fixed 2,000-character window.
    pos = text.find("توافق الشريعة")
    window = text[pos:pos + 5000] if pos >= 0 else text
    verdict = None
    patterns = (
        (r"غير\s*شرعي", "non_compliant"),
        (r"محل\s*نظر", "doubtful"),
        (r"متوافق\s+مع\s+الشريعة", "compliant"),
        (r"(?<!غير\s)\bشرعي\b", "compliant"),
    )
    matches = []
    for pattern, status in patterns:
        match = re.search(pattern, window)
        if match:
            matches.append((match.start(), status))
    if matches:
        # The first verdict near the heading is the published status; do not
        # accidentally use later methodology text describing other categories.
        verdict = min(matches, key=lambda item: item[0])[1]

    date_m = re.search(r"تم التحديث بتاريخ\s*([0-9]{2}-[0-9]{2}-[0-9]{4})", text)
    label = {
        "compliant": "شرعي",
        "non_compliant": "غير شرعي",
        "doubtful": "محل نظر",
    }.get(verdict, "غير واضح / يحتاج تحقق")
    return {
        "source": "يقين",
        "status": verdict,
        "status_ar": label,
        "verified": bool(verdict),
        "updated_at": date_m.group(1) if date_m else None,
        "reason": (
            "نتيجة منشورة مباشرة من صفحة السهم في يقين."
            if verdict else
            "لم يتم العثور على حكم مباشر في قسم التوافق الشرعي."
        ),
        "methodology": "معايير شرعية منشورة وفق معايير الراجحي",
        "url": YAAQEEN_URL.format(symbol=symbol),
    }


async def check_shariah(symbol):
    symbol = str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z])?", symbol):
        return {"symbol": symbol, "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق",
                "verified": False, "sources": [], "message": "رمز غير صالح؛ لم يتم تأليف أي نتيجة."}

    async def fetch_yaaqeen():
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                         headers={"User-Agent":"SAS-PRO-Shariah/1.0"}) as client:
                r = await client.get(YAAQEEN_URL.format(symbol=symbol))
                r.raise_for_status()
                return _parse_yaaqeen_html(r.text, symbol)
        except Exception as e:
            return {"source":"يقين","status":None,"status_ar":"غير متاح","verified":False,
                    "reason":"تعذر الوصول إلى صفحة يقين الآن؛ لم يتم تأليف أي نتيجة.","error":type(e).__name__,
                    "url":YAAQEEN_URL.format(symbol=symbol)}

    # Check independent direct sources in parallel; unavailable sources never count as a verdict.
    direct = await asyncio.gather(fetch_yaaqeen())
    direct = [item for item in direct if item is not None]
    refs = []
    usable = [x.get("status") for x in direct if x.get("verified") and x.get("status") in {"compliant", "non_compliant", "doubtful"}]
    unique = set(usable)
    if len(unique) == 1:
        final = next(iter(unique))
        message = "النتائج المباشرة المتاحة متوافقة على الحالة المنشورة."
    elif len(unique) > 1:
        final = "unclear"
        message = "يوجد اختلاف بين النتائج المباشرة؛ الحالة غير محسومة ولا يتم التخمين."
    else:
        final = "unclear"
        message = "لا توجد نتيجة مباشرة موثقة كافية لهذا الرمز؛ لم يتم تأليف الحكم."

    ai = {"enabled":False,"status":"no_provider"}
    key = settings.gemini_api_key or settings.groq_api_key
    if key:
        base = settings.gemini_base_url.rstrip("/") if settings.gemini_api_key else settings.groq_base_url.rstrip("/")
        model = settings.gemini_model if settings.gemini_api_key else settings.groq_model
        provider = "Gemini" if settings.gemini_api_key else "Groq"
        evidence = [{"source":x.get("source"),"status":x.get("status"),"status_ar":x.get("status_ar"),"verified":x.get("verified"),"updated_at":x.get("updated_at")} for x in direct]
        prompt = ("للسهم "+symbol+" فسّر الأدلة فقط. لا تصدر فتوى ولا تخترع نتيجة. "
                  "إذا لم توجد نتيجة موثقة فقل غير واضح / يحتاج تحقق. إذا اختلفت المصادر اذكر التعارض. "
                  "أعد JSON بالمفاتيح summary,confidence,conflict.\\n"+json.dumps(evidence,ensure_ascii=False))
        try:
            async with httpx.AsyncClient(timeout=min(float(settings.ai_radar_timeout_seconds), 2.5)) as client:
                rr=await client.post(f"{base}/chat/completions",
                    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                    json={"model":model,"temperature":0,"max_tokens":180,
                          "messages":[{"role":"system","content":"Return valid JSON only."},{"role":"user","content":prompt}]})
                rr.raise_for_status()
                content=((((rr.json().get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
                try: parsed=json.loads(content)
                except Exception: parsed={"summary":content[:400],"confidence":"غير واضح","conflict":False}
                ai={"enabled":True,"provider":provider,"status":"ok",**parsed}
        except Exception as e:
            ai={"enabled":False,"status":"provider_error","error":type(e).__name__}

    return {"symbol":symbol,"status":final,"status_ar":{"compliant":"شرعي","non_compliant":"غير شرعي","doubtful":"محل نظر","unclear":"غير واضح / يحتاج تحقق"}.get(final,"غير واضح / يحتاج تحقق"),
            "verified":final in {"compliant","non_compliant","doubtful"},"confidence":"مصدر مباشر متاح" if usable else "غير واضح",
            "message":message,"sources":[*refs,*direct],"ai":ai,
            "note":"تحقق معلوماتي متعدد المصادر وليس فتوى؛ لا يتم اعتماد السهم كشرعي عند غياب أو تعارض الأدلة."}
