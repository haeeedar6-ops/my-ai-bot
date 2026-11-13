import io
import os
import re
import sys
import html
import time
import zipfile
import logging
import threading
from collections import defaultdict, deque

import requests
try:
    from flask import Flask
except ModuleNotFoundError:
    import _flask_compat as _fc
    sys.modules.setdefault("flask", _fc)
    from _flask_compat import Flask

try:
    from google import genai
    from google.genai import types, errors
except ModuleNotFoundError:
    genai = None
    types = None
    errors = None

try:
    import ryn_ai_stack as AIStack
except Exception:
    AIStack = None

try:
    import webapp  # الموقع (واجهة ويب خاصة)
except Exception as _e:  # اختياري
    webapp = None
    print(f'webapp.py غير متاح: {_e}')

visual = None

# ============ الإعدادات ============
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'").strip()
PORT = int(os.environ.get("PORT", 10000))

# موديلات سريعة للأسئلة العادية، وموديلات أعمق للأسئلة المركبة.
FAST_MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip(),
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-4-argon",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
DEEP_MODELS = [
    os.environ.get("GEMINI_DEEP_MODEL", "gemini-3.8-flash").strip(),
    "gemini-3.8-flash",
    "gemini-4-argon",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]
MODELS = FAST_MODELS + DEEP_MODELS  # للعرض بس
FAST_TIMEOUT_MS = 30000
DEEP_TIMEOUT_MS = 75000

CAPABILITIES_MANIFESTO = """
قدرات وإضافات منظومة Ryn المفعّلة لديك (أنت مبرمج عليها بالكامل في كل الموديلات):
1) 🐙 الوصول المباشر لمشاريع وملفات GitHub من داخل المحادثة (قراءة، تعديل جراحي، إنشاء، حذف، ورفع مشاريع .zip كاملة):
   - عندما يطلب المستخدم تعديل ملف في مستودع GitHub المتصل (سواء كتب GitHub أو جيتهاب أو غيتهاب أو كيت هب أو كيترهاب)، اشرح التعديل بوضوح ثم أدرج في نهاية ردك وسم التعديل الجراحي:
     <github_patch path="مسار_الملف" commit="وصف التعديل">
     <<<< SEARCH
     الأسطر الأصلية المراد استبدالها
     ====
     الأسطر الجديدة بعد التعديل
     >>>> REPLACE
     </github_patch>
   - ولإنشاء ملف جديد أو استبداله بالكامل:
     <github_create path="مسار_الملف" commit="وصف التحديث">
     محتوى الملف الكامل
     </github_create>
   - ولحذف ملف: <github_delete path="مسار_الملف" commit="سبب الحذف"/>
   - إذا أرفق المستخدم ملف .zip أو عدة ملفات وطلب رفعها على GitHub، يقوم النظام برفع المشروع كاملاً بـ Commit ذري واحد، ويمكن التراجع عن آخر تعديل بكتابة «تراجع عن تعديل جيتهاب».
   - أمن الحسابات: حساب WEB_PASSWORD يأخذ GITHUB_TOKEN من السيرفر تلقائياً، وأي مستخدم آخر يربط توكنه الشخصي ومستودعه من الشات (`اربط جيتهاب ghp_XXXX مستودع username/repo`).
2) 🧩 العقل الهجين المحسّن Ryn mix + الحارس الأمني ryn_guard (18/18):
   - يدمج قوة Gemini و GPT-OSS 120B و Qwen3 و DeepSeek و Kimi K2 و Llama 4 و Codestral عبر LangGraph + Tree-sitter AST + Promptfoo + DSPy.
3) 🎨 العقل البصري المستقل RYN Visual Brain:
   - توليد صور واقعية وتعديل الصور المرفقة مباشرة من المحادثة مع حفظ الهوية والملامح.
4) 🕸️ قراءة الويب ويوتيوب والذاكرة المحلية على الجهاز وتوليد الملفات:
   - تحويل المواقع إلى Markdown عبر Crawl4AI، قراءة فيديوهات يوتيوب، الذاكرة المحلية على جهاز المستخدم، وتوليد ملفات التحميل تلقائياً.
"""

