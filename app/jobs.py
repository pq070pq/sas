import asyncio
import json
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import SessionLocal, Subscription, RadarSignal, ScheduledReport
from .telegram import send_message, bot_api
from .holiday_radar import publish_holiday_radar
from .timeutil import utcnow, aware
from zoneinfo import ZoneInfo
from .market_calendar import market_status

async def expiry_cycle():
    now = utcnow()
    horizon = now + timedelta(hours=settings.expiry_warning_hours)
    async with SessionLocal() as db:
        rows = (await db.execute(select(Subscription).where(Subscription.active == True))).scalars().all()
        for sub in rows:
            if aware(sub.expires_at) <= now:
                sub.active = False
                if settings.telegram_channel_id:
                    try:
                        await bot_api("banChatMember", {
                            "chat_id": settings.telegram_channel_id,
                            "user_id": sub.telegram_id,
                            "revoke_messages": False,
                        })
                        await bot_api("unbanChatMember", {
                            "chat_id": settings.telegram_channel_id,
                            "user_id": sub.telegram_id,
                            "only_if_banned": True,
                        })
                    except Exception:
                        pass
            elif aware(sub.expires_at) <= horizon and sub.warning_3d_sent_at is None:
                text = (
                    "⚠️ <b>تنبيه اشتراك SAS PRO</b>\n\n"
                    "اشتراكك سينتهي بعد 3 أيام أو أقل.\n"
                    f"تاريخ الانتهاء: {sub.expires_at.strftime('%d/%m/%Y')}\n\n"
                    "جدّد اشتراكك الآن للاستمرار في استخدام جميع مزايا SAS PRO 🚀"
                )
                try:
                    invoice_url = await bot_api("createInvoiceLink", {"title": "SAS PRO monthly", "description": "تجديد اشتراك SAS PRO لمدة 30 يوم", "payload": f"saspro:monthly:{sub.telegram_id}:renew", "currency": "XTR", "prices": [{"label": "SAS PRO monthly", "amount": settings.pro_monthly_stars}]})
                    await send_message(sub.telegram_id, text, {"inline_keyboard": [[{"text": "🔄 تجديد الاشتراك", "url": invoice_url}]]})
                    sub.warning_3d_sent_at = now
                except Exception:
                    pass
        await db.commit()

_radar_open_announced = False
_radar_seen = set()

RADAR_STATUS = """📡 SAS PRO RADAR ⏳

🟢 الرصد مستمر الآن... 🕒

🛰️ نتابع السوق لحظة بلحظة
📊 نفحص الأسهم والنماذج والسلوك
🎯 لا يتم إرسال أي سهم إلا بعد تحقق الشروط المطلوبة

⏳ لا توجد فرصة مؤكدة حاليًا

🚨 عند ظهور فرصة مستوفية للشروط،
سيتم إرسالها مباشرة هنا.

⚠️ تحذير مهم
📈 الأسهم المضاربية عالية المخاطر
💰 قد تتغير الأسعار بسرعة وقد تحدث خسائر كبيرة
🛑 لا تدخل بأموال لا تتحمل خسارتها

🚨 هذا الرصد لأغراض تعليمية ومعلوماتية فقط،
ولا يُعد توصية شراء أو بيع.
قرار التداول وإدارة المخاطر مسؤولية المتداول 🚨

⚡ SAS PRO ⚡
الدقة أولًا • بدون مطاردة • بدون إشارات وهمية"""


async def stock_radar_cycle():
    global _radar_open_announced

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return

    status = market_status()
    if not status["open"]:
        _radar_open_announced = False
        return

    if not _radar_open_announced:
        try:
            await send_message(settings.telegram_channel_id, RADAR_STATUS)
            _radar_open_announced = True
        except Exception:
            return

    try:
        from .scanner import scan_us_low_price_stocks
        from .main import build_report
        from .market import quote

        rows = await scan_us_low_price_stocks()
        session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        async with SessionLocal() as db:
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                if not symbol or symbol in _radar_seen:
                    continue

                existing = (
                    await db.execute(
                        select(RadarSignal).where(
                            RadarSignal.symbol == symbol,
                            RadarSignal.session_date == session_date,
                        )
                    )
                ).scalars().first()
                if existing:
                    _radar_seen.add(symbol)
                    continue

                q = await quote(symbol)
                classification = row.get("classification") or {}
                tech = row.get("targets") or {}
                report = build_report(symbol, q, tech, classification)

                try:
                    await send_message(settings.telegram_channel_id, report)
                    db.add(RadarSignal(
                        symbol=symbol,
                        session_date=session_date,
                        payload=json.dumps(row, ensure_ascii=False),
                    ))
                    await db.commit()
                    _radar_seen.add(symbol)
                except Exception:
                    await db.rollback()
                    continue
    except Exception:
        return



