"""
voice.py — وحدة الصوت لعميل سطح المكتب (Section 10).
تسجّل فقط عندما يضغط المستخدم الزر (جملة واحدة)، ولا يوجد استماع مستمر في الخلفية.
تحتاج: pip install SpeechRecognition PyAudio (اختيارية).
"""
from typing import Any, Dict

from .api_client import RynAgentApiClient


def listen_once(language: str = "ar-SA", timeout: int = 6, phrase_limit: int = 10) -> str:
    try:
        import speech_recognition as sr
    except Exception:
        raise RuntimeError("ثبّت: pip install SpeechRecognition PyAudio")
    rec = sr.Recognizer()
    with sr.Microphone() as mic:
        rec.adjust_for_ambient_noise(mic, duration=0.3)
        audio = rec.listen(mic, timeout=timeout, phrase_time_limit=phrase_limit)
    try:
        return rec.recognize_google(audio, language=language).strip()
    except sr.UnknownValueError:
        raise RuntimeError("ما فهمت الصوت، جرّب مرة ثانية")
    except sr.RequestError as e:
        raise RuntimeError(f"خدمة التعرف على الصوت غير متاحة: {e}")


class PushToTalkController:
    def __init__(self, api_client: RynAgentApiClient):
        self.api_client = api_client
        self.is_recording = False
        self.muted = False

    def send_voice_goal(self, transcribed_text: str, start_task: bool = True) -> Dict[str, Any]:
        if self.muted:
            raise RuntimeError("الميكروفون مكتوم حالياً.")
        return self.api_client.request_json(
            "POST",
            "/api/v1/agent/voice",
            {"transcript": transcribed_text, "start_task": start_task},
            retries=2,
        )

    def listen_and_send(self) -> Dict[str, Any]:
        if self.muted:
            raise RuntimeError("الميكروفون مكتوم حالياً.")
        self.is_recording = True
        try:
            text = listen_once()
        finally:
            self.is_recording = False
        return self.send_voice_goal(text)