SYSTEM_PROMPT = """أنت "Ryn"، مساعد ذكي شامل ومتقدم في البرمجة، الهندسة البرمجية، إدارة مشاريع GitHub، التحليل، والعلوم، مستعد للإجابة عن أي سؤال في شتى المجالات.

هويتك وصانعك (مهم جداً):
- تم تطويرك وبرمجتك بواسطة المطور "haydar_xe".
- أنت مشروع ذكاء اصطناعي مستقل بالكامل.
- إذا سُئلت "مين برمجك؟" أو "من صنعك؟" أو "لمين تتبع؟"، أجب دائماً وبكل فخر: "أنا مساعد ذكي من تطوير وبرمجة haydar_xe".

أسلوبك:
- جاوب بلغة المستخدم. إذا كان السؤال بالعربي، استخدم لهجة شامية بسيطة، ودية، وواضحة.
- قدم إجابات مختصرة، عملية، ومرتبة. قبل كتابة أي كود معقد، تحقق ذهنياً من الشجرة النحوية (AST) والحالات الحدية وأمان الكود.
""" + CAPABILITIES_MANIFESTO + """
قواعد عامة:
- يمكنك قراءة وتحليل الصور والملفات (PDF، Word، نصوص، مشاريع مضغوطة .zip) المرفقة للإجابة عن محتواها أو رفعها وتعديلها على GitHub، وتوليد وتعديل الصور عبر العقل البصري (RYN Visual Brain).
- لا تذكر أبداً أنه تم حفظ أو تعديل أو رفع ملف على GitHub إلا إذا تم تنفيذ عملية كتابة حقيقية والتحقق منها.
- إذا طلب منك المستخدم ملف تحميل (Word أو نص أو HTML أو JSON أو CSV أو ملف كود): اكتب محتوى الملف مباشرةً وبدون أي كلام خارجي — لأن الموقع بيحوّل ردك تلقائياً لملف تحميل حقيقي. الكود حطه ضمن ``` مع اسم اللغة، والنصوص نظّمها بـ Markdown خفيف.
- ما بتقدر تولّد ملفات PDF حالياً؛ إذا طلب منك PDF، جاوب بجملة وحدة إنه غير متاح واقترح Word أو نص أو HTML بدالو.
- إذا لم تكن متأكداً من معلومة، اعترف بذلك ولا تخترع معلومات غير موجودة.
"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _knowledge_files():
    """تم تنظيف المعرفة القديمة بالكامل بناءً على طلب المطور."""
    return []


def load_knowledge():
    return ""


def get_system_prompt():
    return SYSTEM_PROMPT


MAX_HISTORY = 20          # عدد الرسائل المحفوظة لكل مستخدم (سؤال + جواب)
COOLDOWN_SECONDS = 1.5    # حماية من السبام

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot")

# ============ التهيئة ============
app = Flask(__name__)
client = (
    genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=25000),  # 25 ثانية كحد أقصى لكل محاولة
    )
    if (GEMINI_API_KEY and genai is not None and types is not None)
    else None
)

histories = defaultdict(lambda: deque(maxlen=MAX_HISTORY))  # chat_id -> (role, text)
last_request = {}
lock = threading.Lock()


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


DEEP_WORDS = {
    "كيف", "كيفية", "ليش", "لماذا", "شلون", "ازاي", "ليه",
    "مشكلة", "مشاكل", "خطأ", "غلط", "سكربت", "برمجة", "برمج",
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


_gemini_live_cache = {"t": 0.0, "ids": []}


def discover_gemini_models():
    """يكتشف تلقائياً الموديلات المتاحة فعلياً على مفتاح GEMINI_API_KEY الخاص بالمستخدم على Render."""
    if client is None:
        return []
    now = time.time()
    if _gemini_live_cache["ids"] and now - _gemini_live_cache["t"] < 6 * 3600:
        return _gemini_live_cache["ids"]
    found = []
    try:
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            if name.startswith("models/"):
                name = name[7:]
            low = name.lower()
            if low.startswith("gemini-") and not any(x in low for x in ("embedding", "tts", "image", "veo", "lyria", "robotics", "computer-use", "audio", "transcribe", "live")):
                found.append(name)
    except Exception as _ge:
        log.warning("Gemini model discovery warning: %s", _ge)
    if found:
        _gemini_live_cache.update(t=now, ids=found)
    return found


def make_config(deep, system, use_thinking=True, json_mode=False, model=""):
    kwargs = {
        "system_instruction": system,
        "http_options": types.HttpOptions(
            timeout=DEEP_TIMEOUT_MS if deep else FAST_TIMEOUT_MS
        ),
    }
    if json_mode:
        kwargs["response_mime_type"] = "application/json"
    is_gen3_or_4 = not str(model or "").startswith(("gemini-2.", "gemini-1."))
    if use_thinking and is_gen3_or_4:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level="high" if deep else "low"
            )
        except Exception:
            _ = None
    return types.GenerateContentConfig(**kwargs)


def ask_gemini(contents, deep=False, system=None, json_mode=False, preferred_model=None):
    """يستدعي Gemini مباشرة عبر مفتاح GEMINI_API_KEY الموجود على Render مع تبديل ذكي بين موديلات Gemini 3 و 4 و 2.5."""
    if client is None:
        raise RuntimeError("GEMINI_API_KEY غير مضاف في متغيرات البيئة")
    base_models = list(DEEP_MODELS if deep else FAST_MODELS) + list(FAST_MODELS if deep else DEEP_MODELS)
    ordered = ([preferred_model] if preferred_model else []) + base_models
    models = []
    for m in ordered:
        if m and m not in models:
            models.append(m)
    system = system or get_system_prompt()
    last_err = None
    tried = set()
    for model in models:
        tried.add(model)
        for use_thinking in (True, False):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=make_config(deep, system, use_thinking, json_mode, model=model),
                )
                if resp and getattr(resp, "text", None):
                    return resp.text
            except Exception as e:
                last_err = e
                code = getattr(e, "code", None)
                if code in (401, 403):
                    raise
                if code == 400 and use_thinking:
                    continue
                break
    for dyn_model in discover_gemini_models():
        if dyn_model in tried:
            continue
        try:
            resp = client.models.generate_content(
                model=dyn_model,
                contents=contents,
                config=make_config(False, system, False, json_mode, model=dyn_model),
            )
            if resp and getattr(resp, "text", None):
                return resp.text
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("كل موديلات Gemini فشلت")


REVIEW_PROMPT = """راجع جوابك قبل ما ينرسل للمستخدم.
سؤال المستخدم:
{question}
جوابك المبدئي:
{answer}
المطلوب:
1. تأكد من دقة المعلومات وعدم اختراع دوال أو تفاصيل غير موجودة.
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


