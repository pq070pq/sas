#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signal Desk — رصد الذهب والبتكوين كل 5 دقائق والأسهم الذكية عبر Finnhub
"""

import os
import time
import threading
from datetime import datetime
from collections import deque

import requests
import pytz
from flask import Flask, jsonify, render_template

# ============================ الإعدادات الأساسية ============================

RAW_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 300))
MIN_PRICE_CHANGE_PCT = float(os.environ.get("MIN_PRICE_CHANGE_PCT", 0.4))
VOLUME_MULTIPLIER = float(os.environ.get("VOLUME_MULTIPLIER", 1.2))

NY_TZ = pytz.timezone("America/New_York")

SIGNALS = deque(maxlen=100)
STATE_LOCK = threading.Lock()

STATUS = {
    "market_open": True,
    "last_scan_time": None,
    "last_scan_symbols_count": 0,
    "next_scan_eta_seconds": SCAN_INTERVAL_SECONDS,
    "telegram_configured": bool(RAW_TELEGRAM_TOKEN and TELEGRAM_CHANNEL_ID),
    "finnhub_configured": bool(FINNHUB_API_KEY),
}

app = Flask(__name__)

ASSETS_LIST = {
    "BINANCE:BTCUSDT": "البتكوين (Bitcoin BTC)",
    "OANDA:XAU_USD": "الذهب الفوري (Gold XAU)",
    "AAPL": "أبل (AAPL)",
    "TSLA": "تسلا (TSLA)",
    "NVDA": "إنفيديا (NVDA)",
    "AMD": "إيه إم دي (AMD)",
    "MSFT": "مايكروسوفت (MSFT)",
    "AMZN": "أمازون (AMZN)",
    "META": "ميتا (META)",
    "GOOGL": "جوجل (GOOGL)",
    "COIN": "كوينبيس (COIN)",
    "SPY": "إس آند بي 500 (SPY)",
    "QQQ": "ناسداك (QQQ)"
}

def calculate_rsi(closes: list, period: int = 14) -> float:
    try:
        if not closes or len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)
    except Exception:
        return 50.0


def fetch_finnhub_candles(symbol: str):
    if not FINNHUB_API_KEY:
        return None
    url = f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}&resolution=15&count=30&token={FINNHUB_API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("s") == "ok":
                closes = data.get("c", [])
                volumes = data.get("v", [])
                if isinstance(closes, list) and isinstance(volumes, list):
                    valid_closes = [float(x) for x in closes if x is not None]
                    valid_vols = [float(x) for x in volumes if x is not None]
                    if len(valid_closes) >= 15 and len(valid_vols) >= 15:
                        return {"c": valid_closes, "v": valid_vols}
    except Exception as e:
        print(f"[خطأ بيانات] {symbol}: {e}")
    return None


def analyze_asset_smart(symbol: str, name: str):
    candles = fetch_finnhub_candles(symbol)
    if not candles:
        return None

    closes = candles["c"]
    volumes = candles["v"]
    current_price = float(closes[-1])
    prev_price = float(closes[-2])

    if prev_price == 0:
        return None

    price_change_pct = round(((current_price - prev_price) / prev_price) * 100, 2)
    rsi = calculate_rsi(closes, period=14)

    avg_volume = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else 1
    last_volume = volumes[-1] if volumes else 1
    vol_ratio = round(last_volume / avg_volume, 1) if avg_volume > 0 else 1.0

    is_crypto_or_gold = symbol in ["BINANCE:BTCUSDT", "OANDA:XAU_USD"]

    if price_change_pct >= 0.4:
        status_label = "🚀 مرتفع بصاروخ وبزخم قوي" if rsi > 50 else "📈 مرتفع إيجابي"
        direction = "up"
        signal_type = "buy"
    elif price_change_pct <= -0.4:
        status_label = "🩸 هبوط قوي وعنيف ببيع مكثف" if rsi < 50 else "📉 منخفض سلبي"
        direction = "down"
        signal_type = "sell"
    else:
        status_label = "⚖️ حركة متذبذبة جانبية هادئة"
        direction = "neutral"
        signal_type = "neutral"

    if is_crypto_or_gold:
        return {
            "symbol": symbol,
            "name": name,
            "signal_type": signal_type,
            "direction": direction,
            "status_label": status_label,
            "current_price": current_price,
            "prev_price": prev_price,
            "price_change_pct": price_change_pct,
            "rsi": rsi,
            "volume_ratio": vol_ratio,
            "time_ny": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }

    is_real_pump = (price_change_pct >= MIN_PRICE_CHANGE_PCT) and (vol_ratio >= VOLUME_MULTIPLIER) and (rsi > 50)
    is_real_dump = (price_change_pct <= -MIN_PRICE_CHANGE_PCT) and (vol_ratio >= VOLUME_MULTIPLIER) and (rsi < 50)

    if not is_real_pump and not is_real_dump:
        return None

    return {
        "symbol": symbol,
        "name": name,
        "signal_type": "buy" if is_real_pump else "sell",
        "direction": "up" if is_real_pump else "down",
        "status_label": status_label,
        "current_price": current_price,
        "prev_price": prev_price,
        "price_change_pct": price_change_pct,
        "rsi": rsi,
        "volume_ratio": vol_ratio,
        "time_ny": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }


def send_telegram_message(signal: dict):
    if not (RAW_TELEGRAM_TOKEN and TELEGRAM_CHANNEL_ID):
        return

    token_clean = RAW_TELEGRAM_TOKEN.strip()
    if token_clean.startswith("bot"):
        token_clean = token_clean[3:]

    url = f"https://api.telegram.org/bot{token_clean}/sendMessage"

    text = (
        f"<b>{signal['status_label']}</b>\n\n"
        f"📌 الأصل: <b>{signal['name']}</b>\n"
        f"📊 نسبة التغير: <b>{signal['price_change_pct']}%</b>\n"
        f"💵 كم كان؟ <b>${signal['prev_price']}</b>\n"
        f"🚀 كم صار؟ <b>${signal['current_price']}</b>\n"
        f"⚡ قوة السيولة: <b>(x{signal['volume_ratio']}) من المعدل</b>\n"
        f"📈 الزخم (RSI): <b>{signal['rsi']}</b>\n"
        f"⏰ الوقت: {signal['time_ny']}"
    )
    
    try:
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print(f"[خطأ تيليجرام] {e}")


def run_scan_cycle():
    print(f"[معلومة] بدء فحص الذهب، البتكوين، والأسهم...")
    found = 0
    for symbol, name in ASSETS_LIST.items():
        try:
            signal = analyze_asset_smart(symbol, name)
            if signal:
                send_telegram_message(signal)
                with STATE_LOCK:
                    SIGNALS.appendleft(signal)
                found += 1
        except Exception as e:
            print(f"[خطأ فحص] {symbol}: {e}")
        time.sleep(1.2)

    with STATE_LOCK:
        STATUS["last_scan_time"] = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")
        STATUS["last_scan_symbols_count"] = len(ASSETS_LIST)
    print(f"[معلومة] انتهت دورة الفحص، وإرسال {found} تنبيه.")


def background_worker():
    while True:
        try:
            run_scan_cycle()
        except Exception as e:
            print(f"[خطأ دورة العمل] {e}")
        wait = SCAN_INTERVAL_SECONDS
        with STATE_LOCK:
            STATUS["next_scan_eta_seconds"] = wait
        time.sleep(wait)


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


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
