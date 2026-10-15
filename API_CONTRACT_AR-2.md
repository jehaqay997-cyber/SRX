# عقد API الأولي

| الطريقة | المسار | المصادقة | الغرض |
|---|---|---|---|
| GET | `/v1/health` | لا | صحة العملية |
| GET | `/v1/ready` | لا | جاهزية SQLite |
| GET | `/v1/sources?limit=20&offset=0` | Bearer | ترقيم المصادر |
| POST | `/v1/sources` | Bearer | إضافة مصدر ذي منشأ وتصنيف معرفي |
| GET | `/v1/observations?limit=20&offset=0` | Bearer | استعراض الملاحظات مع ترقيم الصفحات |
| GET | `/v1/rules?limit=20&offset=0` | Bearer | استعراض القواعد |
| GET | `/v1/scenarios?limit=20&offset=0` | Bearer | استعراض السيناريوهات التي اجتازت البوابة |
| GET | `/v1/evaluations?limit=20&offset=0` | Bearer | استعراض التقييمات المسجلة |
| POST | `/v1/observations` | Bearer | إضافة ملاحظة مرتبطة بمصدر موجود |
| POST | `/v1/rules` | Bearer | إضافة قاعدة تستند إلى ملاحظات موجودة |
| POST | `/v1/validate-bundle` | Bearer | التحقق من حزمة SRX-021 دون حفظ |
| POST | `/v1/scenarios` | Bearer | إضافة سيناريو بعد اجتياز تتبع الأدلة |
| POST | `/v1/forecasts` | Bearer | تسجيل تنبؤ مرتبط بسيناريو موجود |
| GET | `/v1/forecasts?limit=20&offset=0` | Bearer | استعراض سجل التنبؤات |
| POST | `/v1/evaluations` | Bearer | تسجيل النتيجة المرصودة لتقييم لاحق |

أخطاء متوقعة: `401` مصادقة مفقودة/غير صحيحة، `409` تعارض سجل أو قيد مرجعي، `413` حزمة أكبر من الحد، `422` فشل تحقق البيانات أو بوابة الأدلة، `503` المصادقة غير مضبوطة على واجهة محمية أو الخدمة غير جاهزة.
