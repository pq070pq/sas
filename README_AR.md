# SAS PRO — نظام اشتراك Telegram

نظام اشتراكات مدفوعة داخل Telegram باستخدام Mini App + Telegram Stars، مع قناة مدفوعة منفصلة عن قناة التجربة، وتجديد ذكي، وإدارة مشتركين.

## المزايا
- تجربة مجانية 3 أيام مرة واحدة لكل Telegram ID.
- رابط تجربة استخدام واحد صالح 48 ساعة.
- باقات: 100 / 250 / 500 / 1000 ريال، والمدد 30 / 90 / 180 / 365 يوم.
- Stars قابلة للضبط من لوحة الإدارة.
- لا يتم تفعيل أي اشتراك من إنشاء الفاتورة؛ التفعيل يتم فقط بعد successful_payment.
- التجديد يضيف المدة إلى تاريخ الانتهاء الحالي.
- رابط دخول خاص للقناة المدفوعة، استخدام واحد وصالح 48 ساعة.
- إلغاء تلقائي عند انتهاء الاشتراك.
- تحذير قبل الانتهاء بـ7 أيام مرة واحدة.
- لوحة إدارة خاصة بالمالك.
- Google Sheets اختياري.
- رادار SAS PRO للقناة يبقى يعمل من scheduler الحالي.

## Telegram
يجب أن يكون البوت مشرفًا في القناة المدفوعة وقناة التجربة مع صلاحية الدعوات وإدارة الأعضاء، لأن Bot API يستخدم createChatInviteLink وbanChatMember وunbanChatMember لإدارة الوصول. كما يجب ضبط webhook على HTTPS مع secret_token.

## Stars
Telegram يطلب استخدام XTR لبيع السلع والخدمات الرقمية داخل Telegram. يجب ضبط عدد Stars لكل باقة من لوحة الإدارة قبل تفعيل الدفع. لم يتم اختراع تحويل ثابت بين الريال وStars؛ Telegram يوضح أن تكلفة شراء Stars قد تختلف بحسب VAT والرسوم. لذلك قيمة Stars مستقلة وقابلة للتعديل.

## التشغيل على OVH
1. انسخ المشروع إلى /opt/saspro.
2. أنشئ .env من .env.example.
3. ضع Telegram Bot Token وOwner ID والقناة المدفوعة وقناة التجربة.
4. ضع قيم Stars الفعلية من لوحة الإدارة.
5. شغّل:
   docker compose up -d --build
6. اجعل Nginx يمرر HTTPS إلى 127.0.0.1:8000.
7. اضبط webhook:
   curl -sS -X POST "https://api.telegram.org/botYOUR_TOKEN/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url":"https://YOUR_DOMAIN/api/telegram/webhook","secret_token":"YOUR_SECRET","allowed_updates":["message","pre_checkout_query","chat_member","chat_join_request"]}'

## Google Sheets
أنشئ Google Sheet وفعّل Sheets API، وأنشئ Service Account وامنح بريد الخدمة صلاحية Editor على الورقة. ضع JSON الخاص بالحساب في GOOGLE_SERVICE_ACCOUNT_JSON داخل .env، ولا تضعه في Git.

## أوامر الإدارة
- /STATUS
- /grant TELEGRAM_ID DAYS
- /grant TELEGRAM_ID forever
- /revoke TELEGRAM_ID

