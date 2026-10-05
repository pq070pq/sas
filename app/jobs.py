import asyncio
import logging
import json
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import SessionLocal, Subscription, RadarSignal, RadarOutcome, ScheduledReport, User, ScheduledReport
from .telegram import send_message, edit_message, bot_api
from .holiday_radar import publish_holiday_radar, publish_market_update
from .timeutil import utcnow, aware
from zoneinfo import ZoneInfo
from .market_calendar import market_status
from .market_brief import publish_market_brief
from sqlalchemy import func

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
logger.propagate = False

def _money(value):
    try:
        return f"${float(value):,.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "غير واضح"

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
    """Track active radar targets and link every update to its original radar message."""
    async with SessionLocal() as db:
        signals = (await db.execute(select(RadarSignal))).scalars().all()
        for signal in signals:
            if not signal.telegram_message_id:
                continue

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

            ordered_targets = [
                x for x in (
                    existing.target1, existing.target2,
                    existing.target3, existing.target4, existing.target5
                ) if x is not None
            ]
            if existing.status == "failed" or not ordered_targets or int(existing.achieved_target or 0) >= len(ordered_targets):
                continue

            try:
                from .market import quote
                q = await quote(signal.symbol)
                price = q.get("price")
                if price is None:
                    continue
                price = float(price)
                existing.current_price = price
                existing.evaluated_at = utcnow()

                newly_reached = []
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
                    newly_reached.append((idx, float(target)))

                if newly_reached:
                    from .main import build_report
                    updated_report = build_report(
                        signal.symbol,
                        {"price": price, "change_pct": payload.get("change_pct"), "source": q.get("source")},
                        tech,
                        payload.get("classification") or {},
                        existing,
                    )
                    try:
                        await edit_message(settings.telegram_channel_id, int(signal.telegram_message_id), updated_report)
                    except Exception:
                        logger.exception("Radar parent message edit failed: %s", signal.symbol)

                    for idx, target in newly_reached:
                        profit_text = ""
                        if existing.entry_price and existing.entry_price > 0:
                            profit = ((price / existing.entry_price) - 1.0) * 100.0
                            profit_text = f"\n📈 العائد من دخول الرصد: <b>{profit:+.2f}%</b>"
                        stop_text = (
                            f"\n🛡 الوقف الجديد: <b>\x24{_money(existing.current_stop)}</b>"
                            if existing.current_stop is not None else ""
                        )
                        final = idx == len(ordered_targets)
                        title = "🏆 تحقق آخر هدف" if final else f"🎯 تحقق الهدف {idx}"
                        next_target = ordered_targets[idx] if idx < len(ordered_targets) else None
                        next_text = (
                            f"\n🎯 الهدف التالي: <b>\x24{_money(next_target)}</b>"
                            if next_target is not None else "\n🏆 اكتملت جميع الأهداف المسجلة."
                        )
                        await send_message(
                            settings.telegram_channel_id,
                            f"{title} — <b>\x24{signal.symbol}</b>\n\n"
                            f"💵 السعر الحالي: <b>\x24{_money(price)}</b>"
                            f"{profit_text}\n"
                            f"🎯 الهدف المحقق: <b>\x24{_money(target)}</b>"
                            f"{stop_text}{next_text}\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"📌 حالة الرصد: <b>{'مستمر' if next_target is not None else 'اكتملت الأهداف'}</b>\n"
                            f"⏱ تم التحقق: <b>{utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</b>\n\n"
                            "📡 <b>SAS PRO</b>",
                            reply_to_message_id=int(signal.telegram_message_id),
                        )

                stop_triggered = (
                    existing.current_stop is not None
                    and price <= float(existing.current_stop)
                    and existing.achieved_target < len(ordered_targets)
                )
                if stop_triggered:
                    existing.status = "failed"
                    from .main import build_report
                    updated_report = build_report(
                        signal.symbol,
                        {"price": price, "change_pct": payload.get("change_pct")},
                        tech,
                        payload.get("classification") or {},
                        existing,
                    )
                    try:
                        await edit_message(settings.telegram_channel_id, int(signal.telegram_message_id), updated_report)
                    except Exception:
                        logger.exception("Radar parent message stop edit failed: %s", signal.symbol)

                    await send_message(
                        settings.telegram_channel_id,
                        "🛑 <b>تفعيل الوقف</b> — \x24" + signal.symbol + "\n\n"
                        f"💵 السعر المرصود: <b>\x24{_money(price)}</b>\n"
                        f"🛡 الوقف: <b>\x24{_money(existing.current_stop)}</b>\n"
                        f"🎯 آخر هدف محقق: <b>{existing.achieved_target}</b>\n"
                        "📌 تم إنهاء الرصد وفق مستوى الوقف المسجل.",
                        reply_to_message_id=int(signal.telegram_message_id),
                    )

            except Exception as exc:
                if "429" in str(exc) or "Too Many Requests" in str(exc):
                    logger.warning("Radar outcome quote throttled for %s; skipping this evaluation cycle.", signal.symbol)
                else:
                    logger.exception("Radar outcome evaluation failed: %s", signal.symbol)
                continue

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


