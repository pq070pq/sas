#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signal Desk Pro — بوت رصد الأسهم الأمريكية الانفجارية + استقبال تنبيهات TradingView
=====================================================================================
يجمع بين:
  1) فحص داخلي دوري لسلة أسهم أمريكية (كبار السوق + أسهم رخيصة/زخم عالي) عبر Finnhub،
     يعمل فقط أثناء أوقات تداول السوق الأمريكي.
  2) استقبال تنبيهات Pine Script من TradingView عبر /webhook محمي برمز سري.
  3) داشبورد ويب + إرسال تلجرام موحّد التنسيق للمصدرين.

الإصلاحات مقارنة بالنسخة السابقة:
  - حذف "الأهداف السعرية" الوهمية (كانت نسب ثابتة بدون أي تحليل فني حقيقي).
  - تصنيف الأخبار الآن بكلمات مفتاحية إيجابية/سلبية بوضوح، بدل افتراض إنها
    دايمًا "داعمة للانفجار". ومُعلَّم صراحة كـ"تصنيف آلي تقريبي".
  - /webhook أصبح محمي برمز سري (WEBHOOK_SECRET_TOKEN)، يرفض أي طلب بدونه.
  - تبريد (cooldown) بين تنبيهات نفس الرمز عشان ما يصير سبام لنفس الحركة.
  - الفحص كامل مقتصر على الأسهم الأمريكية فقط، ويعمل حصريًا أثناء
    أوقات تداول السوق الأمريكي (9:30 ص - 4:00 م بتوقيت نيويورك).
  - إذا فشل جلب بيانات الشموع/الحجم لسهم معيّن (شائع بخطة Finnhub المجانية
    للأسهم الأمريكية)، يرجع لتحليل بالسعر اللحظي فقط، ويوضّح بالرسالة إن
    بيانات الحجم غير متوفرة، بدل ما يفشل بصمت أو يعطي بيانات مضللة.

المتغيرات البيئية المطلوبة:
  FINNHUB_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHANNEL_ID
  WEBHOOK_SECRET_TOKEN   (اختاره أنت بنفسك، سلسلة عشوائية طويلة)
