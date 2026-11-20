# my-ai-bot (Ryn + RYN Ultra)

تطبيق ويب (Ryn) مع فريق RYN Ultra (خمس وكلاء ذكاء اصطناعي بنقاش حي). بوت تيليجرام انحذف نهائياً؛ الموقع هو الأساس.

## الملفات
- `bot.py`: نواة الاتصال بالنماذج (Gemini + OpenRouter + Groq) وبيركّب الموقع تلقائياً (`mount_web()`).
- `webapp.py`: واجهة الويب ومساراتها (الدردشة، الصوت، الصور، الذاكرة، حدود النماذج...).
- `ryn_ultra.py` + `ryn_ultra.html`: اجتماع الفريق الحي على `/ultra`.
- `ryn_memory.js`: مكتبة **ذاكرة الجهاز** المشتركة بين الصفحة الرئيسية و`/ultra` (بتنحقن بالصفحتين من السيرفر).
- `visual_brain/`, `image_providers/`, `image_processing/`: توليد وتعديل الصور. `voice/`: الصوت والمحادثة الحية.
- ملاحظة: `ryn_agent.py` و`ryn-desktop/` و`ryn-android/` و`code_intelligence/` **مو موجودين بهالحزمة**. إذا عندك نسخ منهم بالريبو لا تحذفهم عند الرفع.

## التشغيل
```bash
pip install -r requirements.txt
export WEB_PASSWORD=... WEB_SECRET=$(openssl rand -hex 32) GEMINI_API_KEY=...   # الباقي اختياري
python bot.py            # أو على Render:  gunicorn bot:app --workers 1 --threads 8
```
`--threads` ضروري: كل اجتماع Ultra بيفتح اتصال بث (SSE) طول مدة الاجتماع، وبدون threads بيتجمّد الموقع. و`--workers 1` لأنو حالة الطلبات بالذاكرة.

### إعدادات النشر والأمان (مهمة)
| المتغير | شو بيعمل |
|---|---|
| `WEB_SECRET` | مفتاح توقيع الجلسات (نص عشوائي طويل). إذا ما انضاف بيتولّد عشوائي ويتخزّن بـ `RYN_DATA_DIR`. |
| `RYN_DATA_DIR` | مجلد بيانات **دائم** (Render Disk). بدونو سجل Ultra والمحادثات بتنكتب بمجلد مؤقت وبتنمسح مع كل نشر. |
| `TRUSTED_PROXY_HOPS` | كم بروكسي موثوق قدّام التطبيق لتحديد IP العميل بحد محاولات الدخول. الافتراضي `1` (Render مباشرة)، و`2` إذا شغّال عبر Cloudflare (`_worker.js`)، و`0` لتجاهل `X-Forwarded-For`. |
| `LOGIN_GLOBAL_MAX_FAILS` | سقف محاولات الدخول الفاشلة من كل المصادر خلال 10 دقايق (الافتراضي 30). |
| `GEMINI_MODEL` / `GEMINI_DEEP_MODEL` | الافتراضي `gemini-3.8-flash`. الأحدث: `gemini-4-hydrogen` (يومي) و`gemini-4-argon` (تفكير) و`gemini-4-carbon` (الأقوى). |
| `OR_ALLOW_PAID` | بدونو: DeepSeek وKimi بيظهروا أوفلاين بـ Ultra لأنو OpenRouter ما عاد عندو نسخة مجانية منهم. |
| `GROQ_VISION_MODELS` | موديلات رؤية Groq للمدقق البصري. الافتراضي `qwen/qwen3.8-27b` (llama-4 انسحب). |

