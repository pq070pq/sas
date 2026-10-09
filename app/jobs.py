import asyncio
import logging
import json
import html
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import SessionLocal, Subscription, RadarSignal, RadarOutcome, RadarRun, ScheduledReport, User, ScheduledReport
from .telegram import send_message, edit_message, bot_api
from .holiday_radar import publish_holiday_radar, publish_market_update
from .timeutil import utcnow, aware
from zoneinfo import ZoneInfo
from .market_calendar import market_status
from .market_brief import publish_market_brief
from .radar_learning import learn_radar_profile, get_radar_profile
from .shariah import check_shariah
from .maintenance import cleanup_old_data
from .quality_score import score_quality
from .quality_backtest import summarize_quality_outcomes
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
    """Evaluate only today's published radar signals with valid upward targets.

    Historical signals are never re-evaluated against today's price.
    """
    today_session = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    async with SessionLocal() as db:
        signals = (await db.execute(
            select(RadarSignal).where(
                RadarSignal.session_date == today_session,
                RadarSignal.telegram_message_id.is_not(None),
            )
        )).scalars().all()
        logger.info("Radar outcome evaluation: %d today's published signals.", len(signals))

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

            ordered_targets = [
                x for x in (
                    existing.target1, existing.target2,
                    existing.target3, existing.target4, existing.target5
                ) if x is not None
            ]

            # Reject malformed bullish targets: every target must be above entry
            # and targets must increase strictly. Invalid records can never alert.
            if existing.entry_price is None or existing.entry_price <= 0:
                existing.status = "failed"
                logger.warning("Invalid radar outcome without entry: %s id=%s", signal.symbol, signal.id)
                continue
            valid_targets = (
                bool(ordered_targets)
                and all(float(t) > float(existing.entry_price) for t in ordered_targets)
                and all(
                    float(ordered_targets[i]) > float(ordered_targets[i - 1])
                    for i in range(1, len(ordered_targets))
                )
            )
            if not valid_targets:
                existing.status = "failed"
                logger.warning(
                    "Invalid radar targets rejected: %s id=%s entry=%s targets=%s",
                    signal.symbol, signal.id, existing.entry_price, ordered_targets,
                )
                continue

            if existing.status == "failed" or int(existing.achieved_target or 0) >= len(ordered_targets):
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
                    try:
                        signal_payload = json.loads(signal.payload or "{}")
                    except (TypeError, ValueError):
                        signal_payload = {}
                    signal_payload["radar_active"] = False
                    signal_payload["strategy_status"] = "cancelled"
                    signal_payload["strategy_cancel_reason"] = "لم يتحقق الهدف وتم تفعيل الوقف"
                    signal.payload = json.dumps(signal_payload, ensure_ascii=False)
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
                        "❌ <b>لم تتحقق الفرصة</b> — \x24" + signal.symbol + "\n\n"
                        f"💵 السعر عند الإنهاء: <b>\x24{_money(price)}</b>\n"
                        f"🛡 الوقف المفعّل: <b>\x24{_money(existing.current_stop)}</b>\n"
                        f"🎯 الأهداف المحققة: <b>{existing.achieved_target}</b>\n"
                        "🚫 <b>الاستراتيجية ملغية</b>\n"
                        "📌 لم يتحقق الهدف المطلوب وتم تفعيل الوقف، لذلك تم إنهاء الرصد.",
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
_radar_no_opportunity_announced = False
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


def _radar_condition_lines(row, quote_data=None, gate_passed=None, gate_reason=None):
    """Explain every known scanner check and the final channel gate without inventing missing evidence."""
    row = row if isinstance(row, dict) else {}
    checks = row.get("radar_checks") if isinstance(row.get("radar_checks"), dict) else {}
    cls = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    quote_data = quote_data if isinstance(quote_data, dict) else {}
    labels = {
        "momentum": "زخم السهم",
        "sas_core": "شروط SAS الأساسية",
        "liquidity": "السيولة",
        "rvol": "الحجم النسبي RVOL",
        "target": "وجود هدف فني",
        "live_levels": "توفر المستويات الفنية",
        "no_distribution": "عدم وجود تصريف واضح",
        "no_bearish_hs": "عدم وجود نموذج انعكاس هابط قوي",
        "no_chase": "عدم وجود مخاطرة مطاردة سعرية",
        "advanced_confirmation": "التأكيد الفني المتقدم",
        "intraday_confirmation": "تأكيد الحركة خلال الجلسة",
        "breakout_confirmed": "تأكيد الاختراق",
        "accumulation": "علامات التجميع",
    }
    lines = []
    for key, label in labels.items():
        value = checks.get(key, cls.get(key))
        if value is None:
            continue
        if isinstance(value, bool):
            mark = "🟢 تحقق" if value else "🔴 لم يتحقق"
        else:
            mark = "📊 " + html.escape(str(value)[:120])
        lines.append(f"• {label}: <b>{mark}</b>")
    momentum_rvol = checks.get("momentum_rvol")
    threshold = checks.get("momentum_rvol_threshold")
    if momentum_rvol is not None or threshold is not None:
        lines.append(
            "• RVOL مقابل العتبة: <b>"
            + html.escape(str(momentum_rvol if momentum_rvol is not None else "غير متوفر"))
            + "× / "
            + html.escape(str(threshold if threshold is not None else "غير محددة"))
            + "×</b>"
        )
    quote_price = quote_data.get("price")
    quote_source = quote_data.get("source") or "غير متوفر"
    try:
        quote_ok = quote_price is not None and float(quote_price) > 0 and not quote_data.get("stale")
    except (TypeError, ValueError):
        quote_ok = False
    lines.append(f"• السعر الحي: <b>{'🟢 متوفر' if quote_ok else '🔴 غير متوفر/قديم'}</b> — المصدر: {html.escape(str(quote_source)[:100])}")
    confirmed = cls.get("opportunity_status") == "confirmed" and bool(cls.get("confirmation_ready"))
    lines.append(f"• اكتمال تأكيد الفرصة: <b>{'🟢 مكتملة' if confirmed else '🔴 غير مكتملة — مراقبة فقط'}</b>")
    if gate_passed is not None:
        lines.append(f"• بوابة النشر النهائية: <b>{'🟢 اجتاز' if gate_passed else '🔴 لم يجتز'}</b>")
    if gate_reason:
        lines.append("• السبب النهائي: <b>" + html.escape(str(gate_reason)[:300]) + "</b>")
    return lines


