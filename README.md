# SAS PRO Mini App — v1
واجهة البداية لتيليجرام Mini App.
- تصميم داكن مع خلفية SAS.
- خانات منفصلة: الرابحة، Penny، Swing، الاستثمار، الأخبار، النشاط غير الطبيعي.
- شريط أسواق علوي متحرك.
- شريط إخلاء مسؤولية متحرك.
- شريط شركات قيادية سفلي متحرك.
- Telegram WebApp SDK مهيأ.
- الدفع معطل افتراضيًا.

## التشغيل
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

ثم افتح http://localhost:8000
