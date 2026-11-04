"""
task_loop.py — حلقة التنفيذ والتحقق خطوة بخطوة (Capture -> Propose -> Validate -> Execute -> Verify — Section 7 & 8).
"""
import time
from typing import Any, Callable, Dict, Optional

from .action_executor import StrictActionDispatcher
from .api_client import RynAgentApiClient
from .capture import capture_screen_compressed
from .safety import LocalSafetyGate

TERMINAL = ("COMPLETED", "CANCELLED", "FAILED")


def scale_to_screen(cmd: Dict[str, Any], shot: Dict[str, Any]) -> Dict[str, Any]:
    """النموذج يرى صورة مصغّرة؛ نحوّل إحداثياته إلى إحداثيات الشاشة الحقيقية قبل التحقق والتنفيذ."""
    coords = cmd.get("coordinates")
    if not isinstance(coords, dict) or "x" not in coords or "y" not in coords:
        return cmd
    iw = max(1, int(shot.get("image_width") or shot.get("screen_width") or 1))
    ih = max(1, int(shot.get("image_height") or shot.get("screen_height") or 1))
    sx = int(shot.get("screen_width") or iw) / float(iw)
    sy = int(shot.get("screen_height") or ih) / float(ih)
    out = dict(cmd)
    out["coordinates"] = {"x": int(round(float(coords["x"]) * sx)), "y": int(round(float(coords["y"]) * sy))}
    return out


class DesktopTaskRunner:
    def __init__(
        self,
        api_client: RynAgentApiClient,
        safety_gate: LocalSafetyGate,
        dispatcher: StrictActionDispatcher,
        log_cb: Optional[Callable[[str], None]] = None,
        max_width: int = 1280,
        jpeg_quality: int = 78,
    ):
        self.api_client = api_client
        self.safety_gate = safety_gate
        self.dispatcher = dispatcher
        self.log_cb = log_cb or (lambda msg: None)
        self.max_width = max_width
        self.jpeg_quality = jpeg_quality

    def _shot(self) -> Dict[str, Any]:
        return capture_screen_compressed(self.max_width, self.jpeg_quality)

    def run_task_to_completion(self, task_id: str, max_steps: int = 15, approval_wait_s: int = 120) -> Dict[str, Any]:
        self.safety_gate.reset_emergency_stop()
        sequence = 1
        last_obs = "Initial desktop screen"
        steps = 0
        waited_since = None

        while steps < max_steps:
            if self.safety_gate.is_stopped:
                self.log_cb("🛑 تم إيقاف المهمة فوراً بواسطة مفتاح الطوارئ.")
                try:
                    self.api_client.stop_task(task_id)
                except Exception:
                    pass
                return {"status": "CANCELLED", "sequence": sequence}

            shot_before = self._shot()
            if shot_before.get("error"):
                self.log_cb(f"⚠️ تعذّر التقاط الشاشة: {shot_before['error']}")
            try:
                step_resp = self.api_client.request_next_step(task_id, sequence, shot_before, observation=last_obs)
            except Exception as net_e:
                self.log_cb(f"⚠️ توقف آمن بسبب خطأ بالشبكة/السيرفر (لا يُعاد تشغيل أوامر قديمة): {net_e}")
                return {"status": "PAUSED_OFFLINE", "error": str(net_e), "sequence": sequence}

            st = step_resp.get("status", "")
            if st == "idle":
                self.log_cb("ℹ️ لا توجد مهمة نشطة لهذا الجهاز.")
                return {"status": "IDLE", "sequence": sequence}
            if st == "WAITING_FOR_APPROVAL":
                # الانتظار لا يُحسب من عدد الخطوات
                waited_since = waited_since or time.time()
                if time.time() - waited_since > approval_wait_s:
                    self.log_cb("⌛ انتهت مهلة انتظار الموافقة.")
                    return {"status": "APPROVAL_TIMEOUT", "sequence": sequence}
                self.log_cb("⏳ بانتظار موافقة المستخدم على العملية من لوحة التحكم...")
                time.sleep(1.5)
                continue
            waited_since = None
            if st in TERMINAL:
                self.log_cb(f"✅ انتهت المهمة بحالة: {st}")
                return {"status": st, "sequence": sequence}
            if not step_resp.get("command_id"):
                # مثال: NEEDS_USER_INPUT بعد 3 إخفاقات — يرد سؤال بدون أمر قابل للتنفيذ
                self.log_cb(f"❓ المهمة تحتاج تدخلك ({st}): {step_resp.get('question') or step_resp.get('message') or ''}")
                return {"status": st or "NEEDS_USER_INPUT", "sequence": sequence}

            steps += 1
            try:
                cmd = scale_to_screen(step_resp, shot_before)
                validated_cmd = self.safety_gate.validate_incoming_command(
                    cmd,
                    screen_width=int(shot_before["screen_width"]),
                    screen_height=int(shot_before["screen_height"]),
                )
                exec_res = self.dispatcher.execute(validated_cmd)
                time.sleep(0.4)  # نعطي الواجهة وقتاً لتتحدّث قبل لقطة التحقق
                shot_after = self._shot()
                rep = self.api_client.report_result(
                    task_id=task_id,
                    command_id=validated_cmd["command_id"],
                    sequence=sequence,
                    status="succeeded",
                    verification_note=exec_res["verification_note"],
                    shot=shot_after,
                )
                last_obs = exec_res["verification_note"]
                self.log_cb(f"✔ خطوة {sequence} ({validated_cmd['action']}): {last_obs}")
                if validated_cmd["action"] == "ask_user":
                    self.log_cb(f"❓ سؤال من الوكيل: {validated_cmd.get('question') or validated_cmd.get('reason') or ''}")
                    return {"status": "NEEDS_USER_INPUT", "sequence": sequence}
                if validated_cmd["action"] == "done" or rep.get("task_status") == "COMPLETED":
                    return {"status": rep.get("task_status", "COMPLETED"), "sequence": sequence}
                sequence += 1
            except Exception as exec_e:
                self.log_cb(f"⛔ رُفضت أو فشلت الخطوة {sequence}: {exec_e}")
                try:
                    self.api_client.report_result(
                        task_id=task_id,
                        command_id=step_resp["command_id"],
                        sequence=sequence,
                        status="failed",
                        error=str(exec_e),
                    )
                except Exception:
                    pass
                return {"status": "FAILED", "error": str(exec_e), "sequence": sequence}

        return {"status": "MAX_STEPS_REACHED", "sequence": sequence}
