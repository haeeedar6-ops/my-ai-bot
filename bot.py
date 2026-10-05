import io
import os
import re
import html
import json
import time
import base64
import zipfile
import datetime as _dt
import logging
import threading
from collections import defaultdict, deque
from contextlib import contextmanager

import telebot
from flask import Flask
from google import genai
from google.genai import types, errors

try:
    import visual  # رسم السكربتات كصور + مكتبة السكربتات الجاهزة
except Exception as _e:  # الميزة اختيارية، البوت بيشتغل بدونها
    visual = None
    print(f'visual.py غير متاح: {_e}')

# ============ الإعدادات ============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

# موديلات سريعة للأسئلة العادية، وموديلات أعمق للأسئلة المركبة.
# إذا موديل ضغط أو ما اشتغل، بينتقل للتالي تلقائياً.
FAST_MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
]
DEEP_MODELS = [
    os.environ.get("GEMINI_DEEP_MODEL", "gemini-3.8-flash"),
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]
MODELS = FAST_MODELS + DEEP_MODELS  # للعرض بس
FAST_TIMEOUT_MS = 30000
DEEP_TIMEOUT_MS = 75000

SYSTEM_PROMPT = """أنت "Craftland AI"، مساعد ذكي متخصص بـ Free Fire Craftland وأداة Craftland Studio، وبنفس الوقت بتجاوب على أي سؤال تاني بالحياة العامة بشكل ممتاز.

أسلوبك:
- جاوب باللغة اللي كتب فيها المستخدم. إذا كتب عربي، جاوب بلهجة شامية بسيطة وواضحة.
- خلي الأجوبة مختصرة وعملية ومرتبة، وخطوات قصيرة لما يكون السؤال "كيف".
- استخدم أسماء البلوكات بالعربي متل ما بتظهر بالمحرر (مثال: "حدث عالمي"، "منطق"، "عامل"، "وظيفة").

تخصصك بكرافتلاند:
- تصميم الخرائط وبرمجتها بالبلوكات (Craftland Studio)، وبتفهم التبويبات: كيان، إعدادات، منطق، عامل (متغيرات)، وظيفة، وحساب، وقوائم، وفكتور، وسلاسل، وتحويل، وتعداد.
- أحداث عالمية (بدء اللعبة، بدء/نهاية الجولة والمرحلة، القضاء على اللاعب، دخول اللاعب...) وأحداث خاصة بالكيانات (تدمير، تفعيل، إلحاق الضرر، دخول المركبة...).
- المراحل والجولات، الفرق، الاقتصاد والمحفظة، المتاجر، واجهة المستخدم (HUD)، الذكاء الاصطناعي والوحوش، الكاميرا المخصصة، الرسوم المتحركة، الأزياء والمظاهر.
- نصائح النشر وبرنامج شراكة صناع المحتوى.

ملاحظات تقنية معروفة لازم تاخدها بعين الاعتبار:
- البوتات ما بتقدر تتبع مسار مخصص (Custom Path).
- بلوكات الرسوم المتحركة ممكن يتغلب عليها متحكم اللاعب.
- متغيرات منطقة الزناد (Trigger Zone) بتتصفّر لما اللاعب يموت.
- انتبه للفرق بين "تدمير" و"إخفاء" عند إغلاق واجهة المستخدم (HUD) من ناحية التوقيت.
- كتير مستخدمين بيشتغلوا من الموبايل، ومحرر الموبايل بلوكاته أقل من نسخة الكمبيوتر.
- الدليل الرسمي: https://ffcraftland.garena.com/en/tutorial/fe/1-8/

قواعد مهمة:
- إذا طلب المستخدم توليد أو تعديل صورة، وجّهه يكتب /image ووصف الصورة (أو يرد على صورة بـ /image). وإذا بعت ملف PDF أو Word أو نص بتقدر تقراه وتجاوب عن محتواه.
- إذا طلب المستخدم سكربت بصورة، وجّهه يكتب /script ووصف الفكرة (أو يقول "ارسم لي سكربت ... بصورة")، وبيجيه مخطط بلوكات ملوّن. والسكربتات الجاهزة بأمر /scripts.
- لما يجيك سؤال عن كرافتلاند، اعتمد أولاً على "قاعدة المعرفة" اللي بآخر التعليمات (أسماء البلوكات والملاحظات).
- إذا ما كنت متأكد 100% من اسم بلوك أو طريقة عمله، قول هيك بصراحة وما تخترع أسماء أو خصائص مو موجودة. وجّه المستخدم للدليل الرسمي أو اقترح طريقة يجرّب فيها.
- لما تشرح منطق برمجة، اذكر البلوكات المطلوبة بالترتيب (حدث ← شرط ← إجراء).
- الأسئلة العامة (علوم، دراسة، صحة، برمجة، ترجمة، نصائح...) جاوب عليها عادي وبشكل كامل، بدون ما تربطها بكرافتلاند.
"""