def _radar_diagnostic_message(row, quote_data, gate_reason):
    """Telegram message for detected candidates that do not qualify as a confirmed signal."""
    symbol = html.escape(str(row.get("symbol") or "غير معروف"))
    price = quote_data.get("price") if isinstance(quote_data, dict) else None
    change = quote_data.get("change_pct") if isinstance(quote_data, dict) else None
    try:
        price_line = f"💵 السعر الحي: <b>${float(price):.4f}</b>" if price is not None and float(price) > 0 else "💵 السعر الحي: <b>غير متوفر</b>"
    except (TypeError, ValueError):
        price_line = "💵 السعر الحي: <b>غير متوفر</b>"
    try:
        change_line = f"📈 التغير: <b>{float(change):+.2f}%</b>" if change is not None else "📈 التغير: <b>غير متوفر</b>"
    except (TypeError, ValueError):
        change_line = "📈 التغير: <b>غير متوفر</b>"
    checks = _radar_condition_lines(row, quote_data, False, gate_reason)
    return "\n".join([
        "📡 <b>SAS PRO | رصد وتشخيص</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🔎 السهم: <b>{symbol}</b>",
        price_line,
        change_line,
        "",
        "🟡 <b>لماذا أرسله الرادار؟</b>",
        "ظهر السهم ضمن نتائج الفحص الفني، لكن هذا التقرير ليس إشارة دخول مؤكدة.",
        "",
        "🧾 <b>الشروط التي تحققت والتي لم تتحقق</b>",
        *checks,
        "",
        "⚠️ هذا تقرير تشخيصي تعليمي، وليس توصية شراء أو بيع."
    ])


