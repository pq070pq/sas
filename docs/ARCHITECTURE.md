# SAS PRO — تصنيف النظام

هذا الملف هو المرجع الأساسي لتصنيف المشروع. القاعدة: كل قسم له مسؤولية واحدة، والتواصل بين الأقسام عبر واجهات واضحة.

## 1. CORE / التشغيل
- `app/main.py`: FastAPI + API routes + startup orchestration.
- `app/config.py`: الإعدادات.
- `app/timeutil.py`: الوقت والمناطق الزمنية.
- `app/db.py`: نماذج وقاعدة البيانات.

**قاعدة:** لا يوضع منطق تحليل سهم جديد في `main.py`.

## 2. MARKET DATA / بيانات السوق
- `app/market.py`: بيانات الأسهم/السوق العامة.
- `app/twelve_guard.py`: حماية واستدعاء Twelve Data.
- `app/binance_spot.py`: Binance Spot للكريبتو فقط.
- `app/market_calendar.py`: جلسات السوق والعطلات.
- `app/holiday_radar.py`: بيانات/نشر حالة السوق خارج جلسة الأسهم.

**قاعدة:** مزود البيانات يعيد بيانات فعلية فقط. لا يخترع سعرًا أو حجمًا أو مستوى.

## 3. STOCK RADAR / رادار الأسهم
- `app/scanner.py`: اكتشاف وترشيح وتحليل مرشحي الأسهم.
- `app/jobs.py`: دورة الرادار والـ scheduler وتسليم الإشارات.
- `app/radar_learning.py`: التعلم من نتائج الرادار.
- `app/smart_memory.py`: الذاكرة والكاش/منع التكرار.

**قاعدة:** الرادار لا يعتمد على واجهة Mini App ولا على JavaScript.

## 4. TECHNICAL / التحليل الفني
- `app/panwatch.py`: الشموع والمستويات والأهداف الفنية.
- التحليل الفني المستخدم في الرادار يبقى مصدره بيانات فعلية.

**قاعدة:** Entry/Stop/Targets لا تُنشأ بالتخمين.

## 5. FUNDAMENTALS / NEWS / AI
- `app/news.py`: الأخبار والأحداث.
- `app/ai_radar.py`: تلخيص وتحليل AI.
- `app/private_analysis.py`: تجميع التحليل الخاص للمستخدم.

**قاعدة:** AI يشرح البيانات ولا ينشئ سعرًا أو هدفًا أو وقفًا من عنده.

## 6. SHARIAH / الشرعية
- `app/shariah.py`: التصنيف الشرعي ومصادره.
- `app/fcc_reviewer.py`: مراجعة المحتوى/الامتثال.

**قاعدة:** CapEdge أو التحليل الفني ليس مصدرًا لحكم الشرعية.

## 7. TELEGRAM / الإرسال
- `app/telegram.py`: طبقة Telegram.
- وظائف الإرسال في `app/jobs.py`: نشر النتائج والتنبيهات.

**قاعدة:** Telegram يستقبل نتيجة جاهزة ولا يحتوي منطق اكتشاف الأسهم.

## 8. MINI APP / التطبيق
- `web/assets/app.js`: الواجهة.
- Routes الخاصة بـ Mini App موجودة في `app/main.py`.

**قاعدة:** التطبيق يقرأ API ولا يشغل `scanner.py` مباشرة.

## 9. SUBSCRIPTIONS / الاشتراكات
- `app/subscriptions.py`: الخطط والتجربة والدفع والوصول.
- `app/admin.py`: صلاحيات الإدارة.

**قاعدة:** الرادار لا يعدل صلاحيات المستخدمين.

## 10. DATABASE / التخزين
- `app/db.py`: النماذج.
- `RadarRun`: سجل دورة الرادار.
- `RadarSignal`: الإشارات.
- `RadarOutcome`: نتائج الأهداف.
- User/Subscription/Payment: المستخدمون والاشتراكات.

## 11. HEALTH / DIAGNOSTICS / صحة النظام
- `app/radar_health.py`: مراقبة scheduler والرادار وإعادة التشغيل عند التعطل.
- `.github/workflows/radar-diagnostics.yml`: تشخيص يدوي.
- `.github/workflows/radar-live-status.yml`: دليل حالة الإنتاج.
- `.github/workflows/syntax.yml`: اختبارات الكود.
- `.github/workflows/deploy.yml`: التحقق ثم النشر.

## 12. DEPLOYMENT
- `Dockerfile`
- `docker-compose.yml`
- `deploy/*`
- `systemd/*`

## 13. الاختبارات
- `tests/test_architecture.py`: اختبارات الحدود بين الأقسام.

## قواعد منع التخريب المتبادل

### عند تعديل الرادار
يسمح بتعديل:
- scanner
- radar learning
- radar health عند الحاجة
- jobs عند الحاجة لتشغيل/تسليم الرادار
- اختبارات الرادار

ولا يتم تعديل Mini App أو subscriptions أو Binance إلا إذا كان هناك سبب موثق.

### عند تعديل Mini App
يسمح بتعديل:
- web/assets
- API contract في main.py عند الحاجة

ولا يتم تغيير scanner أو scheduler.

### عند تعديل Binance
يسمح بتعديل:
- binance_spot.py
- مسارات crypto في main.py/private_analysis.py
- اختبارات crypto

ولا يتم تغيير Stock Radar.

### عند إضافة Nasdaq/S&P/Dow/Gold
تدخل تحت Market Data / Macro، ولا تُضاف إلى Binance ولا إلى scanner إلا إذا كان الهدف صريحًا هو استخدامها كفلتر للرادار.

## ترتيب التنفيذ الآمن

1. Provider
2. Normalized data
3. Analysis
4. Radar
5. Database
6. API
7. Telegram/Mini App
8. Tests
9. Deploy

لا نعكس هذا الترتيب.

## معيار القبول

أي تغيير جديد يجب أن:
- يمر compile.
- يمر import smoke test.
- يمر architecture tests.
- لا يكسر API الحالية.
- لا يعرض سعرًا غير موثق.
- لا يغير قسمًا غير متعلق بالمهمة دون سبب.
