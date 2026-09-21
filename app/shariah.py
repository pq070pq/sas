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
    symbol = symbol.upper().strip()
    providers = await asyncio.gather(_yaaqeen(symbol), _zoya(symbol))
    usable = [p for p in providers if p.get("status")]
    statuses = {p["status"] for p in usable}
    if len(statuses) == 1 and len(usable) >= 2:
        final, confidence = next(iter(statuses)), "مصدران متفقان"
    elif len(statuses) == 1:
        final, confidence = next(iter(statuses)), "مصدر واحد فقط"
    else:
        final, confidence = "unclear", "المصادر مختلفة" if len(statuses) > 1 else "لا توجد نتيجة موثوقة"
    return {
        "symbol":symbol,
        "status":final,
        "status_ar":{"compliant":"شرعي","non_compliant":"غير شرعي","doubtful":"محل نظر","unclear":"غير واضح / يحتاج تحقق"}.get(final,"غير واضح / يحتاج تحقق"),
        "confidence":confidence,
        "sources":providers,
        "checked_at":datetime.utcnow().isoformat()+"Z",
        "note":"فحص معلوماتي متعدد المصادر وليس فتوى شرعية؛ عند اختلاف المصادر لا يتم اعتماد السهم كمتوافق."
    }
