import os
import re
import time
import logging
import threading
from collections import defaultdict, deque
from contextlib import contextmanager

import telebot
from flask import Flask
from google import genai
from google.genai import types, errors

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


def make_config(deep, system, use_thinking=True):
    kwargs = {
        "system_instruction": system,
        "http_options": types.HttpOptions(
            timeout=DEEP_TIMEOUT_MS if deep else FAST_TIMEOUT_MS
        ),
    }
    if use_thinking:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level="high" if deep else "low"
            )
        except Exception:
            pass  # نسخة المكتبة ما بتدعم thinking_level
    return types.GenerateContentConfig(**kwargs)


def ask_gemini(contents, deep=False, system=None):
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
                    config=make_config(deep, system, use_thinking),
                )
                return resp.text or None  # None إذا انحظر الرد
            except errors.APIError as e:
                last_err = e
                code = getattr(e, "code", None)
                log.error("Gemini [%s] deep=%s thinking=%s code=%s: %s",
                          model, deep, use_thinking, code, e)
                if code == 400 and use_thinking:
                    continue  # جرب نفس الموديل بدون إعداد التفكير
                if code in (404, 400, 429, 500, 503):
                    break  # الموديل التالي
                raise
            except Exception as e:
                last_err = e
                log.exception("Gemini unexpected error [%s]", model)
                break
    if deep:
        log.warning("كل الموديلات العميقة فشلت، برجع للسريعة")
        return ask_gemini(contents, deep=False, system=system)
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
    if code == 429:
        return "⏳ في ضغط كبير حالياً، جرّب بعد شوي."
    if code in (401, 403):
        return "🔑 في مشكلة بمفتاح Gemini، لازم يتحقق منه صاحب البوت."
    return "⚠️ صار خطأ أثناء المعالجة، جرّب مرة تانية."


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
    bot.reply_to(
        message,
        "• اكتب سؤالك عادي\n"
        "• ابعت صورة مع تعليق (اختياري)\n"
        "• ابعت رسالة صوتية\n"
        "• /deep سؤالك  ←  تفكير عميق + مراجعة الجواب\n"
        "• /reset لبدء محادثة جديدة",
    )


@bot.message_handler(commands=["deep"])
def cmd_deep(message):
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        bot.reply_to(message, "اكتب سؤالك بعد الأمر، مثال:\n/deep كيف أعمل نظام دم لا نهائي؟")
        return
    process(message, [types.Part(text=text)], text, deep=True)


@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    with lock:
        histories.pop(message.chat.id, None)
    bot.reply_to(message, "🧹 تم مسح الذاكرة.")


# ============ الرسائل ============
@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = (message.text or "").strip()
    if not text:
        return
    process(message, [types.Part(text=text)], text, deep=is_deep_question(text))


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    try:
        info = bot.get_file(message.photo[-1].file_id)
        data = bot.download_file(info.file_path)
    except Exception:
        log.exception("Photo download error")
        bot.reply_to(message, "ما قدرت أنزّل الصورة، جرّب تاني.")
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


@bot.message_handler(content_types=["sticker", "video", "document", "audio"])
def handle_unsupported(message):
    bot.reply_to(message, "حالياً بدعم النص والصور والرسائل الصوتية بس 🙂")


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