async def weekly_radar_report():
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return
    now_riyadh = datetime.now(ZoneInfo("Asia/Riyadh"))
    if now_riyadh.weekday() != 5 or now_riyadh.hour != 12:
        return
    report_key = now_riyadh.strftime("weekly-radar-%G-W%V")
    async with SessionLocal() as db:
        exists = (await db.execute(select(ScheduledReport).where(ScheduledReport.report_key == report_key))).scalars().first()
        if exists:
            return
        week_start = (now_riyadh - timedelta(days=7)).date().isoformat()
        week_end = (now_riyadh - timedelta(days=1)).date().isoformat()
        rows = (await db.execute(select(RadarSignal).where(RadarSignal.session_date >= week_start, RadarSignal.session_date <= week_end).order_by(RadarSignal.created_at.asc()))).scalars().all()
        hit1 = hit2 = hit3 = missed = pending = 0
        details = []
        async with httpx.AsyncClient(timeout=settings.panwatch_timeout_seconds) as client:
            for signal in rows:
                try:
                    payload = json.loads(signal.payload)
                    targets = (payload.get("targets") or {}).get("targets") or []
                    if not targets:
                        pending += 1
                        continue
                    base = settings.panwatch_base_url.rstrip("/")
                    r = await client.get(f"{base}/api/klines/{signal.symbol}", params={"market": "US", "days": 90, "interval": "1d"})
                    r.raise_for_status()
                    candles = r.json().get("klines", [])
                    highs = []
                    for candle in candles:
                        date_value = str(candle.get("datetime") or candle.get("date") or "")[:10]
                        if date_value > signal.session_date:
                            try:
                                highs.append(float(candle.get("high")))
                            except Exception:
                                pass
                    if not highs:
                        pending += 1
                        continue
                    max_high = max(highs)
                    reached = [max_high >= float(t) for t in targets[:3]]
                    if reached and reached[0]:
                        hit1 += 1
                        if len(reached) > 1 and reached[1]:
                            hit2 += 1
                        if len(reached) > 2 and reached[2]:
                            hit3 += 1
                        details.append(f"✅ {signal.symbol} — أعلى هدف محقق: {min(3, sum(reached))}")
                    else:
                        missed += 1
                        details.append(f"❌ {signal.symbol} — الهدف الأول لم يتحقق")
                except Exception:
                    pending += 1
        total = len(rows)
        evaluated = hit1 + missed
        rate = (hit1 / evaluated * 100) if evaluated else 0
        text = (
            "📊 <b>SAS PRO — الإحصائية الأسبوعية</b>\n\n"
            f"📅 الفترة: {week_start} → {week_end}\n"
            f"📌 إجمالي فرص الرادار: <b>{total}</b>\n"
            f"🎯 حققت الهدف الأول: <b>{hit1}</b>\n"
            f"🎯 حققت الهدف الثاني: <b>{hit2}</b>\n"
            f"🎯 حققت الهدف الثالث: <b>{hit3}</b>\n"
            f"❌ لم تحقق الهدف الأول: <b>{missed}</b>\n"
            f"⏳ لم يمكن تقييمها بعد: <b>{pending}</b>\n"
            f"📈 نسبة تحقق الهدف الأول من الحالات المقيمة: <b>{rate:.1f}%</b>\n\n"
            "━━━━━━━━━━━━━━\n\n<b>تفاصيل الفرص</b>\n"
        )
        text += "\n".join(details[:80]) if details else "لا توجد فرص مرصودة خلال الفترة."
        text += "\n\n━━━━━━━━━━━━━━\n⚠️ الإحصائية تقيس وصول السعر إلى المستويات المحسوبة، ولا تعني نتيجة تداول فعلية أو ضمانًا مستقبليًا."
        try:
            await send_message(settings.telegram_channel_id, text)
            db.add(ScheduledReport(report_key=report_key))
            await db.commit()
        except Exception:
            await db.rollback()

async def scheduler():
    while True:
        try:
            await expiry_cycle()
            await stock_radar_cycle()
            await weekly_radar_report()
        except Exception:
            pass
        await asyncio.sleep(900)