def _radar_channel_gate(status, row, quote_data, learning=None):
    """Final Telegram gate: broad scanner finds candidates, this gate decides what reaches the channel.
    
    Extended-hours prices can move on thin liquidity, so a price jump alone is never
    enough. We keep scanning broadly (so opportunities remain visible to the app/logs),
    but Telegram only receives an extended-hours signal after live-price, liquidity,
    momentum and independent technical confirmation agree.
    """
    session = str((status or {}).get("session") or "")
    extended = session in {"premarket", "afterhours", "night"}

    classification = row.get("classification") or {}
    # لا تسمح بوابة القناة أو التطبيق بفرصة "مراقبة" غير مكتملة.
    # يجب أن يعلن scanner.py صراحةً أن الهدف/الوقف/R:R مكتملة.
    if classification.get("opportunity_status") != "confirmed" or not bool(classification.get("confirmation_ready")):
        return False, "الفرصة ما زالت تحت المراقبة ولم تكتمل شروط التأكيد"
    targets = row.get("targets") or {}
    price = None
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

    # Universal anti-fake safeguards: channel messages must be backed by
    # an actual live quote. Never substitute a discovery snapshot or historical
    # Stooq close when the US market is actively being scanned.
    quote_source = str((quote_data or {}).get("source") or "").strip().lower()
    quote_price = (quote_data or {}).get("price")
    if price <= 0 or volume <= 0 or quote_price is None:
        return False, "بيانات السعر/الحجم الحي غير متاحة"
    if bool((quote_data or {}).get("stale")):
        return False, "السعر موسوم كبيانات قديمة"
    if not extended and (
        "stooq" in quote_source
        or "last close" in quote_source
        or "snapshot" in quote_source
        or quote_source in {"", "unavailable"}
    ):
        return False, "لا يوجد سعر لحظي موثوق للجلسة الحالية"
    if classification.get("distribution_risk") or classification.get("bearish_head_shoulders"):
        return False, "تناقض هابط قوي"
    if classification.get("chase_risk"):
        return False, "مطاردة سعرية"
    # لا نكرر قرار الرادار هنا. scanner.py هو صاحب قرار صلاحية الفرصة.
    # بوابة القناة تتحقق فقط من سلامة بيانات النشر ومخاطر الإرسال، حتى لا
    # تظهر الفرصة في الرادار ثم تختفي بسبب فلتر ثانٍ مختلف.
    # محرك الرادار هو صاحب قرار اكتشاف الفرصة. لا نعيد هنا تطبيق مرشح RVOL
    # والحركة للمرة الثانية، لأن ذلك كان يجعل السهم يظهر في نتائج الرادار ثم
    # يختفي قبل القناة. هذه البوابة مسؤولة عن سلامة النشر فقط:
    # سعر لحظي حقيقي + بيانات حجم + عدم وجود تناقض هابط/مطاردة + وقف منطقي.
    # قوة الفرصة وترتيب أفضل 5 تأتي من scanner.py.
    rvol_gate = float(classification.get("rvol") or 0)
    intraday_confirmation = bool(classification.get("intraday_confirmation"))
    breakout_confirmed = bool(classification.get("breakout_confirmed"))
    accumulation = bool(classification.get("accumulation"))
    fib_zone = bool((classification.get("fibonacci") or {}).get("zone"))

    # A target is optional. If no real resistance is available, publish the
    # opportunity without a fabricated target; the report must say that no
    # confirmed target exists rather than inventing one.

    # Prevent misleading R:R created by an unrealistically tight stop.
    # The stop must leave a minimum 0.75% breathing room from the live entry.
    try:
        stop_level = float(targets.get("exit") or targets.get("stop") or 0)
    except (TypeError, ValueError):
        stop_level = 0
    if stop_level > 0 and price > 0:
        stop_distance_pct = ((price - stop_level) / price) * 100
        if stop_distance_pct < 0.75:
            return False, "وقف ضيق بشكل غير واقعي"

    if not extended:
        # لا نضع حدًا ثانيًا للحركة أو RVOL هنا. إذا وصل السهم إلى هذه
        # المرحلة فهو مرشح من محرك SAS، ونحتاج فقط إلى بيانات لحظية حقيقية
        # حتى لا يتحول فشل مزود الاقتباس إلى إشارة وهمية.
        if price > 0 and volume > 0:
            return True, "جلسة رئيسية — مرشح SAS مؤكد ببيانات لحظية"
        return False, "بيانات السعر/الحجم الحي غير متاحة"

    # Extended-hours channel messages require an explicitly extended quote.
    # If the provider cannot verify that the price is from the active extended
    # session, keep the candidate in the radar/app but do not publish it.
    if not bool((quote_data or {}).get("is_extended_hours")):
        return False, "السعر الحالي غير موثق كبيانات ممتدة"

    # Extended hours: reject price-only spikes. Nasdaq/SEC note that these
    # sessions generally have lower liquidity and higher volatility.
    if change < 1.0:
        return False, "حركة ممتدة ضعيفة"
    # بعد الإغلاق نرصد الحركة الحقيقية حتى لو كانت السيولة أقل من جلسة التداول
    # الرئيسية، لكن لا نسمح بارتفاع سعري وحيد دون تأكيد.
    if dollar_volume < 500_000:
        return False, "سيولة ممتدة غير كافية"

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
    global _radar_open_announced, _radar_no_opportunity_announced
    cycle_started = utcnow()
    status = market_status()
    session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    run = RadarRun(
        session_date=session_date,
        session=str(status.get("session") or "unknown"),
        started_at=cycle_started,
        status="running",
    )
    async with SessionLocal() as db:
        db.add(run)
        await db.commit()
        await db.refresh(run)
    logger.info("RADAR_CYCLE_STARTED id=%s session=%s session_date=%s", run.id, status.get("session"), session_date)

    async def finish_cycle(final_status, diagnostics=None, *, gate_passed=0, sent=0, app_only=0, gate_rejections=None, top_opportunities=None, error=None):
        finished = utcnow()
        run.status = final_status
        run.finished_at = finished
        run.duration_seconds = round((finished - cycle_started).total_seconds(), 2)
        diagnostics = diagnostics or {}
        run.candidates = int(diagnostics.get("candidates") or 0)
        run.shortlist = int(diagnostics.get("shortlist") or 0)
        run.passed = int(diagnostics.get("passed") or 0)
        run.filtered = int(diagnostics.get("filtered") or 0)
        run.errors = int(diagnostics.get("errors") or 0)
        run.channel_gate_passed = int(gate_passed or 0)
        run.channel_sent = int(sent or 0)
        run.channel_app_only = int(app_only or 0)
        run.channel_gate_rejections = json.dumps(gate_rejections or {}, ensure_ascii=False)
        run.top_opportunities = json.dumps(top_opportunities or [], ensure_ascii=False)
        run.diagnostics = json.dumps(diagnostics, ensure_ascii=False)
        if error:
            run.error_type = type(error).__name__
            run.error_message = str(error)[:4000]
        async with SessionLocal() as db:
            db.add(run)
            await db.commit()
        logger.info(
            "RADAR_CYCLE_FINISHED id=%s status=%s duration=%.1fs candidates=%d shortlist=%d passed=%d filtered=%d errors=%d gate_passed=%d sent=%d app_only=%d",
            run.id, final_status, run.duration_seconds or 0, run.candidates, run.shortlist,
            run.passed, run.filtered, run.errors, run.channel_gate_passed,
            run.channel_sent, run.channel_app_only,
        )

    if not settings.telegram_channel_id or not settings.telegram_bot_token:
        await finish_cycle("skipped", {"status": "telegram_not_configured"})
        logger.warning("Stock radar skipped: Telegram channel/token is not configured.")
        return

    try:
        learning_profile = await get_radar_profile()
    except Exception:
        learning_profile = {"rvol_floor": 0.50, "change_floor": 0.0, "samples": 0}

    # الرادار يعمل فقط داخل جلسات الرصد الفعلية: البري ماركت، الرئيسية،
    # بعد الإغلاق، والليل بعد إطلاق جلسة Nasdaq الجديدة.
    # الفترة السابقة للبري ماركت ليست جلسة رصد؛ ننتظر 04:00 ET.
    if status["holiday"] or status["session"] in {"weekend", "overnight", "night_pending"}:
        _radar_open_announced = False
        _radar_no_opportunity_announced = False
        await finish_cycle("skipped", {"status": "market_closed", "session": status.get("session"), "holiday": bool(status.get("holiday"))})
        logger.info("Stock radar skipped: market holiday/weekend (session=%s).", status.get("session"))
        return

    # لا نرسل رسالة "لا توجد فرصة" قبل انتهاء الفحص؛ القرار يعتمد على
    # بوابة القناة الفعلية، وليس على مجرد بدء الدورة.

    try:
        from .scanner import scan_us_low_price_stocks
        from .main import build_report
        from .market import quote
        from .news import company_fundamentals
        from .ai_radar import analyze_stock

        scan_result = await scan_us_low_price_stocks()
        diagnostics = scan_result.get("diagnostics") or {}
        logger.info(
            "RADAR_SCAN_DIAGNOSTICS id=%s candidates=%s shortlist=%s passed=%s filtered=%s errors=%s",
            run.id, diagnostics.get("candidates", 0), diagnostics.get("shortlist", 0),
            diagnostics.get("passed", 0), diagnostics.get("filtered", 0), diagnostics.get("errors", 0),
        )
        if diagnostics.get("twelve_data_quota_exhausted"):
            # Twelve Data احتياطي فقط؛ لا نوقف الرادار ما دامت نتائج PanWatch
            # صالحة. نستمر بإرسال إشارات الأسهم ونكتفي بتسجيل حالة الحصة.
            logger.warning(
                "Twelve Data quota exhausted; continuing radar with PanWatch/primary data."
            )
        rows = scan_result.get("stocks", [])
        # Quality Score is deterministic and runs before AI. Every candidate
        # receives the same weighted model; AI only explains the evidence later.
        for row in rows:
            try:
                row["quality_score"] = score_quality(row)
            except Exception:
                logger.exception("Quality score failed for %s; keeping safe neutral score.", row.get("symbol"))
                row["quality_score"] = score_quality({})
        # القناة: نرتب جميع الفرص أولاً ثم نسمح بأفضل 5 إشارات فقط.
        # التطبيق يستقبل القائمة الكاملة دون هذا القيد.
        rows = sorted(
            rows,
            key=lambda r: (
                float((r.get("quality_score") or {}).get("score") or 0),
                float(r.get("opening_opportunity_score") or 0),
                float(r.get("intraday_confirmation_score") or 0),
                float(r.get("change_pct") or 0),
                float(r.get("momentum_rvol_10d") or 0),
                float(r.get("price") or 0) * float(r.get("volume") or 0),
            ),
            reverse=True,
        )
        logger.info("Stock radar scan completed: %d result(s); channel_limit=5; diagnostics=%s", len(rows), diagnostics)

        # تحقق الشرعية لأفضل المرشحين بالتوازي؛ لا يغيّر ترتيب الرادار ولا بوابة السعر.
        shariah_map = {}
        sh_rows = rows[:5]
        sh_results = await asyncio.gather(
            *(asyncio.wait_for(check_shariah(str(x.get("symbol") or "").upper()), timeout=10.0) for x in sh_rows),
            return_exceptions=True,
        )
        for x, result in zip(sh_rows, sh_results):
            symbol = str(x.get("symbol") or "").upper()
            shariah_map[symbol] = result if isinstance(result, dict) else {
                "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق", "verified": False,
                "message": "تعذر التحقق الآن؛ لم يتم تأليف أي نتيجة.", "sources": []
            }

        session_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        async with SessionLocal() as db:
            # كل سهم اجتاز فلاتر الرادار يُرسل كتقرير مستقل.
            # لا نرسل جدول Daily Momentum مجمعًا؛ تفاصيل السهم وشروط اجتيازه
            # تظهر داخل تقريره الفردي عبر build_report().
            # جهّز الإشارات الحالية دفعة واحدة بدل SELECT لكل سهم.
            symbols = [str(r.get("symbol") or "").upper() for r in rows if r.get("symbol")]
            existing_map = {}
            cycle_stats = {"rows": len(rows), "skipped": 0, "reanalyzed": 0, "sent": 0, "failed": 0}
            if symbols:
                existing_rows = (await db.execute(
                    select(RadarSignal).where(
                        RadarSignal.session_date == session_date,
                        RadarSignal.symbol.in_(symbols),
                    )
                )).scalars().all()
                existing_map = {str(x.symbol).upper(): x for x in existing_rows}

            # حد القناة يومي، أما التطبيق فليس قائمة تاريخية ثابتة:
            # يعرض دائمًا فرص الرادار الحالية، وبحد أقصى 15 فرصة.
            # إذا فقد السهم شروط الرادار يُوسم غير نشط ويخرج من التطبيق،
            # وتدخل فرصة مؤهلة أخرى مكانه.
            daily_app_limit = max(1, int(settings.radar_app_daily_limit or 15))
            # لا نضع سقفًا يوميًا لرسائل القناة: كل نتيجة أعادها الرادار تُرسل كتقرير منفصل أو تشخيص واضح.\n            channel_daily_limit = max(1, daily_channel_sent + len(rows) + 1)
            daily_rows = (await db.execute(
                select(RadarSignal).where(RadarSignal.session_date == session_date)
            )).scalars().all()
            daily_channel_sent = sum(1 for x in daily_rows if x.telegram_message_id)
            # سقف التطبيق = 15 فرصة مؤكدة ونشطة حاليًا، وليس 15 سجلًا تاريخيًا.
            # نحسب القائمة الحالية من نتائج هذه الدورة فقط بعد نجاح الفحص.
            current_confirmed_symbols = {
                str(r.get("symbol") or "").upper()
                for r in rows
                if str((r.get("classification") or {}).get("opportunity_status") or "") == "confirmed"
                and bool((r.get("classification") or {}).get("confirmation_ready"))
            }
            app_active_symbols = {
                str(x.symbol).upper()
                for x in daily_rows
                if str(x.symbol or "").upper() in current_confirmed_symbols
                and bool((json.loads(x.payload or "{}") if x.payload else {}).get("radar_active"))
            }
            app_active_count = len(app_active_symbols)

            # إذا اختفى سهم كان نشطًا من قائمة الفرص المؤكدة في دورة ناجحة،
            # نخرجه من التطبيق حتى تدخل فرصة مؤكدة أخرى مكانه. لا نغيّر سجلات
            # القناة المنشورة أو نتائج الأهداف؛ هذا يغيّر حالة الرصد داخل التطبيق فقط.
            if int(diagnostics.get("errors") or 0) == 0:
                for old in daily_rows:
                    old_symbol = str(old.symbol or "").upper()
                    if old_symbol not in current_confirmed_symbols:
                        try:
                            old_payload = json.loads(old.payload or "{}")
                        except Exception:
                            old_payload = {}
                        if old_payload.get("radar_active"):
                            old_payload["radar_active"] = False
                            old_payload["strategy_status"] = "inactive"
                            old_payload["strategy_inactive_reason"] = "فقد شروط التأكيد في دورة رادار لاحقة"
                            old.payload = json.dumps(old_payload, ensure_ascii=False)
                            old.created_at = utcnow()
                            logger.info(
                                "Radar active opportunity removed: %s | reason=lost_confirmation",
                                old_symbol,
                            )
                await db.commit()

            logger.info(
                "RADAR_DAILY_LIMITS session=%s app_current_limit=%d channel=%d/%d",
                session_date, daily_app_limit, daily_channel_sent, channel_daily_limit,
            )

            # اجلب الأسعار الحية دفعةً واحدة بالتوازي. السعر الحي شرط نشر، لكنه
            # لا ينبغي أن يجعل 20-100 سهم ينتظرون بعضهم بالتسلسل.
            quote_sem = asyncio.Semaphore(10)
            async def _live_quote(row):
                symbol = str(row.get("symbol") or "").upper()
                async with quote_sem:
                    try:
                        q = await quote(symbol, prefer_extended=status.get("session") in {"premarket", "afterhours", "night"})
                        return symbol, q
                    except Exception:
                        logger.exception("Quote provider failed for %s; live quote unavailable.", symbol)
                        return symbol, {"symbol": symbol, "price": None, "source": "unavailable"}

            quote_pairs = await asyncio.gather(*(_live_quote(row) for row in rows), return_exceptions=False)
            live_quotes = dict(quote_pairs)
            channel_gate_passed = 0
            channel_app_only = 0
            gate_rejections = {}
            delivery_errors = {}
            # تشخيص حاسم للدورة: يوضح أين اختفت الفرصة بدل الاكتفاء بعدد النتائج النهائي.
            confirmation_stats = {
                "confirmed": 0,
                "watch": 0,
                "live_quote_unavailable": 0,
                "gate_passed": 0,
                "gate_rejected": 0,
            }

            # كل سهم مؤهل يظهر في التطبيق ضمن سقف اليوم؛ القناة لها سقف يومي مستقل.
            # لا نرسل جدول Daily Momentum مجمعًا؛ تفاصيل السهم وشروط اجتيازه
            # تظهر داخل تقريره الفردي عبر build_report().
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                if not symbol:
                    continue

                existing = existing_map.get(symbol)
                # لا نكرر نفس السهم بلا سبب.
                if existing and existing.telegram_message_id:
                    try:
                        previous = json.loads(existing.payload or "{}")
                    except Exception:
                        previous = {}
                    previous_volume = float(previous.get("volume") or 0)
                    current_volume = float(row.get("volume") or 0)
                    previous_change = float(previous.get("change_pct") or 0)
                    current_change = float(row.get("change_pct") or 0)
                    volume_jump = previous_volume > 0 and current_volume >= previous_volume * 2.0
                    price_jump = abs(current_change - previous_change) >= 5.0
                    if not (volume_jump or price_jump):
                        cycle_stats["skipped"] += 1
                        _radar_seen.add(symbol)
                        logger.info("Radar duplicate skipped: %s | reason=existing_signal_same_session volume_jump=%s price_jump=%s", symbol, volume_jump, price_jump)
                        continue
                    cycle_stats["reanalyzed"] += 1
                    logger.info("Radar reanalysis triggered: %s | volume_jump=%s price_jump=%s", symbol, volume_jump, price_jump)
                elif existing:
                    cycle_stats["reanalyzed"] += 1
                    logger.info("Radar pending candidate rechecked: %s", symbol)

                q = live_quotes.get(symbol) or {"symbol": symbol, "price": None, "source": "unavailable"}
                # لا نستخدم لقطة الاكتشاف كبديل للسعر الحي عند النشر.
                if q.get("price") is None:
                    logger.warning("Radar live quote unavailable; keeping candidate in watchlist: %s source=%s", symbol, q.get("source"))
                classification = row.get("classification") or {}
                if classification.get("opportunity_status") == "confirmed" and bool(classification.get("confirmation_ready")):
                    confirmation_stats["confirmed"] += 1
                else:
                    confirmation_stats["watch"] += 1
                if q.get("price") is None:
                    confirmation_stats["live_quote_unavailable"] += 1
                tech = {
                    **(row.get("targets") or {}),
                    "radar_checks": row.get("radar_checks") or {},
                    "market_session": status.get("label_ar"),
                    "market_session_code": status.get("session"),
                }

                channel_ok, channel_reason = _radar_channel_gate(status, row, q, learning_profile)
                if not channel_ok:
                    confirmation_stats["gate_rejected"] += 1
                    gate_rejections[channel_reason] = int(gate_rejections.get(channel_reason, 0)) + 1
                    cycle_stats["skipped"] += 1
                    row["channel_gate"] = {
                        "passed": False,
                        "session": status.get("session"),
                        "reason": channel_reason,
                    }
                    row["radar_active"] = False
                    # Keep rejected-but-promising candidates as watch records.
                    # They do not generate Telegram messages, but they are
                    # rechecked on the next cycle so a real confirmation is not lost.
                    try:
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
                    except Exception:
                        await db.rollback()
                        logger.exception("Radar watch candidate persistence failed: %s", symbol)
                    # المستخدم يريد رؤية كل سهم رصده المحرك، حتى عندما لا يجتاز بوابة الإشارة.
                    # أرسل تقرير تشخيصي يشرح الشروط التي تحققت والتي لم تتحقق؛ لا نسميه فرصة مؤكدة.
                    try:
                        diagnostic_message = _radar_diagnostic_message(row, q, channel_reason)
                        result = await send_message(settings.telegram_channel_id, diagnostic_message)
                        diagnostic_id = result.get("message_id") if isinstance(result, dict) else None
                        if diagnostic_id and existing:
                            existing.telegram_message_id = int(diagnostic_id)
                            await db.commit()
                            cycle_stats["sent"] += 1
                        elif not diagnostic_id:
                            cycle_stats["failed"] += 1
                            delivery_errors[symbol] = "لم يُرجع Telegram message_id للتقرير التشخيصي"
                    except Exception as exc:
                        cycle_stats["failed"] += 1
                        delivery_errors[symbol] = f"{type(exc).__name__}: {str(exc)[:250]}"
                        logger.exception("Radar diagnostic Telegram delivery failed: %s", symbol)
                    logger.info(
                        "Radar candidate diagnostic sent: %s | session=%s | gate_reason=%s",
                        symbol, status.get("session"), channel_reason,
                    )
                    continue

                channel_gate_passed += 1
                confirmation_stats["gate_passed"] += 1
                # لا نسمح بأكثر من 15 فرصة مؤكدة نشطة في التطبيق.
                # ترتيب rows تم حسمه مسبقًا حسب جودة الفرصة، لذلك الفرص خارج
                # أول 15 تبقى مراقبة ولا تظهر كفرص نشطة في التطبيق.
                # سقف التطبيق لا يمنع نشر التشخيص في القناة؛ يحدد الظهور داخل التطبيق فقط.
                app_has_capacity = symbol in app_active_symbols or app_active_count < daily_app_limit
                if app_has_capacity and symbol not in app_active_symbols:
                    app_active_symbols.add(symbol)
                    app_active_count += 1
                row["channel_gate"] = {
                    "passed": True,
                    "session": status.get("session"),
                    "reason": channel_reason,
                }
                row["radar_active"] = bool(app_has_capacity)
                if not app_has_capacity:
                    row["app_visibility_reason"] = f"تم الوصول إلى الحد الحالي للتطبيق ({daily_app_limit})"
                else:
                    row.pop("app_visibility_reason", None)

                # التطبيق يعرض فقط الفرص المؤكدة التي دخلت سقف الـ15 الحالي.
                if daily_channel_sent >= channel_daily_limit:
                    channel_app_only += 1
                    row["channel_delivery"] = {
                        "published": False,
                        "reason": f"تم الوصول إلى الحد اليومي للقناة ({channel_daily_limit})",
                        "limit": channel_daily_limit,
                    }
                    try:
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
                    except Exception:
                        await db.rollback()
                        logger.exception("Radar app-only candidate persistence failed: %s", symbol)
                    _radar_seen.add(symbol)
                    continue

                # هذا السهم أصبح ضمن قائمة فرص اليوم في التطبيق.
                daily_app_symbols.add(symbol)
                if daily_channel_sent >= channel_daily_limit:
                    channel_app_only += 1
                    row["channel_delivery"] = {
                        "published": False,
                        "reason": f"تم الوصول إلى الحد اليومي للقناة ({channel_daily_limit})",
                        "limit": channel_daily_limit,
                    }
                    try:
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
                    except Exception:
                        await db.rollback()
                        logger.exception("Radar app-only candidate persistence failed: %s", symbol)
                    _radar_seen.add(symbol)
                    continue

                row["channel_delivery"] = {
                    "published": True,
                    "rank": daily_channel_sent + 1,
                    "limit": channel_daily_limit,
                }

                # AI enrichment runs only after the technical radar has already
                # selected the candidate. It cannot create a signal, target,
                # stop, or price level; it only explains supplied evidence.
                if ai_used < ai_budget:
                    try:
                        fundamentals = await company_fundamentals(symbol)
                        # TipRanks دليل تحليلي إضافي للذكاء الاصطناعي فقط.
                        # لا يدخل في بوابة الرادار ولا ينشئ سعراً/هدفاً/وقفاً.
                        from .news import tipranks_analysis
                        tipranks_data = await tipranks_analysis(symbol)
                        ai_analysis = await analyze_stock(
                            symbol,
                            company={
                                "name": row.get("name") or symbol,
                                "exchange": row.get("exchange"),
                            },
                            news=row.get("news_items") or [],
                            fundamentals=fundamentals,
                            tipranks=tipranks_data,
                            market={
                                "momentum_section": row.get("momentum_section"),
                                "quality_score": row.get("quality_score"),
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
                        "quality_score": row.get("quality_score"),
                            "tipranks_analysis": tipranks_data,
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

                # ثبّت بيانات السوق الأساسية داخل التقرير لكل إشارة، حتى لو لم نستخدم AI.
                # لا ننشئ float أو "أسهم متاحة" من التخمين؛ نعرض فقط الأسهم القائمة إذا وفرها المصدر.
                tech.update({
                    "shariah": shariah_map.get(symbol) or {
                        "status": "unknown", "status_ar": "غير واضح / يحتاج تحقق",
                        "verified": False,
                        "message": "لم تتوفر نتيجة مباشرة؛ لم يتم تأليف حكم.",
                        "sources": [],
                    },
                    "volume": row.get("volume"),
                    "dollar_volume": (
                        float(q.get("price") or row.get("live_price") or row.get("price") or 0)
                        * float(row.get("volume") or 0)
                        if float(q.get("price") or row.get("live_price") or row.get("price") or 0) > 0
                        and float(row.get("volume") or 0) > 0
                        else None
                    ),
                    "exchange": row.get("exchange") or q.get("exchange"),
                    "live_price_source": q.get("source") or row.get("live_price_source"),
                    "fundamentals": tech.get("fundamentals") or {},
                })
                report = build_report(symbol, q, tech, classification)
                # Add an explicit pass/fail checklist so readers can see why this stock reached the channel.
                report += "\n\n━━━━━━━━━━━━━━━━━━\n\n🧾 <b>شروط الرادار وسبب النشر</b>\n\n"
                report += "\n".join(_radar_condition_lines(row, q, True, channel_reason))
                report += "\n\n📌 أُرسل لأن محرك الرادار اجتاز بوابة النشر النهائية؛ التفاصيل أعلاه توضح الشروط الفعلية.\n"

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
                    delivery_error = None
                    for delivery_attempt in range(1, 3):
                        try:
                            result = await send_message(settings.telegram_channel_id, report)
                            message_id = result.get("message_id") if isinstance(result, dict) else None
                            if message_id:
                                break
                            delivery_error = "Telegram لم يُرجع message_id"
                        except Exception as exc:
                            delivery_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                            logger.exception("Radar Telegram delivery failed: symbol=%s attempt=%d/2", symbol, delivery_attempt)
                            if delivery_attempt < 2:
                                await asyncio.sleep(1.0)
                    if not message_id:
                        cycle_stats["failed"] += 1
                        delivery_errors[symbol] = delivery_error or "سبب إرسال غير معروف"
                        logger.error("RADAR_CHANNEL_DELIVERY_FAILED symbol=%s error=%s", symbol, delivery_errors[symbol])

                    if message_id:
                        existing.telegram_message_id = int(message_id)
                        await db.commit()

                    if message_id:
                        cycle_stats["sent"] += 1
                        daily_channel_sent += 1
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

        # رسالة الحالة تُرسل فقط عند عدم وجود أي سهم يستحق الإرسال للقناة.
        # لا تتكرر في كل دورة: تُعاد فقط عندما تتغير الحالة من "فرصة موجودة"
        # إلى "لا توجد فرصة مؤكدة". وإذا ظهرت فرصة، نعيد تسليح الرسالة للدورة التالية.
        if channel_gate_passed > 0:
            _radar_no_opportunity_announced = False
        elif not _radar_no_opportunity_announced:
            try:
                await send_message(settings.telegram_channel_id, RADAR_STATUS)
                _radar_no_opportunity_announced = True
                logger.info(
                    "Stock radar no-opportunity status sent: no channel-worthy opportunity in cycle id=%s.",
                    run.id,
                )
            except Exception as exc:
                logger.exception(
                    "Stock radar no-opportunity status failed; continuing radar cycle: %s",
                    exc,
                )

        top = [
            {
                "symbol": str(x.get("symbol") or ""),
                "score": x.get("opening_opportunity_score"),
                "quality_score": (x.get("quality_score") or {}).get("score"),
                "quality_label": (x.get("quality_score") or {}).get("label"),
                "change_pct": x.get("live_change_pct") if x.get("live_change_pct") is not None else x.get("change_pct"),
                "rvol": (x.get("classification") or {}).get("rvol"),
                "quote_price": x.get("live_price"),
                "quote_source": x.get("live_price_source"),
                "channel_gate": (x.get("channel_gate") or {}).get("passed"),
                "channel_gate_reason": (x.get("channel_gate") or {}).get("reason"),
                "delivery": (x.get("channel_delivery") or {}).get("published"),
            }
            for x in rows[:10]
        ]
        # أي سهم من رصد اليوم لم يعد ضمن النتائج المؤهلة الحالية يخرج من التطبيق.
        # لا نحذف السجل التاريخي؛ فقط نعطله حتى يبقى Forward Test محفوظًا.
        current_symbols = {
            str(x.get("symbol") or "").upper()
            for x in rows
            if str(x.get("symbol") or "").strip()
        }
        current_active_symbols = {
            str(x.get("symbol") or "").upper()
            for x in rows
            if bool(x.get("radar_active"))
        }
        try:
            today_signals = (await db.execute(
                select(RadarSignal).where(RadarSignal.session_date == session_date)
            )).scalars().all()
            for signal in today_signals:
                symbol = str(signal.symbol or "").upper()
                if symbol not in current_symbols or symbol not in current_active_symbols:
                    try:
                        payload = json.loads(signal.payload or "{}")
                    except (TypeError, ValueError):
                        payload = {}
                    payload["radar_active"] = False
                    signal.payload = json.dumps(payload, ensure_ascii=False)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Radar active-state refresh failed")

        diagnostics["confirmation_stats"] = confirmation_stats
        diagnostics["gate_rejections"] = gate_rejections
        diagnostics["delivery_errors"] = delivery_errors
        diagnostics["delivery_summary"] = {
            "sent": cycle_stats["sent"],
            "failed": cycle_stats["failed"],
            "errors": len(delivery_errors),
        }
        logger.info(
            "RADAR_FUNNEL id=%s candidates=%d shortlist=%d passed=%d confirmed=%d watch=%d quote_missing=%d gate_passed=%d gate_rejected=%d sent=%d delivery_failed=%d rejections=%s",
            run.id,
            int(diagnostics.get("candidates") or 0),
            int(diagnostics.get("shortlist") or 0),
            int(diagnostics.get("passed") or 0),
            confirmation_stats["confirmed"],
            confirmation_stats["watch"],
            confirmation_stats["live_quote_unavailable"],
            confirmation_stats["gate_passed"],
            confirmation_stats["gate_rejected"],
            cycle_stats["sent"],
            len(delivery_errors),
            gate_rejections,
        )
        await finish_cycle(
            "success",
            diagnostics,
            gate_passed=channel_gate_passed,
            sent=cycle_stats["sent"],
            app_only=channel_app_only,
            gate_rejections=gate_rejections,
            top_opportunities=top,
        )
        if delivery_errors:
            logger.error("RADAR_CHANNEL_DELIVERY_SUMMARY failed=%d details=%s", len(delivery_errors), delivery_errors)
    except Exception as exc:
        logger.exception("Stock radar cycle failed.")
        await finish_cycle("failed", locals().get("diagnostics") or {}, error=exc)
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
        # Quality Score is evaluated only against persisted live outcomes.
        outcome_ids = {x.radar_signal_id for x in outcomes}
        signals = (await db.execute(
            select(RadarSignal).where(RadarSignal.id.in_(outcome_ids))
        )).scalars().all() if outcome_ids else []
        signal_by_id = {x.id: x for x in signals}
        quality_records = []
        for outcome in outcomes:
            signal = signal_by_id.get(outcome.radar_signal_id)
            if not signal:
                continue
            try:
                payload = json.loads(signal.payload or "{}")
            except (TypeError, ValueError):
                payload = {}
            quality_records.append({
                "quality_score": payload.get("quality_score"),
                "status": outcome.status,
                "achieved_target": outcome.achieved_target,
            })
        quality_summary = summarize_quality_outcomes(quality_records)
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
            "🧪 <b>جودة الرادار حسب Quality Score</b>",
        ]
        for label in ("85-100", "75-84", "65-74", "under-65"):
            q = quality_summary[label]
            rate = f"{q['success_rate']:.1f}%" if q["success_rate"] is not None else "لا توجد حالات مكتملة"
            lines.append(
                f"• {label}: مكتملة {q['completed']} | ناجحة {q['success']} | فشل {q['failure']} | نشطة {q['active']} | النجاح {rate}"
            )
        lines.extend([
            "",
            "🏆 <b>الأسهم التي حققت أهدافها</b>",
        ])
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


# Compatibility export: the scheduler now lives in app/scheduler.py.
# Keep this import so existing callers that import scheduler from jobs.py continue to work.
from .scheduler import scheduler
