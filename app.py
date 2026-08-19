#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📈 US Stock Bot - بوت تحليل الأسهم الأمريكية مع الأخبار
"""

import os
import time
import json
import requests
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pytz
import logging
from flask import Flask, request, jsonify

# ═══════════════ الإعدادات ═══════════════
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

# فلترة الأسهم الأمريكية فقط
MIN_PRICE = float(os.environ.get("MIN_PRICE", "1.0"))
MAX_PRICE = float(os.environ.get("MAX_PRICE", "500.0"))
MIN_CHANGE_PCT = float(os.environ.get("MIN_CHANGE_PCT", "5.0"))
MIN_VOLUME = float(os.environ.get("MIN_VOLUME", "1000000"))

NY_TZ = pytz.timezone("America/New_York")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ═══════════════ محلل الأسهم ═══════════════

class StockAnalyzer:
    """محلل الأسهم الأمريكية"""
    
    def __init__(self):
        self.api_key = FINNHUB_API_KEY
        self.cache = {}
        self.cache_time = {}
        
    def analyze_stock(self, symbol: str) -> Dict:
        """تحليل شامل للسهم"""
        symbol = symbol.upper().strip()
        
        # التحقق من الكاش
        if symbol in self.cache:
            cached_time = self.cache_time.get(symbol)
            if cached_time and (datetime.now() - cached_time).seconds < 300:
                return self.cache[symbol]
        
        # جلب البيانات
        quote = self.get_quote(symbol)
        if not quote:
            return {"error": f"❌ رمز السهم {symbol} غير صحيح أو غير موجود"}
        
        profile = self.get_company_profile(symbol)
        candles = self.get_candles(symbol)
        news = self.get_company_news(symbol)
        
        # تحليل شامل
        analysis = {
            "symbol": symbol,
            "name": profile.get("name", symbol) if profile else symbol,
            "quote": quote,
            "profile": profile,
            "candles": candles,
            "news": news,
            "technical": self.technical_analysis(quote, candles),
            "volume_analysis": self.volume_analysis(quote, candles),
            "recommendation": self.get_recommendation(quote, candles, news),
            "timestamp": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # حفظ في الكاش
        self.cache[symbol] = analysis
        self.cache_time[symbol] = datetime.now()
        
        return analysis
    
    def get_quote(self, symbol: str) -> Optional[Dict]:
        """جلب السعر الحالي"""
        url = "https://finnhub.io/api/v1/quote"
        params = {"symbol": symbol, "token": self.api_key}
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("c"):
                    return data
        except Exception as e:
            logger.error(f"خطأ في جلب السعر: {e}")
        
        return None
    
    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """جلب معلومات الشركة"""
        url = "https://finnhub.io/api/v1/stock/profile2"
        params = {"symbol": symbol, "token": self.api_key}
        
        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"خطأ في جلب الملف الشخصي: {e}")
        
        return None
    
    def get_company_news(self, symbol: str) -> List[Dict]:
        """جلب أخبار الشركة"""
        url = "https://finnhub.io/api/v1/company-news"
        params = {
            "symbol": symbol,
            "from": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            "to": datetime.now().strftime("%Y-%m-%d"),
            "token": self.api_key
        }
        
        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                news_data = response.json()
                # تصفية الأخبار المهمة (اللي لها تأثير)
                filtered_news = []
                for item in news_data[:10]:
                    headline = item.get("headline", "")
                    if any(word in headline.lower() for word in ["beat", "miss", "surge", "plunge", "upgrade", "downgrade", "record", "low", "high", "partnership", "acquisition", "lawsuit", "approval", "revenue", "earnings"]):
                        filtered_news.append(item)
                return filtered_news[:5]  # خذ أهم 5 أخبار فقط
        except Exception as e:
            logger.error(f"خطأ في جلب الأخبار: {e}")
        
        return []
    
    def get_candles(self, symbol: str) -> Optional[Dict]:
        """جلب الشموع"""
        url = "https://finnhub.io/api/v1/stock/candle"
        params = {
            "symbol": symbol,
            "resolution": "15",
            "count": 30,
            "token": self.api_key
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("s") == "ok":
                    return data
        except Exception as e:
            logger.error(f"خطأ في جلب الشموع: {e}")
        
        return None
    
    def technical_analysis(self, quote: Dict, candles: Optional[Dict]) -> Dict:
        """التحليل الفني"""
        current_price = quote.get("c", 0)
        open_price = quote.get("o", 0)
        high = quote.get("h", 0)
        low = quote.get("l", 0)
        prev_close = quote.get("pc", 0)
        
        change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
        
        rsi = 50
        if candles and candles.get("c"):
            rsi = self.calculate_rsi(candles["c"])
        
        return {
            "current_price": current_price,
            "open": open_price,
            "high": high,
            "low": low,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "rsi": rsi,
            "day_range": f"${low:.2f} - ${high:.2f}"
        }
    
    def volume_analysis(self, quote: Dict, candles: Optional[Dict]) -> Dict:
        """تحليل الحجم"""
        current_volume = quote.get("v", 0)
        
        volume_info = {
            "current_volume": current_volume,
            "average_volume": 0,
            "volume_ratio": 0,
            "liquidity": "غير معروفة"
        }
        
        if candles and candles.get("v"):
            volumes = candles["v"]
            
            if len(volumes) >= 10:
                avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
                volume_info["average_volume"] = avg_volume
                volume_info["volume_ratio"] = current_volume / avg_volume if avg_volume > 0 else 0
                
                if current_volume > 10000000:
                    volume_info["liquidity"] = "سيولة عالية جداً"
                elif current_volume > 5000000:
                    volume_info["liquidity"] = "سيولة عالية"
                elif current_volume > 1000000:
                    volume_info["liquidity"] = "سيولة متوسطة"
                else:
                    volume_info["liquidity"] = "سيولة منخفضة"
        
        return volume_info
    
    def get_recommendation(self, quote: Dict, candles: Optional[Dict], news: List[Dict]) -> Dict:
        """توصية مع تحليل الأخبار"""
        technical = self.technical_analysis(quote, candles)
        volume = self.volume_analysis(quote, candles)
        
        score = 50
        reasons = []
        news_impact = "محايد"
        
        # تحليل السعر
        if technical["change_pct"] > 3:
            score += 15
            reasons.append("📈 ارتفاع قوي")
        elif technical["change_pct"] > 1:
            score += 8
            reasons.append("📈 ارتفاع طفيف")
        elif technical["change_pct"] < -3:
            score -= 15
            reasons.append("📉 انخفاض قوي")
        elif technical["change_pct"] < -1:
            score -= 8
            reasons.append("📉 انخفاض طفيف")
        
        # تحليل RSI
        if technical["rsi"] > 70:
            score -= 10
            reasons.append("⚠️ RSI في منطقة تشبع شراء")
        elif technical["rsi"] < 30:
            score += 10
            reasons.append("✅ RSI في منطقة تشبع بيع")
        
        # تحليل الحجم
        if volume["volume_ratio"] > 2:
            score += 10
            reasons.append("⚡ حجم تداول استثنائي")
        
        # تحليل الأخبار
        positive_words = ["beat", "surge", "upgrade", "record", "high", "approval", "partnership", "growth", "profit"]
        negative_words = ["miss", "plunge", "downgrade", "lawsuit", "investigation", "loss", "decline", "low"]
        
        for news_item in news[:3]:
            headline = news_item.get("headline", "").lower()
            if any(word in headline for word in positive_words):
                score += 5
                news_impact = "إيجابي"
                reasons.append("📰 أخبار إيجابية")
                break
            elif any(word in headline for word in negative_words):
                score -= 5
                news_impact = "سلبي"
                reasons.append("📰 أخبار سلبية")
                break
        
        # التوصية النهائية
        if score >= 70:
            recommendation = "شراء"
            emoji = "🟢"
        elif score >= 55:
            recommendation = "مراقبة"
            emoji = "🟡"
        elif score >= 40:
            recommendation = "انتظار"
            emoji = "🟠"
        else:
            recommendation = "تجنب"
            emoji = "🔴"
        
        return {
            "score": score,
            "recommendation": recommendation,
            "emoji": emoji,
            "news_impact": news_impact,
            "reasons": reasons[:4]
        }
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """حساب RSI"""
        if len(prices) < period + 1:
            return 50
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
            if diff > 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(diff))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

# ═══════════════ تنسيق الرسائل ═══════════════

def format_analysis_message(analysis: Dict) -> str:
    """تنسيق رسالة التحليل مع الأخبار"""
    if "error" in analysis:
        return analysis["error"]
    
    symbol = analysis["symbol"]
    name = analysis.get("name", symbol)
    technical = analysis["technical"]
    volume = analysis["volume_analysis"]
    recommendation = analysis["recommendation"]
    news = analysis.get("news", [])
    
    change_emoji = "🟢" if technical["change_pct"] > 0 else "🔴" if technical["change_pct"] < 0 else "⚪"
    
    lines = [
        f"📊 <b>تحليل {symbol} ({name})</b>",
        "",
        "━━━━━━━━━━━━━━━━━",
        "💰 <b>السعر:</b>",
        f"السعر الحالي: <b>${technical['current_price']:.2f}</b>",
        f"التغير: {change_emoji} <b>{technical['change_pct']:.2f}%</b>",
        f"نطاق اليوم: {technical['day_range']}",
        "",
        "━━━━━━━━━━━━━━━━━",
        "⚡ <b>الحجم والسيولة:</b>",
        f"حجم التداول: <b>{volume['current_volume']:,}</b> سهم",
        f"السيولة: {volume['liquidity']}",
        "",
        "━━━━━━━━━━━━━━━━━",
        "📐 <b>المؤشرات الفنية:</b>",
        f"RSI: <b>{technical['rsi']:.1f}</b>",
        "",
        "━━━━━━━━━━━━━━━━━",
        f"{recommendation['emoji']} <b>التوصية: {recommendation['recommendation']}</b>",
        f"درجة الثقة: {recommendation['score']}/100",
        f"تأثير الأخبار: {recommendation['news_impact']}",
    ]
    
    if recommendation["reasons"]:
        lines.append("")
        lines.append("📋 <b>الأسباب:</b>")
        for reason in recommendation["reasons"]:
            lines.append(f"• {reason}")
    
    # إضافة الأخبار
    if news:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append("📰 <b>آخر الأخبار الهامة:</b>")
        
        for item in news[:3]:
            headline = item.get("headline", "")
            source = item.get("source", "")
            timestamp = datetime.fromtimestamp(item.get("datetime", 0), NY_TZ).strftime("%m/%d %H:%M")
            
            # تصنيف الخبر
            sentiment = classify_news_sentiment(headline)
            sentiment_emoji = "🟢" if sentiment == "positive" else "🔴" if sentiment == "negative" else "⚪"
            
            lines.append(f"{sentiment_emoji} <b>{headline}</b>")
            lines.append(f"   📍 {source} • {timestamp}")
            lines.append("")
    
    lines.extend([
        "━━━━━━━━━━━━━━━━━",
        f"🕐 {analysis['timestamp']} (توقيت نيويورك)",
        "",
        "⚠️ <b>هذا تحليل آلي وليس توصية استثمارية</b>",
        "♦️ <b>شرعية الأسهم مسؤوليتك نبرأ منها أمام الله</b> ♦️"
    ])
    
    return "\n".join(lines)

def classify_news_sentiment(headline: str) -> str:
    """تصنيف مشاعر الخبر"""
    positive_words = ["beat", "surge", "upgrade", "record", "high", "approval", "partnership", "growth", "profit", "positive", "gain", "rise", "jump"]
    negative_words = ["miss", "plunge", "downgrade", "lawsuit", "investigation", "loss", "decline", "low", "drop", "fall", "negative", "slump"]
    
    headline_lower = headline.lower()
    
    pos_count = sum(1 for word in positive_words if word in headline_lower)
    neg_count = sum(1 for word in negative_words if word in headline_lower)
    
    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    else:
        return "neutral"

# ═══════════════ البوت ═══════════════

class TradingBot:
    """بوت التداول"""
    
    def __init__(self):
        self.analyzer = StockAnalyzer()
        self.last_update_id = 0
        
    def send_telegram(self, chat_id: str, message: str):
        """إرسال رسالة"""
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        try:
            requests.post(url, json=data, timeout=10)
        except Exception as e:
            logger.error(f"خطأ إرسال: {e}")
    
    def handle_private_message(self, message: Dict):
        """معالجة الرسائل الخاصة"""
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        user = message.get("from", {})
        username = user.get("username", user.get("first_name", "مستخدم"))
        
        if not text:
            return
        
        if text.lower() in ["/start", "/help", "مساعدة", "help"]:
            help_message = f"""