## ذاكرة الجهاز لكل النماذج (الموقع كامل)
- الذاكرة بتنخزّن **على جهازك** (`localStorage` بالمتصفح) وبتنقرا من الصفحة الرئيسية ومن `/ultra` نفسها، فكل النماذج (Gemini، GPT-OSS، Qwen، DeepSeek، Kimi، Ryn mix، والوكلاء الخمسة) بتستفيد منها.
- مع كل طلب المتصفح بيبحث بالذاكرة (عربي مع تجذير، ترتيب حسب الصلة) وبيبعت الأنسب بس (حتى 12 معلومة)، والسيرفر ما بيخزّنها.
- قول بالشات «تذكر أني...» أو «لا تنسى إنو...» وبتنحفظ فوراً. السعة حتى 800 معلومة، وما بتنحفظ الأسرار (مفاتيح/كلمات سر).
- تصدير/استيراد ملف `.json` من لوحة الذاكرة (🧠) بالصفحتين.

## النماذج (تم التحقق منها مقابل الصفحات الرسمية، تشرين الثاني 2026)
- **Gemini**: `gemini-3.8-flash` ← `gemini-4-hydrogen` ← `gemini-3.6-flash` ← `gemini-3.5-flash-lite` ← `gemini-3.1-flash-lite` ← `gemini-3-flash-preview` (+ `gemini-4-argon` و`gemini-4-carbon` و`gemini-3.1-pro-preview` للأسئلة العميقة). `gemini-2.5-flash/pro` ملغيين وانشالو. الأسماء الميتة بتنتخطى وبتنحفظ 30 دقيقة.
- **Groq**: `openai/gpt-oss-120b` و`openai/gpt-oss-20b` و`qwen/qwen3.8-27b`. (انسحبوا من الطبقة المجانية: llama 3.x/4، qwen3 وqwen3.6، kimi، deepseek-distill، gemma2.)
- **OpenRouter**: الاختيار من الكتالوج الحي (`openrouter/free` و`qwen/qwen3.8-27b:free`...).
- **الصوت المباشر**: `gemini-3.8-live` أولاً.

## الاختبارات
```bash
python -m unittest discover -s tests
```

لا تضع المفاتيح بالكود أبداً — كلها متغيرات بيئة (على Render من Environment).

## المنظومة الصوتية الموحدة متعددة اللغات (Multilingual ASR & Neural TTS)
- **محرك النطق العصبي (`tts_engine.py` & `voice/__init__.py`)**:
  - يدعم كتالوج أصوات Microsoft Neural الموثّق متعدد اللغات: العربية بلهجاتها (الشامي السوري `ar-SY-LaithNeural` و`ar-SY-AmanyNeural`، السعودي الفصيح `ar-SA-HamedNeural` و`ar-SA-ZariyahNeural`، المصري `ar-EG-SalmaNeural` و`ar-EG-ShakirNeural`، الإماراتي `ar-AE-HamdanNeural`، الأردني `ar-JO-SanaNeural`)، الإنجليزية (`en-US`, `en-GB`)، الألمانية (`de-DE`)، الفرنسية (`fr-FR`)، الإسبانية (`es-ES`, `es-MX`)، الإيطالية (`it-IT`)، التركية (`tr-TR`)، والبرتغالية (`pt-BR`, `pt-PT`).
  - توجيه تلقائي للغة الرد، تقسيم الجمل الطويلة بدون اقتطاع صامت، والتحقق من تواقيع حاويات الصوت (`MP3` / `WAV` / `OGG`).
  - مسار موحد (`/api/v2/voice/speak`) مستخدم في تطبيق الويب، عميل ويندوز (`ryn-desktop/app/voice.py`)، وتطبيق أندرويد (`VoiceController.kt`).
- **التعرّف الصوتي متعدد اللغات (ASR)**:
  - الوضع الافتراضي `ASR_LANGUAGE=auto` (كشف تلقائي للغة واللهجة ودعم الجمل المختلطة Code-Switching) مع إمكانية اختيار لغة BCP-47 صريحة لكل مستخدم.
