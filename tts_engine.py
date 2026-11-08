"""
tts_engine.py — أصوات عربية حقيقية (Microsoft Neural) لـ Ryn.

المزودات (بالترتيب، حسب TTS_PROVIDER = auto | azure | edge | gtts):
  1) azure: خدمة Azure Speech الرسمية (مفاتيح: AZURE_SPEECH_KEY + AZURE_SPEECH_REGION) — مستقرة ورسمية.
  2) edge : حزمة edge-tts (نفس أصوات Microsoft Edge العصبية، بدون مفتاح) — مجانية لكنها غير رسمية، وممكن
            تنحجب من سيرفرات الاستضافة (خطأ 403).
  3) gtts : صوت Google Translate (عربي فصيح، صوت واحد) — شبكة أمان لما تفشل الباقية؛ أبسط بالجودة لكنه بيشتغل غالباً.
لو فشلوا كلهم نرجّع خطأ واضح والواجهة تنتقل لصوت المتصفح؛ ما في صوت تجريبي (بيب) بالإنتاج.

ملاحظة عن VibeVoice (Microsoft): كود الـ TTS انحذف من المستودع الرسمي، والنموذج الحي (Realtime-0.5B)
بحاجة GPU ولا يدعم العربية بشكل رسمي، فما بينفع على Render المجاني. الأصوات العصبية أدناه هي الحل العملي.
"""
import asyncio
import hashlib
import logging
import os
import re
import threading
from collections import OrderedDict
from typing import Callable, Dict, Optional

log = logging.getLogger("ryn.tts")

# معرّفات الأصوات القديمة (أسماء مختلقة) -> أصوات Microsoft الحقيقية
LEGACY_VOICE_ALIASES: Dict[str, str] = {
    "ar-SY-RynNatural": "ar-SY-LaithNeural",
    "ar-SA-HamedStrong": "ar-SA-HamedNeural",
    "ar-EG-SalmaExpressive": "ar-EG-SalmaNeural",
    "ar-AE-FarisTech": "ar-AE-HamdanNeural",
    "ar-XA-Standard": "ar-SA-ZariyahNeural",
    "en-US-RynClear": "en-US-AriaNeural",
    "vibevoice-realtime-exp": "ar-SY-LaithNeural",
}

DEFAULT_VOICE = "ar-SY-LaithNeural"
MAX_CHARS = 3000
_SSML_RATE_MIN, _SSML_RATE_MAX = -40, 60  # نسبة مئوية


def resolve_voice(voice_id: Optional[str]) -> str:
    v = str(voice_id or "").strip()
    v = LEGACY_VOICE_ALIASES.get(v, v)
    return v if re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural", v) else DEFAULT_VOICE


def clean_for_speech(text: str) -> str:
    """يشيل الكود والماركداون والروابط حتى ما ينقرأ شي بلا معنى."""
    t = str(text or "")
    t = re.sub(r"```[\s\S]*?```", " كود برمجي جاهز بالشات. ", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " رابط ", t)
    t = re.sub(r"[*#_~>|]+", " ", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", " ", t)  # إيموجي
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_CHARS]


def _rate_percent(speed: float) -> int:
    try:
        pct = int(round((float(speed) - 1.0) * 100))
    except Exception:
        pct = 0
    return max(_SSML_RATE_MIN, min(_SSML_RATE_MAX, pct))


class _LRU:
    def __init__(self, max_items: int = 48, max_bytes: int = 24 * 1024 * 1024):
        self._d: "OrderedDict[str, bytes]" = OrderedDict()
        self._bytes = 0
        self._max_items, self._max_bytes = max_items, max_bytes
        self._lock = threading.Lock()

    def get(self, k: str) -> Optional[bytes]:
        with self._lock:
            v = self._d.get(k)
            if v is not None:
                self._d.move_to_end(k)
            return v

    def put(self, k: str, v: bytes) -> None:
        with self._lock:
            if k in self._d:
                self._bytes -= len(self._d.pop(k))
            self._d[k] = v
            self._bytes += len(v)
            while self._d and (len(self._d) > self._max_items or self._bytes > self._max_bytes):
                _, old = self._d.popitem(last=False)
                self._bytes -= len(old)


_CACHE = _LRU()
_PROVIDER_BY_KEY: Dict[str, str] = {}


