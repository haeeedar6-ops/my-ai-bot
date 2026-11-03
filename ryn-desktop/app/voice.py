"""
voice.py — وحدة Push-to-Talk الصوتية لعميل سطح المكتب (Section 10).
يسجل الصوت فقط أثناء ضغط المستخدم على زر الميكروفون ولا يعمل باستماع مستمر في الخلفية.
"""
from typing import Any, Dict
from .api_client import RynAgentApiClient


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
