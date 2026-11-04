"""
api_client.py — عميل الاتصال الآمن بـ RYN Core API مع مهلة زمنية وإعادة محاولة بالتراجع الأسي (Section 8 & 11).
"""
import time
from typing import Any, Dict, Optional
import requests


class RynAgentApiClient:
    def __init__(self, server_url: str, device_token: Optional[str] = None, timeout: int = 20):
        self.server_url = server_url.rstrip("/")
        self.device_token = device_token or ""
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "X-Requested-With": "astra"}
        if self.device_token:
            h["Authorization"] = f"Bearer {self.device_token}"
        return h

    def request_json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, retries: int = 3) -> Dict[str, Any]:
        url = self.server_url + path
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                r = requests.request(
                    method=method.upper(),
                    url=url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                try:
                    data = r.json() if r.content else {}
                except ValueError:
                    data = {}
                if r.status_code >= 500:
                    # أخطاء السيرفر المؤقتة (مثل 502 أثناء استيقاظ Render) نعيد المحاولة عليها
                    raise requests.ConnectionError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise RuntimeError((data or {}).get("error") or f"HTTP {r.status_code}")
                return data
            except requests.RequestException as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(0.8 * (2 ** attempt))
                    continue
                raise RuntimeError(f"انقطع الاتصال بالسيرفر (Offline): {e}")
        raise last_err or RuntimeError("فشل الاتصال بالسيرفر")

    def complete_pairing(self, code: str, device_name: str, platform: str = "windows") -> Dict[str, Any]:
        res = self.request_json(
            "POST",
            "/api/v1/agent/pair/complete",
            {"code": code, "device_name": device_name, "platform": platform},
            retries=2,
        )
        if res.get("device_token"):
            self.device_token = res["device_token"]
        return res

    def create_task(self, goal: str) -> Dict[str, Any]:
        return self.request_json("POST", "/api/v1/agent/tasks", {"goal": goal}, retries=2)

    def request_next_step(self, task_id: str, sequence: int, shot: Dict[str, Any], observation: str = "") -> Dict[str, Any]:
        has_img = bool(shot.get("screenshot_b64"))
        return self.request_json(
            "POST",
            "/api/v1/agent/step",
            {
                "task_id": task_id,
                "sequence": sequence,
                "screenshot_b64": shot.get("screenshot_b64", ""),
                # نرسل أبعاد الصورة التي يراها النموذج فعلاً؛ العميل يحوّل الإحداثيات للشاشة الحقيقية
                "screen_width": shot.get("image_width", shot.get("screen_width", 1920)),
                "screen_height": shot.get("image_height", shot.get("screen_height", 1080)),
                "observation": observation,
                # بدونها السيرفر يستعمل المخطط الاحتياطي البسيط ولا يستعمل نموذج الرؤية أبداً
                "use_vision_model": has_img,
            },
            retries=3,
        )

    def report_result(self, task_id: str, command_id: str, sequence: int, status: str, verification_note: str = "", error: str = "", shot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.request_json(
            "POST",
            "/api/v1/agent/result",
            {
                "task_id": task_id,
                "command_id": command_id,
                "sequence": sequence,
                "status": status,
                "verification_note": verification_note,
                "error": error,
                "screenshot": (shot or {}).get("screenshot_b64", ""),
            },
            retries=2,
        )

    def stop_task(self, task_id: str = "") -> Dict[str, Any]:
        return self.request_json("POST", "/api/v1/agent/stop", {"task_id": task_id}, retries=1)