# ============ قاعدة المعرفة (ملفات كرافتلاند) ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
_knowledge_cache = {"sig": None, "text": ""}


def _knowledge_files():
    """يدور على ملفات المعرفة بمجلد knowledge، وإذا ما لقاه بياخدها من نفس مجلد bot.py."""
    skip = {"readme.md", "requirements.txt"}
    found = {}
    for folder in (BASE_DIR, KNOWLEDGE_DIR):  # مجلد knowledge بيغلب إذا في اسم مكرر
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.lower().endswith((".md", ".txt")) and f.lower() not in skip:
                found[f] = os.path.join(folder, f)
    return sorted(found.items())


def load_knowledge():
    """يقرأ ملفات المعرفة ويرجعها نص واحد، وبيعيد القراءة إذا تعدّل أي ملف."""
    files = _knowledge_files()
    sig = tuple((name, os.path.getmtime(path)) for name, path in files)
    if sig == _knowledge_cache["sig"]:
        return _knowledge_cache["text"]
    parts = []
    for name, path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                parts.append(f"##### ملف: {name}\n{fh.read().strip()}")
        except Exception as e:
            log.warning("تعذر قراءة %s: %s", name, e)
    text = "\n\n".join(parts)
    _knowledge_cache.update(sig=sig, text=text)
    log.info("Knowledge loaded: %s files, %s chars", len(files), len(text))
    return text


def get_system_prompt():
    knowledge = load_knowledge()
    if not knowledge:
        return SYSTEM_PROMPT
    return (
        SYSTEM_PROMPT
        + "\n\n=== قاعدة المعرفة الخاصة بكرافتلاند (مرجعك الأساسي) ===\n"
        "اقرأ هالمرجع قبل ما تجاوب على أي سؤال عن كرافتلاند. "
        "استخدم أسماء البلوكات بالضبط متل ما هي مكتوبة هون، "
        "وإذا بلوك مو موجود بالقائمة قول هيك بصراحة وما تخترعه.\n\n"
        + knowledge
    )

MAX_HISTORY = 20          # عدد الرسائل المحفوظة لكل مستخدم (سؤال + جواب)
COOLDOWN_SECONDS = 1.5    # حماية من السبام
TG_LIMIT = 4000           # حد تيليجرام 4096

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise SystemExit("❌ BOT_TOKEN أو GEMINI_API_KEY ناقصين بالـ Environment Variables")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot")

# ============ التهيئة ============
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=6, parse_mode=None)
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=25000),  # 25 ثانية كحد أقصى لكل محاولة
)

histories = defaultdict(lambda: deque(maxlen=MAX_HISTORY))  # chat_id -> (role, text)
last_request = {}
lock = threading.Lock()

try:
    bot.remove_webhook()
except Exception as e:
    log.warning("Webhook cleanup: %s", e)


# ============ Flask ============
@app.route("/")
def home():
    return "Bot is running!"


@app.route("/health")
def health():
    return {"status": "ok", "models": MODELS}


# ============ أدوات مساعدة ============
def build_contents(chat_id):
    with lock:
        items = list(histories[chat_id])
    return [
        types.Content(role=role, parts=[types.Part(text=text)])
        for role, text in items
    ]