# ---------------- Azure Speech (رسمي) ----------------
def _azure_synthesize(text: str, voice: str, speed: float) -> bytes:
    import requests  # موجودة بالمتطلبات

    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not key or not region:
        raise RuntimeError("Azure Speech غير مضبوط")
    lang = "-".join(voice.split("-")[:2])
    from xml.sax.saxutils import escape
    ssml = (
        f"<speak version='1.0' xml:lang='{lang}' xmlns='http://www.w3.org/2001/10/synthesis'>"
        f"<voice name='{voice}'><prosody rate='{_rate_percent(speed):+d}%'>{escape(text)}</prosody></voice></speak>"
    )
    r = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "ryn-tts",
        },
        data=ssml.encode("utf-8"),
        timeout=30,
    )
    if r.status_code != 200 or not r.content:
        raise RuntimeError(f"Azure Speech HTTP {r.status_code}")
    return r.content


# ---------------- edge-tts (بدون مفتاح) ----------------
async def _edge_async(text: str, voice: str, speed: float) -> bytes:
    import edge_tts  # type: ignore

    comm = edge_tts.Communicate(text, voice, rate=f"{_rate_percent(speed):+d}%")
    out = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            out.extend(chunk["data"])
    return bytes(out)


def _edge_synthesize(text: str, voice: str, speed: float) -> bytes:
    try:
        import edge_tts  # noqa: F401
    except Exception:
        raise RuntimeError("حزمة edge-tts غير مثبتة (pip install edge-tts)")
    # كل استدعاء بحلقة أحداث خاصة (ما في حلقة شغالة بخيط Flask)
    data = asyncio.run(asyncio.wait_for(_edge_async(text, voice, speed), timeout=40))
    if not data:
        raise RuntimeError("edge-tts رجّع صوتاً فاضياً")
    return data


# ---------------- gTTS (شبكة أمان) ----------------
def _gtts_synthesize(text: str, voice: str, speed: float) -> bytes:
    try:
        from gtts import gTTS  # type: ignore
    except Exception:
        raise RuntimeError("حزمة gTTS غير مثبتة (pip install gTTS)")
    import io

    lang = "ar" if voice.startswith("ar-") else voice.split("-")[0]
    buf = io.BytesIO()
    gTTS(text=text, lang=lang, slow=float(speed) < 0.85).write_to_fp(buf)
    data = buf.getvalue()
    if not data:
        raise RuntimeError("gTTS رجّع صوتاً فاضياً")
    return data


def provider_order() -> list:
    mode = os.environ.get("TTS_PROVIDER", "auto").strip().lower()
    has_azure = bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))
    if mode in ("azure", "edge", "gtts"):
        return [mode]
    return (["azure"] if has_azure else []) + ["edge", "gtts"]


def synthesize(text: str, voice_id: Optional[str] = None, speed: float = 1.0) -> bytes:
    """يرجّع صوت MP3. يرمي RuntimeError لو كل المزودات فشلت."""
    clean = clean_for_speech(text)
    if not clean:
        raise ValueError("لا يوجد نص صالح للقراءة")
    voice = resolve_voice(voice_id)
    key = hashlib.sha256(f"{voice}|{_rate_percent(speed)}|{clean}".encode("utf-8")).hexdigest()
    hit = _CACHE.get(key)
    if hit:
        synthesize.last_provider = _PROVIDER_BY_KEY.get(key, "cache")  # type: ignore[attr-defined]
        return hit
    errors = []
    for name in provider_order():
        try:
            fn = {"azure": _azure_synthesize, "edge": _edge_synthesize, "gtts": _gtts_synthesize}[name]
            data = fn(clean, voice, speed)
            _CACHE.put(key, data)
            _PROVIDER_BY_KEY[key] = name
            synthesize.last_provider = name  # type: ignore[attr-defined]
            return data
        except Exception as e:  # نجرّب المزود التالي
            errors.append(f"{name}: {str(e)[:120]}")
            log.warning("TTS provider %s failed: %s", name, e)
    raise RuntimeError("فشل توليد الصوت — " + " | ".join(errors))


synthesize.last_provider = ""  # type: ignore[attr-defined]


def get_provider_fn() -> Callable[[str, str, float], bytes]:
    """الدالة اللي بتتحقن بـ VoiceService(tts_provider_fn=...)."""
    return synthesize


def sniff_mime(audio: bytes) -> str:
    if audio[:4] == b"RIFF":
        return "audio/wav"
    return "audio/mpeg"