# ============ OpenRouter (مفتاح واحد لعدة موديلات) ============
DEFAULT_PROVIDER = os.environ.get("DEFAULT_PROVIDER", "gemini").lower()
PROVIDER_NAMES = {
    "gemini": "Gemini Auto",
    "mix": "Ryn mix",
    "chatgpt": "GPT-OSS / ChatGPT",
    "llama": "Llama 4 / 3.3",
    "claude": "Claude",
    "deepseek": "DeepSeek R1/V3",
    "qwen": "Qwen 3 / Coder",
    "kimi": "Kimi K2",
    "gemma": "Gemma 3",
    "mistral": "Mistral / Codestral",
    "nemotron": "Nemotron Ultra",
    "glm": "GLM Z1",
}


def _pname(prov):
    """اسم العرض للمزوّد، وللموديل المختار يدوياً (gemini:<id> أو groq:<id> أو or:<id>)."""
    p = str(prov)
    if p in PROVIDER_NAMES:
        return PROVIDER_NAMES[p]
    if p.startswith("gemini:"):
        return p[7:]
    if p.startswith("groq:"):
        return p[5:]
    if p.startswith("or:"):
        return p[3:].replace(":free", "")
    return p


last_models = {}

OR_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip("/")
OR_ALLOW_PAID = os.environ.get("OPENROUTER_ALLOW_PAID", "0") == "1"


def _or_key():
    """المفتاح بدون مسافات أو علامات تنصيص لو انحطت بالغلط بـ Render."""
    return os.environ.get("OPENROUTER_API_KEY", "").strip().strip('"').strip("'").strip()


def _env_key(name):
    """قيمة متغير بيئة بدون مسافات أو علامات تنصيص."""
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


def _diag(r):
    """معلومات تشخيص عن رد HTTP (الرابط النهائي، التحويلات، نوع المحتوى)."""
    try:
        hist = [h.status_code for h in (getattr(r, "history", None) or [])]
        hd = getattr(r, "headers", None) or {}
        return (f"url={getattr(r, 'url', '?')} | redirects={hist} | "
                f"type={hd.get('content-type')} | server={hd.get('server')}")
    except Exception:
        return "diag?"


OR_BACKEND = {
    "name": "OpenRouter",
    "base": OR_BASE,
    "key_env": "OPENROUTER_API_KEY",
    "timeout_fast": 60,
    "timeout_deep": 150,
}

PROVIDERS = {
    "chatgpt": {"backends": [{**OR_BACKEND, "prov": "chatgpt", "match": ("openai/", "gpt-oss")}]},
    "llama": {"backends": [{**OR_BACKEND, "prov": "llama", "match": ("llama",)}]},
    "claude": {"backends": [{**OR_BACKEND, "prov": "claude", "match": ("anthropic/",)}]},
    "deepseek": {"backends": [{**OR_BACKEND, "prov": "deepseek", "match": ("deepseek",)}]},
    "qwen": {"backends": [{**OR_BACKEND, "prov": "qwen", "match": ("qwen",)}]},
    "kimi": {"backends": [{**OR_BACKEND, "prov": "kimi", "match": ("kimi", "moonshot")}]},
    "gemma": {"backends": [{**OR_BACKEND, "prov": "gemma", "match": ("gemma",)}]},
    "mistral": {"backends": [{**OR_BACKEND, "prov": "mistral", "match": ("mistral", "codestral")}]},
    "nemotron": {"backends": [{**OR_BACKEND, "prov": "nemotron", "match": ("nemotron",)}]},
    "glm": {"backends": [{**OR_BACKEND, "prov": "glm", "match": ("glm", "z-ai")}]},
}
chat_provider = {}

COMPACT_PROMPT = (
    "أنت Ryn، مساعد ذكي شامل ومتقدم من تطوير haydar_xe، متخصص بالبرمجة وهندسة الكود وإدارة مشاريع GitHub والتحليل وبتجاوب على أي سؤال تاني. "
    "جاوب بلغة المستخدم، وإذا كتب عربي جاوب بلهجة شامية بسيطة ومختصرة ومرتبة. "
    "لتعديل ملف في GitHub من المحادثة أدرج بآخر ردك <github_patch path=\"مسار_الملف\" commit=\"الوصف\">\n<<<< SEARCH\n...\n====\n...\n>>>> REPLACE\n</github_patch>، "
    "ولإنشاء ملف كامل <github_create path=\"مسار_الملف\" commit=\"الوصف\">...</github_create>، وللحذف <github_delete path=\"مسار_الملف\" commit=\"السبب\"/>. "
    "إذا مو متأكد من معلومة قول هيك بصراحة وما تخترع."
)

_or_cache = {"t": 0.0, "items": [], "err": ""}


class ProviderError(Exception):
    def __init__(self, code, msg=""):
        self.code = code
        super().__init__(msg)


