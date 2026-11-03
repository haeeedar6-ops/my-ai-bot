"""
voice.py — وحدة التحكم الصوتي والرد المسموع لعميل سطح المكتب (Push-to-Talk + TTS — Section 10).
"""
import subprocess
import sys
from typing import Any, Dict
from .api_client import RynAgentApiClient


class PushToTalkController:
    def __init__(self, api_client: RynAgentApiClient):
        self.api_client = api_client
        self.is_recording = False
        self.muted = False
        self.tts_enabled = True

    def speak(self, text: str) -> None:
        """ينطق رد الوكيل بصوت مسموع على ويندوز عبر SAPI.SpVoice بدون الحاجة لمكتبات خارجية ثقيلة."""
        if not self.tts_enabled or not text:
            return
        clean = str(text).replace('"', " ").replace("'", " ").strip()[:240]
        if sys.platform.startswith("win"):
            try:
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", f"(New-Object -ComObject SAPI.SpVoice).Speak('{clean}')"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                _ = None

    def send_voice_goal(self, transcribed_text: str, start_task: bool = True) -> Dict[str, Any]:
        if self.muted:
            raise RuntimeError("الميكروفون مكتوم حالياً.")
        res = self.api_client.request_json(
            "POST",
            "/api/v1/agent/voice",
            {"transcript": transcribed_text, "start_task": start_task},
            retries=2,
        )
        reply = res.get("assistant_response") or "تم استلام الأمر الصوتي وبدأت المهمة."
        self.speak(reply)
        return res
