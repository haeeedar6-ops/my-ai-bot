# my-ai-bot ( AI + Ryn)

بوت تيليجرام وتطبيق ويب (Ryn) مع وكيل تحكم بالأجهزة (ويندوز + أندرويد).

## الملفات
- `bot.py`: بوت تيليجرام (Gemini + OpenRouter + Groq) — يقرأ `knowledge/` قبل كل جواب.
- `webapp.py`: واجهة الويب و API الوكيل (`/api/v1/agent/*`) ومسارات العقل الصوتي الموحّد (`/api/v2/voice/*`).
- `ryn_agent.py`: نواة الوكيل (إقران، مهام، أوامر، سياسات أمان).
- `ryn-desktop/`: عميل ويندوز (`python run_desktop.py`).
- `ryn-android/`: عميل أندرويد (Android Studio).
- `visual_brain/`, `image_providers/`, `image_processing/`: توليد وتعديل الصور.

## التشغيل
```bash
pip install -r requirements.txt
export BOT_TOKEN=... GEMINI_API_KEY=...   # الباقي اختياري (OPENROUTER_API_KEY, GROQ_API_KEY ...)
python bot.py
```

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
- Resource controls: `RYN_ULTRA_AGENT_TIMEOUT` (default 90 seconds for OpenAI-compatible agents; Gemini continues using the existing RYN Gemini timeout), `RYN_ULTRA_MAX_OUTPUT_TOKENS` (default 3000 for OpenAI-compatible providers), `RYN_ULTRA_MAX_INPUT_CHARS` (default 70000), `RYN_ULTRA_SESSION_CALL_LIMIT` (default 26), `RYN_ULTRA_DAILY_CALL_LIMIT` (default 40), `RYN_ULTRA_DB_PATH` (optional SQLite path). These are request-count/token ceilings, not a guaranteed monetary cost estimate.
- Provider calls are sequential and the UI shows each completed model response as it arrives. This release streams meeting events, not individual output tokens.