def _backend_ready(b):
    return bool(os.environ.get(b["key_env"]))


def provider_ready(name):
    return name in ("gemini", "mix") or any(_backend_ready(b) for b in PROVIDERS.get(name, {}).get("backends", []))


def _text_only(contents):
    for c in contents:
        for p in c.parts:
            if getattr(p, "inline_data", None) is not None or getattr(p, "file_data", None) is not None:
                return False
    return True


def _is_free(item):
    iid = str(item.get("id", ""))
    if iid.endswith(":free"):
        return True
    pr = item.get("pricing") or {}
    try:
        return float(pr.get("prompt", 1)) == 0 and float(pr.get("completion", 1)) == 0
    except Exception:
        return False


def openrouter_catalog(force=False):
    """قائمة موديلات OpenRouter: [{id, free, ctx}]، بتتخزن 6 ساعات."""
    now = time.time()
    if not force and ((_or_cache["items"] and now - _or_cache["t"] < 6 * 3600) or (now - _or_cache.get("fail_t", 0) < 60)):
        return _or_cache["items"]
    items = []
    try:
        key = _or_key()
        r = requests.get(OR_BASE + "/models", headers=({"Authorization": "Bearer " + key} if key else {}), timeout=25)
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                _or_cache["err"] = f"HTTP 200 بس الرد مو JSON: {r.text[:80]!r} | {_diag(r)}"
                log.error("OpenRouter catalog: %s", _or_cache["err"])
                return _or_cache["items"]
            for i in (data.get("data") or []):
                if isinstance(i, dict) and i.get("id"):
                    items.append({"id": i["id"], "free": _is_free(i), "ctx": int(i.get("context_length") or 0)})
            _or_cache["err"] = "" if items else "القائمة رجعت فاضية"
        else:
            _or_cache["err"] = f"HTTP {r.status_code}: {r.text[:200]}"
            log.error("OpenRouter catalog %s", _or_cache["err"])
    except Exception as e:
        _or_cache["err"] = f"{type(e).__name__}: {e}"
        log.error("OpenRouter catalog error: %s", e)
    if items:
        _or_cache.update(t=now, items=items, fail_t=0.0)
    else:
        _or_cache["fail_t"] = now
    return items or _or_cache["items"]


_EXCLUDE = ("embed", "guard", "moderation", "whisper", "tts", "image", "audio", "vision", "-vl", "ocr", "rerank",
            "lyria", "clip", "veo", "imagen", "sora", "safeguard")
_HEAVY = ("r1", "pro", "reason", "think", "235b", "480b", "120b", "80b", "maverick", "70b", "max", "v4", "large", "opus")
_LIGHT = ("flash", "mini", "small", "lite", "8b", "7b", "4b", "3b", "nano", "instruct")
_FAMILY_ORDER = ("openrouter/free", "deepseek", "qwen", "gpt-oss", "kimi", "moonshot", "llama", "mistral", "codestral", "gemma", "nemotron", "glm", "microsoft", "minimax", "trinity")


def free_chat_models(limit=40):
    """كل الموديلات المجانية الصالحة للمحادثة، مرتبة (العائلات المعروفة أول)."""
    items = [i for i in openrouter_catalog()
             if i["free"] and not any(x in i["id"].lower() for x in _EXCLUDE)]

    def key(i):
        iid = i["id"].lower()
        fam = next((n for n, f in enumerate(_FAMILY_ORDER) if f in iid), len(_FAMILY_ORDER))
        return (fam, -i["ctx"])
    items.sort(key=key)
    return [i["id"] for i in items][:limit]


def or_models_for(prov, match, deep):
    """أفضل موديلات (مجانية افتراضياً) للمزوّد، مرتبة: الثقيلة للأسئلة المعقدة والخفيفة للعادية."""
    catalog = openrouter_catalog()
    if not catalog:
        return []
    items = [i for i in catalog
             if any(k in i["id"].lower() for k in match) and not any(x in i["id"].lower() for x in _EXCLUDE)]
    if not OR_ALLOW_PAID:
        items = [i for i in items if i["free"]]

    def score(i):
        iid = i["id"].lower()
        heavy = any(k in iid for k in _HEAVY)
        light = any(k in iid for k in _LIGHT)
        pref = (2 if heavy else 0) if deep else (2 if (light and not heavy) else 0)
        return (pref, i["ctx"])
    items.sort(key=score, reverse=True)
    return [i["id"] for i in items][:5]


def _compact_prompt():
    notes = ""
    for name, path in _knowledge_files():
        if name.lower() == "notes.md":
            try:
                with open(path, encoding="utf-8") as fh:
                    notes = fh.read().strip()[:2500]
            except Exception:
                _ = None
    return COMPACT_PROMPT + (("\n\nملاحظات مرجعية:\n" + notes) if notes else "")


def _build_messages(b, contents):
    system = b.get("system") or (_compact_prompt() if b.get("compact") else get_system_prompt())
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


