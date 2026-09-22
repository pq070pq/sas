import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .config import settings
from .db import SessionLocal, Subscription
from .telegram import send_message, bot_api
from .holiday_radar import publish_holiday_radar
from .timeutil import utcnow, aware
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
                    await send_message(sub.telegram_id, text)
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
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol or symbol in _radar_seen:
                continue

            q = await quote(symbol)
            classification = row.get("classification") or {}
            tech = row.get("targets") or {}
            report = build_report(symbol, q, tech, classification)
            try:
                await send_message(settings.telegram_channel_id, report)
                _radar_seen.add(symbol)
            except Exception:
                continue
    except Exception:
        return


async def scheduler():
    while True:
        try:
            await expiry_cycle()
            await stock_radar_cycle()
        except Exception:
            pass
        await asyncio.sleep(900)
