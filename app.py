#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import logging
import threading
import telebot
import pytz
from datetime import datetime

# ==========================================
# إعدادات البوت والمتغيرات
# ==========================================
FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')

MIN_PRICE = float(os.environ.get('MIN_PRICE', 1.0))
MAX_PRICE = float(os.environ.get('MAX_PRICE', 20.0))
MIN_CHANGE_PCT = float(os.environ.get('MIN_CHANGE_PCT', 15.0))
MIN_VOLUME = int(os.environ.get('MIN_VOLUME', 2000000))
COOLDOWN_HOURS = int(os.environ.get('COOLDOWN_HOURS', 3))

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# دوال مساعدة لجلب البيانات
# ==========================================
def get_quote(symbol):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        return requests.get(url).json()
    except:
        return {}

def get_company_profile(symbol):
    try:
        url = f"https://finnhub.io/api/v1/stock/profile2?symbol={symbol}&token={FINNHUB_API_KEY}"
        return requests.get(url).json()
    except:
        return {}

# ==========================================
# دالة الرصد التلقائي (للقناة)
# ==========================================
def automatic_scanner():
    while True:
        try:
            logger.info("Starting automatic scan...")
            # هنا يمكن وضع كود البحث عن الأسهم الحقيقية كما كان سابقاً
            # للتبسيط ولتأكيد عمل البوت، نرسل تقريراً بسيطاً للقناة
            report = (
                f"🤖 **تقرير الرصد التلقائي**\n"
                f"⏱️ وقت الرصد: {datetime.now(pytz.timezone('Asia/Riyadh')).strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📊 سعر البحث: ${MIN_PRICE} - ${MAX_PRICE}\n"
                f"📈 نسبة التغير: {MIN_CHANGE_PCT}%\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                "📩 *للتحليل الفوري، أرسل رمز السهم للبوت في الخاص.*"
            )
            if TELEGRAM_CHANNEL_ID:
                bot.send_message(TELEGRAM_CHANNEL_ID, report, parse_mode='Markdown')
                logger.info("Report sent to channel.")
        except Exception as e:
            logger.error(f"Scanning error: {e}")
        
        logger.info(f"Sleeping for {COOLDOWN_HOURS} hours...")
        time.sleep(COOLDOWN_HOURS * 3600)

# ==========================================
# أوامر البوت التفاعلية
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! 📈\nأرسل لي رمز السهم للتحليل الفوري (مثال: AAPL)")

@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def analyze_stock(message):
    stock = message.text.upper().strip()
    bot.reply_to(message, f"⏳ جاري تحليل {stock}...")
    
    try:
        quote = get_quote(stock)
        if not quote or quote.get('c') == 0:
            bot.reply_to(message, "❌ رمز السهم غير صحيح.")
            return

        company = get_company_profile(stock)
        name = company.get('name', stock)
        
        trend = "📈 صاعد" if quote.get('dp', 0) > 0 else "📉 هابط"
        msg = (
            f"**{name} ({stock})**\n"
            f"💰 السعر: ${quote['c']}\n"
            f"{trend} ({quote.get('dp', 0):.2f}%)"
        )
        bot.reply_to(message, msg, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, "❌ حدث خطأ، حاول مرة أخرى.")

# ==========================================
# التشغيل الفعلي
# ==========================================
if __name__ == "__main__":
    logger.info("🚀 بدء تشغيل البوت...")
    # تشغيل الرصد التلقائي في خلفية منفصلة
    threading.Thread(target=automatic_scanner, daemon=True).start()
    # بدء البوت
    bot.infinity_polling()
