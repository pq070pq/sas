# SAS PRO Architecture & Change Isolation

## الهدف
منع أي تعديل في رادار الأسهم من كسر Mini App أو الاشتراكات أو Telegram، ومنع تغييرات التطبيق من تغيير منطق الرادار.

## الحدود

| القسم | المسؤولية | ملفات رئيسية | لا يعتمد على |
|---|---|---|---|
| Data Providers | جلب بيانات أصل محدد | `market.py`, `binance_spot.py`, `holiday_radar.py` | Mini App |
| Stock Radar | اكتشاف وفرز فرص الأسهم | `scanner.py`, `jobs.py`, `radar_learning.py` | واجهة الويب |
| Analysis | التحليل الخاص بسهم واحد | `private_analysis.py`, `panwatch.py`, `news.py`, `ai_radar.py` | دورة scheduler |
| Shariah | تصنيف الشرعية | `shariah.py` | واجهة الويب |
| Delivery | إرسال Telegram | `telegram.py`, وظائف الإرسال في `jobs.py` | HTML/JS |
| Mini App API | API التي يستهلكها التطبيق | `main.py` | منطق العرض داخل JS |
| Mini App UI | العرض والتفاعل | `web/assets/app.js` | Python internals |
| Subscriptions/Admin | الاشتراكات والصلاحيات | `subscriptions.py`, `admin.py` | scanner internals |
| Persistence | قاعدة البيانات والنماذج | `db.py` | مزود بيانات خارجي |
| Health/Diagnostics | صحة scheduler والرادار | `radar_health.py`, workflows | UI |

## تدفق البيانات

```
Provider -> normalized market data -> Radar/Analysis -> DB
                                      |             |
                                      v             v
                                  Telegram       Mini App API -> Mini App
```

التطبيق **لا يشغل الرادار**. الرادار يحفظ النتيجة، وMini App يقرأ النتيجة عبر API.

## مصادر الأصول

- الأسهم الأمريكية: مسار الأسهم الحالي في SAS PRO.
- BTC والعملات: `app/binance_spot.py` فقط.
- Nasdaq/S&P 500/Dow Jones/Gold: طبقة بيانات المؤشرات/السلع؛ لا تُمرر إلى Binance Spot.
- الشرعية: `app/shariah.py` فقط، ولا تُستنتج من CapEdge أو التحليل الفني.

## قواعد التعديل

1. لا تنقل منطق Binance إلى `scanner.py`.
2. لا تضف منطق Radar إلى `main.py` أو `web/assets/app.js`.
3. لا تجعل AI ينشئ سعرًا أو هدفًا أو وقفًا غير موجود في البيانات الفنية.
4. أي سعر غير متاح يبقى غير متاح؛ لا يوجد fallback تخميني.
5. أي تغيير في عقد API يجب أن يحافظ على الحقول الحالية أو يضيف حقولًا متوافقة للخلف.
6. أي تعديل قبل `main` يجب أن يمر عبر syntax/import/boundary tests.
7. فشل الاختبارات يمنع النشر.
8. تشخيص الرادار الحي منفصل عن دورة النشر؛ لا نعتبر commit ناجحًا دليلًا على أن دورة الإنتاج نجحت.

## بوابة الأمان

قبل النشر:

- compile لكل Python.
- فحص JavaScript.
- import لـ `app.main`.
- اختبارات حدود الأقسام.
- اختبارات pure functions لمزود Binance.
- بعد النشر: health endpoint وحالة الحاوية.
- الفحص الحي للرادار لا يرسل نتائج إلى القناة ولا يعدل قاعدة الإنتاج إلا عبر مسار الرادار المعتاد.

## سياسة الإصلاح

إذا ظهر خطأ:
1. نحدد القسم المسبب.
2. نصلح أقل عدد ممكن من الملفات.
3. نختبر القسم المسبب + الاختبارات العامة.
4. لا نعيد كتابة أقسام سليمة لمجرد تغيير معماري.
5. لا نخلط refactor مع تغيير سلوك الرادار في commit واحد إلا إذا كان ضروريًا للإصلاح.