👋 <b>مرحباً {username}!</b>

أنا بوت تحليل الأسهم الأمريكية. 

📊 <b>ماذا أفعل؟</b>
✅ أحلل أي سهم أمريكي
✅ أعطيك السعر والتغير
✅ أحلل المؤشرات الفنية (RSI)
✅ أحلل الحجم والسيولة
✅ أعرض آخر الأخبار الهامة
✅ أعطي توصية مبسطة

🎯 <b>كيف تستخدمني؟</b>
أرسل رمز السهم فقط (مثل: AAPL, TSLA, MSFT)

📰 <b>الأخبار:</b>
أعرض آخر 3 أخبار هامة مع تحليل تأثيرها

⚠️ <b>تنبيه:</b>
هذا تحليل آلي وليس توصية استثمارية

📈 <b>أرسل رمز السهم الآن!</b>
"""
            self.send_telegram(chat_id, help_message)
            return
        
        self.send_telegram(chat_id, "🔍 <b>جاري تحليل السهم...</b>")
        analysis = self.analyzer.analyze_stock(text)
        formatted = format_analysis_message(analysis)
        self.send_telegram(chat_id, formatted)
    
    def run(self):
        """التشغيل المستمر"""
        logger.info("🤖 بدء البوت...")
        
        while True:
            try:
                updates = self.get_updates()
                
                for update in updates:
                    message = update.get("message")
                    if message:
                        chat_type = message.get("chat", {}).get("type")
                        if chat_type == "private":
                            self.handle_private_message(message)
                
                time.sleep(3)
                
            except Exception as e:
                logger.error(f"خطأ: {e}")
                time.sleep(10)
    
    def get_updates(self) -> List[Dict]:
        """جلب التحديثات"""
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {
            "offset": self.last_update_id + 1,
            "timeout": 30
        }
        
        try:
            response = requests.get(url, params=params, timeout=35)
            if response.status_code == 200:
                updates = response.json().get("result", [])
                if updates:
                    self.last_update_id = updates[-1]["update_id"]
                return updates
        except Exception as e:
            logger.error(f"خطأ في جلب التحديثات: {e}")
        
        return []

# ═══════════════ مسارات الويب ═══════════════

@app.route("/")
@app.route("/health")
def health():
    """فحص صحة السيرفر"""
    return jsonify({
        "status": "ok",
        "time": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "service": "US Stock Bot with News"
    })

# ═══════════════ التشغيل ═══════════════

if __name__ == "__main__":
    # تشغيل البوت
    bot = TradingBot()
    bot_thread = threading.Thread(target=bot.run, daemon=True)
    bot_thread.start()
    
    # تشغيل السيرفر
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 تشغيل السيرفر على المنفذ {port}")
    app.run(host="0.0.0.0", port=port)