- **متغيرات البيئة الصوتية**:
  - `ASR_PROVIDER`: `groq` | `whisper` | `azure` (مزود حقيقي إلزامي في الإنتاج عبر `GROQ_API_KEY` أو `OPENAI_API_KEY` أو `AZURE_SPEECH_KEY`).
  - `ASR_MODEL`: نموذج التفريغ متعدد اللغات (الافتراضي `whisper-large-v3-turbo`).
  - `ASR_LANGUAGE`: `auto` افتراضياً، أو كود BCP-47 مثل `ar-SY`, `en-US`, `de-DE`.
  - `TTS_PROVIDER`: `auto` | `azure` | `edge` | `gtts` (في الوضع التلقائي يفضّل `azure` الرسمي عند توفر `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`، ثم `edge` العصبي، ثم `gtts` كطوارئ أساسي مُعلَم صراحةً).
  - `TTS_DEFAULT_VOICE`: معرّف صوت موثق من الكتالوج (الافتراضي `ar-SY-LaithNeural`).
  - `VOICE_AUTO_READ`: تفعيل القراءة التلقائية للردود (الافتراضي `false`).
  - `VOICE_FEATURE_ENABLED` / `LIVE_VOICE_ENABLED`: أعلام تفعيل ميزات الصوت والمحادثة الحية.


## RYN Ultra — Live Five-Agent Collaboration
- Separate authenticated workspace: `/ultra` (also linked from the main menu).
- Five roles: Gemini (Strategist), GPT / GPT-OSS (Architect), Qwen (Developer), DeepSeek (Reviewer), and Kimi (Context Analyst). Actual provider model IDs are shown in the meeting UI; unavailable providers are marked offline and are never represented by simulated messages.
- The meeting engine runs independent first-pass analysis followed by configurable sequential debate rounds. The transcript is pushed to the browser with Server-Sent Events and saved per web user in SQLite.
- User controls: pause, resume, stop, another round, and instructions to all agents or one named agent.
- Optional ZIP repository inspection is read-only. It skips sensitive/generated/binary paths, enforces archive limits, redacts common inline secrets, and does not extract or modify project files. RYN Ultra currently produces analysis and proposed next steps; it does not apply code patches.
- Reuses the existing provider configuration: `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and/or `GROQ_API_KEY`. Model overrides: `RYN_ULTRA_GEMINI_MODEL`, `RYN_ULTRA_GPT_MODEL`, `RYN_ULTRA_QWEN_MODEL`, `RYN_ULTRA_DEEPSEEK_MODEL`, `RYN_ULTRA_KIMI_MODEL`.
- Resource controls: `RYN_ULTRA_AGENT_TIMEOUT` (default 90 seconds for OpenAI-compatible agents; Gemini continues using the existing RYN Gemini timeout), `RYN_ULTRA_MAX_OUTPUT_TOKENS` (default 3000 for OpenAI-compatible providers), `RYN_ULTRA_MAX_INPUT_CHARS` (default 70000), `RYN_ULTRA_SESSION_CALL_LIMIT` (default 26), `RYN_ULTRA_DAILY_CALL_LIMIT` (default 40), `RYN_ULTRA_DB_PATH` (optional SQLite path). Also: `RYN_ULTRA_MAX_FALLBACK_MODELS` (default 3 backup models per agent call; each call counts once), `RYN_ULTRA_MAX_PAUSE_SEC` (default 3600; a forgotten paused meeting is stopped). Invalid numeric values fall back to defaults. These are request-count/token ceilings, not a guaranteed monetary cost estimate.
- Provider calls are sequential and the UI shows each completed model response as it arrives. This release streams meeting events, not individual output tokens.

- Prompt budget: when an attached project is large, only the project material is shortened — the task, device memory, round instructions and the discussion transcript are always kept (previously the debate was cut off for GPT/Qwen/DeepSeek/Kimi).
- "Another round" requested during the final report now continues the meeting instead of being lost; session status is `running` while the meeting runs; stop during the report is respected.
- The browser reconnects with `Last-Event-ID`, the server sends an explicit `end` event, and the page ignores duplicate events.
- Device memory (facts the user stored on their own device) is attached per meeting, shown to all five agents and the final report; agents can never write to it.
