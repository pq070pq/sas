
## 📄 6️⃣ ملف `bot.py` (الكود الكامل)

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
💎 Signal Desk Pro - بوت التداول الذكي
=========================================
رصد الانفجارات + تحليل ذكي عند المراسلة
"""

import os
import time
import json
import requests
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import deque
import pytz
import logging
from flask import Flask, request, jsonify

# ═══════════════ الإعدادات ═══════════════
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

# فلترة الانفجارات
MIN_PRICE = float(os.environ.get("MIN_PRICE", "1.0"))
MAX_PRICE = float(os.environ.get("MAX_PRICE", "20.0"))
MIN_CHANGE_PCT = float(os.environ.get("MIN_CHANGE_PCT", "15.0"))
MIN_VOLUME = float(os.environ.get("MIN_VOLUME", "2000000"))
COOLDOWN_HOURS = int(os.environ.get("COOLDOWN_HOURS", "3"))

NY_TZ = pytz.timezone("America/New_York")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ═══════════════ تحليل الأسهم ═══════════════

class StockAnalyzer:
    """محلل الأسهم الذكي"""
    
    def __init__(self):
        self.api_key = FINNHUB_API_KEY
        self.cache = {}
        self.cache_time = {}
        
    def analyze_stock(self, symbol: str) -> Dict:
        """تحليل شامل للسهم"""
        symbol = symbol.upper().strip()
        
        # فحص الكاش
        if symbol in self.cache:
            cached_time = self.cache_time.get(symbol)
            if cached_time and (datetime.now() - cached_time).seconds < 300:
                return self.cache[symbol]
        
        # جلب البيانات
        quote = self.get_quote(symbol)
        if not quote:
            return {"error": f"❌ رمز السهم {symbol} غير صحيح أو غير موجود"}
        
        profile = self.get_company_profile(symbol)
        news = self.get_company_news(symbol)
        candles = self.get_candles(symbol)
        
        # تحليل شامل
        analysis = {
            "symbol": symbol,
            "name": profile.get("name", symbol) if profile else symbol,
            "quote": quote,
            "profile": profile,
            "news": news,
            "candles": candles,
            "technical": self.technical_analysis(quote, candles),
            "momentum": self.momentum_analysis(quote, candles),
            "volume_analysis": self.volume_analysis(quote, candles),
            "direction": self.determine_direction(quote, candles),
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
        except:
            pass
        
        return None
    
    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """جلب معلومات الشركة"""
        url = "https://finnhub.io/api/v1/stock/profile2"
        params = {"symbol": symbol, "token": self.api_key}
        
        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json()
        except:
            pass
        
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
                return response.json()[:10]
        except:
            pass
        
        return []
    
    def get_candles(self, symbol: str) -> Optional[Dict]:
        """جلب الشموع"""
        url = "https://finnhub.io/api/v1/stock/candle"
        params = {
            "symbol": symbol,
            "resolution": "15",
            "count": 50,
            "token": self.api_key
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("s") == "ok":
                    return data
        except:
            pass
        
        return None
    
    def technical_analysis(self, quote: Dict, candles: Optional[Dict]) -> Dict:
        """التحليل الفني"""
        current_price = quote.get("c", 0)
        open_price = quote.get("o", 0)
        high = quote.get("h", 0)
        low = quote.get("l", 0)
        prev_close = quote.get("pc", 0)
        
        rsi = 50
        if candles and candles.get("c"):
            rsi = self.calculate_rsi(candles["c"])
        
        vwap = current_price
        vwap_distance = 0
        if candles and candles.get("c") and candles.get("v"):
            total_volume = sum(candles["v"])
            total_value = sum(c * v for c, v in zip(candles["c"], candles["v"]))
            if total_volume > 0:
                vwap = total_value / total_volume
                vwap_distance = ((current_price - vwap) / vwap) * 100
        
        return {
            "current_price": current_price,
            "open": open_price,
            "high": high,
            "low": low,
            "prev_close": prev_close,
            "change_pct": ((current_price - prev_close) / prev_close * 100) if prev_close else 0,
            "rsi": rsi,
            "vwap": vwap,
            "vwap_distance": vwap_distance,
            "day_range": f"${low} - ${high}"
        }
    
    def momentum_analysis(self, quote: Dict, candles: Optional[Dict]) -> Dict:
        """تحليل الزخم"""
        momentum = {
            "short_term": "محايد",
            "medium_term": "محايد",
            "long_term": "محايد",
            "score": 50
        }
        
        if candles and candles.get("c"):
            closes = candles["c"]
            
            if len(closes) >= 5:
                change_5 = ((closes[-1] - closes[-5]) / closes[-5] * 100) if closes[-5] else 0
                momentum["short_term"] = self.classify_momentum(change_5)
                
            if len(closes) >= 15:
                change_15 = ((closes[-1] - closes[-15]) / closes[-15] * 100) if closes[-15] else 0
                momentum["medium_term"] = self.classify_momentum(change_15)
                
            if len(closes) >= 30:
                change_30 = ((closes[-1] - closes[-30]) / closes[-30] * 100) if closes[-30] else 0
                momentum["long_term"] = self.classify_momentum(change_30)
        
        momentum["score"] = self.calculate_momentum_score(momentum)
        return momentum
    
    def volume_analysis(self, quote: Dict, candles: Optional[Dict]) -> Dict:
        """تحليل الحجم والسيولة"""
        current_volume = quote.get("v", 0)
        
        volume_info = {
            "current_volume": current_volume,
            "average_volume": 0,
            "volume_ratio": 0,
            "liquidity": "غير معروفة",
            "volume_trend": "محايد"
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
                elif current_volume > 500000:
                    volume_info["liquidity"] = "سيولة منخفضة"
                else:
                    volume_info["liquidity"] = "سيولة ضعيفة جداً"
                
                recent_vol = sum(volumes[-5:]) / 5
                if recent_vol > avg_volume * 1.5:
                    volume_info["volume_trend"] = "زيادة قوية"
                elif recent_vol > avg_volume * 1.2:
                    volume_info["volume_trend"] = "زيادة طفيفة"
                elif recent_vol < avg_volume * 0.8:
                    volume_info["volume_trend"] = "انخفاض"
        
        return volume_info
    
    def determine_direction(self, quote: Dict, candles: Optional[Dict]) -> Dict:
        """تحديد اتجاه السهم"""
        direction = {
            "trend": "محايد",
            "strength": 50,
            "recommendation": "انتظار",
            "reasons": []
        }
        
        technical = self.technical_analysis(quote, candles)
        momentum = self.momentum_analysis(quote, candles)
        volume = self.volume_analysis(quote, candles)
        
        if technical["rsi"] > 70:
            direction["reasons"].append("RSI في منطقة تشبع شراء")
        elif technical["rsi"] < 30:
            direction["reasons"].append("RSI في منطقة تشبع بيع")
        
        if technical["vwap_distance"] > 5:
            direction["reasons"].append("السعر فوق VWAP بشكل كبير")
        elif technical["vwap_distance"] < -5:
            direction["reasons"].append("السعر تحت VWAP بشكل كبير")
        
        if volume["volume_ratio"] > 3:
            direction["reasons"].append("حجم تداول استثنائي")
        
        if momentum["score"] > 65 and technical["rsi"] > 55:
            direction["trend"] = "صاعد"
            direction["strength"] = min(100, momentum["score"])
            direction["recommendation"] = "مراقبة للشراء"
        elif momentum["score"] < 35 and technical["rsi"] < 45:
            direction["trend"] = "هابط"
            direction["strength"] = min(100, 100 - momentum["score"])
            direction["recommendation"] = "مراقبة للبيع"
        else:
            direction["trend"] = "عرضي"
            direction["recommendation"] = "انتظار إشارة واضحة"
        
        return direction
    
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
    
    def classify_momentum(self, change_pct: float) -> str:
        """تصنيف الزخم"""
        if change_pct > 10:
            return "زخم إيجابي قوي جداً"
        elif change_pct > 5:
            return "زخم إيجابي قوي"
        elif change_pct > 2:
            return "زخم إيجابي"
        elif change_pct > -2:
            return "محايد"
        elif change_pct > -5:
            return "زخم سلبي"
        elif change_pct > -10:
            return "زخم سلبي قوي"
        else:
            return "زخم سلبي قوي جداً"
    
    def calculate_momentum_score(self, momentum: Dict) -> int:
        """حساب درجة الزخم"""
        score = 50
        
        if "قوي جداً" in momentum["short_term"]:
            score += 20
        elif "قوي" in momentum["short_term"]:
            score += 10
        elif "سلبي قوي جداً" in momentum["short_term"]:
            score -= 20
        elif "سلبي قوي" in momentum["short_term"]:
            score -= 10
        
        if "قوي جداً" in momentum["medium_term"]:
            score += 15
        elif "قوي" in momentum["medium_term"]:
            score += 8
        elif "سلبي قوي جداً" in momentum["medium_term"]:
            score -= 15
        elif "سلبي قوي" in momentum["medium_term"]:
            score -= 8
        
        return max(0, min(100, score))

# ═══════════════ تنسيق الرسائل ═══════════════

def format_analysis_message(analysis: Dict) -> str:
    """تنسيق رسالة التحليل"""
    if "error" in analysis:
        return analysis["error"]
    
    symbol = analysis["symbol"]
    name = analysis.get("name", symbol)
    technical = analysis["technical"]
    momentum = analysis["momentum"]
    volume = analysis["volume_analysis"]
    direction = analysis["direction"]
    
    trend_emoji = "📈" if direction["trend"] == "صاعد" else "📉" if direction["trend"] == "هابط" else "↔️"
    change_emoji = "🟢" if technical["change_pct"] > 0 else "🔴" if technical["change_pct"] < 0 else "⚪"
    
    lines = [
        f"{trend_emoji} <b>تحليل {symbol} ({name})</b>",
        "",
        "━━━━━━━━━━━━━━━━━",
        "💰 <b>السعر:</b>",
        f"السعر الحالي: <b>${technical['current_price']:.2f}</b>",
        f"التغير: {change_emoji} <b>{technical['change_pct']:.2f}%</b>",
        f"نطاق اليوم: {technical['day_range']}",
        "",
        "━━━━━━━━━━━━━━━━━",
        "📊 <b>الزخم:</b>",
        f"قصير المدى: {momentum['short_term']}",
        f"متوسط المدى: {momentum['medium_term']}",
        f"طويل المدى: {momentum['long_term']}",
        f"درجة الزخم: <b>{momentum['score']}/100</b>",
        "",
        "━━━━━━━━━━━━━━━━━",
        "⚡ <b>الحجم والسيولة:</b>",
        f"حجم التداول: <b>{volume['current_volume']:,}</b> سهم",
        f"متوسط الحجم: {volume['average_volume']:,} سهم",
        f"نسبة الحجم: <b>×{volume['volume_ratio']:.1f}</b>",
        f"السيولة: {volume['liquidity']}",
        f"اتجاه الحجم: {volume['volume_trend']}",
        "",
        "━━━━━━━━━━━━━━━━━",
        "📐 <b>المؤشرات الفنية:</b>",
        f"RSI: <b>{technical['rsi']:.1f}</b>",
        f"VWAP: ${technical['vwap']:.2f}",
        f"المسافة عن VWAP: {technical['vwap_distance']:.1f}%",
        "",
        "━━━━━━━━━━━━━━━━━",
        f"🎯 <b>الاتجاه العام: {direction['trend']}</b>",
        f"قوة الاتجاه: {direction['strength']}/100",
        f"التوصية: <b>{direction['recommendation']}</b>",
    ]
    
    if direction["reasons"]:
        lines.append("")
        lines.append("📋 <b>الأسباب:</b>")
        for reason in direction["reasons"]:
            lines.append(f"• {reason}")
    
    if analysis.get("news"):
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append("📰 <b>آخر الأخبار:</b>")
        
        for news in analysis["news"][:3]:
            headline = news.get("headline", "")
            source = news.get("source", "")
            timestamp = datetime.fromtimestamp(news.get("datetime", 0), NY_TZ).strftime("%m/%d %H:%M")
            
            sentiment = classify_news_sentiment(headline)
            sentiment_emoji = "🟢" if sentiment == "positive" else "🔴" if sentiment == "negative" else "⚪"
            
            lines.append(f"{sentiment_emoji} {headline[:100]}")
            lines.append(f"   ({source} - {timestamp})")
    
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━",
        f"🕐 {analysis['timestamp']}",
        "",
        "⚠️ <b>هذا تحليل آلي وليس توصية استثمارية</b>",
        "",
        "♦️ <b>شرعية الأسهم مسؤوليتك نبرأ منها أمام الله</b> ♦️"
    ])
    
    return "\n".join(lines)

def classify_news_sentiment(headline: str) -> str:
    """تصنيف مشاعر الخبر"""
    positive_words = ["beat", "surge", "upgrade", "record", "approval", "partnership", "growth", "profit"]
    negative_words = ["miss", "plunge", "downgrade", "lawsuit", "investigation", "loss", "decline"]
    
    headline_lower = headline.lower()
    
    if any(word in headline_lower for word in positive_words):
        return "positive"
    elif any(word in headline_lower for word in negative_words):
        return "negative"
    else:
        return "neutral"

# ═══════════════ البوت ═══════════════

class SmartTradingBot:
    """البوت الذكي"""
    
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

أنا بوت التداول الذكي. يمكنني:

📊 <b>تحليل أي سهم أمريكي:</b>
- أرسل رمز السهم فقط
- مثال: AAPL أو TSLA

💡 <b>ماذا سأقدم لك:</b>
- السعر الحالي والتغير
- الزخم (قصير، متوسط، طويل)
- الحجم والسيولة
- المؤشرات الفنية (RSI, VWAP)
- اتجاه السهم
- آخر الأخبار

❌ <b>إذا كان الرمز خاطئ:</b>
- سأخبرك أن الرمز غير صحيح

⚠️ <b>تنبيه مهم:</b>
- التحليل آلي وليس توصية
- شرعية الأسهم مسؤوليتك

🎯 <b>أرسل رمز السهم الآن!</b>
"""
            self.send_telegram(chat_id, help_message)
            return
        
        self.send_telegram(chat_id, "🔍 <b>جاري تحليل السهم...</b>")
        analysis = self.analyzer.analyze_stock(text)
        formatted = format_analysis_message(analysis)
        self.send_telegram(chat_id, formatted)
    
    def run(self):
        """التشغيل المستمر"""
        logger.info("🤖 بدء البوت الذكي...")
        
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
        except:
            pass
        
        return []

# ═══════════════ مسارات الويب ═══════════════

@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now(NY_TZ).strftime("%H:%M:%S")})

# ═══════════════ التشغيل ═══════════════

if __name__ == "__main__":
    # تشغيل البوت في خلفية
    bot = SmartTradingBot()
    bot_thread = threading.Thread(target=bot.run, daemon=True)
    bot_thread.start()
    
    # تشغيل خادم الويب
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 تشغيل على المنفذ {port}")
    app.run(host="0.0.0.0", port=port)
