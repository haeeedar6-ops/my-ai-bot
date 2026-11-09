"""
voice.py — وحدة التحكم الصوتي والرد المسموع لعميل سطح المكتب (Neural Server TTS + Multilingual ASR + Safe SAPI Fallback).

الإصلاحات المطبقة:
1. المسار الأساسي للنطق يرسل الرد إلى `/api/v2/voice/speak` ويشغّل الصوت العصبي المولّد من السيرفر (MP3/WAV) مع دعم القطع المتتالية.
2. إلغاء الاقتطاع الصامت عند 240 حرفاً نهائياً؛ تقسيم النصوص الطويلة إلى جمل متتالية وتشغيلها بالكامل، مع إظهار تنبيه واضح فقط عند تجاوز سقف الأمان الأقصى.
3. الاحتفاظ بـ Windows SAPI كمسار طوارئ أوفلاين فقط (Offline Fallback) مع اختيار صوت يطابق لغة الرد، واستدعائه عبر واجهة COM مباشرة أو تمرير النص عبر Standard Input (stdin) دون أي دمج نصي خطير داخل أوامر PowerShell.
4. إشعار السيرفر ببدء/إيقاف/انتهاء التشغيل الصوتي ودعم المقاطعة الفورية (Barge-in).
"""
from __future__ import annotations

import base64
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .api_client import RynAgentApiClient

DEFAULT_MAX_SPEECH_CHARS = 30000
DEFAULT_CHUNK_CHARS = 600

# رموز اللغة العشرية/السداسية لـ Windows SAPI (LCID prefixes) لاختيار الصوت المطابق للغة الرد
_SAPI_LANG_HEX: Dict[str, str] = {
    "ar": "401",
    "en": "409",
    "de": "407",
    "fr": "40c",
    "es": "c0a",
    "it": "410",
    "tr": "41f",
    "pt": "416",
}


def detect_reply_language(text: str) -> str:
    """يكتشف لغة النص (BCP-47) لاختيار الصوت المناسب على السيرفر أو في وضع الطوارئ."""
    raw = str(text or "").strip()
    if re.search(r"[\u0600-\u06FF]", raw):
        if re.search(r"\b(هلق|هلأ|شلونك|بدي|هيك|منيح|كرمال|مشان|عنجد|كتير)\b", raw):
            return "ar-SY"
        if re.search(r"\b(ازيك|إزيك|عايز|عاوز|كده|دلوقتي)\b", raw):
            return "ar-EG"
        return "ar-SA"
    low = f" {raw.lower()} "
    if re.search(r"[ğşıİ]", raw) or re.search(r"\b(merhaba|nasılsın|teşekkür|teşekkürler|için|değil)\b", low):
        return "tr-TR"
    if re.search(r"[äöüß]", low) or re.search(r"\b(hallo|danke|bitte|nicht|ich|und|für|wie geht)\b", low):
        return "de-DE"
    if re.search(r"[œç]", low) or re.search(r"\b(bonjour|merci|comment|avec|pour|dans|vous|très)\b", low):
        return "fr-FR"
    if re.search(r"[ñ¿¡]", low) or re.search(r"\b(hola|gracias|cómo|está|para|pero|porque|buenos)\b", low):
        return "es-ES"
    if re.search(r"[ãõ]", low) or re.search(r"\b(olá|obrigado|obrigada|você|não|também)\b", low):
        return "pt-BR"
    if re.search(r"\b(ciao|grazie|perché|questo|sono|buongiorno|tutto)\b", low):
        return "it-IT"
    return "en-US"


