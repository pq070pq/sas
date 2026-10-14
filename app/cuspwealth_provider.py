"""CUSP Wealth public asset-page adapter.

Uses only fields explicitly present in the public asset page. It is not an
official CUSP API and does not invent quote changes or missing metrics.
"""
import asyncio
import html
import re
import time
from html.parser import HTMLParser

import httpx

_CACHE = {}
_CACHE_TTL = 300


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        elif not self.skip and tag.lower() in {"br", "p", "div", "li", "h1", "h2", "h3", "tr", "td", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "tr", "td", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip and data and data.strip():
            self.parts.append(data.strip())


def _clean(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _number_token(text):
    match = re.search(r"(?<![A-Za-z])([+-]?\d[\d,]*(?:\.\d+)?)", text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _value_after(text, labels, window=180):
    for label in labels:
        match = re.search(re.escape(label), text, re.I)
        if not match:
            continue
        segment = text[match.end():match.end() + window]
        segment = re.sub(r"^[\s:：|—–-]+", "", segment)
        for line in segment.splitlines():
            candidate = _clean(line)
            if candidate and candidate.casefold() not in {str(label).casefold(), "—", "-"}:
                return candidate[:120]
    return None


def _numeric_after(text, labels, window=120):
    value = _value_after(text, labels, window)
    return _number_token(value) if value else None


def _first_currency_number(text):
    # The first currency value after the ticker is the primary asset-page quote.
    matches = list(re.finditer(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:US\$|USD)", text, re.I))
    for match in matches:
        try:
            value = float(match.group(1).replace(",", ""))
            if value > 0:
                return value
        except ValueError:
            continue
    return None


def parse_asset_page(symbol, document, source_url):
    parser = _VisibleText()
    parser.feed(document[:2_500_000])
    text = html.unescape("\n".join(parser.parts))
    # Keep block boundaries so label/value pairs remain separable.
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    lines = [re.sub(r"\s+", " ", html.unescape(x)).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    plain = "\n".join(lines)

    title_match = re.search(r"<title[^>]*>(.*?)</title>", document, re.I | re.S)
    title = _clean(re.sub(r"<[^>]+>", " ", title_match.group(1))) if title_match else ""
    # Prefer a price close to the asset ticker; never use valuation or target price as quote.
    ticker_pos = plain.upper().find("\n" + symbol.upper() + "\n")
    quote_region = plain[ticker_pos + len(symbol) + 1:] if ticker_pos >= 0 else plain
    quote_region = quote_region[:350]
    price = _first_currency_number(quote_region)

    def value(labels, window=150):
        return _value_after(plain, labels, window)

    def numeric(labels, window=150):
        return _numeric_after(plain, labels, window)

    market_status = None
    for status in ("مفتوح", "مغلق", "Open", "Closed"):
        if re.search(r"(?<!\w)" + re.escape(status) + r"(?!\w)", quote_region, re.I):
            market_status = status
            break

    valuation_score_match = re.search(r"(?:^|\n)التقييم\s*\n\s*(\d{1,3})\s*\n", plain, re.I)
    valuation_score = valuation_score_match.group(1) if valuation_score_match else None
    metrics = {
        "opening_price": value(["سعر الافتتاح", "Open price"]),
        "volume": value(["حجم التداول", "Trading volume", "Volume"]),
        "market_cap": value(["القيمة السوقية الإجمالية", "Market cap", "Market capitalization"]),
        "previous_close": value(["سعر إغلاق اليوم السابق", "Previous close"]),
        "day_range": value(["نطاق تداول اليوم", "Day range"]),
        "week_52_range": value(["نطاق 52 أسبوعًا", "52-week range"]),
        "valuation_score": valuation_score,
        "valuation_label": value(["نتيجة التقييم", "Valuation score"]),
        "pe_ratio": value(["نسبة السعر إلى الأرباح", "Price to earnings ratio", "P/E ratio"]),
        "price_sales": value(["نسبة السعر إلى المبيعات", "Price to sales ratio"]),
        "price_book": value(["نسبة السعر إلى القيمة الدفترية", "Price to book ratio"]),
        "current_ratio": value(["النسبة الحالية", "Current ratio"]),
        "debt_to_equity": value(["نسبة الدين إلى حقوق الملكية", "Debt to equity"]),
        "roe": value(["العائد على حقوق الملكية", "Return on equity"]),
        "gross_margin": value(["إجمالي الهامش الربحي", "Gross margin"]),
        "income_growth": value(["نمو الدخل", "Income growth"]),
        "fcf_growth": value(["نمو التدفق النقدي الحر", "Free cash flow growth"]),
        "analyst_consensus": value(["تقديرات إجماع المحللين", "Analyst consensus"]),
        "analyst_target": value(["متوسط السعر المستهدف", "Average price target"]),
        "quarterly_revenue": value(["إيرادات الربع", "Quarterly revenue", "Revenue for quarter"]),
        "net_income": value(["صافي الدخل", "Net income"]),
        "eps_actual": value(["ربحية السهم المُحققة", "Actual EPS"]),
        "eps_estimate": value(["ربحية السهم المقدرة", "Estimated EPS"]),
    }
    # Remove values that are just labels/noise, preserving the fact that unavailable data is missing.
    metrics = {key: val for key, val in metrics.items() if val and val not in {"-", "—", "غير متاح", "N/A"}}
    description_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', document, re.I)
    description = _clean(description_match.group(1)) if description_match else ""
    change_pct = None
    # The detailed CUSP page often displays "-(-)" when change is not published.
    # Only extract an explicit percentage from the small quote block near its price.
    change_match = re.search(r"(?:US\$|USD)[^\n]{0,100}?([+-]?[0-9]+(?:\.[0-9]+)?)\s*%", quote_region, re.I)
    if change_match:
        try:
            change_pct = float(change_match.group(1))
        except ValueError:
            pass

    available = bool(title or price or metrics or description)
    return {
        "symbol": symbol.upper(),
        "available": available,
        "source": "CUSP Wealth",
        "source_url": source_url,
        "title": title[:240] or symbol.upper(),
        "description": description[:1200],
        "price": price,
        "change_pct": change_pct,
        "market_status": market_status,
        "metrics": metrics,
        "text_excerpt": " ".join(lines[:35])[:1800] if available else "",
        "message_ar": "بيانات بحثية مستخرجة من صفحة CUSP Wealth. الحقول غير المنشورة تُترك فارغة ولا تُقدّر.",
    }


async def get_cusp_asset(symbol):
    symbol = re.sub(r"[^A-Z0-9.\-]", "", str(symbol or "").upper().strip())
    if not symbol or len(symbol) > 15:
        return {"symbol": symbol, "available": False, "source": "CUSP Wealth", "message_ar": "رمز سهم غير صالح."}
    now = time.monotonic()
    cached = _CACHE.get(symbol)
    if cached and now - cached[0] < _CACHE_TTL:
        return dict(cached[1])
    url = f"https://cuspwealth.com/ar/asset/{symbol}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SASPRO/2.2; +https://cuspwealth.com)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                result = {"symbol": symbol, "available": False, "source": "CUSP Wealth",
                          "source_url": url, "http_status": response.status_code,
                          "message_ar": "صفحة السهم غير متاحة من CUSP حاليًا."}
            else:
                result = parse_asset_page(symbol, response.text, str(response.url))
        _CACHE[symbol] = (time.monotonic(), dict(result))
        return result
    except Exception as exc:
        return {"symbol": symbol, "available": False, "source": "CUSP Wealth",
                "source_url": url, "message_ar": "تعذر تحميل بيانات CUSP الآن.",
                "error": type(exc).__name__}
