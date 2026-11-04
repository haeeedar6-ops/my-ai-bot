# my-ai-bot ( AI + Ryn)

بوت تيليجرام وتطبيق ويب (Ryn) مع وكيل تحكم بالأجهزة (ويندوز + أندرويد).

## الملفات
- `bot.py`: بوت تيليجرام (Gemini + OpenRouter + Groq) — يقرأ `knowledge/` قبل كل جواب.
- `webapp.py`: واجهة الويب و API الوكيل (`/api/v1/agent/*`).
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
