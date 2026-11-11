# my-ai-bot ( AI + Ryn)

تطبيق ويب (Ryn) مع وكيل تحكم بالأجهزة (ويندوز + أندرويد). تم حذف بوت تيليجرام بالكامل.

## الملفات
- `bot.py`: سيرفر Flask + محرك الموديلات (Gemini + OpenRouter + Groq) وهو اللي بيشغّل الموقع (`webapp.py`). ما فيه أي كود تيليجرام.
- `webapp.py`: واجهة الويب و API الوكيل (`/api/v1/agent/*`) ومسارات العقل الصوتي الموحّد (`/api/v2/voice/*`).
- `ryn_agent.py`: نواة الوكيل (إقران، مهام، أوامر، سياسات أمان).
- `ryn-desktop/`: عميل ويندوز (`python run_desktop.py`، والـ EXE بيتبني بـ GitHub Actions من `.github/workflows/main.yml`).
- `ryn-android/`: عميل أندرويد (Android Studio).
- `visual_brain/`, `image_providers/`, `image_processing/`: توليد وتعديل الصور.

## التشغيل
```bash
pip install -r requirements.txt
export GEMINI_API_KEY=... WEB_PASSWORD=...   # الباقي اختياري (OPENROUTER_API_KEY, GROQ_API_KEY, WEB_USERS, WEB_SECRET ...)
python bot.py            # أو: gunicorn bot:app --workers 1 --threads 8
```

على Render: أمر التشغيل `python bot.py`. تقدر تمسح متغيرات `BOT_TOKEN` و `ENABLE_TELEGRAM` من Environment (ما عاد إلها استخدام).
ملاحظة: مجلد الـ workflow لازم اسمو `.github` (بنقطة) حتى GitHub يشغّله.

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
