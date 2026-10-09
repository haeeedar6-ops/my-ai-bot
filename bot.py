import io
import os
import re
import html
import json
import time
import zipfile
import logging
import threading
from collections import defaultdict, deque
from contextlib import contextmanager

import telebot
import requests
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

SYSTEM_PROMPT = """أنت "Astra AI"، مساعد ذكي شامل ومتقدم، مستعد للإجابة عن أي سؤال في شتى المجالات.

هويتك وصانعك (مهم جداً):
- تم تطويرك وبرمجتك بواسطة المطور "haydar_xe".
- أنت مشروع ذكاء اصطناعي مستقل بالكامل، ولست تابعاً لشركة غارينا (Garena) أو أي شركة أخرى.
- إذا سُئلت "مين برمجك؟" أو "من صنعك؟" أو "لمين تتبع؟"، أجب دائماً وبكل فخر: "أنا مساعد ذكي من تطوير وبرمجة haydar_xe".

أسلوبك:
- جاوب بلغة المستخدم. إذا كان السؤال بالعربي، استخدم لهجة شامية بسيطة، ودية، وواضحة.
- قدم إجابات مختصرة، عملية، ومرتبة. إذا كان السؤال يحتاج لخطوات، اجعلها قصيرة ومباشرة.

خبراتك:
- مساعد عام: تجيب باحترافية على أسئلة التقنية، البرمجة، العلوم، الثقافة، الصحة، الترجمة، والمواضيع اليومية.
- خبير كرافتلاند (مهارة إضافية): تمتلك خبرة في Free Fire Craftland وأداة Craftland Studio. استخدم "قاعدة المعرفة" المرفقة للإجابة بدقة فقط عندما يسألك المستخدم عن هذا المجال تحديداً. استخدم أسماء البلوكات بالعربي متل ما بتظهر بالمحرر (مثال: "حدث عالمي"، "منطق"، "عامل"، "وظيفة").

ملاحظات تقنية معروفة لكرافتلاند:
- البوتات ما بتقدر تتبع مسار مخصص (Custom Path).
- بلوكات الرسوم المتحركة ممكن يتغلب عليها متحكم اللاعب.
- متغيرات منطقة الزناد (Trigger Zone) بتتصفّر لما اللاعب يموت.
- انتبه للفرق بين "تدمير" و"إخفاء" عند إغلاق واجهة المستخدم (HUD) من ناحية التوقيت.
- الدليل الرسمي: https://ffcraftland.garena.com/en/tutorial/fe/1-8/

قواعد عامة:
- يمكنك قراءة وتحليل الصور والملفات (PDF، Word، نصوص) المرفقة للإجابة عن محتواها، لكن لا يمكنك توليد صور.
- إذا طلب المستخدم سكربت كرافتلاند بصورة، وجّهه لاستخدام أمر /script. والسكربتات الجاهزة بأمر /scripts.
- إذا لم تكن متأكداً من معلومة، اعترف بذلك ولا تخترع معلومات غير موجودة.
"""

# ============ قاعدة المعرفة (ملفات كرافتلاند) ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
_knowledge_cache = {"sig": None, "text": ""}


def _knowledge_files():
    """يدور على ملفات المعرفة بمجلد knowledge، وإذا ما لقاه بياخدها من نفس مجلد bot.py."""
    skip = {"readme.md", "requirements.txt"}
    found = {}
    for folder in (BASE_DIR, KNOWLEDGE_DIR):
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.lower().endswith((".md", ".txt")) and f.lower() not in skip:
                found[f] = os.path.join(folder, f)
    return sorted(found.items())


def load_knowledge():
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
            pass
    return types.GenerateContentConfig(**kwargs)

def ask_gemini(contents, deep=False, system=None, json_mode=False):
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
                return resp.text or None
            except errors.APIError as e:
                last_err = e
                code = getattr(e, "code", None)
                if code in (401, 403):
                    raise
                if code == 400 and use_thinking:
                    continue
                break
            except Exception as e:
                last_err = e
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

def review_answer(chat_id, question, answer):
    try:
        prompt = REVIEW_PROMPT.format(question=question, answer=answer)
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        reviewed, _ = ask_model(chat_id, contents, deep=False)
        if reviewed and len(reviewed) >= 0.4 * len(answer):
            return reviewed
    except Exception:
        log.exception("Review failed, using original answer")
    return answer

# ============ GitHub Models ============
DEFAULT_PROVIDER = os.environ.get("DEFAULT_PROVIDER", "gemini").lower()
PROVIDER_NAMES = {
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
    "llama": "Llama 3",
    "claude": "Claude",
    "deepseek": "DeepSeek",
    "qwen": "Qwen"
}

