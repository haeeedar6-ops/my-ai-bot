# -*- coding: utf-8 -*-
"""
voice/__init__.py — RYN Voice Brain: Full Duplex Audio Experience, ASR/TTS Adapters & State Machine
=====================================================================================================
Implements Sections 7, 9, 10, 11, and 12 of RYN Complete Integrated Upgrade Plan + Voice Audit Fixes:
- State machine: `idle -> listening -> transcribing -> thinking -> speaking -> idle`.
  Barge-in (`interrupt` / `stop` / new user speech frame) cancels audio immediately and returns to `listening` or `idle`.
- Explicit playback lifecycle events (`playback_started`, `playback_paused`, `playback_resumed`,
  `playback_ended`, `playback_stopped`) + automatic timeout recovery for abandoned sessions.
- Multilingual ASR (`ASR_LANGUAGE=auto` by default + explicit BCP-47 language selector) with real production
  providers (`whisper` / `groq` / `azure`), mandatory provider check in production, and ASR health metadata.
- Multilingual Neural TTS catalog routing across Arabic dialects (`ar-SY`, `ar-SA`, `ar-EG`, `ar-AE`, `ar-JO`),
  English (`en-US`, `en-GB`), German (`de-DE`), French (`fr-FR`), Spanish (`es-ES`, `es-MX`), Italian (`it-IT`),
  Turkish (`tr-TR`), and Portuguese (`pt-BR`, `pt-PT`), with sentence-aware chunking and strict container validation.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import io
import math
import os
import sqlite3
import struct
import tempfile
import threading
import time
import uuid
import wave
from typing import Any, Callable, Dict, List, Optional, Tuple

import tts_engine

VOICE_STATES = (
    "idle",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "interrupted",
    "reconnecting",
    "error",
)


class _ClosingConnection(sqlite3.Connection):
    """with-block يعمل commit/rollback وبعدها يسكّر الاتصال فعلياً (sqlite3 الافتراضي ما بيسكّر)."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

PLAYBACK_STATES = (
    "stopped",
    "playing",
    "paused",
)

AVAILABLE_VOICES: List[Dict[str, Any]] = [
    dict(v) for v in tts_engine.VOICE_CATALOG
] + [
    {
        "id": "vibevoice-realtime-exp",
        "name": "🧪 VibeVoice Realtime (غير متاح: يحتاج GPU ولا يدعم العربية رسمياً)",
        "lang": "en-US",
        "gender": "n/a",
        "pitch": 1.0,
        "rate": 1.0,
        "provider": "vibevoice-adapter",
        "model_card": "https://github.com/microsoft/VibeVoice",
        "available": False,
        "experimental": True,
    },
]

LEGACY_VOICE_ALIASES = dict(tts_engine.LEGACY_VOICE_ALIASES)
DEFAULT_VOICE_ID = tts_engine.DEFAULT_VOICE

SUPPORTED_ASR_LANGUAGES: List[Dict[str, str]] = [
    {"code": "auto", "name": "🌐 تلقائي متعدد اللغات (Auto-Detect)"},
    {"code": "ar-SY", "name": "🇸🇾 العربية — شامي سوري (ar-SY)"},
    {"code": "ar-LB", "name": "🇱🇧 العربية — شامي ساحلي/لبناني (ar-LB)"},
    {"code": "ar-SA", "name": "🇸🇦 العربية — فصحى / سعودي (ar-SA)"},
    {"code": "ar-EG", "name": "🇪🇬 العربية — مصري (ar-EG)"},
    {"code": "ar-AE", "name": "🇦🇪 العربية — إماراتي (ar-AE)"},
    {"code": "ar-JO", "name": "🇯🇴 العربية — أردني (ar-JO)"},
    {"code": "en-US", "name": "🇺🇸 English (US)"},
    {"code": "en-GB", "name": "🇬🇧 English (UK)"},
    {"code": "de-DE", "name": "🇩🇪 Deutsch (de-DE)"},
    {"code": "fr-FR", "name": "🇫🇷 Français (fr-FR)"},
    {"code": "es-ES", "name": "🇪🇸 Español — España (es-ES)"},
    {"code": "es-MX", "name": "🇲🇽 Español — México (es-MX)"},
    {"code": "it-IT", "name": "🇮🇹 Italiano (it-IT)"},
    {"code": "tr-TR", "name": "🇹🇷 Türkçe (tr-TR)"},
    {"code": "pt-BR", "name": "🇧🇷 Português — Brasil (pt-BR)"},
    {"code": "pt-PT", "name": "🇵🇹 Português — Portugal (pt-PT)"},
]

_CANONICAL_ASR_LANG_MAP: Dict[str, str] = {
    "auto": "auto",
    "ar": "ar-SY",
    "ar-sy": "ar-SY",
    "ar-lb": "ar-LB",
    "ar-sa": "ar-SA",
    "ar-eg": "ar-EG",
    "ar-ae": "ar-AE",
    "ar-jo": "ar-JO",
    "en": "en-US",
    "en-us": "en-US",
    "en-gb": "en-GB",
    "de": "de-DE",
    "de-de": "de-DE",
    "fr": "fr-FR",
    "fr-fr": "fr-FR",
    "es": "es-ES",
    "es-es": "es-ES",
    "es-mx": "es-MX",
    "it": "it-IT",
    "it-it": "it-IT",
    "tr": "tr-TR",
    "tr-tr": "tr-TR",
    "pt": "pt-BR",
    "pt-br": "pt-BR",
    "pt-pt": "pt-PT",
}


def normalize_asr_language(lang: Optional[str], default: str = "auto") -> str:
    raw = str(lang or "").strip()
    if not raw:
        return default
    low = raw.lower().replace("_", "-")
    if low in _CANONICAL_ASR_LANG_MAP:
        return _CANONICAL_ASR_LANG_MAP[low]
    # Preserve valid BCP-47 tag if formatted like xx-YY
    if len(raw) >= 2 and all(part.isalnum() for part in raw.replace("_", "-").split("-")):
        return raw.replace("_", "-")[:20]
    return default


def canonical_voice_id(voice_id: Optional[str], fallback: str = DEFAULT_VOICE_ID) -> str:
    """يحوّل المعرّف (حتى القديم) لصوت حقيقي موجود بالكتالوج؛ وإلا يرجّع fallback الموثق."""
    fb = tts_engine.resolve_voice(fallback, fallback=DEFAULT_VOICE_ID, strict=False)
    return tts_engine.resolve_voice(voice_id, fallback=fb, strict=False)


def _iso_utc(ts: Optional[float] = None) -> str:
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def synthesize_wav_Placeholder_bytes(text: str, speed: float = 1.0, sample_rate: int = 16000) -> bytes:
    """
    Generates a clean, valid PCM WAV audio stream whose duration scales with the text length and speed,
    used ONLY in offline unit tests when external TTS provider credentials are not injected.
    """
    spd = max(0.5, min(2.0, float(speed or 1.0)))
    duration_sec = max(0.25, min(3.0, (len(text or "ryn") * 0.025) / spd))
    n_samples = int(sample_rate * duration_sec)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            env = math.sin(math.pi * i / max(1, n_samples))
            sample = int(3200.0 * env * math.sin(2.0 * math.pi * 440.0 * (i / float(sample_rate))))
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


