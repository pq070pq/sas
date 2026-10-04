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
        from .scanner import classify_sas
        classification = await classify_sas(symbol, quote_data, allow_twelve_fallback=True)
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
    source = quote_data.get("source") or "غير متوفر"

    target_text = "\n".join(
        f"   🎯 الهدف {i}: <b>{_money(level)}</b>"
        for i, level in enumerate(targets[:5], 1)
    ) if targets else "   ⚠️ لا يوجد هدف سعري مؤكد من مقاومة مرصودة."

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
    report = (
        f"🔎 <b>SAS PRO | تحليل السهم: {html.escape(symbol)}</b>\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "📋 <b>الملخص للمتداول</b>\\n"
        f"💵 السعر: <b>{_money(price)}</b>   📈 التغير: <b>{_pct(change)}</b>\\n"
        f"🧭 الحالة الفنية: <b>{html.escape(str(behavior))}</b>\\n"
        f"🏷️ التصنيف: <b>{html.escape(str(stock_type))}</b>\\n"
        f"📡 مصدر السعر: <b>{html.escape(str(source))}</b>\\n"
        "ℹ️ هذا القسم يوضح وضع السهم الآن قبل الدخول في التفاصيل.\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "📌 <b>حكم شروط SAS</b>\\n"
        f"{'🟢' if sas_pass else '🟠'} <b>SAS Core:</b> {html.escape(sas_status.replace('🟢 ','').replace('🟠 ',''))}\\n"
        f"⭐ <b>النتيجة:</b> {score if score is not None else 'غير متوفرة'}\\n"
        f"📊 <b>RVOL:</b> {_num(rvol, '×')}\\n"
        f"{'🧾 <b>سبب الحالة:</b> ' + html.escape(str(sas_reason)) + chr(10) if sas_reason else ''}"
        f"{'🟢' if target_pass else '🟠'} <b>الهدف السعري:</b> {'مؤكد من البيانات الفنية' if target_pass else 'غير مؤكد'}\\n"
        f"{'🟢' if live_levels_pass else '🟠'} <b>المستويات:</b> {'الدخول والوقف والهدف صالحة' if live_levels_pass else 'تحتاج بيانات إضافية'}\\n"
        "💡 <b>للمبتدئ:</b> اجتياز SAS Core يعني أن البوابة الفنية الأساسية تحققت؛ لا يعني ذلك ضمان صعود السهم.\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "🎯 <b>المستويات الفنية</b>\\n"
        f"🟦 <b>الدخول المرجعي:</b> {_money(entry)}\\n"
        f"🛑 <b>الوقف / الدعم:</b> {_money(stop_n)}\\n"
        f"{target_text}\\n"
        f"📏 <b>ATR:</b> {_money(tech.get('atr'))}\\n"
        "📖 <b>كيف تقرأها؟</b> الدخول هو السعر المرجعي، الوقف مستوى حماية، والأهداف مستويات صعود محتملة وليست ضمانًا.\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        f"{rr_block}\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "💼 <b>البيانات المالية</b>\\n"
        f"{fundamentals_text}\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "🔄 <b>الأحداث المؤثرة</b>\\n"
        f"{_format_corporate_actions(events)}\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        f"{_format_news(news)}\\n"
        f"📚 <b>عدد الأخبار المعروضة:</b> {news_count}\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        f"{_format_ai(ai)}\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "🧠 <b>كيف يقرأ المبتدئ التقرير؟</b>\\n"
        "1️⃣ ابدأ بالسعر والتغير لمعرفة وضع السهم.\\n"
        "2️⃣ راجع SAS Core وسبب الحالة بدل الاعتماد على النتيجة وحدها.\\n"
        "3️⃣ راجع الدخول والوقف والهدف ثم R:R قبل اتخاذ أي قرار.\\n"
        "4️⃣ راجع الأخبار والأحداث لمعرفة وجود محفز أو مخاطرة إضافية.\\n"
        "5️⃣ أي خانة غير متوفرة تعني أن المصدر لم يقدم بيانات موثوقة؛ لم يتم تخمينها.\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "⚠️ <b>تنبيه:</b> التقرير معلوماتي وتعليمي فقط، وليس توصية شراء أو بيع. قرار التداول وإدارة المخاطر مسؤولية المتداول."
    )
    # بعض أجزاء التقرير تُبنى بفواصل أسطر مكتوبة كنص حرفي \\n.
    # نحولها قبل الإرسال إلى Telegram إلى فواصل أسطر فعلية.
    return report.replace(chr(92) + "r" + chr(92) + "n", chr(10)).replace(chr(92) + "n", chr(10)).replace(chr(92) + "r", chr(13))