GH_BASE = os.environ.get("GITHUB_MODELS_BASE", "https://models.github.ai").strip().rstrip("/")
if GH_BASE.endswith("/inference"):  # لو انحط معها /inference بالغلط
    GH_BASE = GH_BASE[: -len("/inference")]


def _gh_token():
    """التوكن بدون مسافات أو علامات تنصيص لو انحطت بالغلط بـ Render."""
    return os.environ.get("GITHUB_MODELS_TOKEN", "").strip().strip('"').strip("'").strip()


def _diag(r):
    """معلومات تشخيص عن رد HTTP (الرابط النهائي، التحويلات، نوع المحتوى)."""
    try:
        hist = [h.status_code for h in (getattr(r, "history", None) or [])]
        hd = getattr(r, "headers", None) or {}
        return (f"url={getattr(r, 'url', '?')} | redirects={hist} | "
                f"type={hd.get('content-type')} | server={hd.get('server')}")
    except Exception:
        return "diag?"


GH_BACKEND = {
    "name": "GitHub Models",
    "base": GH_BASE.rstrip("/") + "/inference",
    "key_env": "GITHUB_MODELS_TOKEN",
    "compact": True, 
    "max_chars": 9000,
    "timeout_fast": 60, 
    "timeout_deep": 120,
}

PROVIDERS = {
    "chatgpt": {"backends": [{**GH_BACKEND, "match": "gpt"}]},
    "llama": {"backends": [{**GH_BACKEND, "match": "llama"}]},
    "claude": {"backends": [{**GH_BACKEND, "match": "claude"}]},
    "deepseek": {"backends": [{**GH_BACKEND, "match": "deepseek"}]},
    "qwen": {"backends": [{**GH_BACKEND, "match": "qwen"}]},
}
chat_provider = {}

COMPACT_PROMPT = (
    "أنت Craftland AI، مساعد ذكي متخصص بـ Free Fire Craftland وCraftland Studio، وبتجاوب على أي سؤال تاني. "
    "جاوب بلغة المستخدم، وإذا كتب عربي جاوب بلهجة شامية بسيطة ومختصرة ومرتبة. "
    "إذا مو متأكد من اسم بلوك أو طريقة عمله قول هيك وما تخترع."
)

GH_STATIC = {  # احتياطي بس إذا فشل جلب الكتالوج. الصيغة لازم publisher/model
    "deepseek": ["deepseek/DeepSeek-V3-0324", "deepseek/DeepSeek-R1"],
    "gpt": ["openai/gpt-4o-mini", "openai/gpt-4o"],
    "llama": ["meta/Meta-Llama-3.1-8B-Instruct", "meta/Meta-Llama-3.1-70B-Instruct"],
    "qwen": [],
    "claude": [],
}
_gh_cache = {"t": 0.0, "ids": [], "err": ""}

class ProviderError(Exception):
    def __init__(self, code, msg=""):
        self.code = code
        super().__init__(msg)

def _backend_ready(b):
    return bool(os.environ.get(b["key_env"]))

def provider_ready(name):
    return name == "gemini" or any(_backend_ready(b) for b in PROVIDERS.get(name, {}).get("backends", []))

def _text_only(contents):
    for c in contents:
        for p in c.parts:
            if getattr(p, "inline_data", None) is not None or getattr(p, "file_data", None) is not None:
                return False
    return True