class VoiceService:
    """
    Full Audio Experience & Voice Session State Machine (`/api/v2/voice/*`).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        conversation_service: Optional[Any] = None,
        memory_service: Optional[Any] = None,
        asr_provider_fn: Optional[Callable[..., Any]] = None,
        tts_provider_fn: Optional[Callable[..., bytes]] = None,
        chat_responder_fn: Optional[Callable[[str, str, str], str]] = None,
        allow_test_synthetic_asr: bool = False,
    ):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        os.makedirs(base_dir, exist_ok=True)
        self.db_path = db_path or os.environ.get("RYN_DB_PATH") or os.path.join(base_dir, "ryn_canonical.db")
        self.conversation_service = conversation_service
        self.memory_service = memory_service
        self.asr_provider_fn = asr_provider_fn
        self.tts_provider_fn = tts_provider_fn
        self.chat_responder_fn = chat_responder_fn
        self.allow_test_synthetic_asr = allow_test_synthetic_asr
        self._lock = threading.RLock()
        self._stream_buffers: Dict[str, List[bytes]] = {}
        self._stream_mimes: Dict[str, str] = {}
        self._init_db()

    @property
    def voice_feature_enabled(self) -> bool:
        return os.environ.get("VOICE_FEATURE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

    @property
    def live_voice_enabled(self) -> bool:
        return self.voice_feature_enabled and (
            os.environ.get("LIVE_VOICE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
        )

    @property
    def session_timeout_sec(self) -> float:
        try:
            return max(10.0, float(os.environ.get("VOICE_SESSION_TIMEOUT_SEC", "120")))
        except Exception:
            return 120.0

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'idle',
                    playback_state TEXT NOT NULL DEFAULT 'stopped',
                    partial_transcript TEXT NOT NULL DEFAULT '',
                    final_transcript TEXT NOT NULL DEFAULT '',
                    detected_language TEXT NOT NULL DEFAULT 'auto',
                    voice_id TEXT NOT NULL DEFAULT 'ar-SY-LaithNeural',
                    speed REAL NOT NULL DEFAULT 1.0,
                    mic_permission_granted INTEGER NOT NULL DEFAULT 1,
                    recording_indicator_visible INTEGER NOT NULL DEFAULT 0,
                    retain_raw_audio INTEGER NOT NULL DEFAULT 0,
                    retention_days INTEGER NOT NULL DEFAULT 7,
                    audio_seconds_used REAL NOT NULL DEFAULT 0.0,
                    interrupted_count INTEGER NOT NULL DEFAULT 0,
                    last_turn_idempotency_key TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_voice_user ON voice_sessions(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS voice_assets (
                    asset_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'tts',
                    transcript TEXT NOT NULL DEFAULT '',
                    storage_ref TEXT NOT NULL DEFAULT '',
                    audio_b64 TEXT NOT NULL DEFAULT '',
                    duration_sec REAL NOT NULL DEFAULT 0.0,
                    retention_expires_at REAL NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES voice_sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_vasset_user ON voice_assets(user_id, deleted);

                CREATE TABLE IF NOT EXISTS user_voice_settings (
                    user_id TEXT PRIMARY KEY,
                    voice_id TEXT NOT NULL DEFAULT 'ar-SY-LaithNeural',
                    speed REAL NOT NULL DEFAULT 1.0,
                    asr_language TEXT NOT NULL DEFAULT 'auto',
                    auto_read INTEGER NOT NULL DEFAULT 0,
                    retain_raw_audio INTEGER NOT NULL DEFAULT 0,
                    retention_days INTEGER NOT NULL DEFAULT 7,
                    updated_at REAL NOT NULL
                );
                """
            )
            # Safe schema migrations for existing SQLite databases
            sess_cols = {r["name"] for r in conn.execute("PRAGMA table_info(voice_sessions)").fetchall()}
            if "detected_language" not in sess_cols:
                conn.execute("ALTER TABLE voice_sessions ADD COLUMN detected_language TEXT NOT NULL DEFAULT 'auto'")
            pref_cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_voice_settings)").fetchall()}
            if "auto_read" not in pref_cols:
                conn.execute("ALTER TABLE user_voice_settings ADD COLUMN auto_read INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _normalize_user(user_id: str) -> str:
        u = str(user_id or "").strip()
        if not u:
            raise PermissionError("user_id مطلوب من الجلسة الموثقة على السيرفر")
        return u

    def _check_feature_enabled(self, require_live: bool = False) -> None:
        if not self.voice_feature_enabled:
            raise RuntimeError("ميزة الصوت معطّلة حالياً عبر VOICE_FEATURE_ENABLED؛ الدردشة النصية والذاكرة تعملان بشكل طبيعي.")
        if require_live and not self.live_voice_enabled:
            raise RuntimeError("المحادثة الصوتية الحية معطّلة حالياً عبر LIVE_VOICE_ENABLED.")

    # ---------------- Production ASR Adapter & Health Verification ----------------
    @staticmethod
    def _resolve_asr_provider_config() -> Dict[str, Any]:
        raw_prov = os.environ.get("ASR_PROVIDER", "groq").strip().lower()
        if raw_prov in ("groq-whisper-v3-turbo", "groq"):
            provider = "groq"
            default_model = "whisper-large-v3-turbo"
        elif raw_prov in ("whisper", "openai"):
            provider = "whisper"
            default_model = "whisper-1"
        elif raw_prov == "azure":
            provider = "azure"
            default_model = "azure-speech-stt-v1"
        else:
            provider = raw_prov
            default_model = "whisper-large-v3-turbo"

        model = os.environ.get("ASR_MODEL", default_model).strip() or default_model
        lang_default = normalize_asr_language(os.environ.get("ASR_LANGUAGE", "auto"), default="auto")
        has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip())
        has_openai = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        has_azure = bool(os.environ.get("AZURE_SPEECH_KEY", "").strip() and os.environ.get("AZURE_SPEECH_REGION", "").strip())

        if provider == "groq":
            configured = has_groq
        elif provider == "whisper":
            configured = has_openai or has_groq
        elif provider == "azure":
            configured = has_azure
        else:
            configured = False

        return {
            "provider": provider,
            "raw_provider": raw_prov,
            "model": model,
            "default_language": lang_default,
            "configured": configured,
            "supported_providers": ["whisper", "groq", "azure"],
        }

    def asr_health_check(self) -> Dict[str, Any]:
        """يتحقق من جاهزية مزود التفريغ الصوتي (ASR) وإعداداته في الإنتاج."""
        cfg = self._resolve_asr_provider_config()
        has_custom_cb = callable(self.asr_provider_fn)
        ready = bool(has_custom_cb or cfg["configured"])
        return {
            "ok": ready,
            "asr_provider": "custom-callback" if has_custom_cb else cfg["provider"],
            "asr_model": cfg["model"],
            "asr_language_default": cfg["default_language"],
            "configured": ready,
            "custom_callback_injected": has_custom_cb,
            "supported_languages": SUPPORTED_ASR_LANGUAGES,
            "error": "" if ready else (
                f"مزود التعرف الصوتي ({cfg['provider']}) غير مضبوط بمفاتيح الإنتاج "
                "(اضبط GROQ_API_KEY أو OPENAI_API_KEY أو AZURE_SPEECH_KEY+AZURE_SPEECH_REGION)."
            ),
        }

    def _run_production_asr(self, audio_bytes: bytes, mime_type: str, language: str = "auto") -> Dict[str, Any]:
        """
        ينفذ التفريغ الصوتي متعدد اللغات عبر المزود الحقيقي المضبوط (Whisper / Groq / Azure).
        يرفض اختلاق أي نص وهمي عند غياب المزود أو فشل الشبكة.
        """
        import requests

        if not audio_bytes or len(audio_bytes) < 16:
            raise ValueError("بيانات التسجيل الصوتي فارغة أو قصيرة جداً للتفريغ")

        cfg = self._resolve_asr_provider_config()
        prov = cfg["provider"]
        model = cfg["model"]
        norm_lang = normalize_asr_language(language, default=cfg["default_language"])
        iso_lang = "" if norm_lang == "auto" else norm_lang.split("-")[0].lower()

        clean_mime = (mime_type or "audio/webm").split(";")[0].strip() or "audio/webm"
        ext = {
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mp4": "m4a",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/x-m4a": "m4a",
        }.get(clean_mime, "webm")

        if prov in ("groq", "whisper"):
            groq_key = os.environ.get("GROQ_API_KEY", "").strip()
            openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if prov == "groq" or (prov == "whisper" and groq_key and not openai_key):
                if not groq_key:
                    raise RuntimeError("مزود ASR (Groq Whisper) غير مضبوط: المفتاح GROQ_API_KEY مفقود")
                url = os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1").rstrip("/") + "/audio/transcriptions"
                api_key = groq_key
                used_prov = "groq"
            else:
                if not openai_key:
                    raise RuntimeError("مزود ASR (OpenAI Whisper) غير مضبوط: المفتاح OPENAI_API_KEY مفقود")
                url = os.environ.get("OPENAI_BASE", "https://api.openai.com/v1").rstrip("/") + "/audio/transcriptions"
                api_key = openai_key
                used_prov = "whisper"

            form_data: Dict[str, str] = {
                "model": model,
                "response_format": "verbose_json",
            }
            if iso_lang:
                form_data["language"] = iso_lang

            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (f"voice.{ext}", audio_bytes, clean_mime)},
                data=form_data,
                timeout=60,
            )
            if r.status_code != 200:
                raise RuntimeError(f"فشل التفريغ الصوتي عبر {used_prov} (HTTP {r.status_code})")
            payload = r.json() if r.content else {}
            text = str(payload.get("text") or "").strip()
            if not text:
                raise RuntimeError("المزود الصوتي أرجع نصاً فارغاً")
            raw_det = str(payload.get("language") or "").strip()
            det_bcp47 = tts_engine.detect_text_language(text, hint_lang=norm_lang if norm_lang != "auto" else raw_det)
            return {
                "text": text,
                "detected_language": det_bcp47,
                "raw_detected_language": raw_det or det_bcp47,
                "requested_language": norm_lang,
                "provider": used_prov,
                "model": model,
                "duration_sec": float(payload.get("duration") or 0.0),
            }

        if prov == "azure":
            az_key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
            az_reg = os.environ.get("AZURE_SPEECH_REGION", "").strip()
            if not az_key or not az_reg:
                raise RuntimeError("مزود ASR (Azure Speech) غير مضبوط: AZURE_SPEECH_KEY / AZURE_SPEECH_REGION مفقود")
            az_lang = norm_lang if norm_lang != "auto" else "ar-SY"
            url = f"https://{az_reg}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
            r = requests.post(
                url,
                params={"language": az_lang, "format": "detailed"},
                headers={
                    "Ocp-Apim-Subscription-Key": az_key,
                    "Content-Type": clean_mime,
                    "Accept": "application/json",
                },
                data=audio_bytes,
                timeout=60,
            )
            if r.status_code != 200:
                raise RuntimeError(f"فشل التفريغ الصوتي عبر Azure STT (HTTP {r.status_code})")
            payload = r.json() if r.content else {}
            text = str(payload.get("DisplayText") or "").strip()
            if not text:
                nbest = payload.get("NBest")
                if isinstance(nbest, list) and nbest:
                    text = str(nbest[0].get("Display") or nbest[0].get("Lexical") or "").strip()
            if not text:
                raise RuntimeError("Azure STT أرجع نصاً فارغاً")
            det_bcp47 = tts_engine.detect_text_language(text, hint_lang=az_lang)
            return {
                "text": text,
                "detected_language": det_bcp47,
                "raw_detected_language": az_lang,
                "requested_language": norm_lang,
                "provider": "azure",
                "model": model,
                "duration_sec": float(payload.get("Duration") or 0.0) / 10_000_000.0 if payload.get("Duration") else 0.0,
            }

        raise RuntimeError(f"مزود ASR غير مدعوم ({prov})؛ استخدم whisper أو groq أو azure.")

    def _transcribe_audio_internal(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language: str = "auto",
    ) -> Dict[str, Any]:
        norm_lang = normalize_asr_language(language, default="auto")
        cfg = self._resolve_asr_provider_config()

        if callable(self.asr_provider_fn):
            raw_res = self.asr_provider_fn(audio_bytes, mime_type, norm_lang)
            if isinstance(raw_res, dict):
                txt = str(raw_res.get("text") or raw_res.get("transcript") or "").strip()
                det = str(raw_res.get("detected_language") or raw_res.get("language") or "").strip()
                prov = str(raw_res.get("provider") or "custom-callback")
                mdl = str(raw_res.get("model") or cfg["model"])
            elif isinstance(raw_res, tuple) and len(raw_res) >= 2:
                txt = str(raw_res[0] or "").strip()
                det = str(raw_res[1] or "").strip()
                prov = "custom-callback"
                mdl = cfg["model"]
            else:
                txt = str(raw_res or "").strip()
                det = ""
                prov = "custom-callback"
                mdl = cfg["model"]
            if not txt:
                raise RuntimeError("مزود التعرف الصوتي أرجع نصاً فارغاً")
            det_lang = normalize_asr_language(
                det,
                default=tts_engine.detect_text_language(txt, hint_lang=norm_lang if norm_lang != "auto" else None),
            )
            if det_lang == "auto":
                det_lang = tts_engine.detect_text_language(txt)
            return {
                "text": txt,
                "detected_language": det_lang,
                "requested_language": norm_lang,
                "provider": prov,
                "model": mdl,
            }

        if cfg["configured"]:
            return self._run_production_asr(audio_bytes, mime_type, language=norm_lang)

        # Synthetic fallback is strictly forbidden in production; allowed ONLY if explicitly enabled in test harness
        test_flag = self.allow_test_synthetic_asr or (os.environ.get("RYN_ASR_TEST_SYNTHETIC", "0").strip() == "1")
        if test_flag:
            try:
                decoded = audio_bytes.decode("utf-8").strip()
                if decoded and not decoded.startswith("RIFF") and not decoded.startswith("ID3") and not decoded.startswith("OggS"):
                    det = tts_engine.detect_text_language(decoded, hint_lang=norm_lang if norm_lang != "auto" else None)
                    return {
                        "text": decoded,
                        "detected_language": det,
                        "requested_language": norm_lang,
                        "provider": "test-synthetic",
                        "model": "test-synthetic",
                    }
            except Exception:
                pass
            raise RuntimeError("بيانات الاختبار الصوتية لا تحتوي على نص UTF-8 صالح")

        raise RuntimeError(
            "لا يوجد مزود ASR مضبوط للإنتاج (ASR_PROVIDER). يرجى ضبط GROQ_API_KEY أو OPENAI_API_KEY أو AZURE_SPEECH_KEY."
        )

    # ---------------- Settings & Preferences ----------------
    def get_settings(self, user_id: str) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        default_voice = canonical_voice_id(os.environ.get("TTS_DEFAULT_VOICE", DEFAULT_VOICE_ID))
        default_lang = normalize_asr_language(os.environ.get("ASR_LANGUAGE", "auto"), default="auto")
        default_auto_read = os.environ.get("VOICE_AUTO_READ", "false").strip().lower() in ("1", "true", "yes", "on")
        ret_days = int(os.environ.get("AUDIO_RETENTION_DAYS", "7"))
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM user_voice_settings WHERE user_id = ?", (u,)).fetchone()
            if row:
                prefs = {
                    "voice_id": canonical_voice_id(row["voice_id"], default_voice),
                    "speed": float(row["speed"]),
                    "asr_language": normalize_asr_language(row["asr_language"], default=default_lang),
                    "auto_read": bool(row["auto_read"]) if "auto_read" in row.keys() else default_auto_read,
                    "retain_raw_audio": bool(row["retain_raw_audio"]),
                    "retention_days": int(row["retention_days"]),
                }
            else:
                prefs = {
                    "voice_id": default_voice,
                    "speed": 1.0,
                    "asr_language": default_lang,
                    "auto_read": default_auto_read,
                    "retain_raw_audio": False,
                    "retention_days": ret_days,
                }
        asr_info = self.asr_health_check()
        return {
            "ok": True,
            "user_id": u,
            "voice_feature_enabled": self.voice_feature_enabled,
            "live_voice_enabled": self.live_voice_enabled,
            "asr_provider": asr_info["asr_provider"],
            "asr_model": asr_info["asr_model"],
            "asr_ready": asr_info["ok"],
            "supported_asr_languages": SUPPORTED_ASR_LANGUAGES,
            "tts_provider": os.environ.get("TTS_PROVIDER", "auto"),
            "tts_model": os.environ.get("TTS_MODEL", "microsoft-neural"),
            "available_voices": AVAILABLE_VOICES,
            "preferences": prefs,
        }

    def update_settings(
        self,
        user_id: str,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
        asr_language: Optional[str] = None,
        auto_read: Optional[bool] = None,
        retain_raw_audio: Optional[bool] = None,
        retention_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        cur = self.get_settings(u)["preferences"]
        new_vid = canonical_voice_id(voice_id, cur["voice_id"]) if voice_id is not None else cur["voice_id"]
        new_spd = max(0.5, min(2.0, float(speed if speed is not None else cur["speed"])))
        new_lang = normalize_asr_language(asr_language, default=cur["asr_language"]) if asr_language is not None else cur["asr_language"]
        new_auto = bool(auto_read if auto_read is not None else cur.get("auto_read", False))
        new_ret = bool(retain_raw_audio if retain_raw_audio is not None else cur["retain_raw_audio"])
        new_days = max(1, min(90, int(retention_days if retention_days is not None else cur["retention_days"])))

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_voice_settings (
                    user_id, voice_id, speed, asr_language, auto_read, retain_raw_audio, retention_days, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (u, new_vid, new_spd, new_lang, 1 if new_auto else 0, 1 if new_ret else 0, new_days, time.time()),
            )
        return self.get_settings(u)

    # ---------------- Sessions & Timeout Recovery ----------------
    def recover_abandoned_sessions(self, timeout_sec: Optional[float] = None) -> int:
        """
        يعيد الجلسات المتروكة في حالة speaking / listening / transcribing / thinking
        إلى حالة idle / stopped بعد انتهاء مهلة عدم النشاط.
        """
        t_sec = float(timeout_sec if timeout_sec is not None else self.session_timeout_sec)
        cutoff = time.time() - max(5.0, t_sec)
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE voice_sessions
                SET state = 'idle', playback_state = 'stopped', recording_indicator_visible = 0, updated_at = ?
                WHERE (state != 'idle' OR playback_state != 'stopped') AND updated_at < ?
                """,
                (time.time(), cutoff),
            )
            return int(cur.rowcount or 0)

    def create_session(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        mic_permission_granted: bool = True,
        retain_raw_audio: bool = False,
    ) -> Dict[str, Any]:
        """POST /api/v2/voice/sessions — Creates an authenticated voice session linked to a canonical conversation."""
        self._check_feature_enabled(require_live=False)
        u = self._normalize_user(user_id)
        if not mic_permission_granted:
            raise PermissionError("🎤 يتطلب بدء المحادثة الصوتية منح إذن الميكروفون صراحةً (mic_permission_granted=True).")

        self.recover_abandoned_sessions()
        cid = str(conversation_id or "").strip() or f"conv_{u}_default"
        if self.conversation_service is not None:
            self.conversation_service.ensure_conversation(u, cid)

        prefs = self.get_settings(u)["preferences"]
        vid = canonical_voice_id(voice_id, prefs["voice_id"])
        spd = max(0.5, min(2.0, float(speed or prefs["speed"])))
        sid = f"vs_{uuid.uuid4().hex[:14]}"
        now = time.time()

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO voice_sessions (
                    session_id, user_id, conversation_id, state, playback_state,
                    partial_transcript, final_transcript, detected_language, voice_id, speed,
                    mic_permission_granted, recording_indicator_visible, retain_raw_audio,
                    retention_days, audio_seconds_used, interrupted_count,
                    last_turn_idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, 'idle', 'stopped', '', '', ?, ?, ?, 1, 0, ?, ?, 0.0, 0, '', ?, ?)
                """,
                (sid, u, cid, prefs["asr_language"], vid, spd, 1 if retain_raw_audio else 0, int(prefs["retention_days"]), now, now),
            )
            row = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (sid,)).fetchone()
            return self._row_to_session(row)

    def get_session(self, user_id: str, session_id: str, auto_recover_timeout: bool = True) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        if auto_recover_timeout:
            self.recover_abandoned_sessions()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row:
                raise KeyError("الجلسة الصوتية غير موجودة")
            if row["user_id"] != u:
                raise PermissionError("⛔ لا يمكنك الوصول إلى جلسة صوتية تخص مستخدماً آخر")
            return self._row_to_session(row)

    def transition_state(
        self,
        user_id: str,
        session_id: str,
        new_state: str,
        playback_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        if new_state not in VOICE_STATES:
            raise ValueError(f"حالة صوتية غير صالحة: {new_state}")
        rec_vis = 1 if new_state in ("listening", "transcribing") else 0
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row or row["user_id"] != u:
                raise PermissionError("⛔ جلسة صوتية غير موجودة أو غير مصرح بها")
            pb = playback_state if playback_state in PLAYBACK_STATES else (
                "playing" if new_state == "speaking" else ("stopped" if new_state in ("idle", "listening") else row["playback_state"])
            )
            conn.execute(
                "UPDATE voice_sessions SET state = ?, playback_state = ?, recording_indicator_visible = ?, updated_at = ? WHERE session_id = ?",
                (new_state, pb, rec_vis, time.time(), session_id),
            )
            updated = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (session_id,)).fetchone()
            return self._row_to_session(updated)

    def stop_or_interrupt(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        barge_in: bool = False,
        playback_action: str = "stop",
    ) -> Dict[str, Any]:
        """
        POST /api/v2/voice/stop — Cancels generation/playback, handles user speech interruption (barge-in),
        and processes explicit client playback lifecycle events:
        `playback_started`, `playback_paused` (`pause`), `playback_resumed` (`resume`),
        `playback_ended` (`ended`), `playback_stopped` (`stop`), `error`, `cancelled`.
        """
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            if session_id:
                row = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (session_id,)).fetchone()
                if not row or row["user_id"] != u:
                    raise PermissionError("⛔ جلسة صوتية غير موجودة أو غير مصرح بها")
                target_sid = session_id
            else:
                row = conn.execute(
                    "SELECT * FROM voice_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                    (u,),
                ).fetchone()
                if not row:
                    return {"ok": True, "state": "idle", "playback_state": "stopped"}
                target_sid = row["session_id"]

            act = (playback_action or "stop").strip().lower()
            if barge_in or act in ("barge_in", "interrupt"):
                new_state = "listening"
                new_pb = "stopped"
                inc_int = 1
                rec_vis = 1
                barge_in = True
            elif act in ("pause", "playback_paused"):
                new_state = row["state"] if row["state"] == "speaking" else "speaking"
                new_pb = "paused"
                inc_int = 0
                rec_vis = 0
            elif act in ("resume", "playback_resumed", "playback_started", "start"):
                new_state = "speaking"
                new_pb = "playing"
                inc_int = 0
                rec_vis = 0
            elif act in ("ended", "playback_ended", "stop", "playback_stopped", "error", "cancelled", "navigation_away"):
                new_state = "idle"
                new_pb = "stopped"
                inc_int = 0
                rec_vis = 0
            else:
                new_state = "idle"
                new_pb = "stopped"
                inc_int = 0
                rec_vis = 0

            conn.execute(
                """
                UPDATE voice_sessions
                SET state = ?, playback_state = ?, recording_indicator_visible = ?,
                    interrupted_count = interrupted_count + ?, updated_at = ?
                WHERE session_id = ?
                """,
                (new_state, new_pb, rec_vis, inc_int, time.time(), target_sid),
            )
            updated = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (target_sid,)).fetchone()
            return {"ok": True, "barge_in": barge_in, "playback_action": act, **self._row_to_session(updated)}

    # ---------------- Multilingual Transcription (ASR) ----------------
    def transcribe_upload(
        self,
        user_id: str,
        audio_bytes: bytes = b"",
        mime_type: str = "audio/webm",
        language: Optional[str] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        corrected_transcript: Optional[str] = None,
        auto_send_to_chat: bool = False,
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        """
        POST /api/v2/voice/transcribe — Multilingual upload ASR with auto-detect by default or explicit BCP-47 locale.
        Requires a real ASR provider in production (never fabricates placeholder text).
        Preserves detected_language and transcript metadata for each turn.
        """
        self._check_feature_enabled(require_live=False)
        u = self._normalize_user(user_id)
        prefs = self.get_settings(u)["preferences"]
        req_lang = normalize_asr_language(language, default=prefs.get("asr_language", "auto"))

        if corrected_transcript is not None and str(corrected_transcript).strip():
            transcript = str(corrected_transcript).strip()
            detected_lang = tts_engine.detect_text_language(transcript, hint_lang=req_lang if req_lang != "auto" else None)
            asr_meta = {
                "provider": "user-corrected",
                "model": "manual",
                "requested_language": req_lang,
                "detected_language": detected_lang,
                "mime_type": mime_type,
                "audio_bytes": len(audio_bytes or b""),
            }
        elif audio_bytes:
            asr_res = self._transcribe_audio_internal(audio_bytes, mime_type, language=req_lang)
            transcript = asr_res["text"]
            detected_lang = asr_res["detected_language"]
            asr_meta = {
                "provider": asr_res.get("provider", ""),
                "model": asr_res.get("model", ""),
                "requested_language": req_lang,
                "detected_language": detected_lang,
                "mime_type": mime_type,
                "audio_bytes": len(audio_bytes),
            }
        else:
            raise ValueError("لم يتم إرسال بيانات صوتية أو نص مصحح")

        cid = str(conversation_id or "").strip() or f"conv_{u}_default"
        if session_id:
            sess = self.get_session(u, session_id)
            cid = sess["conversation_id"]
            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    UPDATE voice_sessions
                    SET state = 'transcribing', final_transcript = ?, detected_language = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (transcript, detected_lang, time.time(), session_id),
                )

        saved_user_msg = None
        saved_bot_msg = None
        if auto_send_to_chat and self.conversation_service is not None:
            idem = str(idempotency_key or f"voice_turn_{hashlib.sha256(transcript.encode('utf-8')).hexdigest()[:12]}")
            saved_user_msg = self.conversation_service.append_message(
                user_id=u,
                role="user",
                content=transcript,
                conversation_id=cid,
                modality="voice",
                status="succeeded",
                metadata=asr_meta,
                idempotency_key=idem,
            )
            if callable(self.chat_responder_fn):
                reply_txt = self.chat_responder_fn(u, cid, transcript)
                if reply_txt:
                    reply_lang = tts_engine.detect_text_language(reply_txt, hint_lang=detected_lang)
                    saved_bot_msg = self.conversation_service.append_message(
                        user_id=u,
                        role="assistant",
                        content=reply_txt,
                        conversation_id=cid,
                        modality="voice",
                        status="succeeded",
                        metadata={"detected_language": reply_lang},
                        idempotency_key=f"{idem}_reply",
                    )

        if session_id:
            self.transition_state(u, session_id, "idle", playback_state="stopped")

        return {
            "ok": True,
            "text": transcript,
            "transcript": transcript,
            "detected_language": detected_lang,
            "requested_language": req_lang,
            "asr_provider": asr_meta["provider"],
            "asr_model": asr_meta["model"],
            "transcript_metadata": asr_meta,
            "conversation_id": cid,
            "session_id": session_id or "",
            "user_message": saved_user_msg,
            "assistant_message": saved_bot_msg,
        }

    # ---------------- Neural TTS & Read-Aloud ----------------
    def speak(
        self,
        user_id: str,
        text: str = "",
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
        read_full_conversation: bool = False,
        session_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
        language: Optional[str] = None,
        auto_route_language: bool = True,
        page: int = 1,
        page_size: int = 10,
        include_user_messages: bool = True,
    ) -> Dict[str, Any]:
        """
        POST /api/v2/voice/speak — Reads replies, selected text, a single message (`message_id`),
        or a paginated conversation (`read_full_conversation=True`).
        Validates voice IDs against the catalog allow-list, routes multilingual text to matching Neural voices,
        chunks long messages without silent truncation, and validates container signatures.
        """
        self._check_feature_enabled(require_live=False)
        u = self._normalize_user(user_id)
        prefs = self.get_settings(u)["preferences"]
        requested_vid = str(voice_id or "").strip()
        base_vid = canonical_voice_id(voice_id, prefs["voice_id"])
        voice_fallback_applied = bool(requested_vid and not tts_engine.is_valid_voice_id(requested_vid))
        spd = max(0.5, min(2.0, float(speed if speed is not None else prefs["speed"])))
        cid = str(conversation_id or "").strip() or f"conv_{u}_default"

        target_text = (text or "").strip()
        msg_lang_hint = str(language or "").strip()
        conversation_pagination: Optional[Dict[str, Any]] = None

        if not target_text and self.conversation_service is not None:
            msgs = self.conversation_service.get_messages(u, cid, limit=200)
            if message_id:
                found = [m for m in msgs if m["message_id"] == message_id]
                if not found:
                    raise KeyError("الرسالة المطلوبة للقراءة الصوتية غير موجودة")
                msg_obj = found[0]
                target_text = msg_obj["content"]
                meta = msg_obj.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("detected_language"):
                    msg_lang_hint = str(meta["detected_language"])
            elif read_full_conversation:
                filtered_msgs = [
                    m for m in msgs
                    if include_user_messages or m.get("role") != "user"
                ]
                pg = max(1, int(page or 1))
                pg_sz = max(1, min(50, int(page_size or 10)))
                total_msgs = len(filtered_msgs)
                total_pages = max(1, math.ceil(total_msgs / float(pg_sz)))
                start_idx = (pg - 1) * pg_sz
                slice_msgs = filtered_msgs[start_idx:start_idx + pg_sz]

                lines = []
                items_meta = []
                for m in slice_msgs:
                    m_txt = str(m.get("content") or "").strip()
                    if not m_txt:
                        continue
                    m_lang = tts_engine.detect_text_language(m_txt)
                    is_ar = m_lang.startswith("ar")
                    speaker = ("المستخدم" if m.get("role") == "user" else "رين") if is_ar else ("User" if m.get("role") == "user" else "RYN")
                    labeled = f"{speaker}: {m_txt}"
                    lines.append(labeled)
                    items_meta.append({
                        "message_id": m.get("message_id", ""),
                        "role": m.get("role", "assistant"),
                        "speaker_label": speaker,
                        "language": m_lang,
                        "text": m_txt,
                    })
                target_text = "\n".join(lines)
                conversation_pagination = {
                    "page": pg,
                    "page_size": pg_sz,
                    "total_messages": total_msgs,
                    "total_pages": total_pages,
                    "has_more": (start_idx + pg_sz) < total_msgs,
                    "items": items_meta,
                }

        if not target_text:
            raise ValueError("لا يوجد نص لقراءته صوتياً")

        # Determine language and best matching neural voice from catalog
        routing = tts_engine.select_voice_for_language(
            text=target_text,
            preferred_voice_id=base_vid,
            detected_language=msg_lang_hint or None,
        )
        effective_vid = routing["voice_id"] if auto_route_language else base_vid
        detected_lang = routing["detected_language"]

        # Split long text into sentence-aware chunks without silent 3,000-char truncation
        chunk_plan = tts_engine.split_into_sentence_chunks(
            target_text,
            max_chunk_chars=tts_engine.MAX_CHARS,
            speed=spd,
        )
        if not chunk_plan["clean_text"] or not chunk_plan["chunks"]:
            raise ValueError("لا يوجد نص صالح للقراءة بعد التنظيف")

        if session_id:
            self.transition_state(u, session_id, "speaking", playback_state="playing")

        tts_error = ""
        fallback_browser = False
        provider_used = ""
        fallback_provider = ""
        fallback_quality = "neural"
        is_neural = True
        out_format = "mp3"
        mime = "audio/mpeg"
        synthesized_chunks: List[Dict[str, Any]] = []
        raw_parts: List[bytes] = []

        if callable(self.tts_provider_fn):
            try:
                for idx, ch_txt in enumerate(chunk_plan["chunks"]):
                    part_bytes = self.tts_provider_fn(ch_txt, effective_vid, spd)
                    fmt_info = tts_engine.validate_audio_payload(part_bytes)
                    if idx == 0:
                        out_format = fmt_info["output_format"]
                        mime = fmt_info["mime_type"]
                    raw_parts.append(part_bytes)
                    ch_dur = round(max(0.2, (len(ch_txt) * 0.06) / spd), 2)
                    synthesized_chunks.append({
                        "chunk_index": idx,
                        "total_chunks": len(chunk_plan["chunks"]),
                        "text": ch_txt,
                        "audio_b64": base64.b64encode(part_bytes).decode("ascii"),
                        "mime_type": fmt_info["mime_type"],
                        "output_format": fmt_info["output_format"],
                        "duration_sec": ch_dur,
                    })
                audio_bytes = tts_engine._merge_audio_chunks(raw_parts, out_format)
                provider_used = str(getattr(self.tts_provider_fn, "last_provider", "") or "custom")
                last_meta = getattr(self.tts_provider_fn, "last_metadata", None)
                if isinstance(last_meta, dict) and last_meta.get("provider") == provider_used:
                    fallback_provider = str(last_meta.get("fallback_provider") or "")
                    fallback_quality = str(last_meta.get("fallback_quality") or ("basic_emergency_non_neural" if provider_used == "gtts" else "neural"))
                    is_neural = bool(last_meta.get("is_neural", provider_used != "gtts"))
                elif provider_used == "gtts":
                    fallback_provider = "gtts"
                    fallback_quality = "basic_emergency_non_neural"
                    is_neural = False
            except Exception as e:
                tts_error = str(e)[:240]
                audio_bytes = b""
                synthesized_chunks = []
                fallback_browser = True
                fallback_provider = "browser_speech_synthesis"
                fallback_quality = "device_local_fallback"
                is_neural = False
                if session_id:
                    self.transition_state(u, session_id, "idle", playback_state="stopped")
        else:
            # Offline test environment without external provider callback
            for idx, ch_txt in enumerate(chunk_plan["chunks"]):
                part_bytes = synthesize_wav_Placeholder_bytes(ch_txt, speed=spd)
                fmt_info = tts_engine.validate_audio_payload(part_bytes)
                if idx == 0:
                    out_format = fmt_info["output_format"]
                    mime = fmt_info["mime_type"]
                raw_parts.append(part_bytes)
                ch_dur = round(max(0.2, (len(ch_txt) * 0.06) / spd), 2)
                synthesized_chunks.append({
                    "chunk_index": idx,
                    "total_chunks": len(chunk_plan["chunks"]),
                    "text": ch_txt,
                    "audio_b64": base64.b64encode(part_bytes).decode("ascii"),
                    "mime_type": fmt_info["mime_type"],
                    "output_format": fmt_info["output_format"],
                    "duration_sec": ch_dur,
                })
            audio_bytes = tts_engine._merge_audio_chunks(raw_parts, out_format)
            provider_used = "offline-test-wav"
            fallback_quality = "test_synthetic_wav"
            is_neural = False

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii") if audio_bytes else ""
        dur_sec = chunk_plan["estimated_duration_sec"]

        # Never claim gTTS emergency fallback is a Microsoft Neural voice ID
        reported_voice_id = (
            f"gtts-{detected_lang.split('-')[0].lower()}-basic"
            if provider_used == "gtts"
            else effective_vid
        )

        asset_id = f"va_{uuid.uuid4().hex[:14]}"
        now = time.time()
        exp_ts = now + (int(prefs["retention_days"]) * 86400)
        if session_id and audio_b64:
            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO voice_assets (
                        asset_id, session_id, user_id, conversation_id, kind,
                        transcript, storage_ref, audio_b64, duration_sec,
                        retention_expires_at, deleted, created_at
                    ) VALUES (?, ?, ?, ?, 'tts', ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        asset_id,
                        session_id,
                        u,
                        cid,
                        target_text[:1000],
                        f"mem://{asset_id}.{out_format}",
                        audio_b64,
                        dur_sec,
                        exp_ts,
                        now,
                    ),
                )

        res_state = "idle" if fallback_browser else "speaking"
        res_pb = "stopped" if fallback_browser else "playing"

        out: Dict[str, Any] = {
            "ok": True,
            "asset_id": asset_id,
            "session_id": session_id or "",
            "conversation_id": cid,
            "message_id": message_id or "",
            "requested_voice_id": requested_vid or base_vid,
            "voice_id": effective_vid,
            "effective_voice_id": reported_voice_id,
            "voice_fallback_applied": voice_fallback_applied,
            "language": ALLOWED_VOICES_MAP.get(effective_vid, {}).get("lang", detected_lang),
            "detected_language": detected_lang,
            "speed": spd,
            "state": res_state,
            "playback_state": res_pb,
            "text_read": target_text,
            "clean_text": chunk_plan["clean_text"],
            "duration_sec": dur_sec,
            "output_format": out_format,
            "mime_type": mime,
            "audio_b64": audio_b64,
            "chunks": synthesized_chunks,
            "chunk_count": len(synthesized_chunks),
            "truncated": chunk_plan["truncated"],
            "warning": chunk_plan["warning"],
            "is_neural": is_neural,
            "fallback_browser_tts": fallback_browser,
            "fallback_provider": fallback_provider,
            "fallback_quality": fallback_quality,
            "tts_error": tts_error,
            "tts_provider_used": provider_used,
        }
        if conversation_pagination is not None:
            out["conversation_pagination"] = conversation_pagination
        return out

    # ---------------- Live Voice Streaming & Full Duplex Barge-In ----------------
    def process_stream_event(
        self,
        user_id: str,
        session_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        WS / POST `/api/v2/voice/stream` — Handles live voice streaming audio frames, partial transcripts,
        playback state updates, and full-duplex barge-in:
        - `start_listening`: transitions `idle -> listening` (and triggers barge-in if currently speaking).
        - `audio_frame`: accepts real binary/base64 microphone frames (`audio_b64` / `audio_bytes`), buffers them,
          optionally runs incremental ASR, and cancels active TTS playback immediately on barge-in.
        - `partial_transcript`: updates live partial transcript (explicitly labeled `transcript_only_event=True`).
        - `end_of_turn` / `final_transcript`: transcribes buffered audio frames (or uses final transcript),
          saves user voice message in canonical conversation store, generates RYN reply & streamed TTS chunks,
          and prevents duplicate turns via `idempotency_key`.
        - `barge_in` / `interrupt`: cancels current playback immediately and returns to `listening`.
        - `playback_started` / `playback_paused` / `playback_resumed` / `playback_ended` / `playback_stopped`:
          updates session playback state cleanly so sessions are never left stuck in `speaking`.
        """
        self._check_feature_enabled(require_live=True)
        u = self._normalize_user(user_id)
        sess = self.get_session(u, session_id, auto_recover_timeout=False)
        cid = sess["conversation_id"]
        ev = (event_type or "").strip().lower()
        data = payload or {}

        if ev in ("barge_in", "interrupt"):
            with self._lock:
                self._stream_buffers.pop(session_id, None)
            return self.stop_or_interrupt(u, session_id=session_id, barge_in=True)

        if ev in ("playback_started", "playback_paused", "playback_resumed", "playback_ended", "playback_stopped"):
            return self.stop_or_interrupt(u, session_id=session_id, barge_in=False, playback_action=ev)

        if ev == "start_listening":
            with self._lock:
                self._stream_buffers[session_id] = []
            if sess["state"] == "speaking" or sess["playback_state"] == "playing":
                return self.stop_or_interrupt(u, session_id=session_id, barge_in=True)
            return self.transition_state(u, session_id, "listening", playback_state="stopped")

        if ev in ("audio_frame", "partial_transcript"):
            barge_triggered = False
            if sess["state"] == "speaking" or sess["playback_state"] == "playing":
                self.stop_or_interrupt(u, session_id=session_id, barge_in=True)
                barge_triggered = True

            raw_frame = b""
            if isinstance(data.get("audio_bytes"), (bytes, bytearray)):
                raw_frame = bytes(data["audio_bytes"])
            elif data.get("audio_b64"):
                try:
                    b64_str = str(data["audio_b64"]).split(",")[-1].strip()
                    raw_frame = base64.b64decode(b64_str)
                except Exception as e:
                    raise ValueError(f"تعذر فك ترميز إطار الصوت (audio_b64): {e}")

            frame_mime = str(data.get("mime_type") or "audio/webm").strip()
            has_real_audio = len(raw_frame) > 0
            buffered_bytes = 0
            frame_count = 0

            if has_real_audio:
                with self._lock:
                    buf = self._stream_buffers.setdefault(session_id, [])
                    buf.append(raw_frame)
                    self._stream_mimes[session_id] = frame_mime
                    frame_count = len(buf)
                    buffered_bytes = sum(len(x) for x in buf)

            partial_txt = str(data.get("partial_transcript") or data.get("text") or "").strip()
            detected_lang = sess.get("detected_language", "auto")

            # If real audio frames were streamed and incremental ASR is requested
            if has_real_audio and not partial_txt and data.get("transcribe_frame"):
                with self._lock:
                    joined_audio = b"".join(self._stream_buffers.get(session_id, []))
                asr_out = self._transcribe_audio_internal(
                    joined_audio,
                    frame_mime,
                    language=str(data.get("language") or sess.get("detected_language") or "auto"),
                )
                partial_txt = asr_out["text"]
                detected_lang = asr_out["detected_language"]
            elif partial_txt:
                detected_lang = tts_engine.detect_text_language(partial_txt)

            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    UPDATE voice_sessions
                    SET state = 'transcribing', playback_state = 'stopped',
                        partial_transcript = ?, detected_language = ?,
                        recording_indicator_visible = 1, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (partial_txt or sess["partial_transcript"], detected_lang, time.time(), session_id),
                )
                updated = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (session_id,)).fetchone()
            out = self._row_to_session(updated)
            out["barge_in_triggered"] = barge_triggered
            out["transport_mode"] = "audio_stream" if has_real_audio else "transcript_events"
            out["transcript_only_event"] = not has_real_audio
            out["buffered_frames"] = frame_count
            out["buffered_bytes"] = buffered_bytes
            return out

        if ev in ("end_of_turn", "final_transcript"):
            final_txt = str(data.get("final_transcript") or data.get("text") or "").strip()
            req_lang = normalize_asr_language(
                str(data.get("language") or ""),
                default=self.get_settings(u)["preferences"].get("asr_language", "auto"),
            )
            detected_lang = req_lang
            asr_provider_used = "client-transcript"

            with self._lock:
                buffered_frames = self._stream_buffers.pop(session_id, [])
                buffered_mime = self._stream_mimes.pop(session_id, str(data.get("mime_type") or "audio/webm"))

            if not final_txt and buffered_frames:
                joined_audio = b"".join(buffered_frames)
                asr_res = self._transcribe_audio_internal(joined_audio, buffered_mime, language=req_lang)
                final_txt = asr_res["text"]
                detected_lang = asr_res["detected_language"]
                asr_provider_used = asr_res["provider"]
            elif not final_txt:
                final_txt = str(sess["partial_transcript"] or "").strip()

            if not final_txt:
                raise ValueError("النص النهائي للدور الصوتي فارغ")

            if detected_lang == "auto" or not detected_lang:
                detected_lang = tts_engine.detect_text_language(final_txt, hint_lang=req_lang if req_lang != "auto" else None)

            idem_key = str(
                data.get("idempotency_key")
                or f"vturn_{session_id}_{hashlib.sha256(final_txt.encode('utf-8')).hexdigest()[:12]}"
            )

            # Check duplicate turn recovery
            if sess["last_turn_idempotency_key"] == idem_key and not data.get("force"):
                msgs = self.conversation_service.get_messages(u, cid, limit=10) if self.conversation_service else []
                return {
                    "ok": True,
                    "duplicate_prevented": True,
                    "session_id": session_id,
                    "conversation_id": cid,
                    "final_transcript": final_txt,
                    "detected_language": detected_lang,
                    "recent_messages": msgs[-2:],
                }

            self.transition_state(u, session_id, "thinking", playback_state="stopped")

            user_msg = None
            bot_msg = None
            reply_text = str(data.get("mock_reply") or "").strip()

            if self.conversation_service is not None:
                user_msg = self.conversation_service.append_message(
                    user_id=u,
                    role="user",
                    content=final_txt,
                    conversation_id=cid,
                    modality="voice",
                    status="succeeded",
                    metadata={
                        "detected_language": detected_lang,
                        "asr_provider": asr_provider_used,
                    },
                    idempotency_key=idem_key,
                )

            if not reply_text and callable(self.chat_responder_fn):
                reply_text = str(self.chat_responder_fn(u, cid, final_txt) or "").strip()
            if not reply_text:
                reply_text = (
                    f"سمعتك بوضوح: {final_txt}"
                    if detected_lang.startswith("ar")
                    else f"I heard you clearly: {final_txt}"
                )

            reply_lang = tts_engine.detect_text_language(reply_text, hint_lang=detected_lang)

            if self.conversation_service is not None:
                bot_msg = self.conversation_service.append_message(
                    user_id=u,
                    role="assistant",
                    content=reply_text,
                    conversation_id=cid,
                    modality="voice",
                    status="succeeded",
                    metadata={"detected_language": reply_lang},
                    idempotency_key=f"{idem_key}_assistant",
                )

            tts_out = self.speak(
                user_id=u,
                text=reply_text,
                conversation_id=cid,
                message_id=bot_msg["message_id"] if isinstance(bot_msg, dict) else None,
                session_id=session_id,
                voice_id=sess["voice_id"],
                speed=sess["speed"],
                language=reply_lang,
                auto_route_language=True,
            )

            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    UPDATE voice_sessions
                    SET final_transcript = ?, partial_transcript = '',
                        detected_language = ?,
                        last_turn_idempotency_key = ?,
                        audio_seconds_used = audio_seconds_used + ?,
                        updated_at = ?
                    WHERE session_id = ?
                    """,
                    (
                        final_txt,
                        detected_lang,
                        idem_key,
                        float(tts_out.get("duration_sec", 1.0)),
                        time.time(),
                        session_id,
                    ),
                )
                updated_sess = conn.execute("SELECT * FROM voice_sessions WHERE session_id = ?", (session_id,)).fetchone()

            return {
                "ok": True,
                "duplicate_prevented": False,
                "detected_language": detected_lang,
                "reply_language": reply_lang,
                "transport_mode": "audio_stream" if buffered_frames else "transcript_events",
                "session": self._row_to_session(updated_sess),
                "user_message": user_msg,
                "assistant_message": bot_msg,
                "tts": tts_out,
            }

        raise ValueError(f"حدث دفق صوتي غير معروف: {event_type}")

    def delete_user_audio_assets(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Privacy control: deletes stored voice assets for the authenticated user."""
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            if session_id:
                conn.execute(
                    "UPDATE voice_assets SET deleted = 1, audio_b64 = '' WHERE user_id = ? AND session_id = ?",
                    (u, session_id),
                )
            else:
                conn.execute(
                    "UPDATE voice_assets SET deleted = 1, audio_b64 = '' WHERE user_id = ?",
                    (u,),
                )
        return {"ok": True, "user_id": u, "deleted": True}

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Dict[str, Any]:
        keys = row.keys()
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "conversation_id": row["conversation_id"],
            "state": row["state"],
            "playback_state": row["playback_state"],
            "partial_transcript": row["partial_transcript"],
            "final_transcript": row["final_transcript"],
            "detected_language": row["detected_language"] if "detected_language" in keys else "auto",
            "voice_id": canonical_voice_id(row["voice_id"]),
            "speed": float(row["speed"]),
            "mic_permission_granted": bool(row["mic_permission_granted"]),
            "recording_indicator_visible": bool(row["recording_indicator_visible"]),
            "retain_raw_audio": bool(row["retain_raw_audio"]),
            "retention_days": int(row["retention_days"]),
            "audio_seconds_used": float(row["audio_seconds_used"]),
            "interrupted_count": int(row["interrupted_count"]),
            "last_turn_idempotency_key": row["last_turn_idempotency_key"],
            "created_at": _iso_utc(row["created_at"]),
            "updated_at": _iso_utc(row["updated_at"]),
        }


ALLOWED_VOICES_MAP: Dict[str, Dict[str, Any]] = {
    v["id"]: v for v in AVAILABLE_VOICES if v.get("available", True)
}
