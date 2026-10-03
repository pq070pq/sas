import asyncio
import html

from .market import quote
from .panwatch import technical_targets
from .news import company_news, corporate_events, company_fundamentals
from .ai_radar import analyze_stock


def _money(value):
    try:
        return "$" + f"{float(value):,.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "غير متوفر"


def _pct(value):
    try:
        n = float(value)
        return f"{n:+.2f}%"
    except (TypeError, ValueError):
        return "غير متوفر"


def _num(value, suffix=""):
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        return "غير متوفر"


def _event_date(row):
    for key in ("date", "fromDate", "toDate"):
        value = row.get(key) if isinstance(row, dict) else None
        if value:
            return str(value)
    return "غير محدد"


def _format_corporate_actions(events):
    lines = []
    splits = events.get("splits") if isinstance(events, dict) else []
    earnings = events.get("earnings") if isinstance(events, dict) else []
    dividends = events.get("dividends") if isinstance(events, dict) else []

    if splits:
        lines.append("🔄 <b>تقسيم/دمج أسهم</b>")
        for item in splits[:5]:
            if not isinstance(item, dict):
                continue
            ratio = item.get("ratio")
            action = "تقسيم/دمج"
            try:
                ratio_n = float(ratio)
                if ratio_n < 1:
                    action = "دمج أسهم (Reverse Split)"
                elif ratio_n > 1:
                    action = "تقسيم أسهم (Split)"
            except (TypeError, ValueError):
                pass
            ratio_text = f" بنسبة {ratio}" if ratio is not None else ""
            lines.append(f"• {action}{ratio_text} — {_event_date(item)}")
    else:
        lines.append("🔄 <b>تقسيم/دمج:</b> لا توجد عملية موثقة ضمن البيانات المتاحة.")

    if earnings:
        lines.append("📅 <b>الأرباح القادمة</b>")
        for item in earnings[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(f"• {_event_date(item)}")

    if dividends:
        lines.append("💰 <b>التوزيعات</b>")
        for item in dividends[:3]:
            if not isinstance(item, dict):
                continue
            amount = item.get("amount")
            lines.append(f"• {_event_date(item)} — {amount if amount is not None else 'بيانات التوزيع متاحة'}")

    return "\n".join(lines)


def _format_news(news):
    if not news:
        return "📰 <b>الأخبار:</b> لا توجد أخبار موثقة حديثة من المصادر المتاحة."
    lines = ["📰 <b>أحدث الأخبار</b>"]
    for item in news[:5]:
        if not isinstance(item, dict):
            continue
        headline = html.escape(str(item.get("headline") or "").strip())
        source = html.escape(str(item.get("source") or "").strip())
        url = html.escape(str(item.get("url") or "").strip(), quote=True)
        if not headline:
            continue
        if url:
            lines.append(f'• <a href="{url}">{headline}</a> — {source or "المصدر"}')
        else:
            lines.append(f"• {headline} — {source or 'المصدر'}")
    return "\n".join(lines)


def _format_ai(ai):
    if not ai or not ai.get("enabled"):
        return "🧠 <b>تحليل الأخبار بالذكاء الاصطناعي:</b> غير متاح لعدم وجود خبر موثق صالح للتحليل."
    risks = ai.get("risk_flags") or []
    risk_text = "\n".join(f"• {x}" for x in risks) if risks else "• لا توجد مخاطر موثقة إضافية في الأدلة الخبرية."
    return (
        "🧠 <b>زبدة تحليل الأخبار</b>\n"
        f"• <b>الخلاصة:</b> {ai.get('key_takeaway') or 'غير واضح'}\n"
        f"• <b>ماذا حدث؟</b> {ai.get('headline_summary') or 'غير واضح'}\n"
        f"• <b>العلاقة بالحركة:</b> {ai.get('why_rising') or 'غير واضح'}\n"
        f"• <b>تقييم الخبر:</b> {ai.get('news_assessment') or 'غير واضح'}\n"
        f"• <b>الزخم:</b> {ai.get('momentum_assessment') or 'غير واضح'}\n"
        f"• <b>المخاطر:</b>\n{risk_text}\n"
        "🛡️ AI يفسّر الأدلة الموثقة فقط ولا يغيّر المستويات أو قرار الرادار."
    )


async def build_private_analysis(symbol: str):
    symbol = symbol.upper().strip()
    quote_data, tech, events, news, fundamentals = await asyncio.gather(
        quote(symbol),
        technical_targets(symbol),
        corporate_events(symbol),
        company_news(symbol, days=3),
        company_fundamentals(symbol),
        return_exceptions=True,
    )

    def value(result, fallback):
        return fallback if isinstance(result, Exception) else result

    quote_data = value(quote_data, {"symbol": symbol})
    tech = value(tech, {"status": "error", "targets": []})
    events = value(events, {"splits": [], "earnings": [], "dividends": []})
    news = value(news, [])
    fundamentals = value(fundamentals, {})

    try:
        from .scanner import classify_faisal
        classification = await classify_faisal(symbol, quote_data)
    except Exception:
        classification = {}

    try:
        ai = await analyze_stock(
            symbol,
            company=fundamentals,
            news=news,
            fundamentals=fundamentals,
            market={"change_pct": quote_data.get("change_pct"), "classification": classification},
        )
    except Exception:
        ai = {"enabled": False}

    price = quote_data.get("price")
    change = quote_data.get("change_pct")
    targets = tech.get("targets") or []
    stop = tech.get("exit") or tech.get("support")
    rvol = classification.get("rvol") if isinstance(classification, dict) else None
    if rvol is None:
        rvol = tech.get("volume_ratio")

    target_text = "\n".join(
        f"• الهدف {i}: <b>{_money(level)}</b>"
        for i, level in enumerate(targets[:5], 1)
    ) or "• لا يوجد هدف سعري مؤكد من مقاومة مرصودة."

    # حساب R:R من المستويات الفعلية نفسها، بدون اختراع هدف أو وقف.
    entry = float(tech.get("price") or price or 0) if str(tech.get("price") or price or "").replace(".", "", 1).isdigit() else 0
    try:
        stop_n = float(stop) if stop is not None else 0
        target1_n = float(targets[0]) if targets else 0
    except (TypeError, ValueError):
        stop_n, target1_n = 0, 0
    risk = entry - stop_n
    reward = target1_n - entry
    risk_reward = (reward / risk) if risk > 0 and reward > 0 else None

    sas_pass = bool(classification.get("pass"))
    target_pass = bool(targets) and str(tech.get("status") or "ok").lower() == "ok"
    live_levels_pass = entry > 0 and stop_n > 0 and target1_n > entry
    sas_status = "اجتاز شروط SAS" if sas_pass else "لم يثبت اجتياز شروط SAS"
    rr_block = (
        f"⚖️ <b>المخاطرة مقابل العائد (R:R)</b>\n"
        f"1 : {risk_reward:.2f}\n"
        f"{'🟢 <b>التقييم: مناسبة</b>' if risk_reward >= 1.5 else '🟠 <b>التقييم: منخفضة — تحذير فقط</b>'}\n"
        f"📖 <b>المعنى:</b> مقابل كل 1 وحدة مخاطرة، يوجد عائد محتمل قدره {risk_reward:.2f} وحدة عند الهدف الأول."
        if risk_reward is not None
        else "⚖️ <b>المخاطرة مقابل العائد (R:R)</b>\nغير محسوبة\nℹ️ <b>التقييم: غير متوفر</b>"
    )

    fundamentals_text = (
        f"• الشركة: {fundamentals.get('name') or symbol}\n"
        f"• القطاع: {fundamentals.get('industry') or 'غير متوفر'}\n"
        f"• القيمة السوقية: {_num(fundamentals.get('market_cap_m'), 'M')}\n"
        f"• P/E: {_num(fundamentals.get('pe_ttm'))}\n"
        f"• EPS: {_num(fundamentals.get('eps_ttm'))}\n"
        f"• نمو الإيرادات 3 سنوات: {_pct(fundamentals.get('revenue_growth_3y'))}\n"
        f"• الهامش الصافي: {_pct(fundamentals.get('net_margin'))}\n"
        f"• ROE: {_pct(fundamentals.get('roe_ttm'))}\n"
        f"• الدين/حقوق الملكية: {_num(fundamentals.get('debt_to_equity'))}"
    )

    return (
        f"🔎 <b>SAS PRO — تحليل خاص للسهم {symbol}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>السعر:</b> {_money(price)}\n"
        f"📈 <b>التغير:</b> {_pct(change)}\n"
        f"📊 <b>RVOL/حجم:</b> {_num(rvol, '×')}\n"
        f"🏷️ <b>المصدر:</b> {quote_data.get('source') or 'غير متوفر'}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>المستويات الفنية</b>\n"
        f"• الدخول المرجعي: <b>{_money(tech.get('price') or price)}</b>\n"
        f"• الوقف/الدعم: <b>{_money(stop)}</b>\n"
        f"{target_text}\n"
        f"• ATR: <b>{_money(tech.get('atr'))}</b>\n"
        f"{rr_block}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>شروط SAS</b>\n"
        f"🏷️ <b>نوع الرصد:</b> {classification.get('section') or classification.get('type') or 'غير محدد'}\n"
        f"{'✅' if sas_pass else '⚠️'} <b>SAS Core:</b> {'مستوفى' if sas_pass else 'غير مستوفى'}\n"
        f"{'✅' if target_pass else '⚠️'} <b>الهدف السعري:</b> {'مؤكد' if target_pass else 'غير مؤكد'}\n"
        f"{'✅' if live_levels_pass else '⚠️'} <b>المستويات الحية:</b> {'الدخول/الوقف/الهدف صالحة' if live_levels_pass else 'غير مكتملة'}\n"
        f"📊 <b>RVOL:</b> {_num(rvol, '×')}\n"
        f"⭐ <b>النتيجة:</b> {classification.get('score') if classification.get('score') is not None else 'غير متوفر'}\n"
        f"📍 <b>الحالة:</b> {'🟢 ' + sas_status if sas_pass else '🟠 ' + sas_status}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💼 <b>البيانات المالية</b>\n"
        f"{fundamentals_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{_format_corporate_actions(events)}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{_format_news(news)}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{_format_ai(ai)}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>هذا التقرير معلوماتي وتعليمي فقط، وليس توصية شراء أو بيع. قرار التداول وإدارة المخاطر مسؤولية المتداول.</b>"
    )
