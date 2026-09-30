import os
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

# أول موديل هو الأساسي، والباقي احتياطي إذا الأساسي ما اشتغل
MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
]

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


def load_knowledge():
    """يقرأ كل ملفات .md و .txt من مجلد knowledge ويرجعها نص واحد.
    بيعيد القراءة تلقائياً إذا تعدّل أي ملف."""
    if not os.path.isdir(KNOWLEDGE_DIR):
        return ""
    files = sorted(
        f for f in os.listdir(KNOWLEDGE_DIR) if f.lower().endswith((".md", ".txt"))
    )
    sig = tuple((f, os.path.getmtime(os.path.join(KNOWLEDGE_DIR, f))) for f in files)
    if sig == _knowledge_cache["sig"]:
        return _knowledge_cache["text"]
    parts = []
    for f in files:
        try:
            with open(os.path.join(KNOWLEDGE_DIR, f), encoding="utf-8") as fh:
                parts.append(f"##### ملف: {f}\n{fh.read().strip()}")
        except Exception as e:
            log.warning("تعذر قراءة %s: %s", f, e)
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
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, parse_mode=None)
client = genai.Client(api_key=GEMINI_API_KEY)

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


def ask_gemini(contents, rounds=2):
    """يجرب الموديلات بالترتيب. إذا موديل مضغوط (503/429) ينتقل للتالي فوراً."""
    last_err = None
    for rnd in range(rounds):
        for model in MODELS:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=get_system_prompt(),
                    ),
                )
                if resp.text:
                    return resp.text
                return None  # الرد انحظر
            except errors.APIError as e:
                last_err = e
                code = getattr(e, "code", None)
                log.error("Gemini [%s] code=%s round=%s: %s", model, code, rnd + 1, e)
                if code in (404, 400, 429, 500, 503):
                    continue  # جرب الموديل التالي
                raise
            except Exception as e:
                last_err = e
                log.exception("Gemini unexpected error")
        time.sleep(2)  # استراحة قصيرة قبل الجولة التانية
    raise last_err or RuntimeError("كل الموديلات فشلت")


def friendly_error(e):
    code = getattr(e, "code", None)
    if code == 429:
        return "⏳ في ضغط كبير حالياً، جرّب بعد شوي."
    if code in (401, 403):
        return "🔑 في مشكلة بمفتاح Gemini، لازم يتحقق منه صاحب البوت."
    return "⚠️ صار خطأ أثناء المعالجة، جرّب مرة تانية."


def process(message, user_parts, history_label):
    """المعالجة المشتركة: نص / صورة / صوت."""
    chat_id = message.chat.id
    if is_spam(chat_id):
        bot.reply_to(message, "استنى شوي وأرسل تاني 🙂")
        return
    try:
        with typing(chat_id):
            contents = build_contents(chat_id)
            contents.append(types.Content(role="user", parts=user_parts))
            answer = ask_gemini(contents)
        if answer:
            send_long(message, answer)
            save_turn(chat_id, history_label, answer)
        else:
            bot.reply_to(message, "ما قدرت أجاوب على هاد، جرّب صياغة تانية.")
    except Exception as e:
        log.exception("Process error")
        bot.reply_to(message, friendly_error(e))


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
        "• /reset لبدء محادثة جديدة",
    )


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
    process(message, [types.Part(text=text)], text)


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
    process(message, parts, f"[صورة: {caption}]")


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
