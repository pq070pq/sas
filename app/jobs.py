import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .config import settings
from .db import SessionLocal, Subscription
from .telegram import send_message, bot_api
from .holiday_radar import publish_holiday_radar
from .timeutil import utcnow, aware

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

async def scheduler():
    while True:
        try:
            await expiry_cycle()
        except Exception:
            pass
        await asyncio.sleep(3600)
