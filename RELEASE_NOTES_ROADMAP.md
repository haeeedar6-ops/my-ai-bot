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

---
# الإضافة: مكالمة صوتية حية (Gemini Live) — للويب على كروم الموبايل

**الفكرة:** قناة وحدة بالاتجاهين بدل (تعرّف كلام → رد كامل → نطق). المتصفح بيبعت صوتك مباشرة، والرد بيرجع صوت متواصل، والمقاطعة بتشتغل، وما بدك تطفي/تشغّل المايك.

**الملفات الجديدة:** `voice/live_bridge.py` (السيرفر) — `voice/live_client.js` (المتصفح). تعديلات صغيرة بـ `webapp.py` و`voice/duplex_vad.js` و`requirements.txt`.

## الإعداد على Render
1. `requirements.txt` صار فيه `flask-sock` (لازم يتثبّت بالنشر).
2. تأكد إن `GEMINI_API_KEY` موجود (نفس مفتاحك الحالي).
3. إذا بتشغّل التطبيق بـ gunicorn لازم threads: `gunicorn ... --workers 1 --threads 8`. (بتشغيل `python bot.py` المباشر ما بتحتاج شي.)
4. اختياري: `GEMINI_LIVE_MODEL` (اسم/أسماء موديل Live مفصولة بفواصل)، `GEMINI_LIVE_VOICE` (مثلاً Charon/Orus/Puck)، `LIVE_MAX_CONCURRENT` (افتراضي 3).

## كيف بيشتغل
- السيرفر بيكتشف موديلات Live المتاحة على مفتاحك تلقائياً (bidiGenerateContent) وبيجرّب أكتر من موديل/إعداد لحد ما واحد يتصل. إذا فشلوا كلهم الموقع بيرجع تلقائياً للوضع التقليدي.
- ميكروفون واحد بس مع إلغاء صدى المتصفح، ومقاطعة محلية فورية + مقاطعة Gemini.
- النص (كلامك ورد رين) بينحفظ بسجل المحادثة بعد كل دور.
- اللهجة السورية والجمل القصيرة بتعليمات النظام. تغيير الصوت: `localStorage.ryn_live_voice='Orus'`. الرجوع للتقليدي: `localStorage.ryn_live_mode='classic'`.
- حماية: جلسة وحدة لكل مستخدم، سقف 3 مكالمات بنفس الوقت، مهلة خمول 120 ثانية، إعادة اتصال تلقائية مع ملخص الحوار (Gemini بيقطع الجلسة الصوتية بعد ~15 دقيقة).

## حدود صريحة
- **ما انجرّب مع Gemini الحقيقي** (ما في إنترنت هون). المنطق مختبر بمحاكاة (سيرفر + متصفح محاكى)، بس أسماء الموديلات والـ SDK ممكن تحتاج ضبط أول مرة. لو ما اشتغل بتشوف الرسالة بـ `/api/v2/live/status` والرجوع التلقائي للتقليدي.
- **الخلفية:** كروم الموبايل بيوقف الميكروفون لما الصفحة تروح بالخلفية أو الشاشة تنطفي؛ الموقع بيمنع إطفاء الشاشة (Wake Lock) بس ما بيقدر يشتغل بالخلفية. الحل الحقيقي = APK مع Foreground Service (خطوة منفصلة).
- حدود الاستخدام المجاني لـ Live API عند Google: تأكد منها من لوحة AI Studio.
- الصدى: جرّب بالسماعات أولاً؛ على سبيكر الموبايل ممكن يقاطع حاله.

---
# تحديث v4 (حسب طلباتك)

**الأزرار العائمة (بالترتيب):** رسم — Gemini — Ryn mix — **Gemini Live ✨** — Live Voice — كل الموديلات — **GitHub ON/OFF**.
- **حذفت** زر "كود ON/OFF" (توجيه الكود التلقائي لـ Groq/GPT) من الموقع، والتوجيه نفسه صار مطفي على الموقع (`WEB_CODE_ROUTING=1` لإرجاعه). تليجرام ما تغيّر.
- **زر GitHub:** ON = متل ما كان (التعديل والرفع من الشات). OFF = قراءة وفهم فقط؛ كل دوال الكتابة (حفظ/حذف/رفع مشروع/فرع) بتنرفض، وأي بلوك تعديل بالرد بينشال مع تنبيه. محفوظ لكل مستخدم.
- **يفهم أي مشروع:** تحليل تلقائي (لغات، أطر، نقاط دخول، اختبارات، CI/نشر، مجلدات، README) بينحقن بسياق الشات. يدعم Python/JS/TS/Kotlin/Java/Go/Rust/PHP/Ruby/C#/Dart/Swift وغيرها. `GET /api/github/project-profile`.
- **Gemini Live (زر منفصل)** حسب خطة جمنايت: اتصال **مباشر من المتصفح لـ Google** بدون ما يمر الصوت على Render. السيرفر بيعطي توكن مؤقت (أو المفتاح إذا ما اشتغل المؤقت وLIVE_ALLOW_RAW_KEY=1) + الشخصية والذاكرة وهوية haydar_xe عبر `POST /api/v2/live/token`. المتصفح بيجرب أكتر من صياغة setup لحد ما تشتغل. Live Voice القديم بقي كما هو.
- **النطق:** (1) Live: تعليمات نطق الحروف العربية بمخارجها. (2) Live Voice/TTS: الأرقام والرموز والكلمات اللاتينية (GitHub، API، Python...) بتتحول لحروف عربية قبل النطق.

**تنبيه أمان:** بوضع المفتاح الخام المفتاح بيوصل لمتصفح المستخدم المسجّل. مقبول لـ 3 أشخاص تثق فيهم؛ للمنع اضبط `LIVE_ALLOW_RAW_KEY=0` (بيبقى التوكن المؤقت فقط).


## RYN UI Refresh — Plus Launcher and Readability Update
- Moved the RYN Ultra entry out of the three-dot menu into the plus launcher, directly below RYN Mix.
- Refreshed the main RYN interface with an emerald-green glass visual treatment and improved desktop spacing.
- Improved RYN Ultra transcript wrapping, width constraints, bidirectional text handling, and mobile readability.