def save_turn(chat_id, user_text, bot_text):
    with lock:
        histories[chat_id].append(("user", user_text))
        histories[chat_id].append(("model", bot_text))


def is_spam(chat_id):
    now = time.time()
    with lock:
        prev = last_request.get(chat_id, 0)
        last_request[chat_id] = now
    return now - prev < COOLDOWN_SECONDS


def clean(text):
    # تيليجرام بدون parse_mode ما بيعرض ** فنشيلها
    return text.replace("**", "").replace("__", "").strip()


def send_long(message, text):
    text = clean(text)
    chunks = [text[i:i + TG_LIMIT] for i in range(0, len(text), TG_LIMIT)] or [""]
    for i, chunk in enumerate(chunks):
        if i == 0:
            bot.reply_to(message, chunk)
        else:
            bot.send_message(message.chat.id, chunk)


@contextmanager
def typing(chat_id):
    """يبعت 'يكتب...' طول ما Gemini عم يفكر."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            stop.wait(4)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


DEEP_WORDS = {
    "كيف", "كيفية", "ليش", "لماذا", "شلون", "ازاي", "ليه",
    "مشكلة", "مشاكل", "خطأ", "غلط", "بلوك", "بلوكات", "سكربت", "برمجة", "برمج",
    "منطق", "صمم", "تصميم", "اعمل", "نظام", "قارن", "حل", "حلول", "اشرح", "شرح",
    "طريقة", "خطوات", "متغير", "متغيرات", "حدث", "احداث", "وظيفة", "دالة", "فكرة",
    "اقتصاد", "متجر", "واجهة", "كاميرا", "مسار", "ذكاء", "وحش", "جولة", "مرحلة",
    "حلل", "تحليل", "افضل", "أفضل", "قارنلي", "اقترح", "خطة",
}


def is_deep_question(text):
    """قرار بسيط وسريع (بدون طلب API): هل السؤال محتاج تفكير عميق؟"""
    t = (text or "").strip()
    if len(t) >= 110 or "```" in t or "def " in t:
        return True
    tokens = set(re.findall(r"[\w\u0600-\u06FF]+", t.lower()))
    return bool(tokens & DEEP_WORDS)


def make_config(deep, system, use_thinking=True, json_mode=False):
    kwargs = {
        "system_instruction": system,
        "http_options": types.HttpOptions(
            timeout=DEEP_TIMEOUT_MS if deep else FAST_TIMEOUT_MS
        ),
    }
    if json_mode:
        kwargs["response_mime_type"] = "application/json"
    if use_thinking:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level="high" if deep else "low"
            )
        except Exception:
            pass  # نسخة المكتبة ما بتدعم thinking_level
    return types.GenerateContentConfig(**kwargs)


def ask_gemini(contents, deep=False, system=None, json_mode=False):
    """يجرب الموديلات بالترتيب. إذا موديل ضغط أو ما اشتغل ينتقل للتالي.
    إذا فشلت الموديلات العميقة، بيرجع للموديلات السريعة."""
    models = DEEP_MODELS if deep else FAST_MODELS
    system = system or get_system_prompt()
    last_err = None
    for model in models:
        for use_thinking in (True, False):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=make_config(deep, system, use_thinking, json_mode),
                )
                return resp.text or None  # None إذا انحظر الرد
            except errors.APIError as e:
                last_err = e
                code = getattr(e, "code", None)
                log.error("Gemini [%s] deep=%s thinking=%s code=%s: %s",
                          model, deep, use_thinking, code, e)
                if code in (401, 403):
                    raise  # مشكلة بمفتاح Gemini، ما بينفع نكمل
                if code == 400 and use_thinking:
                    continue  # جرب نفس الموديل بدون إعداد التفكير
                break  # أي خطأ تاني (404، 429، 500، 503، 504...) ← الموديل التالي
            except Exception as e:
                last_err = e
                log.exception("Gemini unexpected error [%s]", model)
                break
    if deep:
        log.warning("كل الموديلات العميقة فشلت، برجع للسريعة")
        return ask_gemini(contents, deep=False, system=system, json_mode=json_mode)
    raise last_err or RuntimeError("كل الموديلات فشلت")


REVIEW_PROMPT = """راجع جوابك قبل ما ينرسل للمستخدم.

