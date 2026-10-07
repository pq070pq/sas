import json
import re
import asyncio
from datetime import datetime
import httpx

from .config import settings

YAAQEEN_URL = "https://yaaqen.com/stocks/{symbol}"
ZOYA_URL = "https://api.zoya.finance/graphql"

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

async def _zoya(symbol):
    if not settings.zoya_api_key:
        return {"source":"Zoya","status":None,"status_ar":"غير مفعّل","error":"Zoya API key غير موجود"}
    query = """query GetReport($symbol: String!) {
      basicCompliance { report(symbol: $symbol) {
        symbol name exchange status purificationRatio reportDate
      }}
    }"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(ZOYA_URL, json={"query":query,"variables":{"symbol":symbol.upper()}},
                                  headers={"Authorization":f"Bearer {settings.zoya_api_key}"})
            r.raise_for_status()
            data = r.json()
        report = (((data.get("data") or {}).get("basicCompliance") or {}).get("report"))
        status = _normalize((report or {}).get("status"))
        return {"source":"Zoya","methodology":"AAOIFI","status":status,
                "status_ar":{"compliant":"شرعي","non_compliant":"غير شرعي","doubtful":"محل نظر"}.get(status,"غير واضح"),
                "updated_at":(report or {}).get("reportDate"),
                "purification_ratio":(report or {}).get("purificationRatio")}
    except Exception as e:
        return {"source":"Zoya","status":None,"status_ar":"غير متاح","error":str(e)}

async def check_shariah(symbol):
    symbol = str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z])?", symbol):
        return {"symbol": symbol, "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق",
                "verified": False, "sources": [], "message": "رمز غير صالح؛ لم يتم تأليف أي نتيجة."}

    async def fetch_yaaqeen():
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                         headers={"User-Agent":"SAS-PRO-Shariah/1.0"}) as client:
                r = await client.get(YAAQEEN_URL.format(symbol=symbol))
                r.raise_for_status()
                text = re.sub(r"<[^>]+>", " ", r.text)
                text = re.sub(r"\s+", " ", text)
                pos = text.find("توافق الشريعة")
                window = text[pos:pos+1600] if pos >= 0 else text[:2500]
                # صفحة يقين تعرض الحكم مباشرة ضمن قسم التوافق الشرعي.
                m = re.search(r"(غير\\s+شرعي|محل\\s+نظر|شرعي)", window)
                status = _normalize(m.group(1) if m else None)
                date_m = re.search(r"تم التحديث بتاريخ\s*([0-9]{2}-[0-9]{2}-[0-9]{4})", text)
                label = {"compliant":"شرعي","non_compliant":"غير شرعي","doubtful":"محل نظر"}.get(status,"غير واضح / يحتاج تحقق")
                return {"source":"يقين","status":status,"status_ar":label,
                        "verified":bool(status),"updated_at":date_m.group(1) if date_m else None,
                        "reason":("نتيجة منشورة مباشرة من صفحة السهم في يقين." if status else "لم يتم العثور على حكم مباشر في الصفحة."),
                        "methodology":"معايير شرعية منشورة وفق معايير الراجحي","url":YAAQEEN_URL.format(symbol=symbol)}

    async def fetch_filterna():
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                         headers={"User-Agent":"SAS-PRO-Shariah/1.0"}) as client:
                r = await client.get("https://filterna.com/")
                r.raise_for_status()
                text = re.sub(r"<[^>]+>", " ", r.text)
                method = "القرار رقم ( 485 )" in text or "القرار رقم (485)" in text
                return {"source":"فلترنا","status":None,"status_ar":"غير واضح / يحتاج تحقق","verified":False,
                        "methodology_verified":method,
                        "reason":"نتيجة السهم المباشرة غير متاحة كبيانات عامة قابلة للتحقق؛ لم يتم تأليف نتيجة.",
                        "methodology":"فلترنا يذكر اعتماده على ضوابط قرار الهيئة الشرعية للراجحي رقم 485 وتعديله.",
                        "url":"https://filterna.com/"}
        except Exception as e:
            return {"source":"فلترنا","status":None,"status_ar":"غير متاح","verified":False,"error":type(e).__name__}

    direct = await asyncio.gather(fetch_yaaqeen(), fetch_filterna())
    refs = [
        {"source":"مصرف الراجحي","role":"مرجع منهجي","status":"reference","status_ar":"مرجع","verified":True,
         "reason":"لا ننسب للراجحي حكم سهم محدد دون نتيجة منشورة قابلة للتحقق.",
         "url":"https://www.alrajhibank.com.sa/About-alrajhi-bank/Shariah-Group"},
        {"source":"بنك البلاد","role":"مرجع منهجي","status":"reference","status_ar":"مرجع","verified":True,
         "reason":"لا ننسب لبنك البلاد حكم سهم محدد دون نتيجة منشورة قابلة للتحقق.",
         "url":"https://www.bankalbilad.com/"}
    ]
    usable = [x.get("status") for x in direct if x.get("verified") and x.get("status")]
    unique = set(usable)
    if len(unique) == 1:
        final = next(iter(unique)); message = "النتيجة المباشرة المتاحة واضحة."
    elif len(unique) > 1:
        final = "unclear"; message = "يوجد تعارض بين النتائج المباشرة؛ يحتاج تحقق."
    else:
        final = "unclear"; message = "لا توجد نتيجة مباشرة موثقة كافية؛ لم يتم تأليف الحكم."

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
            async with httpx.AsyncClient(timeout=settings.ai_radar_timeout_seconds) as client:
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
