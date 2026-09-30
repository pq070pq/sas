import asyncio
import json
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import SessionLocal, Subscription, RadarSignal, RadarOutcome, ScheduledReport, User, ScheduledReport
from .telegram import send_message, bot_api
from .holiday_radar import publish_holiday_radar
from .timeutil import utcnow, aware
from zoneinfo import ZoneInfo
from .market_calendar import market_status
from sqlalchemy import func

async def expiry_cycle():
    now = utcnow()
    horizon = now + timedelta(hours=settings.expiry_warning_hours)
    async with SessionLocal() as db:
        subs = (await db.execute(select(Subscription).where(Subscription.active == True))).scalars().all()
        for sub in subs:
            expires_at = aware(sub.expires_at)
            user = (await db.execute(select(User).where(User.telegram_id == sub.telegram_id))).scalars().first()
            if user and user.free_access:
                continue
            if expires_at <= now:
                sub.active = False
                if user:
                    user.status = "expired"
                    user.subscription_expires = expires_at
                    user.updated_at = now
                try:
                    from .main import set_channel_access
                    await set_channel_access(sub.telegram_id, allow=False)
                except Exception:
                    pass
                try:
                    await send_message(sub.telegram_id,
                        "⛔ <b>انتهى اشتراك SAS PRO</b>\n\n"
                        f"📅 تاريخ الانتهاء: <b>{expires_at.strftime('%d/%m/%Y')}</b>\n\n"
                        "جدّد اشتراكك من Mini App للاستمرار."
                    )
                except Exception:
                    pass
            elif expires_at <= horizon and sub.warning_3d_sent_at is None:
                try:
                    await send_message(sub.telegram_id,
                        "⚠️ <b>تنبيه الاشتراك</b>\n\n"
                        "متبقي على اشتراكك <b>3 أيام أو أقل</b>.\n"
                        "بادر بالتجديد من صفحة التطبيق حتى يستمر وصولك بدون انقطاع.",
                        {"inline_keyboard": [[{"text": "📱 فتح صفحة الاشتراك", "web_app": {"url": settings.app_base_url}}]]} if settings.app_base_url else None
                    )
                    sub.warning_3d_sent_at = now
                    if user:
                        user.warning_sent_at = now
                        user.updated_at = now
                except Exception:
                    pass

        # تنبيه التجربة قبل انتهائها بثلاثة أيام، مرة واحدة فقط.
        trial_warning_users = (await db.execute(select(User).where(
            User.trial_expires.is_not(None),
            User.trial_expires > now,
            User.trial_expires <= horizon,
            User.status == "trial",
            User.warning_sent_at.is_(None),
        ))).scalars().all()
        for user in trial_warning_users:
            remaining_days = max(1, int((aware(user.trial_expires) - now).total_seconds() // 86400))
            try:
                await send_message(user.telegram_id,
                    "⚠️ <b>تنبيه قرب انتهاء التجربة المجانية</b>\n\n"
                    f"متبقي على تجربتك المجانية <b>{remaining_days} يوم</b>.\n"
                    "بادر بالاشتراك من صفحة التطبيق قبل انتهاء التجربة حتى لا يتوقف وصولك للقناة.\n\n"
                    "📱 افتح صفحة SAS PRO واختر الباقة المناسبة.",
                    {"inline_keyboard": [[{"text": "📱 فتح صفحة الاشتراك", "web_app": {"url": settings.app_base_url}}]]} if settings.app_base_url else None
                )
                user.warning_sent_at = now
                user.updated_at = now
            except Exception:
                pass

        # انتهاء التجربة: استبعاد المستخدم من قناة SAS PRO الرئيسية.
        trial_users = (await db.execute(select(User).where(User.trial_expires.is_not(None), User.trial_expires <= now, User.status == "trial"))).scalars().all()
        for user in trial_users:
            user.status = "expired"
            user.updated_at = now
            if settings.telegram_channel_id:
                try:
                    await bot_api("banChatMember", {
                        "chat_id": settings.telegram_channel_id,
                        "user_id": user.telegram_id,
                        "revoke_messages": False,
                    })
                except Exception:
                    pass
            try:
                await send_message(user.telegram_id,
                    "⏳ <b>انتهت تجربتك المجانية في SAS PRO</b>\n\n"
                    "تم إيقاف وصولك للقناة. يمكنك اختيار إحدى الباقات المدفوعة من Mini App للاستمرار."
                )
            except Exception:
                pass
        await db.commit()


async def evaluate_radar_outcomes():
    """Track live radar targets and emit alerts only after an observed market price reaches them."""
    async with SessionLocal() as db:
        signals = (await db.execute(select(RadarSignal))).scalars().all()
        for signal in signals:
            payload = json.loads(signal.payload or "{}")
            tech = payload.get("targets") or {}
            targets = []
            for value in (tech.get("targets") or [])[:5]:
                try:
                    level = float(value)
                    if level > 0:
                        targets.append(level)
                except (TypeError, ValueError):
                    continue
            entry = payload.get("live_price") or payload.get("price")
            try:
                entry = float(entry) if entry is not None else None
            except (TypeError, ValueError):
                entry = None
            exit_level = tech.get("exit")
            try:
                exit_level = float(exit_level) if exit_level is not None else None
            except (TypeError, ValueError):
                exit_level = None

            existing = (await db.execute(
                select(RadarOutcome).where(RadarOutcome.radar_signal_id == signal.id)
            )).scalars().first()

            if existing is None:
                existing = RadarOutcome(
                    radar_signal_id=signal.id,
                    symbol=signal.symbol,
                    session_date=signal.session_date,
                    target1=targets[0] if len(targets) > 0 else None,
                    target2=targets[1] if len(targets) > 1 else None,
                    target3=targets[2] if len(targets) > 2 else None,
                    target4=targets[3] if len(targets) > 3 else None,
                    target5=targets[4] if len(targets) > 4 else None,
                    exit_level=exit_level,
                    entry_price=entry,
                    current_stop=exit_level,
                )
                db.add(existing)
                await db.flush()

            if existing.entry_price is None and entry is not None:
                existing.entry_price = entry
            if existing.current_stop is None:
                existing.current_stop = existing.exit_level

            try:
                from .market import quote
                q = await quote(signal.symbol)
                price = q.get("price")
                if price is None:
                    continue
                price = float(price)
                existing.current_price = price
                existing.evaluated_at = utcnow()

                ordered_targets = [
                    x for x in (
                        existing.target1, existing.target2,
                        existing.target3, existing.target4, existing.target5
                    ) if x is not None
                ]

                for idx, target in enumerate(ordered_targets, start=1):
                    if idx <= int(existing.last_alert_target or 0):
                        continue
                    if price < float(target):
                        break

                    existing.achieved_target = idx
                    existing.last_alert_target = idx
                    existing.status = f"target{idx}"

                    if existing.entry_price is not None:
                        new_stop = existing.entry_price if idx == 1 else ordered_targets[idx - 2]
                        if existing.current_stop is None or new_stop > existing.current_stop:
                            existing.current_stop = new_stop

                    profit_text = ""
                    if existing.entry_price and existing.entry_price > 0:
                        profit = ((price / existing.entry_price) - 1.0) * 100.0
                        profit_text = f"\\n📈 العائد من دخول الرصد: <b>{profit:+.2f}%</b>"

                    stop_text = (
                        f"\\n🛡 الوقف الجديد: <b>{_money(existing.current_stop)}</b>"
                        if existing.current_stop is not None else ""
                    )
                    final = idx == len(ordered_targets)
                    title = "🔥 تحقق الهدف %d" % idx if final else "🎯 تحقق الهدف %d" % idx
                    footer = "\\n🏆 اكتملت أهداف الرصد" if final else ""
                    await send_message(
                        settings.telegram_channel_id,
                        f"{title} — <b>$" + signal.symbol + "</b>\\n\\n"
                        f"💵 السعر المرصود: <b>{_money(price)}</b>"
                        f"{profit_text}"
                        f"{stop_text}\\n"
                        f"📊 المستوى: <b>{_money(target)}</b>\\n"
                        f"⏱ تم التحقق: <b>{utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</b>"
                        f"{footer}"
                    )

                if (
                    existing.status.startswith("target")
                    and existing.current_stop is not None
                    and price <= float(existing.current_stop)
                    and existing.achieved_target < len(ordered_targets)
                ):
                    existing.status = "failed"
                    await send_message(
                        settings.telegram_channel_id,
                        "🛑 <b>تفعيل الوقف — $" + signal.symbol + "</b>\\n\\n"
                        f"💵 السعر المرصود: <b>{_money(price)}</b>\\n"
                        f"🛡 الوقف: <b>{_money(existing.current_stop)}</b>\\n"
                        f"🎯 آخر هدف محقق: <b>{existing.achieved_target}</b>\\n"
                        "📌 تم إنهاء الرصد وفق مستوى الوقف المسجل."
                    )
                elif (
                    existing.achieved_target == 0
                    and existing.current_stop is not None
                    and price <= float(existing.current_stop)
                ):
                    existing.status = "failed"
                    await send_message(
                        settings.telegram_channel_id,
                        "🛑 <b>تفعيل الوقف — $" + signal.symbol + "</b>\\n\\n"
                        f"💵 السعر المرصود: <b>{_money(price)}</b>\\n"
                        f"🛡 الوقف: <b>{_money(existing.current_stop)}</b>\\n"
                        "📌 لم يتحقق الهدف الأول قبل بلوغ حد الإلغاء."
                    )

            except Exception:
                continue

        await db.commit()


async def weekly_radar_report():
    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    if now.weekday() != 5 or now.hour != 12:
        return
    key = f"weekly-radar:{now.strftime('%Y-%m-%d')}"
    async with SessionLocal() as db:
        sent = (await db.execute(
            select(ScheduledReport).where(ScheduledReport.report_key == key)
        )).scalars().first()
        if sent or not settings.telegram_channel_id or not settings.telegram_bot_token:
            return

        week_start = (now.date() - timedelta(days=6)).isoformat()
        outcomes = (await db.execute(
            select(RadarOutcome).where(RadarOutcome.session_date >= week_start)
        )).scalars().all()

        reached = [x for x in outcomes if x.achieved_target > 0]
        failed = [x for x in outcomes if x.status == "failed"]
        active = [x for x in outcomes if x.status == "active"]

        lines = [
            "📊 <b>SAS PRO — تقرير الرادار الأسبوعي</b>",
            "",
            f"📅 الفترة: {week_start} → {now.strftime('%Y-%m-%d')}",
            f"🔎 عدد الأسهم التي رصدها الرادار: <b>{len(outcomes)}</b>",
            f"🎯 وصلت للهدف: <b>{len(reached)}</b>",
            f"❌ لم تصل للهدف/وصلت لحد الخروج: <b>{len(failed)}</b>",
            f"⏳ ما زالت تحت المتابعة: <b>{len(active)}</b>",
            "",
            "━━━━━━━━━━━━━━",
            "",
            "🎯 <b>أسهم وصلت إلى أهدافها</b>",
        ]
        lines += [
            f"• {x.symbol} — وصل للهدف {x.achieved_target} 🎯"
            for x in reached
        ] or ["• لا توجد أسهم وصلت إلى هدف خلال الفترة"]

        lines += [
            "",
            "━━━━━━━━━━━━━━",
            "",
            "❌ <b>أسهم لم تصل إلى الهدف</b>",
        ]
        lines += [
            f"• {x.symbol} — {('وصل لحد الخروج' if x.status == 'failed' else 'لم يصل للهدف')}"
            for x in failed
        ] or ["• لا توجد حالات مسجلة"]

        lines += [
            "",
            "━━━━━━━━━━━━━━",
            "",
            "⏳ <b>أسهم ما زالت تحت المتابعة</b>",
        ]
        lines += [f"• {x.symbol}" for x in active] or ["• لا توجد أسهم مفتوحة حاليًا"]

        lines += [
            "",
            "━━━━━━━━━━━━━━",
            "",
            "⚠️ الإحصائية تعتمد على سعر السوق مقارنة بالأهداف وحد الخروج المسجلين وقت إرسال الرادار.",
            "",
            "🚨 <b>SAS PRO معلومات وتحليل فقط وليست توصية أو مشورة استثمارية.</b>",
            "الأهداف تحليلية وليست ضمانًا للنتيجة، وقرار الاستثمار والتداول وإدارة المخاطر مسؤولية المتداول ⚠️",
        ]

        await send_message(settings.telegram_channel_id, "\n".join(lines))
        db.add(ScheduledReport(report_key=key))
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

    # الأسهم تُرصد طوال أيام السوق على مدار اليوم:
    # قبل الافتتاح + الجلسة الرئيسية + بعد الإغلاق + خارج الجلسة.
    # في عطلة السوق ونهاية الأسبوع يتحول النظام إلى رادار بيتكوين.
    if status["holiday"] or status["session"] == "weekend":
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

        scan_result = await scan_us_low_price_stocks()
        rows = scan_result.get("stocks", [])
        session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        async with SessionLocal() as db:
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                if not symbol:
                    continue

                existing = (
                    await db.execute(
                        select(RadarSignal).where(
                            RadarSignal.symbol == symbol,
                            RadarSignal.session_date == session_date,
                        )
                    )
                ).scalars().first()

                # لا نكرر نفس السهم بلا سبب.
                # نعيد التحليل فقط إذا ظهرت قفزة موثوقة في التداول مقارنة بآخر رصد محفوظ.
                if existing:
                    try:
                        previous = json.loads(existing.payload or "{}")
                    except Exception:
                        previous = {}
                    previous_volume = float(previous.get("volume") or 0)
                    current_volume = float(row.get("volume") or 0)
                    previous_change = float(previous.get("change_pct") or 0)
                    current_change = float(row.get("change_pct") or 0)
                    volume_jump = (
                        previous_volume > 0
                        and current_volume >= previous_volume * 2.0
                    )
                    price_jump = abs(current_change - previous_change) >= 5.0
                    if not (volume_jump or price_jump):
                        _radar_seen.add(symbol)
                        continue

                q = await quote(symbol)
                # Keep the radar price populated from the scan row when the
                # live quote provider is temporarily unavailable.
                if q.get("price") is None:
                    fallback_price = row.get("live_price") or row.get("price")
                    fallback_change = row.get("live_change_pct")
                    if fallback_price is not None:
                        q = {
                            "symbol": symbol,
                            "price": fallback_price,
                            "change_pct": fallback_change if fallback_change is not None else row.get("change_pct"),
                            "source": row.get("live_price_source") or row.get("source") or "scan data",
                        }
                classification = row.get("classification") or {}
                tech = row.get("targets") or {}
                report = build_report(symbol, q, tech, classification)

                try:
                    await send_message(settings.telegram_channel_id, report)
                    if existing:
                        existing.payload = json.dumps(row, ensure_ascii=False)
                        existing.created_at = utcnow()
                    else:
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
        text += "\n\n━━━━━━━━━━━━━━\n⚠️ تنبيه: هذه الإحصائية تقيس وصول السعر إلى المستويات المحسوبة فقط، ولا تُعد نتيجة تداول فعلية ولا تضمن النتائج المستقبلية. قرار الاستثمار والتداول وإدارة المخاطر مسؤولية المتداول."
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
            await evaluate_radar_outcomes()
            await weekly_radar_report()

            status = market_status()
            if status["holiday"] or status["session"] == "weekend":
                # في عطلة السوق/نهاية الأسبوع: بيتكوين حسب شروط رادار الإجازة السابقة.
                from .holiday_radar import publish_holiday_radar
                await publish_holiday_radar()
            else:
                # الأسهم: الرصد مستمر طوال اليوم في كل جلسات السوق.
                await stock_radar_cycle()

            await weekly_radar_report()
        except Exception:
            pass
        await asyncio.sleep(max(600, int(settings.radar_interval_minutes) * 60))