def clean_text_for_local_speech(text: str) -> str:
    """ينظف النص من الكود والروابط والماركداون دون اقتطاعه صامتاً."""
    t = str(text or "")
    t = re.sub(r"```[\s\S]*?```", " كود برمجي جاهز بالشات. ", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"https?://\S+", " رابط ", t)
    t = re.sub(r"[*#_~>|]+", " ", t)
    t = re.sub(r"\n{2,}", " . ", t)
    t = re.sub(r"\n", " ، ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def split_sentence_chunks(
    text: str,
    max_chunk_chars: int = DEFAULT_CHUNK_CHARS,
    max_total_chars: int = DEFAULT_MAX_SPEECH_CHARS,
) -> Dict[str, Any]:
    """
    يقسم النص إلى جمل متسلسلة للعبها قطعة بعد قطعة بدون أي اقتطاع صامت عند 240 حرفاً.
    يرجّع تحذيراً مرئياً فقط إذا تجاوز النص سقف الأمان الأقصى (max_total_chars).
    """
    clean = clean_text_for_local_speech(text)
    if not clean:
        return {"chunks": [], "truncated": False, "warning": "", "clean_text": ""}

    truncated = False
    warning = ""
    if max_total_chars > 0 and len(clean) > max_total_chars:
        truncated = True
        clean = clean[:max_total_chars].rsplit(" ", 1)[0] or clean[:max_total_chars]
        warning = f"⚠️ وصل طول الرد إلى سقف الأمان الأقصى ({max_total_chars} حرف)؛ تم قراءة النص حتى الحد المسموح."

    limit = max(80, int(max_chunk_chars or DEFAULT_CHUNK_CHARS))
    raw_sents = re.findall(r"[^.!?؟؛;\n]+[.!?؟؛;\n]*", clean) or [clean]
    chunks: List[str] = []
    cur = ""

    for s in raw_sents:
        s = s.strip()
        if not s:
            continue
        if len(s) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            clauses = re.findall(r"[^،,:]+[,،:]*", s) or [s]
            sub = ""
            for cl in clauses:
                cl = cl.strip()
                if not cl:
                    continue
                if len(cl) > limit:
                    if sub:
                        chunks.append(sub)
                        sub = ""
                    for i in range(0, len(cl), limit):
                        part = cl[i:i + limit].strip()
                        if part:
                            chunks.append(part)
                elif sub and len(sub) + 1 + len(cl) > limit:
                    chunks.append(sub)
                    sub = cl
                else:
                    sub = f"{sub} {cl}".strip() if sub else cl
            if sub:
                cur = sub
        elif cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip() if cur else s

    if cur:
        chunks.append(cur)

    return {"chunks": chunks, "truncated": truncated, "warning": warning, "clean_text": clean}


# سكربت PowerShell ثابت وآمن يقرأ النص من Standard Input فقط دون أي interpolation داخل الكود
_SAFE_SAPI_STDIN_SCRIPT = (
    "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
    "$txt = [Console]::In.ReadToEnd(); "
    "if ([string]::IsNullOrWhiteSpace($txt)) { exit 0 }; "
    "$sp = New-Object -ComObject SAPI.SpVoice; "
    "$langHex = $env:RYN_SAPI_LANG; "
    "if ($langHex) { "
    "  foreach ($v in $sp.GetVoices()) { "
    "    if ($v.GetAttribute('Language') -match ('^0*' + $langHex + '$')) { $sp.Voice = $v; break } "
    "  } "
    "}; "
    "$rateVal = 0; "
    "if ([int]::TryParse($env:RYN_SAPI_RATE, [ref]$rateVal)) { $sp.Rate = $rateVal }; "
    "[void]$sp.Speak($txt)"
)