def github_catalog_ids(force=False):
    token = _gh_token()
    now = time.time()
    if not force and _gh_cache["ids"] and now - _gh_cache["t"] < 6 * 3600:
        return _gh_cache["ids"]
    ids = []
    if token:
        try:
            r = requests.get(
                GH_BASE.rstrip("/") + "/catalog/models",
                headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=20,
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    _gh_cache["err"] = f"HTTP 200 بس الرد مو JSON: {r.text[:80]!r} | {_diag(r)}"
                    log.error("GitHub catalog: %s", _gh_cache["err"])
                    return _gh_cache["ids"]
                items = data if isinstance(data, list) else (data.get("models") or data.get("data") or [])
                ids = [i.get("id") for i in items if isinstance(i, dict) and i.get("id")]
                _gh_cache["err"] = "" if ids else "الكتالوج رجع فاضي"
            else:
                _gh_cache["err"] = f"HTTP {r.status_code}: {r.text[:200]}"
                log.error("GitHub catalog %s", _gh_cache["err"])
        except Exception as e:
            _gh_cache["err"] = f"{type(e).__name__}: {e}"
            log.error("GitHub catalog error: %s", e)
    else:
        _gh_cache["err"] = "GITHUB_MODELS_TOKEN مو مضاف"
    if ids:
        _gh_cache.update(t=now, ids=ids)
    return ids or _gh_cache["ids"]

def github_models_for(match, deep):
    ids = [i for i in github_catalog_ids() if match in i.lower()] or GH_STATIC.get(match, [])
    # اختيار موديل أخف وأكثر استقراراً لتجنب مشاكل الاستجابة
    light = [i for i in ids if any(k in i.lower() for k in ("mini", "8b", "v3", "flash", "instruct"))]
    heavy = [i for i in ids if i not in light]
    order = (light + heavy) if not deep else (heavy + light)
    return order or GH_STATIC.get(match, [])

def _compact_prompt():
    notes = ""
    for name, path in _knowledge_files():
        if name.lower() == "notes.md":
            try:
                with open(path, encoding="utf-8") as fh:
                    notes = fh.read().strip()[:2500]
            except Exception:
                pass
    return COMPACT_PROMPT + (("\n\nملاحظات مرجعية:\n" + notes) if notes else "")

def _build_messages(b, contents):
    system = _compact_prompt() if b.get("compact") else get_system_prompt()
    msgs = []
    for c in contents:
        text = "\n".join(p.text for p in c.parts if getattr(p, "text", None))
        if text:
            msgs.append({"role": "assistant" if c.role == "model" else "user", "content": text})
    cap = b.get("max_chars")
    if cap:
        def total():
            return len(system) + sum(len(m["content"]) for m in msgs)
        while total() > cap and len(msgs) > 1:
            msgs.pop(0)
        if msgs and total() > cap:
            last = msgs[-1]["content"]
            tail = last[-600:]
            allowed = max(300, cap - len(system) - len(tail) - 80)
            msgs[-1]["content"] = last[:allowed] + "\n...[انقطع الملف لأنو طويل على هالموديل]...\n" + tail
        while msgs and msgs[0]["role"] == "assistant":
            msgs.pop(0)
    return [{"role": "system", "content": system}] + msgs

def _merge_system(messages):
    sys_txt, rest = messages[0]["content"], [dict(m) for m in messages[1:]]
    if rest and rest[0]["role"] == "user":
        rest[0]["content"] = sys_txt + "\n\n" + rest[0]["content"]
    else:
        rest.insert(0, {"role": "user", "content": sys_txt})
    return rest

def _call_backend(b, contents, deep, json_mode):
    key = _gh_token() if b.get("key_env") == "GITHUB_MODELS_TOKEN" else os.environ.get(b["key_env"], "")
    messages = _build_messages(b, contents)
    models = github_models_for(b["match"], deep)
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if b.get("match"):
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    last = None
    for model in models:
        body = {
            "model": model,
            "messages": _merge_system(messages) if "r1" in model.lower() else messages,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(
                b["base"].rstrip("/") + "/chat/completions",
                headers=headers, json=body,
                timeout=b["timeout_deep"] if deep else b["timeout_fast"],
            )
        except requests.RequestException as e:
            last = ProviderError(None, f"{model}: {type(e).__name__}: {str(e)[:150]}")
            log.error("GitHub [%s] network error: %s", model, e)
            continue
        if r.status_code == 200:
            try:
                text = (r.json()["choices"][0]["message"].get("content") or "")
            except Exception:
                last = ProviderError(502, f"{model}: HTTP 200 بس الرد مو JSON: {r.text[:80]!r} | {_diag(r)}")
                log.error("GitHub [%s] bad response: %s | %s", model, r.text[:200], _diag(r))
                continue
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            return text or None
        last = ProviderError(r.status_code, f"{model}: {r.text[:200]}")
        log.error("GitHub [%s] code=%s: %s", model, r.status_code, r.text[:300])
        if r.status_code in (401, 403):
            raise last
    raise last or ProviderError(None, "no models")

def ask_openai_compat(prov, contents, deep=False, json_mode=False):
    last = None
    for b in PROVIDERS[prov]["backends"]:
        if not _backend_ready(b):
            continue
        try:
            return _call_backend(b, contents, deep, json_mode)
        except ProviderError as e:
            last = e
    raise last or ProviderError(None, "no backend ready")

def ask_model(chat_id, contents, deep=False, json_mode=False):
    """بيرجّع (الجواب، ملاحظة). البوت بيجبر الموديل المختار فقط وبدون تبديل تلقائي."""
    prov = chat_provider.get(chat_id, DEFAULT_PROVIDER)
    text_only = _text_only(contents)
    
    if not text_only and prov != "gemini":
        ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
        return ans, f"(استخدمت Gemini لأن {PROVIDER_NAMES[prov]} ما بيقدر يقرأ صور/صوت)"

    if prov == "gemini":
        ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
        return ans, None

    if prov in PROVIDERS and provider_ready(prov):
        try:
            ans = ask_openai_compat(prov, contents, deep=deep, json_mode=json_mode)
            if ans:
                return ans, None
        except Exception as e:
            code = getattr(e, "code", None)
            raise ProviderError(code, f"{PROVIDER_NAMES[prov]} ← {e}")
            
    raise ValueError(f"الموديل {PROVIDER_NAMES.get(prov, prov)} مو متاح.")

def friendly_error(e):
    code = getattr(e, "code", None)
    tag = f"\n(الخطأ: {str(e)[:350]})" if isinstance(e, ProviderError) else (f"\n(رمز: {code})" if code else f"\n({type(e).__name__})")
    if code == 429:
        msg = "⏳ السيرفرات مضغوطة حالياً أو خلص الحد المجاني للطلب، جرّب بعد شوي."
    elif code in (500, 502, 503, 504):
        msg = "⏳ السيرفرات تبع الذكاء الاصطناعي مو مستقرة، جرّب تاني."
    elif code in (401, 403):
        msg = "🔑 في مشكلة بالمفتاح السري بالـ Environment Variables."
    else:
        msg = "⚠️ صار خطأ أثناء المعالجة، جرّب مرة تانية."
    return msg + tag

def process(message, user_parts, history_label, deep=False):
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
            answer, note = ask_model(chat_id, contents, deep=deep)
            if deep and answer and len(answer) > 150 and load_knowledge() and not note:
                answer = review_answer(chat_id, history_label, answer)
        if answer:
            send_long(message, answer + (f"\n\n{note}" if note else ""))
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
            raw, _ = ask_model(chat_id, contents, deep=True, json_mode=True)
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

# ============ قراءة الملفات ============
DOC_TTL = 30 * 60
MAX_FILE_BYTES = 15 * 1024 * 1024

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".xml",
            ".log", ".ini", ".yaml", ".yml", ".lua", ".java", ".c", ".cpp", ".cs", ".sql", ".tsv"}

doc_ctx = {}

def _docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t")
    return html.unescape(re.sub(r"<[^>]+>", "", xml))

def build_doc_parts(name, data):
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

def pdf_alt_parts(name, data):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((pg.extract_text() or "") for pg in list(reader.pages)[:60]).strip()
    except Exception:
        return None
    if len(text) < 200:
        return None
    if len(text) > 150_000:
        text = text[:150_000] + "\n...[انقطع الملف لأنه طويل]"
    return [types.Part(text=f"[محتوى الملف: {name}]\n{text}")]

def set_doc(chat_id, name, parts, alt=None):
    doc_ctx[chat_id] = {"name": name, "parts": parts, "alt": alt, "exp": time.time() + DOC_TTL}

def get_doc(chat_id):
    ctx = doc_ctx.get(chat_id)
    if ctx and ctx["exp"] < time.time():
        doc_ctx.pop(chat_id, None)
        return None
    return ctx

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
        "• /deep سؤالك  ←  تفكير عميق + مراجعة الجواب",
        "• /model  ←  تبديل الموديل (Gemini / ChatGPT / Llama / Claude...)",
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

@bot.message_handler(commands=["clearfile"])
def cmd_clearfile(message):
    doc_ctx.pop(message.chat.id, None)
    bot.reply_to(message, "🗑️ تم حذف الملف من المحادثة.")

@bot.message_handler(commands=["model"])
def cmd_model(message):
    arg = (message.text or "").partition(" ")[2].strip().lower()
    aliases = {
        "gemini": "gemini", "جيميني": "gemini", 
        "deepseek": "deepseek", "ديب سيك": "deepseek", "ديبسيك": "deepseek",
        "qwen": "qwen", "كوين": "qwen",
        "chatgpt": "chatgpt", "gpt": "chatgpt", "شات جي بي تي": "chatgpt",
        "claude": "claude", "كلود": "claude",
        "llama": "llama", "لاما": "llama"
    }
    chat_id = message.chat.id
    
    if arg in aliases:
        name = aliases[arg]
        if not provider_ready(name):
            bot.reply_to(message, f"مفتاح GITHUB_MODELS_TOKEN مو مضاف على Render.")
            return
        chat_provider[chat_id] = name
        bot.reply_to(message, f"✅ صرت أجاوب بـ {PROVIDER_NAMES[name]}.")
        return
        
    cur = chat_provider.get(chat_id, DEFAULT_PROVIDER)
    kb = telebot.types.InlineKeyboardMarkup()
    lines = []
    for name, label in PROVIDER_NAMES.items():
        mark = "✅ " if name == cur else ("" if provider_ready(name) else "⚠️ ")
        kb.add(telebot.types.InlineKeyboardButton(mark + label, callback_data=f"mdl:{name}"))
        lines.append(f"• {label}")
    
    bot.send_message(
        chat_id,
        f"الموديل الحالي: {PROVIDER_NAMES.get(cur, cur)}\n" + "\n".join(lines) +
        "\n\n• تم إيقاف التبديل التلقائي.\n• النماذج بتشتغل حصراً عن طريق GitHub Models باستثناء Gemini.",
        reply_markup=kb)

@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("mdl:"))
def cb_model(call):
    name = call.data.split(":", 1)[1]
    try:
        if name not in PROVIDER_NAMES or not provider_ready(name):
            bot.answer_callback_query(call.id, "المفتاح مو مضاف على Render")
            return
        chat_provider[call.message.chat.id] = name
        bot.answer_callback_query(call.id, f"تم: {PROVIDER_NAMES[name]}")
        bot.send_message(call.message.chat.id, f"✅ صرت أجاوب بـ {PROVIDER_NAMES[name]}.")
    except Exception:
        log.exception("Model switch error")

