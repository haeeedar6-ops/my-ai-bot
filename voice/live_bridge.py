# -*- coding: utf-8 -*-
"""
voice/live_bridge.py — مكالمة صوتية حية (Gemini Live) بقناة وحدة بالاتجاهين.
=====================================================================
المتصفح ──PCM16 16kHz──▶ السيرفر ──▶ Gemini Live ──PCM 24kHz──▶ المتصفح

- المفتاح بيضل على السيرفر (ما بينبعت للمتصفح).
- المقاطعة وكشف نهاية الكلام عند Gemini نفسه (VAD سيرفر) + المتصفح بيوقف التشغيل فوراً.
- اختيار الموديل: متغيّر GEMINI_LIVE_MODEL (بيقبل قائمة مفصولة بفواصل) ثم الاكتشاف التلقائي ثم احتياطي.
  لأن أسماء موديلات Live بتتغير؛ فالمحاولة بتتم على أكتر من موديل/إعداد لحد ما واحد يتصل.
- LiveBridge مفصول عن Flask وعن مكتبة Google (واجهات async بسيطة) عشان ينختبر بدون شبكة.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z\-_]{20,}|sk-[A-Za-z0-9\-_]{16,}|Bearer\s+[A-Za-z0-9\-._~+/]+=*)"
)


def redact_secrets(text: Any) -> str:
    return _SECRET_RE.sub("[REDACTED]", str(text or ""))


IDLE = object()  # recv() ترجّعه إذا ما وصل شي خلال المهلة (مو إغلاق)

LIVE_VOICES = (
    "Charon", "Orus", "Puck", "Fenrir", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Algenib",
    "Rasalgethi", "Alnilam", "Schedar", "Achird", "Zubenelgenubi", "Sadachbia", "Sadaltager",
    "Kore", "Aoede", "Zephyr", "Leda", "Callirrhoe", "Autonoe", "Despina", "Erinome", "Laomedeia",
    "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat",
)
DEFAULT_LIVE_VOICE = "Charon"
FALLBACK_MODELS = ("gemini-3.1-flash-live-preview", "gemini-3.8-live", "gemini-live-2.5-flash-preview")
MAX_FRAME_BYTES = 64 * 1024
MAX_MEMORY_CHARS = 1500
MAX_HISTORY_TURNS = 8

_DIALECT_NAMES = {
    "syrian": "السورية (الشامية)", "lebanese": "اللبنانية", "jordanian": "الأردنية", "palestinian": "الفلسطينية",
    "egyptian": "المصرية", "gulf": "الخليجية", "iraqi": "العراقية", "moroccan": "المغربية", "msa": "الفصحى المبسّطة",
}


# ---------------------------------------------------------------- إعدادات
class LiveLimits:
    def __init__(self) -> None:
        def num(k: str, d: float) -> float:
            try:
                return float(os.environ.get(k, d))
            except Exception:
                return float(d)

        self.max_session_sec = num("LIVE_MAX_SESSION_SEC", 840)      # Gemini بيقطع الجلسة الصوتية تقريباً بعد ~15 دقيقة
        self.max_total_sec = num("LIVE_MAX_TOTAL_SEC", 3600)
        self.idle_timeout_sec = num("LIVE_IDLE_TIMEOUT_SEC", 120)
        self.max_reconnects = int(num("LIVE_MAX_RECONNECTS", 5))
        self.max_concurrent = int(num("LIVE_MAX_CONCURRENT", 3))
        self.connect_timeout_sec = num("LIVE_CONNECT_TIMEOUT_SEC", 10)
        self.max_attempts = int(num("LIVE_MAX_CONNECT_ATTEMPTS", 4))


def sanitize_voice(v: Any) -> str:
    v = str(v or "").strip()
    return v if v in LIVE_VOICES else (os.environ.get("GEMINI_LIVE_VOICE", "").strip() if os.environ.get("GEMINI_LIVE_VOICE", "").strip() in LIVE_VOICES else DEFAULT_LIVE_VOICE)


DEVELOPER_NAME = "haydar_xe"


def build_system_instruction(dialect: str = "syrian", memory: str = "", history: Optional[List[Tuple[str, str]]] = None) -> str:
    name = _DIALECT_NAMES.get(str(dialect or "syrian").lower(), _DIALECT_NAMES["syrian"])
    parts = [
        f"أنت «رين» (RYN)، مساعد صوتي ذكي طوّرك {DEVELOPER_NAME}. بتحكي مع صاحبك بمحادثة حية متل مكالمة هاتف.",
        f"احكي باللهجة {name} بشكل طبيعي وودّي، وبجمل قصيرة وواضحة (جملة أو جملتين غالباً) إلا إذا طلب شرح مفصّل.",
        "ابدأ بالجواب مباشرة بدون مقدمات طويلة. إذا الطلب غامض اسأل سؤال واحد قصير.",
        "النطق: انطق كل حرف عربي بمخرجه الصحيح وبوضوح (ق غير ك، ض غير د، ظ غير ذ، ع غير ء، ح غير ه، خ غير غ، ث غير س، ص غير س)، "
        "ولا تبتلع الحروف ولا تستبدلها. أخرج التاء المربوطة والهمزة والتنوين بشكل صحيح. "
        "انطق الكلمات التقنية والأسماء الأجنبية بحروف عربية واضحة وبسرعة معتدلة. إذا طلب منك المستخدم تهجئة كلمة، انطق أسماء الحروف (ألف، باء، تاء...).",
        "انطق الأرقام والتواريخ بالكلمات، ولا تقرأ رموز أو روابط أو علامات ماركداون بصوت عالي.",
        "إذا قاطعك المستخدم اسكت فوراً وسمّع كلامه. لا تذكر تفاصيل تقنية عن نفسك ولا اسم الموديل.",
        "لا تدّعي أنك نفّذت شي ما نفّذته فعلاً. إذا ما بتعرف قول ما بعرف.",
    ]
    mem = str(memory or "").strip()[:MAX_MEMORY_CHARS]
    if mem:
        parts.append("معلومات عن المستخدم (استخدمها بس إذا بتفيد الجواب):\n" + mem)
    if history:
        lines = []
        for role, text in history[-MAX_HISTORY_TURNS:]:
            t = str(text or "").strip().replace("\n", " ")[:300]
            if t:
                lines.append(("المستخدم: " if role == "user" else "رين: ") + t)
        if lines:
            parts.append("ملخص آخر الحوار (كمّل منه بدون ما تعيده):\n" + "\n".join(lines))
    return "\n".join(parts)


def build_config_variants(system_instruction: str, voice: str) -> List[Dict[str, Any]]:
    """من الأغنى للأبسط. إذا الـ SDK/الموديل رفض حقل، بنجرب الأبسط بدل ما تفشل المكالمة."""
    base = {"response_modalities": ["AUDIO"], "system_instruction": system_instruction}
    speech = {"voice_config": {"prebuilt_voice_config": {"voice_name": voice}}}
    full = dict(base, speech_config=speech, input_audio_transcription={}, output_audio_transcription={})
    basic = dict(base, input_audio_transcription={}, output_audio_transcription={})
    return [full, basic, dict(base)]


# ---------------------------------------------------------------- تطبيع رسائل Gemini
def _g(obj: Any, name: str) -> Any:
    return getattr(obj, name, None) if obj is not None else None


def normalize_server_message(msg: Any) -> List[Dict[str, Any]]:
    """رسالة SDK ← أحداث بسيطة: audio/user_text/bot_text/interrupted/turn_complete/go_away/error."""
    out: List[Dict[str, Any]] = []
    sc = _g(msg, "server_content")
    if sc is not None:
        mt = _g(sc, "model_turn")
        for part in (_g(mt, "parts") or []):
            inline = _g(part, "inline_data")
            data = _g(inline, "data")
            if data:
                if isinstance(data, str):
                    import base64
                    try:
                        data = base64.b64decode(data)
                    except Exception:
                        data = b""
                if data:
                    out.append({"kind": "audio", "data": bytes(data)})
        it = _g(_g(sc, "input_transcription"), "text")
        if it:
            out.append({"kind": "user_text", "text": str(it)})
        ot = _g(_g(sc, "output_transcription"), "text")
        if ot:
            out.append({"kind": "bot_text", "text": str(ot)})
        if _g(sc, "interrupted"):
            out.append({"kind": "interrupted"})
        if _g(sc, "turn_complete"):
            out.append({"kind": "turn_complete"})
    if _g(msg, "go_away") is not None:
        out.append({"kind": "go_away"})
    return out


# ---------------------------------------------------------------- مزوّد Gemini الفعلي
class GeminiSessionAdapter:
    """بيغلّف جلسة SDK بواجهة بسيطة: send_audio / send_text / events / aclose."""

    def __init__(self, cm: Any, session: Any, model: str):
        self._cm, self._s, self.model = cm, session, model

    async def send_audio(self, pcm: bytes) -> None:
        from google.genai import types  # type: ignore
        await self._s.send_realtime_input(audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000"))

    async def send_text(self, text: str) -> None:
        await self._s.send_realtime_input(text=text)

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        # receive() بالـ SDK بتخلص بعد كل دور (turn_complete)؛ لازم نعيد استدعاءها لحد ما الجلسة تنسكّر.
        while True:
            got = 0
            async for msg in self._s.receive():
                got += 1
                for ev in normalize_server_message(msg):
                    yield ev
            if not got:
                await asyncio.sleep(0.05)

    async def aclose(self) -> None:
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:
            pass


def _version_key(name: str) -> Tuple[int, float]:
    live = 1 if ("live" in name or "native-audio" in name) else 0
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    return (live, float(m.group(1)) if m else 0.0)


class GeminiLiveFactory:
    _disc: Dict[str, Any] = {"t": 0.0, "models": []}

    def __init__(self, api_key: str, client: Any = None):
        self.api_key = api_key
        self._client = client

    def client(self) -> Any:
        if self._client is None:
            from google import genai  # type: ignore
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def discover(self) -> List[str]:
        """موديلات بتدعم bidiGenerateContent على مفتاحك (مخزّنة 6 ساعات)."""
        now = time.time()
        if self._disc["models"] and now - self._disc["t"] < 6 * 3600:
            return list(self._disc["models"])
        found: List[str] = []
        try:
            for m in self.client().models.list():
                acts = list(_g(m, "supported_actions") or [])
                if "bidiGenerateContent" in acts:
                    found.append(str(_g(m, "name") or "").replace("models/", ""))
        except Exception:
            found = []
        found = [f for f in found if f]
        found.sort(key=_version_key, reverse=True)
        GeminiLiveFactory._disc.update(t=now, models=found)
        return list(found)

    def candidate_models(self) -> List[str]:
        env = [x.strip() for x in os.environ.get("GEMINI_LIVE_MODEL", "").split(",") if x.strip()]
        seen: List[str] = []
        for m in env + self.discover() + list(FALLBACK_MODELS):
            if m and m not in seen:
                seen.append(m)
        return seen

    async def open(self, system_instruction: str, voice: str, limits: LiveLimits) -> GeminiSessionAdapter:
        loop = asyncio.get_running_loop()
        models = await loop.run_in_executor(None, self.candidate_models)
        variants = build_config_variants(system_instruction, voice)
        last_err: Optional[BaseException] = None
        attempts = 0
        for model in models:
            for cfg in variants:
                if attempts >= limits.max_attempts:
                    raise ConnectionError(redact_secrets(f"live_connect_failed: {last_err}"))
                attempts += 1
                cm = self.client().aio.live.connect(model=model, config=cfg)
                try:
                    session = await asyncio.wait_for(cm.__aenter__(), timeout=limits.connect_timeout_sec)
                    return GeminiSessionAdapter(cm, session, model)
                except Exception as e:  # الموديل/الإعداد مرفوض → جرّب التالي
                    last_err = e
                    try:
                        await cm.__aexit__(None, None, None)
                    except Exception:
                        pass
        raise ConnectionError(redact_secrets(f"live_connect_failed: {last_err}"))


# ---------------------------------------------------------------- اتصال مباشر (متصفح ← Google) — خطة Gemini
DIRECT_WS_BASE = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService."


def raw_key_allowed() -> bool:
    """للاستخدام الخاص (3 أشخاص): السماح بإرسال المفتاح الخام للمتصفح إذا فشل التوكن المؤقت. LIVE_ALLOW_RAW_KEY=0 لمنعه."""
    return os.environ.get("LIVE_ALLOW_RAW_KEY", "1").strip().lower() in ("1", "true", "yes", "on")


def create_direct_credentials(api_key: str, client: Any = None) -> Dict[str, Any]:
    """يرجّع بيانات اتصال المتصفح المباشر: توكن مؤقت (مفضّل) أو المفتاح الخام (إذا مسموح). ما بيرمي إلا إذا ما في أي خيار."""
    err = ""
    try:
        if client is None:
            from google import genai  # type: ignore
            client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
        import datetime as _dt
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        tok = client.auth_tokens.create(config={
            "uses": 1,
            "expire_time": now + _dt.timedelta(minutes=30),
            "new_session_expire_time": now + _dt.timedelta(minutes=2),
        })
        name = str(getattr(tok, "name", "") or "")
        if name:
            return {"ephemeral": True, "auth_param": "access_token", "token": name,
                    "endpoint": DIRECT_WS_BASE + "BidiGenerateContentConstrained"}
    except Exception as e:
        err = redact_secrets(f"{type(e).__name__}: {e}")[:160]
    if raw_key_allowed() and api_key:
        return {"ephemeral": False, "auth_param": "key", "token": api_key,
                "endpoint": DIRECT_WS_BASE + "BidiGenerateContent", "ephemeral_error": err}
    raise PermissionError("ما قدرت أجهّز توكن مؤقت، والمفتاح الخام ممنوع (LIVE_ALLOW_RAW_KEY=0). " + err)


def build_direct_session_config(system_instruction: str, voice: str, models: List[str]) -> Dict[str, Any]:
    """كل شي بدو المتصفح ليبني رسالة setup بنفسه (مع أكتر من صياغة للتوافق)."""
    return {"system_instruction": system_instruction, "voice": voice, "models": list(models)[:4]}


# ---------------------------------------------------------------- سجل الجلسات (حماية الذاكرة)
class LiveRegistry:
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max(1, int(max_concurrent))
        self._active: Dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, user: str) -> bool:
        with self._lock:
            if self._active.get(user, 0) >= 1 or sum(self._active.values()) >= self.max_concurrent:
                return False
            self._active[user] = self._active.get(user, 0) + 1
            return True

    def release(self, user: str) -> None:
        with self._lock:
            if self._active.get(user, 0) <= 1:
                self._active.pop(user, None)
            else:
                self._active[user] -= 1

    def count(self) -> int:
        with self._lock:
            return sum(self._active.values())


# ---------------------------------------------------------------- الجسر
Recv = Callable[[], Awaitable[Any]]          # bytes | str | IDLE | None(=مسكّر)
Send = Callable[[Any], Awaitable[None]]      # bytes | str(JSON)
OpenFn = Callable[[str, str], Awaitable[Any]]  # (system_instruction, voice) → session adapter


class LiveBridge:
    def __init__(self, open_session: OpenFn, limits: Optional[LiveLimits] = None, clock: Callable[[], float] = time.monotonic):
        self.open_session = open_session
        self.limits = limits or LiveLimits()
        self.clock = clock

    @staticmethod
    def _j(obj: Dict[str, Any]) -> str:
        return json.dumps(obj, ensure_ascii=False)

    async def run(self, recv: Recv, send: Send, start: Dict[str, Any]) -> Dict[str, Any]:
        lim = self.limits
        voice = sanitize_voice(start.get("voice"))
        dialect = str(start.get("dialect") or "syrian")
        memory = str(start.get("memory") or "")
        history: List[Tuple[str, str]] = []
        for h in (start.get("history") or [])[-MAX_HISTORY_TURNS:]:
            if isinstance(h, dict) and h.get("role") in ("user", "bot", "assistant", "model"):
                history.append(("user" if h["role"] == "user" else "bot", str(h.get("text") or "")))

        t_total = self.clock()
        stats = {"reconnects": 0, "turns": 0, "audio_in": 0, "audio_out": 0, "end": "", "model": ""}
        cur = {"user": "", "bot": ""}
        end_reason = {"v": ""}

        async def connect() -> Any:
            si = build_system_instruction(dialect, memory, history)
            return await self.open_session(si, voice)

        try:
            session = await connect()
        except Exception as e:
            await send(self._j({"type": "error", "code": "connect_failed", "message": redact_secrets(str(e))[:200]}))
            stats["end"] = "connect_failed"
            return stats
        stats["model"] = getattr(session, "model", "")
        await send(self._j({"type": "ready", "model": stats["model"], "voice": voice}))

        state = {"session": session, "session_t0": self.clock(), "last_in": self.clock(), "reconnecting": False, "stop": False}

        async def reader() -> None:
            while not state["stop"]:
                if self.clock() - state["last_in"] > lim.idle_timeout_sec:
                    end_reason["v"] = "idle_timeout"
                    state["stop"] = True
                    return
                if self.clock() - t_total > lim.max_total_sec:
                    end_reason["v"] = "max_total"
                    state["stop"] = True
                    return
                msg = await recv()
                if msg is None:
                    end_reason["v"] = "client_closed"
                    state["stop"] = True
                    return
                if msg is IDLE:
                    continue
                state["last_in"] = self.clock()
                if isinstance(msg, (bytes, bytearray)):
                    if len(msg) > MAX_FRAME_BYTES or state["reconnecting"]:
                        continue
                    stats["audio_in"] += len(msg)
                    try:
                        await state["session"].send_audio(bytes(msg))
                    except Exception:
                        pass  # الكاتب رح يتعامل مع انقطاع الجلسة
                else:
                    try:
                        ctl = json.loads(msg)
                    except Exception:
                        continue
                    t = ctl.get("type")
                    if t == "end":
                        end_reason["v"] = "client_end"
                        state["stop"] = True
                        return
                    if t == "text" and not state["reconnecting"]:
                        txt = str(ctl.get("text") or "").strip()[:2000]
                        if txt:
                            try:
                                await state["session"].send_text(txt)
                            except Exception:
                                pass
                    if t == "ping":
                        await send(self._j({"type": "pong"}))

        async def flush_turn() -> None:
            u, b = cur["user"].strip(), cur["bot"].strip()
            cur["user"], cur["bot"] = "", ""
            if u or b:
                stats["turns"] += 1
                if u:
                    history.append(("user", u))
                if b:
                    history.append(("bot", b))
                del history[:-MAX_HISTORY_TURNS * 2]
                await send(self._j({"type": "turn", "user": u, "bot": b}))

        async def writer() -> None:
            while not state["stop"]:
                sess = state["session"]
                ended_by_server = True
                try:
                    async for ev in sess.events():
                        if state["stop"]:
                            ended_by_server = False
                            break
                        k = ev["kind"]
                        if k == "audio":
                            stats["audio_out"] += len(ev["data"])
                            await send(ev["data"])
                        elif k == "user_text":
                            cur["user"] += ev["text"]
                            await send(self._j({"type": "user_text", "text": ev["text"]}))
                        elif k == "bot_text":
                            cur["bot"] += ev["text"]
                            await send(self._j({"type": "bot_text", "text": ev["text"]}))
                        elif k == "interrupted":
                            await send(self._j({"type": "interrupted"}))
                        elif k == "turn_complete":
                            await flush_turn()
                            await send(self._j({"type": "turn_complete"}))
                        elif k == "go_away":
                            break
                        if self.clock() - state["session_t0"] > lim.max_session_sec:
                            break
                except Exception as e:
                    if state["stop"]:
                        return
                    await send(self._j({"type": "warning", "message": redact_secrets(type(e).__name__)}))
                if state["stop"]:
                    return
                await flush_turn()
                # الجلسة انتهت من جهة السيرفر/الحد الزمني → أعد الاتصال مع ملخص الحوار
                if stats["reconnects"] >= lim.max_reconnects:
                    end_reason["v"] = "max_reconnects"
                    state["stop"] = True
                    return
                stats["reconnects"] += 1
                state["reconnecting"] = True
                await send(self._j({"type": "reconnecting"}))
                try:
                    await sess.aclose()
                except Exception:
                    pass
                try:
                    state["session"] = await connect()
                    state["session_t0"] = self.clock()
                    state["reconnecting"] = False
                    await send(self._j({"type": "ready", "model": getattr(state["session"], "model", ""), "voice": voice, "resumed": True}))
                except Exception as e:
                    await send(self._j({"type": "error", "code": "reconnect_failed", "message": redact_secrets(str(e))[:200]}))
                    end_reason["v"] = "reconnect_failed"
                    state["stop"] = True
                    return

        r_task = asyncio.ensure_future(reader())
        w_task = asyncio.ensure_future(writer())
        try:
            await asyncio.wait({r_task, w_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            state["stop"] = True
            for t in (r_task, w_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(r_task, w_task, return_exceptions=True)
            try:
                await state["session"].aclose()
            except Exception:
                pass
        stats["end"] = end_reason["v"] or "ended"
        try:
            await send(self._j({"type": "closed", "reason": stats["end"]}))
        except Exception:
            pass
        return stats


# ---------------------------------------------------------------- ربط WebSocket (متزامن) — بيشتغل مع flask-sock
def serve_ws(ws: Any, user: str, open_session: OpenFn, registry: LiveRegistry,
             limits: Optional[LiveLimits] = None, start_timeout: float = 10.0) -> Dict[str, Any]:
    """ws: أي كائن فيه receive(timeout) و send(data) (simple-websocket). بيرجّع إحصائيات الجلسة."""
    limits = limits or LiveLimits()
    jd = lambda o: json.dumps(o, ensure_ascii=False)  # noqa: E731
    if not registry.acquire(user):
        try:
            ws.send(jd({"type": "error", "code": "busy", "message": "في مكالمة حية شغالة (أو السيرفر مشغول). سكّرها وجرّب."}))
        except Exception:
            pass
        return {"end": "busy"}
    loop = asyncio.new_event_loop()
    try:
        start: Optional[Dict[str, Any]] = None
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < start_timeout and start is None:
                m = ws.receive(timeout=1.0)
                if isinstance(m, str):
                    try:
                        j = json.loads(m)
                    except Exception:
                        continue
                    if j.get("type") == "start":
                        start = j
        except Exception:
            return {"end": "client_closed_before_start"}
        if start is None:
            try:
                ws.send(jd({"type": "error", "code": "start_timeout", "message": "ما وصلت رسالة البدء"}))
            except Exception:
                pass
            return {"end": "start_timeout"}

        async def recv() -> Any:
            try:
                m = await loop.run_in_executor(None, lambda: ws.receive(timeout=1.0))
            except Exception:
                return None
            return IDLE if m is None else m

        async def send(x: Any) -> None:
            ws.send(x)

        bridge = LiveBridge(open_session, limits)
        return loop.run_until_complete(bridge.run(recv, send, start))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        except Exception:
            pass
        loop.close()
        registry.release(user)
