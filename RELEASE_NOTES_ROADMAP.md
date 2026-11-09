# RYN — ملاحظات الإصدار حسب الخطة (Master Development Roadmap)

## شو تغيّر
**Phase A — أساس الصوت**
- Azure (مدفوع) مقفل افتراضياً: بيشتغل بس مع `TTS_PROVIDER=azure` أو `TTS_ALLOW_PAID=1` (وبيجي آخر خيار).
- تقطيع نص الويب 600 → 240 حرف. اتصالات SQLite الصوتية بتنسكّر فعلياً.
- حالات الجلسة: + `interrupted` / `reconnecting` / `error`.

**Phase B — مقاطعة المستخدم (Barge-in)**
- ويب: `voice/duplex_vad.js` (ميكروفون بإلغاء صدى المتصفح + VAD تكيفي). 3 مستويات: normal / headphones / speaker.
- ويندوز: `ryn-desktop/app/duplex.py` (مطفي افتراضياً: `RYN_DESKTOP_DUPLEX=1`، والأفضل سماعات). يرجع لوضع الضغط-للكلام إذا ما في ميكروفون/مكتبة.
- أندرويد: `DuplexVad.kt` (منطق الكشف فقط، غير موصول بـ VoiceController).

**Phase C — اللهجات**
- سجل 9 لهجات (`DIALECT_REGISTRY`) مع إفصاح صريح: فلسطيني/عراقي/مغربي = أقرب صوت (تقريبي). ما في لهجة معلّمة "مؤكدة سمعياً". الافتراضي: سوري ذكر (ليث).
- `GET /api/v2/voice/dialects`.

**Phase E — RYN Mix كمنسّق** (`core/orchestrator.py`)
- INPUT→INTENT→PLAN→ROUTE→EXECUTE→VALIDATE→SYNTHESIS→AUDIT، ميزانيات (وقت/أدوات/إعادة)، تأكيد للإجراءات الخطرة، رجوع آمن لرد RYN Mix المباشر، تدقيق بدون أسرار.
- طلبات الصور تمر كـ `protected` (ما بتنلمس).
- `POST /api/v2/mix/plan` (dry-run فقط)، `GET /api/v2/mix/audit`.
- **ما انربط بـ `/api/chat`** عن قصد (حماية سلوك الموديلات الحالي). الربط الفعلي قرار منفصل.

**Phase F — GitHub**
- كل رفع بدو موافقة صريحة (حتى لو `GITHUB_REQUIRE_APPROVAL=0`)، مربوطة ببصمة التعديل بالضبط + مدة صلاحية (`GITHUB_APPROVAL_TTL_SEC`، افتراضي 900).
- عمليات: edit / create / delete (الحذف بدو كتابة المسار بالضبط).
- `/api/github/save` و`/api/github/delete` ما بيكتبوا مباشرة هلأ: بيرجعوا 428 مع diff، والرفع من `POST /api/v2/github/changes/<id>/commit`.
- سجل تدقيق: `GET /api/v2/github/audit`. إخفاء أسرار. تحقق صارم من المسارات. إنشاء فرع عمل تلقائي (ما بيلمس main).

## شو ما انلمس
توليد/تعديل الصور، كتالوج الموديلات وشخصياتها، الاشتراكات، بيانات الإنتاج.

## حدود معروفة (إفصاح)
- ما في اختبار على أجهزة حقيقية: صدى سبيكر الموبايل/ويندوز، زمن الاستجابة، البلوتوث.
- لا يُعتبر Full-Duplex مكتملاً قبل اختباره على الأجهزة (معيار الخطة).
- كل أصوات TTS بحاجة اختبار سمعي قبل تعليمها "verified".
- أندرويد: لا يوجد ربط/بناء (ما في Gradle هون).
- مسارات GitHub الحية (إنشاء فرع، read/write/delete عبر API) مختبرة بمحاكاة فقط.
- ملف `my-ai-bot-main (30).zip` الداخلي لسا موجود (الوثائق بتقول يُحذف).

## كيف تطفي الميزات
`VOICE_FEATURE_ENABLED=0`، `LIVE_VOICE_ENABLED=0`، `localStorage.ryn_duplex='off'` (ويب)، `RYN_DESKTOP_DUPLEX=0` (ويندوز).

## Rollback
كل تعديل منفصل بملفات جديدة أو إضافات صغيرة؛ رجّع النسخة السابقة من المستودع. لا ترحيلات هدّامة (أعمدة جديدة بس بجدول code_change_requests).