def _call_backend(b, contents, deep, json_mode, models=None, out=None):
    key = _env_key(b["key_env"])
    messages = _build_messages(b, contents)
    if models is None:
        models = or_models_for(b["prov"], b["match"], deep)
    if not models:
        why = _or_cache.get("err") or "ما في موديل مجاني متاح حالياً من هالعائلة"
        raise ProviderError(404, f"ما لقيت موديل لـ {_pname(b['prov'])} على OpenRouter ({why}).")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if b["name"] == "OpenRouter":
        headers["HTTP-Referer"] = os.environ.get("OPENROUTER_REFERER", "https://my-ai-bot-vmf4.onrender.com")
        headers["X-Title"] = "Ryn"
    last = None
    for model in models:
        body = {
            "model": model,
            "messages": _merge_system(messages) if "r1" in model.lower() else messages,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if b.get("max_tokens"):
            body["max_tokens"] = b["max_tokens"]
        try:
            r = requests.post(
                b["base"].rstrip("/") + "/chat/completions",
                headers=headers, json=body,
                timeout=b["timeout_deep"] if deep else b["timeout_fast"],
            )
        except requests.RequestException as e:
            last = ProviderError(None, f"{model}: {type(e).__name__}: {str(e)[:150]}")
            log.error("%s [%s] network error: %s", b["name"], model, e)
            continue
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                last = ProviderError(502, f"{model}: الرد مو JSON: {r.text[:80]!r} | {_diag(r)}")
                log.error("%s [%s] bad response: %s", b["name"], model, r.text[:200])
                continue
            if not data.get("choices"):
                err = data.get("error") or {}
                try:
                    code = int(err.get("code"))
                except Exception:
                    code = 502
                last = ProviderError(code, f"{model}: {str(err.get('message') or data)[:200]}")
                log.error("%s [%s] error body: %s", b["name"], model, str(data)[:300])
                continue
            text = (data["choices"][0].get("message", {}).get("content") or "")
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            if text:
                if out is not None:
                    out["model"] = model
                return text
            last = ProviderError(502, f"{model}: رد فاضي")
            continue
        last = ProviderError(r.status_code, f"{model}: {r.text[:200]}")
        log.error("%s [%s] code=%s: %s", b["name"], model, r.status_code, r.text[:300])
        if r.status_code in (401, 403):
            raise last
    raise last or ProviderError(None, "no models")


# ============ Groq: كود برمجي تلقائي ============
GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip().rstrip("/")
CODE_SYSTEM_PROMPT = (
    "أنت Ryn (من تطوير haydar_xe)، مهندس برمجيات خبير. اكتب كود صحيح ونظيف ومفحوص نحوياً (AST) وأمنياً وجاهز للتشغيل، وحطه داخل ``` مع اسم اللغة. "
    "بعد الكود اشرح بشكل قصير ومرتب بلغة المستخدم (إذا كتب عربي استعمل لهجة شامية بسيطة). "
    "إذا كان الطلب تعديل ملف في مستودع GitHub المتصل، التزم بإدراج <github_patch path=\"مسار_الملف\" commit=\"وصف التعديل\">\n<<<< SEARCH\n...\n====\n...\n>>>> REPLACE\n</github_patch> في آخر ردك، "
    "أو <github_create path=\"مسار_الملف\" commit=\"الوصف\">...</github_create> لإنشاء ملف جديد. "
    "ما تخترع مكتبات أو دوال مو موجودة، وإذا مو متأكد من شي قول هيك بصراحة."
)
GROQ_BACKEND = {
    "name": "Groq", "base": GROQ_BASE, "key_env": "GROQ_API_KEY",
    "timeout_fast": 60, "timeout_deep": 120,
    "system": CODE_SYSTEM_PROMPT, "max_chars": 24000, "max_tokens": 6000,
}
GROQ_CODE_PREF = (
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.3-70b-versatile",
)
GROQ_FREE_DEFAULTS = (
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "deepseek-r1-distill-llama-70b",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
)
OR_FREE_DEFAULTS = (
    "openrouter/free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen3-235b-a22b:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "moonshotai/kimi-k2:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-4-maverick:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
    "thudm/glm-z1-32b:free",
    "microsoft/mai-ds-r1:free",
)
GROQ_CHAT_BACKEND = {**GROQ_BACKEND, "system": None, "compact": True}
_GROQ_SKIP = ("whisper", "tts", "guard", "safeguard", "vision", "embed", "compound", "orpheus", "playai")
_groq_cache = {"t": 0.0, "ids": [], "err": ""}
code_route_off = set()
WEB_CODE_ROUTING = os.environ.get("WEB_CODE_ROUTING", "0").strip().lower() in ("1", "true", "yes", "on")


def _groq_ready():
    return bool(_env_key("GROQ_API_KEY"))


def groq_catalog(force=False):
    now = time.time()
    if not force and ((_groq_cache["ids"] and now - _groq_cache["t"] < 6 * 3600) or (now - _groq_cache.get("fail_t", 0) < 60)):
        return _groq_cache["ids"]
    ids = []
    try:
        r = requests.get(GROQ_BASE + "/models", headers={"Authorization": "Bearer " + _env_key("GROQ_API_KEY")}, timeout=20)
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                _groq_cache["err"] = f"HTTP 200 بس الرد مو JSON: {r.text[:80]!r}"
                return _groq_cache["ids"]
            ids = [i["id"] for i in (data.get("data") or []) if isinstance(i, dict) and i.get("id")]
            _groq_cache["err"] = "" if ids else "القائمة رجعت فاضية"
        else:
            _groq_cache["err"] = f"HTTP {r.status_code}: {r.text[:200]}"
            log.error("Groq catalog %s", _groq_cache["err"])
    except Exception as e:
        _groq_cache["err"] = f"{type(e).__name__}: {e}"
        log.error("Groq catalog error: %s", e)
    if ids:
        _groq_cache.update(t=now, ids=ids, fail_t=0.0)
    else:
        _groq_cache["fail_t"] = now
    return ids or _groq_cache["ids"]


def groq_code_models():
    """أفضل موديلات الكود المتاحة فعلاً عند Groq، بالترتيب (الأسماء الميتة بتنتخطى)."""
    ids = groq_catalog()
    order = []
    env = os.environ.get("GROQ_CODE_MODEL", "").strip()
    if env:
        order.append(env)
    for p in GROQ_CODE_PREF:
        if (not ids or p in ids) and p not in order:
            order.append(p)
    for i in ids:
        low = i.lower()
        if i not in order and any(k in low for k in ("gpt-oss", "qwen", "llama", "kimi")) and not any(x in low for x in _GROQ_SKIP):
            order.append(i)
    return order[:4]


_CODE_STRONG = re.compile(
    r"(```|\bdef \w+\(|\bimport \w+|#include|console\.log|\bSELECT\b.+\bFROM\b|<\s*(div|html|script)\b|"
    r"\b(python|javascript|typescript|java|c\+\+|c#|php|html|css|sql|bash|react|flask|django|node\.?js|kotlin|swift|rust|golang|regex)\b|"
    r"بايثون|جافاسكربت|جافا سكربت|\bجافا\b)",
    re.I | re.S,
)
_CODE_WEAK = ("كود", "كودات", "برمج", "برمجة", "دالة", "دوال", "خوارزمية", "debug", "bug", "كلاس", "function", "algorithm")


def is_code_request(text):
    """فحص ما إذا كان الطلب برمجياً."""
    t = (text or "").lower()
    if _CODE_STRONG.search(text or ""):
        return True
    return any(w in t for w in _CODE_WEAK)


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


def ask_model(chat_id, contents, deep=False, json_mode=False, code=False):
    """طلبات الكود بتروح تلقائياً لـ Groq، وإذا فشل بيجاوب Gemini."""
    pre_note = None
    if (code and not json_mode and _groq_ready() and chat_id not in code_route_off
            and (WEB_CODE_ROUTING or not str(chat_id).startswith("web:"))
            and chat_id not in chat_provider and _text_only(contents)):
        try:
            out = {}
            ans = _call_backend(GROQ_BACKEND, contents, deep, False, models=groq_code_models(), out=out)
            if ans:
                return ans, f"(الكود من Groq: {out.get('model')})"
        except Exception as e:
            c = getattr(e, "code", None)
            log.error("Groq code route failed (%s): %s", c, e)
            pre_note = "(Groq ما اشتغل" + (f"، رمز {c}" if c else "") + "، جاوب Gemini)"
    ans, note = _ask_model_core(chat_id, contents, deep=deep, json_mode=json_mode)
    return ans, (note or pre_note)


def _ask_model_core(chat_id, contents, deep=False, json_mode=False):
    """بيرجّع (الجواب، ملاحظة) مع دعم جميع النماذج المجانية (Gemini / Groq / OpenRouter / Codestral) والتبديل الذكي عند ضغط السيرفر."""
    prov = chat_provider.get(chat_id, DEFAULT_PROVIDER)
    text_only = _text_only(contents)

    if not text_only and not (prov == "gemini" or prov.startswith("gemini:")):
        ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
        return ans, f"(استخدمت Gemini لأن {_pname(prov)} ما بيقدر يقرأ صور/صوت)"

    if prov == "mix":
        return ask_mix(contents, deep=deep)

    if prov == "gemini" or prov.startswith("gemini:"):
        pref_m = prov[7:] if prov.startswith("gemini:") else None
        ans = ask_gemini(contents, deep=deep, json_mode=json_mode, preferred_model=pref_m)
        return ans, (pref_m if pref_m else None)

    if prov == "codestral":
        c_key = _env_key("CODESTRAL_API_KEY")
        if c_key:
            try:
                c_backend = {
                    "name": "Codestral",
                    "base": "https://codestral.mistral.ai/v1",
                    "key_env": "CODESTRAL_API_KEY",
                    "timeout_fast": 60,
                    "timeout_deep": 120,
                    "system": CODE_SYSTEM_PROMPT,
                }
                ans = _call_backend(c_backend, contents, deep, json_mode, models=[os.environ.get("CODESTRAL_MODEL", "codestral-latest")])
                if ans:
                    return ans, "Codestral"
            except Exception as e:
                log.warning("Codestral direct fallback: %s", e)
        if _or_key():
            b_or = {**OR_BACKEND, "prov": "mistral", "match": ("codestral", "mistral")}
            ans = _call_backend(b_or, contents, deep, json_mode)
            if ans:
                return ans, "Codestral (OR)"

    if prov.startswith("groq:"):
        target_m = prov[5:]
        if not _groq_ready():
            if client:
                ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
                return ans, "(احتياط Gemini — مفتاح Groq غير مضاف)"
            raise ProviderError(401, "GROQ_API_KEY مو مضاف على Render")
        avail_g = groq_code_models()
        chain_g = [target_m] + [m for m in avail_g if m != target_m]
        out_g = {}
        try:
            ans = _call_backend(GROQ_CHAT_BACKEND, contents, deep, json_mode, models=chain_g, out=out_g)
            if ans:
                used_m = out_g.get("model") or target_m
                note_g = None if used_m == target_m else f"(تبديل ذكي إلى {used_m})"
                return ans, note_g
        except Exception as e:
            if client:
                log.warning("Groq model %s failed (%s), falling back to Gemini", target_m, e)
                ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
                return ans, f"(استخدمت Gemini لأن {_pname(prov)} مضغوط حالياً)"
            raise ProviderError(getattr(e, "code", None), f"{_pname(prov)} ← {e}")
        raise ProviderError(502, f"{_pname(prov)} ← رد فاضي")

    if prov.startswith("or:"):
        target_or = prov[3:]
        if not _or_key():
            if client:
                ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
                return ans, "(احتياط Gemini — مفتاح OpenRouter غير مضاف)"
            raise ProviderError(401, "OPENROUTER_API_KEY مو مضاف على Render")
        b = {**OR_BACKEND, "prov": "custom", "match": ()}
        free_fallbacks = [m for m in free_chat_models(limit=12) if m != target_or]
        chain_or = [target_or] + free_fallbacks[:4]
        if "openrouter/free" not in chain_or:
            chain_or.append("openrouter/free")
        out_or = {}
        try:
            ans = _call_backend(b, contents, deep, json_mode, models=chain_or, out=out_or)
            if ans:
                used_m = out_or.get("model") or target_or
                note_or = None if used_m == target_or else f"(تبديل ذكي إلى {used_m.replace(':free', '')})"
                return ans, note_or
        except Exception as e:
            if client:
                log.warning("OpenRouter model %s failed (%s), falling back to Gemini", target_or, e)
                ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
                return ans, f"(استخدمت Gemini لأن {_pname(prov)} مضغوط حالياً)"
            raise ProviderError(getattr(e, "code", None), f"{_pname(prov)} ← {e}")
        raise ProviderError(502, f"{_pname(prov)} ← رد فاضي")

    if prov in PROVIDERS and provider_ready(prov):
        try:
            ans = ask_openai_compat(prov, contents, deep=deep, json_mode=json_mode)
            if ans:
                return ans, None
        except Exception as e:
            if client:
                ans = ask_gemini(contents, deep=deep, json_mode=json_mode)
                return ans, f"(استخدمت Gemini لأن {_pname(prov)} مضغوط حالياً)"
            code = getattr(e, "code", None)
            raise ProviderError(code, f"{_pname(prov)} ← {e}")

    raise ValueError(f"الموديل {_pname(prov)} مو متاح.")


# ============ Ryn mix: محرك Ryn mix المطور (مستوحى من gstack) ============
MIX_SYSTEM = (
    "أنت Ryn (من تطوير haydar_xe). وصلك سؤال المستخدم مع إجابات عدة موديلات ذكاء اصطناعي. "
    "ادمجهم بجواب واحد نهائي، صحيح ومرتب ومختصر: خذ الأدق من كل إجابة، "
    "وصحّح أي تناقض أو خطأ، وما تذكر أسماء الموديلات ولا إنك قارنت إجابات. "
    "حافظ بالكامل على أي بلوكات <github_patch> أو <github_create> أو <github_delete> موجودة لتنفيذ تعديلات GitHub. "
    "جاوب بلغة المستخدم، وإذا كتب عربي جاوب بلهجة شامية بسيطة. "
    "إذا في معلومة مو متأكد منها قول هيك بصراحة وما تخترع."
)


def _redact_bot_secrets(text):
    if webapp is not None and hasattr(webapp, "redact_secrets"):
        return webapp.redact_secrets(text)
    return text or ""


def _mix_members(deep, code=False):
    members = [("Gemini", lambda c: ask_gemini(c, deep=False))]
    if _groq_ready():
        ids = groq_catalog()
        gpt = "openai/gpt-oss-120b"
        if not ids or gpt in ids:
            members.append(("GPT-OSS", lambda c: _call_backend(GROQ_CHAT_BACKEND, c, False, False, models=[gpt])))
        qw = sorted([i for i in ids if "qwen" in i.lower() and not any(x in i.lower() for x in _GROQ_SKIP)], reverse=True)
        if qw:
            members.append(("Qwen", lambda c, m=qw[0]: _call_backend(GROQ_CHAT_BACKEND, c, False, False, models=[m])))
    if _or_key():
        members.append(("DeepSeek", lambda c: ask_openai_compat("deepseek", c, deep=False)))
    return members


def ask_mix(contents, deep=False):
    import concurrent.futures as cf
    question = ""
    for c in reversed(contents):
        if c.role == "user":
            question = "\n".join(p.text for p in c.parts if getattr(p, "text", None))
            break
    code_req = is_code_request(question)
    if webapp is not None and hasattr(webapp, "mix_answer"):
        try:
            fix_req = bool(webapp._FIX_RE.search(question)) if hasattr(webapp, "_FIX_RE") else False
            is_eng = webapp.is_mix_engineering_request(question) if hasattr(webapp, "is_mix_engineering_request") else (code_req or fix_req)
            ans, note = webapp.mix_answer(
                sys.modules[__name__], contents, _mix_members(deep, code=is_eng),
                code=is_eng, fix=fix_req
            )
            ans = re.sub(r"\[\[MEM:\s*.+?\s*\]\]", "", ans or "").strip()
            return _redact_bot_secrets(ans), f"({note})"
        except Exception as e:
            log.warning("Ryn mix (webapp engine) fallback: %s", _redact_bot_secrets(str(e)[:160]))
    members = _mix_members(deep, code=code_req)
    answers = {}
    ex = cf.ThreadPoolExecutor(max_workers=max(1, len(members)))
    futs = {ex.submit(fn, contents): name for name, fn in members}
    try:
        for f in cf.as_completed(futs, timeout=60 if code_req else 45):
            name = futs[f]
            try:
                t = _redact_bot_secrets((f.result() or "").strip())
                if t:
                    answers[name] = t[:12000 if code_req else 6000]
            except Exception as e:
                log.warning("Ryn mix: %s فشل: %s", name, _redact_bot_secrets(str(e)[:160]))
    except cf.TimeoutError:
        log.warning("Ryn mix: خلصت المهلة، بكمّل بالأجوبة يلي وصلت")
    ex.shutdown(wait=False)
    if not answers:
        raise ProviderError(502, "Ryn mix ← ولا موديل رد")
    if len(answers) == 1:
        name, only = next(iter(answers.items()))
        return only, f"(Ryn mix: رد {name} بس، الباقي ما اشتغل)"
    block = "\n\n".join(f"--- إجابة {i} ---\n{t}" for i, t in enumerate(answers.values(), 1))
    prompt = f"سؤال المستخدم:\n{_redact_bot_secrets(question[:6000])}\n\n{block}\n\nاكتب الجواب النهائي المدموج فقط."
    final = None
    try:
        final = ask_gemini([types.Content(role="user", parts=[types.Part(text=prompt)])],
                           deep=False, system=MIX_SYSTEM)
    except Exception as e:
        log.warning("Ryn mix: الدمج فشل: %s", _redact_bot_secrets(str(e)[:160]))
    if not final:
        final = max(answers.values(), key=len)
    return _redact_bot_secrets(final), "(Ryn mix: " + " + ".join(answers.keys()) + ")"


def friendly_error(e):
    code = getattr(e, "code", None)
    safe_err = _redact_bot_secrets(str(e)[:350])
    tag = f"\n(الخطأ: {safe_err})" if isinstance(e, ProviderError) else (f"\n(رمز: {code})" if code else f"\n({type(e).__name__})")
    if code == 429:
        msg = "⏳ السيرفرات مضغوطة حالياً أو خلص الحد المجاني للطلب، جرّب بعد شوي."
    elif code in (500, 502, 503, 504):
        msg = "⏳ السيرفرات تبع الذكاء الاصطناعي مو مستقرة، جرّب تاني."
    elif code in (401, 403):
        msg = "🔑 في مشكلة بالمفتاح السري بالـ Environment Variables."
    elif code == 404:
        msg = "⚠️ هالموديل مو متاح هلأ (ممكن شالوا المجاني منو). اختار غيرو من القائمة."
    else:
        msg = "⚠️ صار خطأ أثناء المعالجة، جرّب مرة تانية."
    return msg + tag


# ============ قراءة الملفات ============
DOC_TTL = 30 * 60
MAX_FILE_BYTES = 15 * 1024 * 1024
INLINE_MAX_BYTES = 15 * 1024 * 1024

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
    if ext == ".pdf" and len(data) > INLINE_MAX_BYTES:
        parts = pdf_alt_parts(name, data, max_pages=2000, cap=1_500_000)
        if not parts:
            raise ValueError("pdf_big")
        return parts
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


def pdf_alt_parts(name, data, max_pages=60, cap=150_000):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((pg.extract_text() or "") for pg in list(reader.pages)[:max_pages]).strip()
    except Exception:
        return None
    if len(text) < 200:
        return None
    if len(text) > cap:
        text = text[:cap] + "\n...[انقطع الملف لأنه طويل]"
    return [types.Part(text=f"[محتوى الملف: {name}]\n{text}")]


def set_doc(chat_id, name, parts, alt=None):
    doc_ctx[chat_id] = {"name": name, "parts": parts, "alt": alt, "exp": time.time() + DOC_TTL}


def get_doc(chat_id):
    ctx = doc_ctx.get(chat_id)
    if ctx and ctx["exp"] < time.time():
        doc_ctx.pop(chat_id, None)
        return None
    return ctx


_web_mounted = False


def mount_web():
    global _web_mounted
    if _web_mounted:
        return
    if webapp is not None:
        try:
            webapp.register(app, sys.modules[__name__])
            _web_mounted = True
        except Exception:
            log.exception("تعذر تفعيل الموقع")
    else:
        @app.route("/")
        def home():
            return "Ryn Web Service is running!"


mount_web()

if __name__ == "__main__":
    if not GEMINI_API_KEY:
        raise SystemExit("❌ GEMINI_API_KEY ناقص بالـ Environment Variables")
    app.run(host="0.0.0.0", port=PORT)