@bot.message_handler(commands=["ghmodels"])
def cmd_ghmodels(message):
    if not os.environ.get("GITHUB_MODELS_TOKEN"):
        bot.reply_to(message, "GITHUB_MODELS_TOKEN مو مضاف على Render.")
        return
    ids = github_catalog_ids(force=True)
    if not ids:
        bot.reply_to(message, "ما قدرت أجيب قائمة موديلات GitHub.\nالسبب: " + (_gh_cache.get("err") or "مجهول"))
        return
    pick = [i for i in ids if any(k in i.lower() for k in ("deepseek", "qwen", "gpt", "llama", "claude"))] or ids[:30]
    bot.reply_to(message, "موديلات GitHub Models المتاحة:\n" + "\n".join(pick[:40]))


@bot.message_handler(commands=["ghtest"])
def cmd_ghtest(message):
    """تشخيص: /ghtest [model]  ← بيجرب الطلب بطريقتين وبيعرض الرد الخام."""
    token = _gh_token()
    if not token:
        bot.reply_to(message, "GITHUB_MODELS_TOKEN مو مضاف على Render.")
        return
    model = (message.text or "").partition(" ")[2].strip() or GH_STATIC["deepseek"][0]
    url = GH_BASE + "/inference/chat/completions"
    lines = [f"الموديل: {model}", f"الرابط: {url}",
             f"التوكن: يبدأ بـ {token[:11]}… طوله {len(token)}"]
    body = {"model": model, "messages": [{"role": "user", "content": "قول مرحبا بكلمة وحدة"}], "stream": False}
    variants = [
        ("مع X-GitHub-Api-Version", {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}),
        ("بدون X-GitHub-Api-Version", {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                       "Accept": "application/json"}),
    ]
    for label, headers in variants:
        try:
            r = requests.post(url, headers=headers, json=body, timeout=60)
            lines.append(f"\n[{label}]\nHTTP {r.status_code} | {_diag(r)}\nالرد: {r.text[:250]!r}")
        except Exception as e:
            lines.append(f"\n[{label}]\nخطأ شبكة: {type(e).__name__}: {str(e)[:200]}")
    bot.reply_to(message, "\n".join(lines)[:3900])


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
    ctx = get_doc(message.chat.id)
    if ctx:
        base = ctx["alt"] if ctx.get("alt") else ctx["parts"]
        parts = base + [types.Part(text=text)]
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
            msg = "نوع الملف مو مدعوم حالياً. المدعوم: PDF، Word (docx)، صور، وملفات نصية."
        elif reason == "empty":
            msg = "الملف فاضي أو ما قدرت أقرأ نصه."
        else:
            msg = "ما قدرت أفتح الملف، يمكن تالف."
        bot.reply_to(message, msg)
        return
    chat_id = message.chat.id
    alt = pdf_alt_parts(name, data) if name.lower().endswith(".pdf") else None
    set_doc(chat_id, name, parts, alt)
    question = (message.caption or "").strip() or "لخّصلي هالملف واذكر أهم النقاط."
    base = alt if alt else parts
    process(message, base + [types.Part(text=question)],
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