"""

import os
import time
import threading
from datetime import datetime, timedelta
from collections import deque

import requests
import pytz
from flask import Flask, request, jsonify, render_template

# ============================ الإعدادات ============================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if TELEGRAM_BOT_TOKEN.startswith("bot"):
    TELEGRAM_BOT_TOKEN = TELEGRAM_BOT_TOKEN[3:]
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN", "")

SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 300))
MIN_PRICE_CHANGE_PCT = float(os.environ.get("MIN_PRICE_CHANGE_PCT", 4.0))
VOLUME_MULTIPLIER = float(os.environ.get("VOLUME_MULTIPLIER", 2.0))
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", 20))

# نطاق السعر المستهدف: من السنتات إلى 100 دولار
PRICE_MIN = float(os.environ.get("PRICE_MIN", 0.01))
PRICE_MAX = float(os.environ.get("PRICE_MAX", 100.0))

NY_TZ = pytz.timezone("America/New_York")

# قائمة الأصول: أسهم فقط (كبار السوق + قائمة أسهم رخيصة/زخم عالي)
# تُفحص فقط أثناء أوقات تداول السوق الأمريكي (راجع is_market_open)
ASSETS = {
    "AAPL": {"name": "أبل (AAPL)", "category": "stock"},
    "TSLA": {"name": "تسلا (TSLA)", "category": "stock"},
    "NVDA": {"name": "إنفيديا (NVDA)", "category": "stock"},
    "AMD": {"name": "إيه إم دي (AMD)", "category": "stock"},
    "COIN": {"name": "كوينبيس (COIN)", "category": "stock"},
    "SPY": {"name": "إس آند بي 500 (SPY)", "category": "stock"},
    "QQQ": {"name": "ناسداك (QQQ)", "category": "stock"},

    # === قائمة أسهم رخيصة/عالية الزخم ===
    # حدّثها يدويًا من فاحص TradingView Stock Screener (فلتر: السعر بين
    # $0.01-$100، Relative Volume > 3، Change % > 5) لأن Finnhub المجاني
    # ما يوفر فاحص أسهم صغيرة تلقائي بدقة كافية.
    # مثال (بدّلها برموز فعلية محدثة يوميًا):
    # "GME": {"name": "GameStop (GME)", "category": "stock"},
}

SIGNALS = deque(maxlen=100)
LAST_ALERT_TIME = {}          # symbol -> datetime آخر تنبيه أُرسل له
PRICE_ROLLING = {}             # symbol -> deque أسعار لحساب momentum/RSI تقريبي
STATE_LOCK = threading.Lock()

STATUS = {
    "last_scan_time": None,
    "last_scan_symbols_count": 0,
    "next_scan_eta_seconds": SCAN_INTERVAL_SECONDS,
    "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID),
    "finnhub_configured": bool(FINNHUB_API_KEY),
    "webhook_secured": bool(WEBHOOK_SECRET_TOKEN),
}

app = Flask(__name__)

POSITIVE_KEYWORDS = [
    "beats", "surge", "upgrade", "record", "approval", "acquisition",
    "partnership", "breakthrough", "soars", "jumps", "raises guidance",
    "buyback", "outperform", "profit rise",
]
NEGATIVE_KEYWORDS = [
    "lawsuit", "downgrade", "recall", "investigation", "bankruptcy",
    "delisting", "fraud", "misses", "plunge", "halts", "resigns",
    "sec charges", "restatement", "default", "layoffs",
]

# ============================ أدوات مساعدة ============================


def is_market_open() -> bool:
    now = datetime.now(NY_TZ)
    if now.weekday() >= 5:
        return False
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_time <= now <= close_time


def calculate_rsi(closes: list, period: int = 14) -> float:
    try:
        if not closes or len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)
    except Exception:
        return None


def classify_headline(headline: str) -> str:
    """تصنيف تقريبي بالكلمات المفتاحية فقط — ليس تحليل مشاعر حقيقي"""
    text = headline.lower()
    if any(k in text for k in NEGATIVE_KEYWORDS):
        return "negative"
    if any(k in text for k in POSITIVE_KEYWORDS):
        return "positive"
    return "neutral"


def get_news_context(symbol: str):
    if not FINNHUB_API_KEY or ":" in symbol:  # نتخطى الكريبتو/الفوركس
        return None
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        since = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        url = "https://finnhub.io/api/v1/company-news"
        resp = requests.get(
            url,
            params={"symbol": symbol, "from": since, "to": today, "token": FINNHUB_API_KEY},
            timeout=6,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                headline = data[0].get("headline", "")
                return {"headline": headline, "classification": classify_headline(headline)}
    except Exception as e:
        print(f"[خطأ أخبار] {symbol}: {e}")
    return None


def fetch_quote(symbol: str):
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data or data.get("c") in (None, 0):
            return None
        return {
            "current": float(data["c"]),
            "open": float(data.get("o") or 0),
            "high": float(data.get("h") or 0),
            "low": float(data.get("l") or 0),
            "prev_close": float(data.get("pc") or 0),
        }
    except Exception as e:
        print(f"[خطأ سعر] {symbol}: {e}")
        return None


def fetch_candles(symbol: str, category: str):
    """يحاول جلب شموع 15 دقيقة (يعمل غالبًا للكريبتو/الفوركس، وأحيانًا للأسهم)"""
    if not FINNHUB_API_KEY:
        return None
    url = f"https://finnhub.io/api/v1/{category}/candle"
    try:
        resp = requests.get(
            url,
            params={"symbol": symbol, "resolution": 15, "count": 30, "token": FINNHUB_API_KEY},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict) or data.get("s") != "ok":
            return None
        closes = [float(x) for x in data.get("c", []) if x is not None]
        volumes = [float(x) for x in data.get("v", []) if x is not None]
        if len(closes) >= 15 and len(volumes) >= 15:
            return {"closes": closes, "volumes": volumes}
    except Exception as e:
        print(f"[خطأ شموع] {symbol}: {e}")
    return None


def is_cooldown_active(symbol: str) -> bool:
    last = LAST_ALERT_TIME.get(symbol)
    if not last:
        return False
    return (datetime.now(NY_TZ) - last).total_seconds() < ALERT_COOLDOWN_MINUTES * 60


# ============================ التحليل ============================


def analyze_asset(symbol: str, meta: dict):
    category = meta["category"]

    if category == "stock" and not is_market_open():
        return None

    now = datetime.now(NY_TZ)
    candles = fetch_candles(symbol, category)

    if candles:
        closes = candles["closes"]
        volumes = candles["volumes"]
        current_price = closes[-1]
        prev_price = closes[-2]
        if prev_price <= 0:
            return None
        change_pct = ((current_price - prev_price) / prev_price) * 100
        rsi = calculate_rsi(closes)
        avg_volume = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else 0
        last_volume = volumes[-1]
        vol_ratio = round(last_volume / avg_volume, 1) if avg_volume > 0 else None
        data_quality = "candles"
    else:
        # لا توجد بيانات شموع (شائع للأسهم بالخطة المجانية) — نستخدم السعر اللحظي فقط
        quote = fetch_quote(symbol)
        if not quote or quote["current"] <= 0:
            return None
        current_price = quote["current"]

        with STATE_LOCK:
            hist = PRICE_ROLLING.setdefault(symbol, deque(maxlen=20))
            hist.append(current_price)
            hist_list = list(hist)

        if len(hist_list) < 3:
            return None  # أول فحصين بس نبني تاريخ الأسعار، بدون إشارة

        prev_price = hist_list[-2]
        if prev_price <= 0:
            return None
        change_pct = ((current_price - prev_price) / prev_price) * 100
        rsi = calculate_rsi(hist_list) if len(hist_list) >= 15 else None
        vol_ratio = None  # غير متوفر بدون شموع
        data_quality = "quote_only"

    # فلتر النطاق السعري (سنتات إلى 100 دولار)
    if not (PRICE_MIN <= current_price <= PRICE_MAX) and category == "stock":
        return None

    if abs(change_pct) < MIN_PRICE_CHANGE_PCT:
        return None

    # فلتر الحجم يُطبّق فقط إذا كانت البيانات متوفرة أصلًا
    if vol_ratio is not None and vol_ratio < VOLUME_MULTIPLIER:
        return None

    if is_cooldown_active(symbol):
        return None

    direction = "buy" if change_pct > 0 else "sell"

    with STATE_LOCK:
        LAST_ALERT_TIME[symbol] = now

    return {
        "symbol": symbol,
        "name": meta["name"],
        "direction": direction,
        "current_price": round(current_price, 4 if current_price < 1 else 2),
        "change_pct": round(change_pct, 2),
        "rsi": rsi,
        "volume_ratio": vol_ratio,
        "data_quality": data_quality,
        "source": "internal_scan",
        "time_ny": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================ تنسيق وإرسال تلجرام ============================


def format_signal_message(signal: dict) -> str:
    is_buy = signal["direction"] == "buy"
    header = "🟢 رصد شراء قوي متراكم" if is_buy else "🔴 رصد بيع قوي متراكم"
    arrow = "📈 مرتفع" if is_buy else "📉 منخفض"

    lines = [
        f"<b>{header}</b>",
        "",
        f"📌 الرمز: <b>{signal['symbol']}</b> ({signal.get('name', signal['symbol'])})",
        f"💵 السعر الحالي: <b>${signal['current_price']}</b>",
        f"📊 الاتجاه: {arrow} ({signal['change_pct']}%)",
    ]

    if signal.get("volume_ratio") is not None:
        lines.append(f"⚡ الحجم: ×{signal['volume_ratio']} من المعدل")
    else:
        lines.append("⚡ الحجم: غير متوفر لهذا الرمز (قيود الخطة المجانية)")

    if signal.get("rsi") is not None:
        lines.append(f"📈 RSI تقريبي: {signal['rsi']}")

    news = signal.get("news")
    if news:
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}[news["classification"]]
        lines.append(f"📰 آخر خبر ({emoji} تصنيف آلي تقريبي): {news['headline']}")
        lines.append("⚠️ التصنيف تلقائي بكلمات مفتاحية فقط — راجع الخبر بنفسك.")

    lines.append(f"🕐 الوقت (نيويورك): {signal['time_ny']}")
    lines.append("")
    lines.append("⚠️ مؤشر تقني آلي وليس توصية استثمارية. تحقق من السيولة والأخبار دائمًا قبل أي قرار.")

    return "\n".join(lines)


def format_webhook_message(payload: dict) -> str:
    direction = payload.get("direction", "buy")
    is_buy = direction == "buy"
    header = "🔔🟢 تنبيه TradingView — شراء قوي" if is_buy else "🔔🔴 تنبيه TradingView — بيع قوي"

    lines = [
        f"<b>{header}</b>",
        "",
        f"📌 الرمز: <b>{payload.get('symbol', '?')}</b>",
        f"💵 السعر: <b>${payload.get('price', '?')}</b>",
    ]
    if payload.get("change_pct"):
        lines.append(f"📊 التغير: {payload['change_pct']}%")
    if payload.get("rvol"):
        lines.append(f"⚡ RVOL: {payload['rvol']}")

    lines.append(f"🕐 الوقت (نيويورك): {datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("⚠️ المصدر: مؤشر Pine Script مخصص على TradingView. تحقق من الشرط قبل أي إجراء.")
    return "\n".join(lines)


def send_telegram(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID):
        print("[تنبيه] Telegram غير مُهيأ، تم تجاوز الإرسال.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[خطأ تلجرام] {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[خطأ تلجرام] {e}")


# ============================ حلقة الفحص الداخلي ============================


def run_scan_cycle():
    print(f"[معلومة] فحص {len(ASSETS)} أصل...")
    found = 0
    for symbol, meta in ASSETS.items():
        try:
            signal = analyze_asset(symbol, meta)
            if signal:
                if meta["category"] != "stock":  # الأخبار متاحة للأسهم فقط
                    pass
                else:
                    signal["news"] = get_news_context(symbol)
                send_telegram(format_signal_message(signal))
                with STATE_LOCK:
                    SIGNALS.appendleft(signal)
                found += 1
        except Exception as e:
            print(f"[خطأ فحص] {symbol}: {e}")
        time.sleep(1.2)

    with STATE_LOCK:
        STATUS["last_scan_time"] = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")
        STATUS["last_scan_symbols_count"] = len(ASSETS)
    print(f"[معلومة] انتهت الدورة، عدد التنبيهات: {found}")


def background_worker():
    while True:
        if FINNHUB_API_KEY:
            try:
                run_scan_cycle()
            except Exception as e:
                print(f"[خطأ دورة العمل] {e}")
        wait = SCAN_INTERVAL_SECONDS
        with STATE_LOCK:
            STATUS["next_scan_eta_seconds"] = wait
        time.sleep(wait)


# ============================ مسارات الويب ============================


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/webhook", methods=["POST"])
def tradingview_webhook():
    token = request.args.get("token", "")
    if not WEBHOOK_SECRET_TOKEN or token != WEBHOOK_SECRET_TOKEN:
        return jsonify({"status": "error", "message": "unauthorized"}), 403

    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "invalid json"}), 400

    if not payload.get("symbol"):
        return jsonify({"status": "error", "message": "missing symbol"}), 400

    signal = {
        "symbol": payload.get("symbol"),
        "name": payload.get("symbol"),
        "direction": payload.get("direction", "buy"),
        "current_price": payload.get("price", "?"),
        "change_pct": payload.get("change_pct"),
        "rsi": None,
        "volume_ratio": payload.get("rvol"),
        "data_quality": "tradingview_webhook",
        "source": "tradingview",
        "time_ny": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }

    send_telegram(format_webhook_message(payload))
    with STATE_LOCK:
        SIGNALS.appendleft(signal)

    return jsonify({"status": "success"}), 200


@app.route("/api/status")
def api_status():
    with STATE_LOCK:
        return jsonify(dict(STATUS))


@app.route("/api/signals")
def api_signals():
    with STATE_LOCK:
        return jsonify(list(SIGNALS))


@app.route("/health")
def health():
    return jsonify({"ok": True})


worker_thread = threading.Thread(target=background_worker, daemon=True)
worker_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
