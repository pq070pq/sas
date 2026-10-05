import asyncio
import html

from .market import quote
from .panwatch import technical_targets
from .news import company_news, corporate_events, company_fundamentals, tipranks_analysis
from .ai_radar import analyze_stock
from .smart_memory import dedupe_records


def _money(value):
    try:
        n = float(value)
        return "$" + f"{n:,.2f}"
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
    insiders = events.get("insider_transactions") if isinstance(events, dict) else []
    filings = events.get("filings") if isinstance(events, dict) else []

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

    if insiders:
        lines.append("👤 <b>معاملات المطلعين</b>")
        for item in insiders[:5]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("insider") or "مطلع"
            trans = item.get("transaction") or item.get("transactionType") or item.get("change") or "معاملة مسجلة"
            lines.append(f"• {html.escape(str(name))} — {html.escape(str(trans))} — {_event_date(item)}")

    if filings:
        lines.append("📑 <b>إفصاحات الشركة</b>")
        for item in filings[:5]:
            if not isinstance(item, dict):
                continue
            form = item.get("form") or item.get("formType") or "إفصاح"
            filed = item.get("fileDate") or item.get("filedDate") or _event_date(item)
            desc = item.get("description") or item.get("accessNumber") or ""
            lines.append(f"• {html.escape(str(form))} — {html.escape(str(filed))}" + (f" — {html.escape(str(desc)[:180])}" if desc else ""))

    material = events.get("material_events") if isinstance(events, dict) else []
    if material:
        lines.append("⚠️ <b>أحداث جوهرية أخرى</b>")
        for item in material[:8]:
            if not isinstance(item, dict):
                continue
            cats = " + ".join(str(x) for x in (item.get("category") or [])[:2])
            headline = html.escape(str(item.get("headline") or "").strip())
            if headline:
                lines.append(f"• <b>{html.escape(cats or 'حدث جوهري')}</b> — {headline}")

    return "\n".join(lines)


def _format_news(news):
    if not news:
        return "📰 <b>الأخبار:</b> لا توجد أخبار موثقة حديثة من المصادر المتاحة."
    lines = ["📰 <b>أهم الأخبار الأخيرة</b>"]
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


def _format_tipranks(tipranks, ai):
    summary = (ai or {}).get("tipranks_summary") or (tipranks or {}).get("summary")
    signal = (ai or {}).get("tipranks_signal") or (tipranks or {}).get("signal")
    if not summary and not signal:
        return ""
    return (
        "🌐 <b>TipRanks — ترجمة الذكاء الاصطناعي</b>\n"
        f"• <b>الخلاصة:</b> {html.escape(str(summary or 'غير واضح'))}\n"
        f"• <b>الإشارة:</b> {html.escape(str(signal or 'غير واضح'))}\n"
        "ℹ️ معلومة خارجية تفسيرية فقط؛ لا تحدد الدخول أو الوقف أو الأهداف."
    )

def _format_ai(ai):
    if not ai or not ai.get("enabled"):
        return "🧠 <b>تحليل الأخبار بالذكاء الاصطناعي:</b> غير متاح لعدم وجود خبر موثق صالح للتحليل."
    risks = ai.get("risk_flags") or []
    risk_text = "\n".join(f"• {x}" for x in risks) if risks else "• لا توجد مخاطر موثقة إضافية في الأدلة الخبرية."
    return (
        "🧠 <b>شرح الأخبار ببساطة</b>\n"
        f"• <b>الخلاصة:</b> {ai.get('key_takeaway') or 'غير واضح'}\n"
        f"• <b>ماذا حدث؟</b> {ai.get('headline_summary') or 'غير واضح'}\n"
        f"• <b>العلاقة بالحركة:</b> {ai.get('why_rising') or 'غير واضح'}\n"
        f"• <b>تقييم الخبر:</b> {ai.get('news_assessment') or 'غير واضح'}\n"
        f"• <b>الزخم:</b> {ai.get('momentum_assessment') or 'غير واضح'}\n"
        f"• <b>المخاطر:</b>\n{risk_text}\n"
        "🛡️ AI يفسّر الأدلة الموثقة فقط ولا يغيّر المستويات أو قرار الرادار."
    )



def _filter_relevant_news(news, symbol: str, company_name=None):
    """Filter provider leakage so unrelated companies do not appear in a symbol report."""
    key = str(symbol or "").upper().strip()
    name = str(company_name or "").strip()
    tokens = {key} if key else set()
    for token in name.replace("-", " ").replace("/", " ").split():
        clean = "".join(ch for ch in token if ch.isalnum())
        if len(clean) >= 4:
            tokens.add(clean.upper())
    tokens.difference_update({"CORPORATION", "COMPANY", "LIMITED", "HOLDINGS", "GROUP", "PLC", "INCORPORATED"})
    if not tokens:
        return [x for x in news if isinstance(x, dict) and x.get("headline")]
    return [
        x for x in news
        if isinstance(x, dict)
        and x.get("headline")
        and any(token in str(x.get("headline")).upper() for token in tokens)
    ]