def _radar_channel_gate(status, row, quote_data):
    """Final Telegram gate: broad scanner finds candidates, this gate decides what reaches the channel.
    
    Extended-hours prices can move on thin liquidity, so a price jump alone is never
    enough. We keep scanning broadly (so opportunities remain visible to the app/logs),
    but Telegram only receives an extended-hours signal after live-price, liquidity,
    momentum and independent technical confirmation agree.
    """
    session = str((status or {}).get("session") or "")
    extended = session in {"premarket", "afterhours", "night"}

    classification = row.get("classification") or {}
    targets = row.get("targets") or {}
    price = _money_float = None
    try:
        price = float((quote_data or {}).get("price") or row.get("live_price") or row.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0

    change = (quote_data or {}).get("change_pct")
    if change is None:
        change = row.get("live_change_pct")
    if change is None:
        change = row.get("change_pct")
    try:
        change = float(change or 0)
    except (TypeError, ValueError):
        change = 0.0

    try:
        volume = float(row.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    dollar_volume = price * volume

    # Universal anti-fake safeguards.
    if price <= 0 or volume <= 0:
        return False, "سعر/حجم حي غير صالح"
    if classification.get("distribution_risk") or classification.get("bearish_head_shoulders"):
        return False, "تناقض هابط قوي"
    if classification.get("chase_risk"):
        return False, "مطاردة سعرية"
    if not isinstance(targets.get("targets"), list) or not targets.get("targets"):
        return False, "لا يوجد هدف سعري مؤكد"

    if not extended:
        return True, "جلسة رئيسية — بوابة SAS الأساسية"

    # Extended hours: reject price-only spikes. Nasdaq/SEC note that these
    # sessions generally have lower liquidity and higher volatility.
    if change < 3.0:
        return False, "ارتفاع ممتد دون حركة سعرية كافية"
    if dollar_volume < 1_500_000:
        return False, "سيولة نقدية ممتدة غير كافية"

    rvol = float(classification.get("rvol") or 0)
    intraday_confirmation = bool(classification.get("intraday_confirmation"))
    breakout_confirmed = bool(classification.get("breakout_confirmed"))
    advanced_confirmation = bool(classification.get("advanced_confirmation_pass"))
    catalyst = bool(row.get("catalyst") or row.get("news_count"))

    independent_confirmation = (
        intraday_confirmation
        or (breakout_confirmed and rvol >= 1.5)
        or (advanced_confirmation and rvol >= 1.5)
        or (catalyst and rvol >= 2.0 and change >= 5.0)
    )
    if not independent_confirmation:
        return False, "ارتفاع سعري بلا تأكيد حجم/بنية مستقل"

    return True, "تأكيد ممتد: سعر + سيولة + حجم + بنية/خبر"


async def stock_radar_cycle():
    global _radar_open_announced

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        logger.warning("Stock radar skipped: Telegram channel/token is not configured.")
        return

    status = market_status()

    # الرادار يعمل على جلسات الأسهم المتاحة: قبل الافتتاح، الرئيسية،
    # بعد الإغلاق، والليل عند تفعيل جلسة Nasdaq الجديدة.
    # في العطلة ونهاية الأسبوع يتحول النظام إلى رادار بيتكوين.
    if status["holiday"] or status["session"] == "weekend":
        _radar_open_announced = False
        logger.info("Stock radar skipped: market holiday/weekend (session=%s).", status.get("session"))
        return

    if not _radar_open_announced:
        try:
            await send_message(settings.telegram_channel_id, RADAR_STATUS)
            _radar_open_announced = True
            logger.info("Stock radar status message sent.")
        except Exception:
            logger.exception("Stock radar status message failed.")
            return

    try:
        from .scanner import scan_us_low_price_stocks
        from .main import build_report
        from .market import quote
        from .news import company_fundamentals
        from .ai_radar import analyze_stock

        scan_result = await scan_us_low_price_stocks()
        diagnostics = scan_result.get("diagnostics") or {}
        if diagnostics.get("twelve_data_quota_exhausted"):
            # Twelve Data احتياطي فقط؛ لا نوقف الرادار ما دامت نتائج PanWatch
            # صالحة. نستمر بإرسال إشارات الأسهم ونكتفي بتسجيل حالة الحصة.
            logger.warning(
                "Twelve Data quota exhausted; continuing radar with PanWatch/primary data."
            )
        rows = scan_result.get("stocks", [])
        logger.info("Stock radar scan completed: %d result(s); diagnostics=%s", len(rows), diagnostics)
        session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        # كل سهم اجتاز فلاتر الرادار يُرسل كتقرير مستقل.
        # لا نرسل جدول Daily Momentum مجمعًا؛ تفاصيل السهم وشروط اجتيازه
        # تظهر داخل تقريره الفردي عبر build_report().
        async with SessionLocal() as db:
            cycle_stats = {"rows": len(rows), "skipped": 0, "reanalyzed": 0, "sent": 0, "failed": 0}
            ai_used = 0
            ai_budget = max(0, int(settings.ai_max_calls_per_cycle))
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
                        cycle_stats["skipped"] += 1
                        _radar_seen.add(symbol)
                        logger.info(
                            "Radar duplicate skipped: %s | reason=existing_signal_same_session "
                            "volume_jump=%s price_jump=%s",
                            symbol, volume_jump, price_jump,
                        )
                        continue

                    cycle_stats["reanalyzed"] += 1
                    logger.info(
                        "Radar reanalysis triggered: %s | volume_jump=%s price_jump=%s",
                        symbol, volume_jump, price_jump,
                    )

                try:
                    q = await quote(symbol)
                except Exception:
                    logger.exception("Quote provider failed for %s; using scan data.", symbol)
                    q = {"symbol": symbol}
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
                tech = {
                    **(row.get("targets") or {}),
                    "radar_checks": row.get("radar_checks") or {},
                    "market_session": status.get("label_ar"),
                    "market_session_code": status.get("session"),
                }

                channel_ok, channel_reason = _radar_channel_gate(status, row, q)
                if not channel_ok:
                    cycle_stats["skipped"] += 1
                    logger.info(
                        "Radar channel gate skipped: %s | session=%s | reason=%s",
                        symbol, status.get("session"), channel_reason,
                    )
                    continue

                row["channel_gate"] = {
                    "passed": True,
                    "session": status.get("session"),
                    "reason": channel_reason,
                }

                # AI enrichment runs only after the technical radar has already
                # selected the candidate. It cannot create a signal, target,
                # stop, or price level; it only explains supplied evidence.
                if ai_used < ai_budget:
                    try:
                        fundamentals = await company_fundamentals(symbol)
                        ai_analysis = await analyze_stock(
                            symbol,
                            company={
                                "name": row.get("name") or symbol,
                                "exchange": row.get("exchange"),
                            },
                            news=row.get("news_items") or [],
                            fundamentals=fundamentals,
                            market={
                                "momentum_section": row.get("momentum_section"),
                                "daily_change_pct": row.get("change_pct"),
                                "relative_volume_10d": row.get("momentum_rvol_10d"),
                                "relative_volume_threshold": row.get("momentum_rvol_threshold"),
                                "session_verified": row.get("momentum_session_verified"),
                                "earnings_within_5_days": row.get("earnings_within_5_days"),
                                "radar_gate": {
                                    "momentum": bool((row.get("radar_checks") or {}).get("momentum")),
                                    "sas_core": bool((row.get("radar_checks") or {}).get("sas_core")),
                                    "liquidity": bool((row.get("radar_checks") or {}).get("liquidity")),
                                    "target": bool((row.get("radar_checks") or {}).get("target")),
                                    "live_levels": bool((row.get("radar_checks") or {}).get("live_levels")),
                                    "risk_reward_warning": bool((row.get("radar_checks") or {}).get("risk_reward_warning")),
                                },
                            },
                        )
                        row["ai_analysis"] = ai_analysis
                        ai_used += 1
                        tech = {
                            **tech,
                            "ai_analysis": ai_analysis,
                            "fundamentals": fundamentals,
                            "news_items": row.get("news_items") or [],
                        }
                        if ai_analysis.get("enabled"):
                            logger.info(
                                "AI radar analysis ready: %s | provider=%s",
                                symbol, ai_analysis.get("provider"),
                            )
                    except Exception:
                        ai_used += 1
                        logger.exception("AI radar enrichment failed: %s", symbol)
                else:
                    tech = {
                        **tech,
                        "ai_analysis": {
                            "enabled": False,
                            "status": "cycle_budget",
                            "key_takeaway": "تم تجاوز حد استدعاءات الذكاء الاصطناعي لهذه الدورة لحماية الحصة."
                        },
                        "news_items": row.get("news_items") or [],
                    }

                report = build_report(symbol, q, tech, classification)

                try:
                    # احفظ نتيجة الرادار أولاً حتى تبقى بيانات السهم ظاهرة في
                    # Mini App حتى لو تعذر إرسال رسالة Telegram مؤقتًا.
                    if existing:
                        existing.payload = json.dumps(row, ensure_ascii=False)
                        existing.created_at = utcnow()
                    else:
                        existing = RadarSignal(
                            symbol=symbol,
                            session_date=session_date,
                            payload=json.dumps(row, ensure_ascii=False),
                        )
                        db.add(existing)
                    await db.commit()

                    message_id = None
                    try:
                        result = await send_message(settings.telegram_channel_id, report)
                        message_id = result.get("message_id") if isinstance(result, dict) else None
                    except Exception:
                        cycle_stats["failed"] += 1
                        logger.exception("Radar Telegram delivery failed but signal was persisted: %s", symbol)

                    if message_id:
                        existing.telegram_message_id = int(message_id)
                        await db.commit()

                    if message_id:
                        cycle_stats["sent"] += 1
                    _radar_seen.add(symbol)
                    logger.info(
                        "Radar signal persisted: %s | telegram_message_id=%s",
                        symbol, message_id,
                    )
                except Exception:
                    cycle_stats["failed"] += 1
                    await db.rollback()
                    logger.exception("Radar persistence failed: %s", symbol)
                    continue

            logger.info(
                "Stock radar delivery summary: rows=%d skipped=%d reanalyzed=%d sent=%d failed=%d",
                cycle_stats["rows"],
                cycle_stats["skipped"],
                cycle_stats["reanalyzed"],
                cycle_stats["sent"],
                cycle_stats["failed"],
            )
    except Exception:
        logger.exception("Stock radar cycle failed.")
        return



async def weekly_radar_report():
    """Saturday 12:00 Saudi report based only on persisted live outcome records."""
    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        return
    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    if now.weekday() != 5 or now.hour != 12:
        return
    report_key = f"weekly-radar:{now.strftime('%Y-%m-%d')}"
    async with SessionLocal() as db:
        exists = (await db.execute(
            select(ScheduledReport).where(ScheduledReport.report_key == report_key)
        )).scalars().first()
        if exists:
            return
        start = (now.date() - timedelta(days=6)).isoformat()
        outcomes = (await db.execute(
            select(RadarOutcome).where(RadarOutcome.session_date >= start).order_by(RadarOutcome.session_date.asc())
        )).scalars().all()
        reached = [x for x in outcomes if int(x.achieved_target or 0) > 0]
        stopped = [x for x in outcomes if x.status == "failed"]
        active = [x for x in outcomes if x.status == "active"]
        lines = [
            "📊 <b>SAS PRO | التقرير الأسبوعي</b>",
            f"📅 الفترة: {start} → {now.strftime('%Y-%m-%d')}",
            "🕛 السبت | 12:00 ظهرًا 🇸🇦",
            "",
            f"📌 فرص الرادار المسجلة: <b>{len(outcomes)}</b>",
            f"🎯 حققت هدفًا فعليًا: <b>{len(reached)}</b>",
            f"🛑 أوقفت/ألغيت: <b>{len(stopped)}</b>",
            f"⏳ تحت المتابعة: <b>{len(active)}</b>",
            "",
            "🏆 <b>الأسهم التي حققت أهدافها</b>",
        ]
        if reached:
            lines.extend(
                f"• {x.symbol} — تحقق الهدف {int(x.achieved_target)}"
                + (f" — السعر الأخير المرصود {_money(x.current_price)}" if x.current_price else "")
                for x in reached
            )
        else:
            lines.append("• لا توجد حالات هدف محققة مسجلة.")
        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━",
            "⚠️ تعتمد الإحصائية على أحداث تحقق الأهداف التي رصدها النظام فعليًا أثناء التشغيل، وليست إعادة احتساب تاريخية.",
            "لا يعد هذا التقرير توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️",
        ]
        await send_message(settings.telegram_channel_id, "\n".join(lines))
        db.add(ScheduledReport(report_key=report_key))
        await db.commit()

async def scheduler():
    logger.info("SAS PRO scheduler started.")
    # Let the Telegram Mini App authenticate before the first heavy scheduler cycle.
    # Some startup cycles can take tens of seconds and must not race with /api/me.
    await asyncio.sleep(60)
    logger.info("SAS PRO initial scheduler grace period completed.")
    while True:
        cycle_started = utcnow()
        try:
            logger.info("Scheduler cycle started.")
            await expiry_cycle()
            logger.info("Scheduler: expiry cycle completed.")
            await evaluate_radar_outcomes()
            logger.info("Scheduler: radar outcome evaluation completed.")
            await weekly_radar_report()
            await publish_market_brief()
            logger.info("Scheduler: market brief cycle completed.")

            status = market_status()
            if status["holiday"] or status["session"] == "weekend":
                # في عطلة السوق/نهاية الأسبوع: بيتكوين حسب شروط رادار الإجازة السابقة.
                from .holiday_radar import publish_holiday_radar
                logger.info("Scheduler mode: holiday/weekend radar.")
                await publish_holiday_radar()
            else:
                # الأسهم: الرصد كل 30 دقيقة مع استهلاك محدود للبيانات.
                logger.info("Scheduler mode: stock radar.")
                await stock_radar_cycle()

            await weekly_radar_report()
            logger.info("Scheduler cycle completed in %.1fs.", (utcnow() - cycle_started).total_seconds())
        except Exception:
            logger.exception("Scheduler cycle failed.")
        await asyncio.sleep(max(1800, int(settings.radar_interval_minutes) * 60))
