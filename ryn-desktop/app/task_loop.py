"""
task_loop.py — حلقة التنفيذ والتحقق خطوة بخطوة (Capture -> Propose -> Validate -> Execute -> Verify — Section 7 & 8).
"""
import time
from typing import Any, Callable, Dict, Optional

from .action_executor import StrictActionDispatcher
from .api_client import RynAgentApiClient
from .capture import capture_screen_compressed
from .safety import LocalSafetyGate


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

    def run_task_to_completion(self, task_id: str, max_steps: int = 15) -> Dict[str, Any]:
        self.safety_gate.reset_emergency_stop()
        sequence = 1
        last_obs = "Initial desktop screen"

        for _ in range(max_steps):
            if self.safety_gate.is_stopped:
                self.log_cb("🛑 تم إيقاف المهمة فوراً بواسطة مفتاح الطوارئ.")
                self.api_client.stop_task(task_id)
                return {"status": "CANCELLED", "sequence": sequence}

            shot_before = capture_screen_compressed(max_width=self.max_width, quality=self.jpeg_quality)
            try:
                step_resp = self.api_client.request_next_step(task_id, sequence, shot_before, observation=last_obs)
            except Exception as net_e:
                self.log_cb(f"⚠️ توقف آمن بسبب انقطاع الشبكة (لا يُعاد تشغيل أوامر قديمة): {net_e}")
                return {"status": "PAUSED_OFFLINE", "error": str(net_e), "sequence": sequence}

            st = step_resp.get("status", "")
            if st == "WAITING_FOR_APPROVAL":
                self.log_cb("⏳ بانتظار موافقة المستخدم على العملية من لوحة التحكم...")
                time.sleep(1.5)
                continue
            if st in ("COMPLETED", "CANCELLED", "FAILED", "IDLE"):
                self.log_cb(f"✅ انتهت المهمة بحالة: {st}")
                return {"status": st, "sequence": sequence}

            try:
                img_w = int(shot_before.get("image_width") or shot_before.get("screen_width") or 1920)
                img_h = int(shot_before.get("image_height") or shot_before.get("screen_height") or 1080)
                real_w = int(shot_before.get("screen_width") or img_w)
                real_h = int(shot_before.get("screen_height") or img_h)

                cmd_copy = dict(step_resp)
                if cmd_copy.get("action") in ("click", "double_click") and isinstance(cmd_copy.get("coordinates"), dict):
                    cx = float(cmd_copy["coordinates"].get("x", 0))
                    cy = float(cmd_copy["coordinates"].get("y", 0))
                    sx = int(round(cx * (real_w / float(max(1, img_w)))))
                    sy = int(round(cy * (real_h / float(max(1, img_h)))))
                    cmd_copy["coordinates"] = {"x": sx, "y": sy}

                validated_cmd = self.safety_gate.validate_incoming_command(
                    cmd_copy,
                    screen_width=real_w,
                    screen_height=real_h,
                )
                exec_res = self.dispatcher.execute(validated_cmd)
                shot_after = capture_screen_compressed(max_width=self.max_width, quality=self.jpeg_quality)
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
                if validated_cmd["action"] in ("done", "ask_user") or rep.get("task_status") == "COMPLETED":
                    return {"status": rep.get("task_status", "COMPLETED"), "sequence": sequence}
                sequence += 1
            except Exception as exec_e:
                self.log_cb(f"⛔ رُفضت أو فشلت الخطوة {sequence}: {exec_e}")
                if step_resp.get("command_id"):
                    try:
                        self.api_client.report_result(
                            task_id=task_id,
                            command_id=step_resp["command_id"],
                            sequence=sequence,
                            status="failed",
                            error=str(exec_e),
                        )
                    except Exception:
                        _ = None
                return {"status": "FAILED", "error": str(exec_e), "sequence": sequence}

        return {"status": "MAX_STEPS_REACHED", "sequence": sequence}