سؤال المستخدم:
{question}

جوابك المبدئي:
{answer}

المطلوب:
1. كل اسم بلوك مذكور لازم يكون مكتوب بالضبط بقاعدة المعرفة. إذا في اسم مو موجود، بدّله بأقرب بلوك موجود، أو احذفه وقول إنك مو متأكد منه.
2. صحّح أي خطأ بالمنطق أو أي خطوة ناقصة بالترتيب.
3. إذا الجواب سليم، رجّعه كما هو.
رجّع الجواب النهائي فقط، بدون أي كلام عن المراجعة."""


def review_answer(question, answer):
    """مراجعة ذاتية: يتأكد من أسماء البلوكات ومنطق الجواب."""
    try:
        prompt = REVIEW_PROMPT.format(question=question, answer=answer)
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        reviewed = ask_gemini(contents, deep=False)
        if reviewed and len(reviewed) >= 0.4 * len(answer):
            return reviewed
    except Exception:
        log.exception("Review failed, using original answer")
    return answer


def friendly_error(e):
    code = getattr(e, "code", None)
    tag = f"\n(رمز الخطأ: {code})" if code else f"\n({type(e).__name__})"
    if code == 429:
        msg = "⏳ وصلنا لحد الاستخدام عند جوجل، جرّب بعد شوي."
    elif code in (500, 502, 503, 504):
        msg = "⏳ سيرفرات جوجل مضغوطة حالياً، جرّب مرة تانية بعد شوي."
    elif code in (401, 403):
        msg = "🔑 في مشكلة بمفتاح Gemini، لازم يتحقق منه صاحب البوت."
    else:
        msg = "⚠️ صار خطأ أثناء المعالجة، جرّب مرة تانية."
    return msg + tag


def process(message, user_parts, history_label, deep=False):
    """المعالجة المشتركة: نص / صورة / صوت."""
    chat_id = message.chat.id
    if is_spam(chat_id):
        bot.reply_to(message, "استنى شوي وأرسل تاني 🙂")
        return
    status = None
    try:
        if deep:
            try:
                status = bot.send_message(chat_id, "🧠 سؤال مركّب، عم فكر فيه بعمق...")
            except Exception:
                pass
        with typing(chat_id):
            contents = build_contents(chat_id)
            contents.append(types.Content(role="user", parts=user_parts))
            answer = ask_gemini(contents, deep=deep)
            if deep and answer and len(answer) > 150 and load_knowledge():
                answer = review_answer(history_label, answer)
        if answer:
            send_long(message, answer)
            save_turn(chat_id, history_label, answer)
        else:
            bot.reply_to(message, "ما قدرت أجاوب على هاد، جرّب صياغة تانية.")
    except Exception as e:
        log.exception("Process error")
        bot.reply_to(message, friendly_error(e))
    finally:
        if status:
            try:
                bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass


# ============ سكربتات بصورة ============
SCRIPT_PROMPT = """صمّم سكربت كرافتلاند للطلب التالي، ورجّع JSON فقط (بدون أي كلام ولا علامات ```)، بهالشكل:
{{
  "title": "عنوان قصير",
  "scripts": [
    {{
      "event": "اسم بلوك الحدث",
      "steps": [
        {{"kind": "condition|action|variable|loop|function", "name": "اسم البلوك", "note": "شرح قصير اختياري", "children": []}}
      ]
    }}
  ],
  "tips": ["نصيحة قصيرة"]
}}

القواعد:
- أسماء البلوكات (event و name) لازم تكون مكتوبة بالضبط متل ما هي بقاعدة المعرفة، وممنوع تخترع اسم. إذا احتجت شي ما بتلاقيه بالقائمة، اوصفه بوضوح واكتب بالـ note إنه "غير مؤكد".
- kind: condition للشرط (إذا / إذا-آخر)، loop للحلقات، variable لإنشاء أو تعديل عامل، function لاستدعاء وظيفة، action لباقي البلوكات.
- البلوكات اللي جوا الشرط أو الحلقة بتنحط بـ children.
- رتّب منطقياً (حدث ثم شروط ثم إجراءات)، بحد أقصى 3 سكربتات و16 بلوك لكل سكربت، وراعي القيود التقنية المكتوبة بالملاحظات.
- note وtips بالعربي وقصيرة.

الطلب: {request}"""

SCRIPT_WORDS = ("سكربت", "سكريبت", "نظام", "بلوكات")
IMG_WORDS = ("صورة", "صوره", "ارسم", "رسم", "مخطط")


def wants_script_image(text):
    return any(w in text for w in SCRIPT_WORDS) and any(w in text for w in IMG_WORDS)


def send_script(message, request_text):
    chat_id = message.chat.id
    if visual is None:
        bot.reply_to(message, "ميزة رسم السكربتات مو مفعّلة (ملف visual.py ناقص).")
        return
    if is_spam(chat_id):
        bot.reply_to(message, "استنى شوي وأرسل تاني 🙂")
        return
    status = None
    try:
        try:
            status = bot.send_message(chat_id, "🛠️ عم صمّم السكربت وأرسمه...")
        except Exception:
            pass
        with typing(chat_id):
            prompt = SCRIPT_PROMPT.format(request=request_text)
            contents = build_contents(chat_id)
            contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
            raw = ask_gemini(contents, deep=True, json_mode=True)
        spec = visual.parse_spec(raw or "")
        if not spec:
            bot.reply_to(message, "ما قدرت أطلّع سكربت مرتب، جرّب توصف الفكرة بشكل أوضح.")
            return
        png = visual.spec_to_image(spec, load_knowledge())
        bio = io.BytesIO(png)
        bio.name = "script.png"
        title = str(spec.get("title") or "السكربت")
        tips = [str(t) for t in (spec.get("tips") or [])][:4]
        caption = "🧩 " + title
        if tips:
            caption += "\n\n" + "\n".join("• " + t for t in tips)
        bot.send_photo(chat_id, bio, caption=caption[:1000], reply_to_message_id=message.message_id)
        save_turn(chat_id, request_text,
                  "[أرسلت سكربت كصورة] " + json.dumps(spec, ensure_ascii=False)[:1500])
    except Exception as e:
        log.exception("Script error")
        bot.reply_to(message, friendly_error(e))
    finally:
        if status:
            try:
                bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass


# ============ ملفات + توليد صور ============
IMAGE_MODELS = [
    os.environ.get("IMAGE_MODEL", "gemini-3.1-flash-lite-image"),
    "gemini-3.1-flash-image",
    "gemini-2.5-flash-image",
]
IMAGE_DAILY_LIMIT = int(os.environ.get("IMAGE_DAILY_LIMIT", "20"))  # حماية من الفاتورة
DOC_TTL = 30 * 60                      # الملف بيضل مرفق بالمحادثة 30 دقيقة
MAX_FILE_BYTES = 15 * 1024 * 1024
IMAGE_TIMEOUT_MS = 90000

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".xml",
            ".log", ".ini", ".yaml", ".yml", ".lua", ".java", ".c", ".cpp", ".cs", ".sql", ".tsv"}

doc_ctx = {}       # chat_id -> {"name", "parts", "exp"}
image_counts = {}  # (chat_id, تاريخ) -> عدد الصور
IMG_VERBS = ("ارسم", "ولد", "ولّد", "صمم", "اعمل", "سوي", "انشئ", "أنشئ", "اصنع")
IMG_NOUNS = ("صورة", "صوره", "لوغو", "شعار", "بوستر", "خلفية")


def _docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t")
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def build_doc_parts(name, data):
    """يحوّل الملف لأجزاء بيفهمها Gemini. بيرمي ValueError إذا النوع مو مدعوم أو الملف فاضي."""
    ext = os.path.splitext(name.lower())[1]
    if ext in MIME_BY_EXT:
        return [types.Part.from_bytes(data=data, mime_type=MIME_BY_EXT[ext])]
    if ext == ".docx":
        try:
            text = _docx_text(data)
        except Exception:
            raise ValueError("bad_docx")
    elif ext in TEXT_EXT:
        text = None
        for enc in ("utf-8-sig", "cp1256"):
            try:
                text = data.decode(enc)
                break
            except Exception:
                continue
    else:
        raise ValueError("unsupported")
    text = (text or "").strip()
    if not text:
        raise ValueError("empty")
    if len(text) > 150_000:
        text = text[:150_000] + "\n...[انقطع الملف لأنه طويل]"
    return [types.Part(text=f"[محتوى الملف: {name}]\n{text}")]


def set_doc(chat_id, name, parts):
    doc_ctx[chat_id] = {"name": name, "parts": parts, "exp": time.time() + DOC_TTL}


def get_doc(chat_id):
    ctx = doc_ctx.get(chat_id)
    if ctx and ctx["exp"] < time.time():
        doc_ctx.pop(chat_id, None)
        return None
    return ctx


def wants_image_gen(text):
    if any(w in text for w in SCRIPT_WORDS):
        return False
    return any(v in text for v in IMG_VERBS) and any(n in text for n in IMG_NOUNS)


def _image_quota(chat_id, take=True):
    key = (chat_id, _dt.date.today().isoformat())
    n = image_counts.get(key, 0)
    if take:
        if n >= IMAGE_DAILY_LIMIT:
            return False
        image_counts[key] = n + 1
        return True
    image_counts[key] = max(0, n - 1)  # استرجاع محاولة فاشلة
    return True


def generate_image(prompt, src=None):
    """يرجّع (بايتات الصورة، نص) أو (None، نص الرفض). src = صورة للتعديل."""
    contents = []
    if src:
        contents.append(types.Part.from_bytes(data=src, mime_type="image/jpeg"))
    contents.append(types.Part(text=prompt))
    last_err = None
    for model in IMAGE_MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    http_options=types.HttpOptions(timeout=IMAGE_TIMEOUT_MS),
                ),
            )
            img, text = None, ""
            for cand in (getattr(resp, "candidates", None) or []):
                content = getattr(cand, "content", None)
                for part in (getattr(content, "parts", None) or []):
                    inline = getattr(part, "inline_data", None)
                    if inline is not None and getattr(inline, "data", None):
                        d = inline.data
                        img = base64.b64decode(d) if isinstance(d, str) else d
                    elif getattr(part, "text", None):
                        text += part.text
            return img, text
        except errors.APIError as e:
            last_err = e
            code = getattr(e, "code", None)
            log.error("Image [%s] code=%s: %s", model, code, e)
            if code in (401, 403):
                raise
            continue
        except Exception as e:
            last_err = e
            log.exception("Image unexpected error [%s]", model)
            continue
    raise last_err or RuntimeError("فشل توليد الصورة")


def image_error_message(e):
    code = getattr(e, "code", None)
    if code in (401, 403):
        return ("🔑 مفتاح Gemini تبعك ما بيسمح بتوليد الصور. غالباً بدها فوترة مفعّلة بحساب جوجل.\n"
                f"(رمز الخطأ: {code})")
    return friendly_error(e)


def send_image(message, prompt, src=None):
    chat_id = message.chat.id
    if is_spam(chat_id):
        bot.reply_to(message, "استنى شوي وأرسل تاني 🙂")
        return
    if not _image_quota(chat_id):
        bot.reply_to(message, f"وصلت الحد اليومي للصور ({IMAGE_DAILY_LIMIT}). بنكمّل بكرا 🙂")
        return
    status, ok = None, False
    try:
        try:
            status = bot.send_message(chat_id, "🎨 عم أرسم الصورة...")
        except Exception:
            pass
        with typing(chat_id):
            img, text = generate_image(prompt, src)
        if img:
            bio = io.BytesIO(img)
            bio.name = "image.png"
            cap = "🎨 " + prompt[:300]
            bot.send_photo(chat_id, bio, caption=cap, reply_to_message_id=message.message_id)
            ok = True
            save_turn(chat_id, f"[طلبت صورة] {prompt}", "[أرسلت الصورة]")
        else:
            bot.reply_to(message, (text or "ما قدرت أولّد الصورة، ممكن الوصف مرفوض. جرّب صياغة تانية.")[:3500])
    except Exception as e:
        log.exception("Image error")
        bot.reply_to(message, image_error_message(e))
    finally:
        if not ok:
            _image_quota(chat_id, take=False)
        if status:
            try:
                bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass


def _download_photo(photo_sizes):
    info = bot.get_file(photo_sizes[-1].file_id)
    return bot.download_file(info.file_path)


# ============ الأوامر ============
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.reply_to(
        message,
        "أهلاً فيك! 👋\nابعتلي أي سؤال أو صورة أو رسالة صوتية وأنا بجاوبك.\n"
        "/reset لمسح الذاكرة\n/help للمساعدة",
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    lines = [
        "• اكتب سؤالك عادي",
        "• ابعت صورة مع تعليق (اختياري)",
        "• ابعت ملف (PDF / Word / نص) مع سؤال، وبضل مرفق 30 دقيقة",
        "• ابعت رسالة صوتية",
        "• /image وصف  ←  توليد صورة (أو رد على صورة لتعديلها)",
        "• /deep سؤالك  ←  تفكير عميق + مراجعة الجواب",
    ]
    if visual is not None:
        lines += ["• /script فكرتك  ←  سكربت بصورة بلوكات", "• /scripts  ←  مكتبة السكربتات الجاهزة"]
    lines += ["• /clearfile لحذف الملف المرفق", "• /reset لبدء محادثة جديدة"]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["deep"])
def cmd_deep(message):
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        bot.reply_to(message, "اكتب سؤالك بعد الأمر، مثال:\n/deep كيف أعمل نظام دم لا نهائي؟")
        return
    process(message, [types.Part(text=text)], text, deep=True)


@bot.message_handler(commands=["script"])
def cmd_script(message):
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        bot.reply_to(message, "اكتب فكرة السكربت بعد الأمر، مثال:\n/script نظام محفظة ومتجر")
        return
    send_script(message, text)


@bot.message_handler(commands=["scripts"])
def cmd_scripts(message):
    files = visual.list_library() if visual else []
    if not files:
        bot.reply_to(message, "مكتبة السكربتات فاضية حالياً. 📚")
        return
    kb = telebot.types.InlineKeyboardMarkup()
    for i, f in enumerate(files[:40]):
        kb.add(telebot.types.InlineKeyboardButton(visual.library_title(f), callback_data=f"lib:{i}"))
    bot.send_message(message.chat.id, "📚 مكتبة السكربتات الجاهزة، اختار واحد:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("lib:"))
def cb_library(call):
    try:
        files = visual.list_library()
        f = files[int(call.data.split(":")[1])]
        with open(os.path.join(visual.LIB_DIR, f), "rb") as fh:
            bot.send_photo(call.message.chat.id, fh, caption=visual.library_caption(f))
        bot.answer_callback_query(call.id)
    except Exception:
        log.exception("Library error")
        try:
            bot.answer_callback_query(call.id, "ما قدرت أفتح هالسكربت")
        except Exception:
            pass


@bot.message_handler(commands=["image"])
def cmd_image(message):
    prompt = (message.text or "").partition(" ")[2].strip()
    src = None
    reply = getattr(message, "reply_to_message", None)
    if reply is not None and getattr(reply, "photo", None):
        try:
            src = _download_photo(reply.photo)  # تعديل على صورة رد عليها
        except Exception:
            log.exception("Reply photo download error")
    if not prompt:
        bot.reply_to(message, "اكتب وصف الصورة بعد الأمر، مثال:\n/image شعار ذهبي لهلال وتاج\n"
                              "ولتعديل صورة: رد عليها بـ /image وشو بدك تغيّر.")
        return
    send_image(message, prompt, src)


@bot.message_handler(commands=["clearfile"])
def cmd_clearfile(message):
    doc_ctx.pop(message.chat.id, None)
    bot.reply_to(message, "🗑️ تم حذف الملف من المحادثة.")


@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    doc_ctx.pop(message.chat.id, None)
    with lock:
        histories.pop(message.chat.id, None)
    bot.reply_to(message, "🧹 تم مسح الذاكرة.")


# ============ الرسائل ============
@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = (message.text or "").strip()
    if not text:
        return
    if visual is not None and wants_script_image(text):
        send_script(message, text)
        return
    if wants_image_gen(text):
        send_image(message, text)
        return
    ctx = get_doc(message.chat.id)
    if ctx:
        parts = ctx["parts"] + [types.Part(text=text)]
        label = f"[بخصوص الملف {ctx['name']}] {text}"
    else:
        parts, label = [types.Part(text=text)], text
    process(message, parts, label, deep=is_deep_question(text))


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    try:
        info = bot.get_file(message.photo[-1].file_id)
        data = bot.download_file(info.file_path)
    except Exception:
        log.exception("Photo download error")
        bot.reply_to(message, "ما قدرت أنزّل الصورة، جرّب تاني.")
        return
    cap_raw = (message.caption or "").strip()
    if cap_raw.lower().startswith(("/image", "/edit")):
        prompt = cap_raw.partition(" ")[2].strip() or "حسّن هالصورة وخلّيها أوضح وأجمل."
        send_image(message, prompt, src=data)
        return
    caption = message.caption or "اشرح هالصورة."
    parts = [
        types.Part.from_bytes(data=data, mime_type="image/jpeg"),
        types.Part(text=caption),
    ]
    process(message, parts, f"[صورة: {caption}]", deep=bool(message.caption) and is_deep_question(caption))


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    try:
        info = bot.get_file(message.voice.file_id)
        data = bot.download_file(info.file_path)
    except Exception:
        log.exception("Voice download error")
        bot.reply_to(message, "ما قدرت أنزّل الرسالة الصوتية، جرّب تاني.")
        return
    parts = [
        types.Part.from_bytes(data=data, mime_type="audio/ogg"),
        types.Part(text="افهم هالرسالة الصوتية وجاوب عليها."),
    ]
    process(message, parts, "[رسالة صوتية]")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    doc = message.document
    name = doc.file_name or "file"
    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        bot.reply_to(message, "الملف كبير، الحد الأقصى 15 ميجا. 📦")
        return
    try:
        info = bot.get_file(doc.file_id)
        data = bot.download_file(info.file_path)
    except Exception:
        log.exception("Document download error")
        bot.reply_to(message, "ما قدرت أنزّل الملف، جرّب تاني.")
        return
    try:
        parts = build_doc_parts(name, data)
    except ValueError as e:
        reason = str(e)
        if reason == "unsupported":
            msg = "نوع الملف مو مدعوم حالياً. المدعوم: PDF، Word (docx)، صور، وملفات نصية (txt, md, csv, json, py...)."
        elif reason == "empty":
            msg = "الملف فاضي أو ما قدرت أقرأ نصه."
        else:
            msg = "ما قدرت أفتح الملف، يمكن تالف."
        bot.reply_to(message, msg)
        return
    chat_id = message.chat.id
    set_doc(chat_id, name, parts)
    question = (message.caption or "").strip() or "لخّصلي هالملف واذكر أهم النقاط."
    process(message, parts + [types.Part(text=question)],
            f"[ملف: {name}] {question}", deep=is_deep_question(question))
    try:
        bot.send_message(chat_id, "📎 الملف مرفق بالمحادثة 30 دقيقة، اسأل عنه براحتك. /clearfile لحذفه.")
    except Exception:
        pass


@bot.message_handler(content_types=["sticker", "video", "audio"])
def handle_unsupported(message):
    bot.reply_to(message, "حالياً بدعم النص والصور والملفات والرسائل الصوتية بس 🙂")


# ============ التشغيل ============
def run_polling():
    while True:
        try:
            log.info("Polling started | models=%s", MODELS)
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            log.error("Polling crashed: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=run_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