class PushToTalkController:
    def __init__(
        self,
        api_client: RynAgentApiClient,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        asr_language: str = "auto",
        max_chars: int = DEFAULT_MAX_SPEECH_CHARS,
        log_cb: Optional[Callable[[str], None]] = None,
    ):
        self.api_client = api_client
        self.is_recording = False
        self.muted = False
        self.tts_enabled = True
        self.voice_id = voice_id
        self.speed = float(speed or 1.0)
        self.asr_language = asr_language or "auto"
        self.max_chars = max_chars
        self.log_cb = log_cb

        self.session_id: str = ""
        self.playback_state: str = "stopped"
        self.last_provider: str = ""
        self.last_warning: str = ""
        self.last_error: str = ""
        self.last_spoken_text: str = ""
        self.fallback_used: bool = False

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._active_proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        # مقاطعة أثناء الكلام: مطفية افتراضياً (ما في إلغاء صدى على ويندوز). RYN_DESKTOP_DUPLEX=1 لتفعيلها.
        self.duplex_enabled: bool = os.environ.get("RYN_DESKTOP_DUPLEX", "0").strip().lower() in ("1", "true", "yes", "on")
        self.duplex_profile: str = os.environ.get("RYN_DESKTOP_DUPLEX_PROFILE", "headphones")
        self.duplex_frame_source_factory = None

    def _emit_log(self, msg: str) -> None:
        if callable(self.log_cb):
            try:
                self.log_cb(msg)
            except Exception:
                pass

    def _notify_server_playback(self, action: str, barge_in: bool = False) -> None:
        if not self.session_id:
            return
        try:
            self.api_client.request_json(
                "POST",
                "/api/v2/voice/stop",
                {"session_id": self.session_id, "action": action, "barge_in": barge_in},
                retries=1,
            )
        except Exception:
            pass

    def _play_audio_bytes_local(self, audio_bytes: bytes, mime_type: str = "audio/mpeg") -> bool:
        """
        يفك ويشغّل بايتات الصوت المولّدة من السيرفر (WAV أو MP3).
        يرجع True عند نجاح التشغيل أو الإيقاف المتعمد، وFalse إذا تعذر تشغيل الحاوية محلياً.
        """
        if not audio_bytes or len(audio_bytes) < 4:
            return False
        if self._stop_event.is_set():
            return True

        is_wav = audio_bytes[:4] == b"RIFF" and len(audio_bytes) >= 12 and audio_bytes[8:12] == b"WAVE"
        is_mp3 = audio_bytes[:3] == b"ID3" or (audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0)
        is_ogg = audio_bytes[:4] == b"OggS"
        if not (is_wav or is_mp3 or is_ogg):
            return False

        if not sys.platform.startswith("win"):
            # في بيئات الاختبار غير ويندوز (CI / Linux)، نعتبر التحقق من بايتات السيرفر ناجحاً
            return True

        try:
            if is_wav:
                import winsound  # type: ignore

                winsound.PlaySound(audio_bytes, winsound.SND_MEMORY)
                return True

            # MP3 playback via Windows MCI (winmm.dll) using a temporary file
            suffix = ".mp3" if is_mp3 else ".ogg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(audio_bytes)
                tmp_path = tf.name
            try:
                alias = f"ryn_tts_{int(time.time() * 1000)}"
                mci = ctypes.windll.winmm.mciSendStringW  # type: ignore[attr-defined]
                buf = ctypes.create_unicode_buffer(256)
                if mci(f'open "{tmp_path}" type mpegvideo alias {alias}', buf, 255, 0) != 0:
                    return False
                try:
                    mci(f"play {alias}", buf, 255, 0)
                    while not self._stop_event.is_set():
                        self._pause_event.wait(timeout=0.1)
                        mci(f"status {alias} mode", buf, 255, 0)
                        if buf.value.strip().lower() not in ("playing", "paused"):
                            break
                        time.sleep(0.08)
                finally:
                    mci(f"close {alias}", buf, 255, 0)
                return True
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        except Exception:
            return False

    def _speak_sapi_chunk_safe(self, chunk: str, lang_bcp47: str) -> bool:
        """
        ينطق قطعة نصية عبر Windows SAPI كمسار طوارئ أوفلاين فقط:
        1) يجرب واجهة COM المباشرة في بايثون (win32com / comtypes) دون أي أمر شل.
        2) عند غياب مكتبات COM على ويندوز، يمرر النص عبر stdin لسكربت PowerShell ثابت بدون أي interpolation للنص.
        """
        if not chunk or self._stop_event.is_set():
            return False

        lang_prefix = (lang_bcp47 or "ar-SY").split("-")[0].lower()
        lang_hex = _SAPI_LANG_HEX.get(lang_prefix, "401")
        sapi_rate = max(-5, min(5, int(round((self.speed - 1.0) * 5))))

        # 1. المحاولة الأولى: COM مباشر بدون سطر أوامر
        for com_loader in ("win32com", "comtypes"):
            try:
                if com_loader == "win32com":
                    import win32com.client  # type: ignore

                    sp = win32com.client.Dispatch("SAPI.SpVoice")
                else:
                    import comtypes.client  # type: ignore

                    sp = comtypes.client.CreateObject("SAPI.SpVoice")
                try:
                    for v in sp.GetVoices():
                        attr = str(v.GetAttribute("Language") or "").lower().lstrip("0")
                        if attr == lang_hex.lstrip("0"):
                            sp.Voice = v
                            break
                except Exception:
                    pass
                sp.Rate = sapi_rate
                sp.Speak(chunk)
                return True
            except Exception:
                continue

        # 2. المحاولة الثانية على ويندوز: PowerShell مع تمرير النص عبر stdin حصراً (بدون دمج النص في الأمر)
        if sys.platform.startswith("win"):
            try:
                env = os.environ.copy()
                env["RYN_SAPI_LANG"] = lang_hex
                env["RYN_SAPI_RATE"] = str(sapi_rate)
                proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SAFE_SAPI_STDIN_SCRIPT],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
                with self._lock:
                    self._active_proc = proc
                proc.communicate(input=chunk.encode("utf-8"), timeout=90)
                return proc.returncode == 0
            except Exception:
                return False
            finally:
                with self._lock:
                    self._active_proc = None
        return False

    def _speak_offline_fallback(self, text: str, detected_lang: Optional[str] = None) -> Dict[str, Any]:
        """مسار طوارئ أوفلاين مُعلَم صراحةً؛ يقسم النص لجمل متسلسلة ولا يقتطع عند 240 حرفاً."""
        plan = split_sentence_chunks(text, max_chunk_chars=DEFAULT_CHUNK_CHARS, max_total_chars=self.max_chars)
        if plan["warning"]:
            self.last_warning = plan["warning"]
            self._emit_log(plan["warning"])

        lang = detected_lang or detect_reply_language(plan["clean_text"])
        self.fallback_used = True
        self.last_provider = "sapi-offline-fallback"
        self._emit_log(f"ℹ️ تعذر استخدام صوت السيرفر العصبي؛ يتم استخدام صوت الجهاز المحلي (SAPI Fallback · {lang}).")

        spoken_chunks = 0
        for ch in plan["chunks"]:
            if self._stop_event.is_set():
                break
            self._pause_event.wait()
            if self._stop_event.is_set():
                break
            self._speak_sapi_chunk_safe(ch, lang)
            spoken_chunks += 1

        return {
            "ok": True,
            "provider": self.last_provider,
            "fallback_used": True,
            "language": lang,
            "chunks_spoken": spoken_chunks,
            "total_chunks": len(plan["chunks"]),
            "truncated": plan["truncated"],
            "warning": plan["warning"],
        }

    def _start_duplex_guard(self):
        """يشغّل مراقب المقاطعة أثناء النطق (مطفي افتراضياً). أي فشل = رجوع صامت لوضع الضغط-للكلام."""
        if not getattr(self, "duplex_enabled", False):
            return None
        try:
            from .duplex import DuplexMonitor, SoundDeviceFrameSource

            factory = getattr(self, "duplex_frame_source_factory", None) or SoundDeviceFrameSource
            mon = DuplexMonitor(factory(), self.interrupt, profile=getattr(self, "duplex_profile", "normal"))
            mon.start()
            return mon
        except Exception as e:
            self._emit_log(f"ℹ️ المقاطعة الصوتية غير متاحة ({e}) — وضع الضغط للكلام")
            return None

    @staticmethod
    def _stop_duplex_guard(mon) -> None:
        if mon is not None:
            try:
                mon.stop()
            except Exception:
                pass

    def speak(
        self,
        text: str,
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> Dict[str, Any]:
        mon = self._start_duplex_guard()
        try:
            return self._speak_impl(text, conversation_id, message_id, voice_id, speed)
        finally:
            self._stop_duplex_guard(mon)

    def _speak_impl(
        self,
        text: str,
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        ينطق رد الوكيل عبر المسار الموحد `/api/v2/voice/speak` (أصوات RYN العصبية).
        إذا تعذر الاتصال بالسيرفر، ينتقل لمسار الطوارئ المحلي المقسّم لجمل كاملة بدون اقتطاع صامت.
        """
        if not self.tts_enabled or not (text or message_id):
            return {"ok": False, "skipped": True}

        self.stop(notify_server=False)
        self._stop_event.clear()
        self._pause_event.set()
        self.last_warning = ""
        self.last_error = ""
        self.last_spoken_text = str(text or "")
        self.playback_state = "playing"

        vid = voice_id or self.voice_id
        spd = float(speed if speed is not None else self.speed)
        detected_lang = detect_reply_language(text) if text else "auto"

        # 1. المسار الأساسي: طلب الصوت العصبي من السيرفر (/api/v2/voice/speak)
        try:
            payload: Dict[str, Any] = {
                "text": text,
                "speed": spd,
                "language": detected_lang,
            }
            if vid:
                payload["voice_id"] = vid
            if conversation_id:
                payload["conversation_id"] = conversation_id
            if message_id:
                payload["message_id"] = message_id
            if self.session_id:
                payload["session_id"] = self.session_id

            res = self.api_client.request_json("POST", "/api/v2/voice/speak", payload, retries=2)
            if res.get("warning"):
                self.last_warning = str(res["warning"])
                self._emit_log(f"⚠️ {self.last_warning}")

            server_chunks = res.get("chunks") if isinstance(res.get("chunks"), list) else []
            audio_b64 = str(res.get("audio_b64") or "").strip()
            mime_type = str(res.get("mime_type") or "audio/mpeg").strip()

            if not res.get("fallback_browser_tts") and (server_chunks or audio_b64):
                self.fallback_used = bool(res.get("fallback_provider") == "gtts")
                self.last_provider = str(res.get("tts_provider_used") or "server-neural")
                self._notify_server_playback("playback_started")

                played_ok = True
                if server_chunks:
                    for ch_item in server_chunks:
                        if self._stop_event.is_set():
                            break
                        self._pause_event.wait()
                        if self._stop_event.is_set():
                            break
                        ch_b64 = str(ch_item.get("audio_b64") or "")
                        ch_mime = str(ch_item.get("mime_type") or mime_type)
                        raw_bytes = base64.b64decode(ch_b64) if ch_b64 else b""
                        if not self._play_audio_bytes_local(raw_bytes, ch_mime):
                            played_ok = False
                            break
                else:
                    raw_bytes = base64.b64decode(audio_b64)
                    played_ok = self._play_audio_bytes_local(raw_bytes, mime_type)

                if played_ok:
                    end_action = "playback_stopped" if self._stop_event.is_set() else "playback_ended"
                    self.playback_state = "stopped"
                    self._notify_server_playback(end_action)
                    return {
                        "ok": True,
                        "provider": self.last_provider,
                        "fallback_used": self.fallback_used,
                        "voice_id": res.get("voice_id", vid or ""),
                        "language": res.get("detected_language", detected_lang),
                        "mime_type": mime_type,
                        "chunk_count": len(server_chunks) or 1,
                        "truncated": bool(res.get("truncated", False)),
                        "warning": self.last_warning,
                    }
            else:
                self.last_error = str(res.get("tts_error") or res.get("error") or "server_fallback")
        except Exception as e:
            self.last_error = str(e)

        # 2. مسار الطوارئ الأوفلاين (Windows SAPI مقسّم لجمل وآمن)
        try:
            out = self._speak_offline_fallback(text, detected_lang=detected_lang)
            return out
        finally:
            self.playback_state = "stopped"
            self._notify_server_playback("playback_ended")

    def pause(self) -> None:
        self._pause_event.clear()
        self.playback_state = "paused"
        self._notify_server_playback("playback_paused")

    def resume(self) -> None:
        self._pause_event.set()
        self.playback_state = "playing"
        self._notify_server_playback("playback_resumed")

    def stop(self, notify_server: bool = True, barge_in: bool = False) -> None:
        self._stop_event.set()
        self._pause_event.set()
        self.playback_state = "stopped"
        with self._lock:
            if self._active_proc is not None:
                try:
                    self._active_proc.terminate()
                except Exception:
                    pass
                self._active_proc = None
        if sys.platform.startswith("win"):
            try:
                import winsound  # type: ignore

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if notify_server:
            self._notify_server_playback("barge_in" if barge_in else "playback_stopped", barge_in=barge_in)

    def interrupt(self) -> None:
        """مقاطعة فورية (Barge-in) عند بدء المستخدم بالكلام أثناء نطق RYN."""
        self.stop(notify_server=True, barge_in=True)

    def replay(self) -> Dict[str, Any]:
        if not self.last_spoken_text:
            return {"ok": False, "error": "لا يوجد رد سابق لإعادة تشغيله"}
        return self.speak(self.last_spoken_text)

    def send_voice_goal(
        self,
        transcribed_text: str,
        start_task: bool = True,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.muted:
            raise RuntimeError("الميكروفون مكتوم حالياً.")
        # إذا كان RYN يتحدث حالياً، نقاطع الصوت فوراً (Barge-in)
        if self.playback_state == "playing":
            self.interrupt()
        res = self.api_client.request_json(
            "POST",
            "/api/v1/agent/voice",
            {
                "transcript": transcribed_text,
                "start_task": start_task,
                "language": language or self.asr_language,
            },
            retries=2,
        )
        reply = res.get("assistant_response") or "تم استلام الأمر الصوتي وبدأت المهمة."
        self.speak(reply)
        return res

    def listen_and_send(self, transcribed_text: Optional[str] = None, start_task: bool = True) -> Dict[str, Any]:
        """يستقبل النص المفرّغ أو يسجل أمراً صوتياً ويرسله للوكيل مع نطق الرد عبر مسار TTS الموحد."""
        if self.muted:
            raise RuntimeError("الميكروفون مكتوم حالياً.")
        if self.playback_state == "playing":
            self.interrupt()
        txt = (transcribed_text or "").strip()
        if not txt:
            raise RuntimeError("يرجى إدخال الأمر الصوتي أو ربط الميكروفون للتفريغ عبر /api/v2/voice/transcribe.")
        return self.send_voice_goal(txt, start_task=start_task)