def _evidence_summary(behavior, rvol, rsi, score, stop, target1):
    parts = [str(behavior)] if behavior else []
    if rvol is not None and rvol < 1:
        parts.append("الحجم دون متوسطه، لذلك التأكيد ضعيف")
    elif rvol is not None and rvol >= 1:
        parts.append("الحجم أعلى من متوسطه")
    try:
        if rsi is not None:
            rv = float(rsi)
            parts.append("RSI في المنطقة المحايدة" if 30 <= rv <= 70 else ("RSI في تشبع بيعي" if rv < 30 else "RSI في تشبع شرائي"))
    except (TypeError, ValueError):
        pass
    try:
        if score is not None and float(score) < 30:
            parts.append("قوة SAS Core محدودة")
    except (TypeError, ValueError):
        pass
    if stop is None or target1 is None:
        parts.append("لا توجد مستويات وقف وهدف موثوقة كافية لحساب R:R")
    return "، ".join(parts) + "." if parts else "لا توجد أدلة كافية لبناء خلاصة موثوقة."


async def build_private_analysis(symbol: str):
    symbol = symbol.upper().strip()
    async def bounded(coro, timeout, fallback):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return fallback

    quote_data, tech, events, news, fundamentals, tipranks = await asyncio.gather(
        bounded(quote(symbol), 8, {"symbol": symbol}),
        bounded(technical_targets(symbol), 10, {"status": "watch", "targets": []}),
        bounded(corporate_events(symbol), 10, {"splits": [], "earnings": [], "dividends": [], "material_events": []}),
        bounded(company_news(symbol, days=14), 8, []),
        bounded(company_fundamentals(symbol), 8, {}),
        bounded(tipranks_analysis(symbol), 10, {}),
    )

    def value(result, fallback):
        return fallback if isinstance(result, Exception) else result

    quote_data = value(quote_data, {"symbol": symbol})
    tech = value(tech, {"status": "error", "targets": []})
    events = value(events, {"splits": [], "earnings": [], "dividends": []})
    news = dedupe_records(value(news, []), ("headline", "url", "datetime"))
    fundamentals = value(fundamentals, {})
    tipranks = value(tipranks, {})
    # Normalize provider results so a transient/empty response cannot abort
    # the whole private report.
    if not isinstance(quote_data, dict):
        quote_data = {"symbol": symbol}
    if not isinstance(tech, dict):
        tech = {"status": "error", "targets": []}
    if not isinstance(events, dict):
        events = {"splits": [], "earnings": [], "dividends": []}
    if not isinstance(news, list):
        news = []
    if not isinstance(fundamentals, dict):
        fundamentals = {}
    if not isinstance(tipranks, dict):
        tipranks = {}

    news = _filter_relevant_news(news, symbol, fundamentals.get("name"))[:10]

    try:
        from .scanner import classify_sas
        classification = await asyncio.wait_for(
            classify_sas(symbol, quote_data, allow_twelve_fallback=True),
            timeout=12,
        )
    except Exception:
        classification = {}
    if not isinstance(classification, dict):
        classification = {}

    try:
        ai = await analyze_stock(
            symbol,
            company=fundamentals,
            news=news,
            fundamentals=fundamentals,
            market={"change_pct": quote_data.get("change_pct"), "classification": classification},
            tipranks=tipranks,
            events=events,
        ), timeout=15)
    except Exception:
        ai = {"enabled": False}
    if not isinstance(ai, dict):
        ai = {"enabled": False}

    price = quote_data.get("price")
    change = quote_data.get("change_pct")
    targets = tech.get("targets") or []
    stop = tech.get("exit") or tech.get("support")
    rvol = classification.get("rvol") if isinstance(classification, dict) else None
    if rvol is None:
        rvol = tech.get("volume_ratio")

    def _safe_float(value):
        try:
            n = float(value)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    entry = _safe_float(tech.get("price")) or _safe_float(price)
    stop_n = _safe_float(stop)
    target1_n = _safe_float(targets[0]) if targets else None
    risk = (entry - stop_n) if entry is not None and stop_n is not None else None
    reward = (target1_n - entry) if entry is not None and target1_n is not None else None
    risk_reward = (reward / risk) if risk and risk > 0 and reward and reward > 0 else None

    sas_pass = bool(classification.get("pass"))
    target_pass = bool(targets) and str(tech.get("status") or "ok").lower() == "ok"
    live_levels_pass = bool(entry and stop_n and target1_n and target1_n > entry and stop_n < entry)
    score = classification.get("score")
    stock_type = classification.get("type") or "غير محدد"
    behavior = classification.get("behavior") or "غير واضح"
    trading_style = classification.get("trading_style") or "غير محدد"
    risk_level = classification.get("risk_level") or "غير محدد"
    risk_score = classification.get("risk_score")
    risk_emoji = classification.get("risk_emoji") or "⚠️"
    holding_horizon = classification.get("holding_horizon")
    risk_reasons = classification.get("risk_reasons") or []
    source = quote_data.get("source") or "غير متوفر"

    target_text = "\n".join(
        f"   🎯 الهدف {i}: <b>{_money(level)}</b>"
        for i, level in enumerate(targets[:5], 1)
    ) if targets else "   ⚠️ لا يوجد هدف سعري مؤكد من مقاومة مرصودة."
    target1_text = _money(target1_n) if target1_n is not None else "غير متوفر"

    rr_text = f"1 : {risk_reward:.2f}" if risk_reward is not None else "غير محسوب"

    if risk_reward is not None:
        rr_eval = "🟢 مناسب" if risk_reward >= 1.5 else "🟠 منخفض — تحذير فقط"
        rr_block = (
            "⚖️ <b>المخاطرة مقابل العائد (R:R)</b>\n"
            f"   1 : <b>{risk_reward:.2f}</b>\n"
            f"   {rr_eval}\n"
            f"   📖 يعني ذلك: كل 1 وحدة مخاطرة مقابل {risk_reward:.2f} وحدة عائد محتمل عند الهدف الأول.\n"
            "   ℹ️ الحد المرجعي في SAS هو 1.5، وR:R تحذيري ولا يلغي الفرصة."
        )
    else:
        rr_block = ("⚖️ <b>المخاطرة مقابل العائد (R:R)</b>\n"
                    "   — غير محسوب\n"
                    "   ℹ️ لا توجد مستويات صالحة كافية لحسابه؛ لم يتم التخمين.")

    # عرض البيانات المالية المتاحة فقط حتى لا يبدو التقرير عشوائيًا أو ممتلئًا
    # بعبارات "غير متوفر". أي قيمة غير موثقة تُحذف من القائمة ولا يتم تخمينها.
    fundamental_fields = [
        ("🏢", "الشركة", fundamentals.get("name"), "text"),
        ("🏷️", "القطاع", fundamentals.get("industry"), "text"),
        ("💰", "القيمة السوقية", fundamentals.get("market_cap_m"), "market_cap"),
        ("📐", "P/E", fundamentals.get("pe_ttm"), "number"),
        ("🧮", "EPS", fundamentals.get("eps_ttm"), "number"),
        ("📈", "نمو الإيرادات (3 سنوات)", fundamentals.get("revenue_growth_3y"), "pct"),
        ("💵", "الهامش الصافي", fundamentals.get("net_margin"), "pct"),
        ("📊", "ROE", fundamentals.get("roe_ttm"), "pct"),
        ("🏦", "الدين/حقوق الملكية", fundamentals.get("debt_to_equity"), "number"),
    ]
    fundamental_lines = []
    for icon, label, raw, kind in fundamental_fields:
        if raw is None or raw == "":
            continue
        if kind == "market_cap":
            value_text = _num(raw, "M")
        elif kind == "pct":
            value_text = _pct(raw)
        elif kind == "number":
            value_text = _num(raw)
        else:
            value_text = html.escape(str(raw))
        fundamental_lines.append(f"   {icon} {label}: <b>{value_text}</b>")
    fundamentals_text = (
        "\n".join(fundamental_lines)
        if fundamental_lines
        else "   ℹ️ لا تتوفر بيانات مالية موثوقة من المصدر الحالي."
    )

    sas_status = "🟢 اجتاز SAS Core" if sas_pass else "🟠 لم يثبت اجتياز SAS Core"
    sas_reason = classification.get("reason") if isinstance(classification, dict) else None
    news_count = len(news) if isinstance(news, list) else 0
    corporate_text = _format_corporate_actions(events)
    tipranks_summary = str(ai.get("tipranks_summary") or "").strip()
    company_status = str(ai.get("company_status") or "غير واضح").strip()
    dilution_risk = str(ai.get("dilution_risk") or "غير واضح").strip()
    events_summary = str(ai.get("corporate_events_summary") or "").strip()

    # تقرير الخاص منظم على شكل ملف حالة متكامل للسهم.
 ومباشر وموحّد: لا نعرض مستوى تداول غير موثوق
    # ولا نملأ الحقول الناقصة بتخمينات.
    risk_text = f"{risk_emoji} {html.escape(str(risk_level))}"
    try:
        if risk_score is not None:
            risk_text += f" — {float(risk_score):.0f}/10"
    except (TypeError, ValueError):
        pass

    reasons_text = ""
    if risk_reasons:
        reasons_text = (
            "⚠️ <b>ملاحظات المخاطر:</b> "
            + html.escape(" + ".join(str(x) for x in risk_reasons))
            + "\n"
        )

    displayed_headlines = []
    if isinstance(news, list):
        for item in news[:5]:
            if isinstance(item, dict) and item.get("headline"):
                displayed_headlines.append("• " + html.escape(str(item.get("headline"))))
    news_count = len(displayed_headlines)
    news_text = ""
    if displayed_headlines:
        news_text = (
            "📰 <b>آخر الأخبار</b> "
            f"({news_count})\n"
            + "\n".join(displayed_headlines)
            + "\n"
        )

    rvol_text = _num(rvol, "×") if rvol is not None else "غير متوفر"
    rvol_note = ""
    rvol_n = None
    try:
        rvol_n = float(rvol) if rvol is not None else None
        if rvol_n is not None and rvol_n < 1:
            rvol_note = " — أقل من متوسط الحجم"
        elif rvol_n is not None and rvol_n > 1:
            rvol_note = " — فوق متوسط الحجم"
    except (TypeError, ValueError):
        rvol_n = None
    behavior_display = str(behavior)
    if "اختراق" in behavior_display and rvol_n is not None and rvol_n < 1:
        behavior_display = "اختراق تحت المراقبة"
    rsi14 = classification.get("rsi14") if isinstance(classification, dict) else None
    rsi_text = _num(rsi14) if rsi14 is not None else "غير متوفر"
    try:
        score_text = f"{float(score):.0f}/100" if score is not None else "غير متوفر"
    except (TypeError, ValueError):
        score_text = "غير متوفر"

    corporate_text = _format_corporate_actions(events)
    tipranks_text = _format_tipranks(tipranks, ai)
    ai_text = _format_ai(ai)

    news_lines = []
    for item in news[:8]:
        if not isinstance(item, dict) or not item.get("headline"):
            continue
        headline = html.escape(str(item.get("headline")))
        url = html.escape(str(item.get("url") or ""), quote=True)
        news_lines.append(f'<a href="{url}">• {headline}</a>' if url else f"• {headline}")
    news_text = "\n".join(news_lines) if news_lines else "• لا توجد أخبار موثقة حديثة."

    report = (
        f"🚀 <b>SAS PRO | التحليل الخاص — {html.escape(symbol)}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>الحالة الحالية</b>\n"
        f"💵 السعر: <b>{_money(price)}</b>   📈 التغير: <b>{_pct(change)}</b>\n"
        f"🧭 السلوك: <b>{html.escape(behavior_display)}</b>\n"
        f"📊 RVOL: <b>{rvol_text}{rvol_note}</b>   📉 RSI: <b>{rsi_text}</b>\n"
        f"⭐ SAS Core: <b>{score_text}</b>   ⚠️ المخاطرة: <b>{risk_text}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>الرصد الفني</b>\n"
        f"🔵 الدخول المرصود: <b>{_money(entry)}</b>\n"
        f"🛑 الوقف: <b>{_money(stop_n)}</b>\n"
        f"{target_text}\n"
        f"{rr_block}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📰 <b>أخبار السهم</b>\n"
        f"{news_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏛️ <b>أحداث وإجراءات الشركة</b>\n"
        f"{corporate_text or 'لا توجد أحداث موثقة إضافية.'}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>الوضع المالي المختصر</b>\n"
        f"{fundamentals_text}\n"
        + (f"━━━━━━━━━━━━━━━━━━\n{tipranks_text}\n" if tipranks_text else "")
        + f"━━━━━━━━━━━━━━━━━━\n{ai_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>الخلاصة:</b> {html.escape(_evidence_summary(behavior_display, rvol_n, rsi14, score, stop_n, target1_n))}\n"
        f"{('🧾 <b>سبب الحالة:</b> ' + html.escape(str(sas_reason)) + chr(10)) if sas_reason else ''}"
        "⚠️ <b>تنبيه:</b> معلومات تعليمية وإخبارية فقط، وليست توصية شراء أو بيع. قرار التداول وإدارة المخاطر مسؤولية المتداول."
    )
    return report.replace(chr(92) + "r" + chr(92) + "n", chr(10)).replace(chr(92) + "n", chr(10)).replace(chr(92) + "r", chr(13))
