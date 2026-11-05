"""اختبار شامل لحلقة عميل ويندوز مقابل نواة الوكيل (بدون شبكة ولا شاشة)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ryn-desktop"))

import ryn_agent  # noqa: E402
from app import task_loop  # noqa: E402
from app.action_executor import StrictActionDispatcher  # noqa: E402
from app.api_client import RynAgentApiClient  # noqa: E402
from app.safety import LocalSafetyGate, BLOCKED_LOCAL_RE  # noqa: E402


class InProcessApi(RynAgentApiClient):
    """يوجّه نفس مسارات HTTP مباشرة إلى النواة بنفس منطق webapp.py."""

    def __init__(self, core):
        super().__init__("http://local", None)
        self.core = core
        self.last_step_payload = None

    def request_json(self, method, path, payload=None, retries=3):
        p = payload or {}
        c = self.core
        if path.endswith("/pair/complete"):
            r = c.complete_pairing(p["code"], p.get("device_name", "t"), p.get("platform", "windows"))
            self.device_token = r["device_token"]
            return r
        if path.endswith("/tasks"):
            dev = c.authenticate_device(self.device_token)
            return c.create_task(dev["user_id"], p["goal"], device_id=dev["id"])
        if path.endswith("/step"):
            self.last_step_payload = p
            return c.propose_next_step(
                self.device_token, task_id=p["task_id"], sequence=int(p["sequence"]),
                screenshot_b64=p.get("screenshot_b64", ""), screen_width=int(p["screen_width"]),
                screen_height=int(p["screen_height"]), observation=p.get("observation", ""),
            )
        if path.endswith("/result"):
            return c.report_step_result(
                self.device_token, p["task_id"], p["command_id"], int(p["sequence"]),
                status=p["status"], error=p.get("error", ""),
                verification_note=p.get("verification_note", ""), screenshot_b64=p.get("screenshot", ""),
            )
        if path.endswith("/stop"):
            dev = c.authenticate_device(self.device_token)
            return c.stop_task(dev["user_id"], p.get("task_id", ""))
        raise AssertionError(path)


def fake_shot(*_a, **_k):
    # شاشة حقيقية 2560x1440 والصورة المرسلة 1280x720 (نسبة 2x)
    return {"screenshot_b64": "", "image_width": 1280, "image_height": 720,
            "screen_width": 2560, "screen_height": 1440, "bytes_len": 0}


class TestDesktopRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = ryn_agent.RynAgentCoreManager(db_path=os.path.join(self.tmp.name, "t.db"))
        code = self.core.start_pairing("u1")["pairing_code"]
        self.api = InProcessApi(self.core)
        self.api.complete_pairing(code, "pc")
        self.logs = []
        self.gate = LocalSafetyGate(confirm_callback=lambda cmd: True)
        self.runner = task_loop.DesktopTaskRunner(
            self.api, self.gate, StrictActionDispatcher(dry_run=True), log_cb=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()

    def test_type_then_done(self):
        with mock.patch.object(task_loop, "capture_screen_compressed", fake_shot):
            tid = self.api.create_task("Open Notepad and type Hello")["task_id"]
            res = self.runner.run_task_to_completion(tid)
        self.assertEqual(res["status"], "COMPLETED", self.logs)

    def test_click_is_scaled_to_real_screen(self):
        seen = []
        orig = StrictActionDispatcher.execute

        def spy(self_, cmd):
            if cmd["action"] == "click":
                seen.append(cmd["coordinates"])
            return orig(self_, cmd)
        with mock.patch.object(task_loop, "capture_screen_compressed", fake_shot), \
                mock.patch.object(StrictActionDispatcher, "execute", spy):
            tid = self.api.create_task("click the button")["task_id"]
            res = self.runner.run_task_to_completion(tid)
        self.assertEqual(res["status"], "COMPLETED", self.logs)
        # الخطوة المقترحة (640,360) في فضاء الصورة -> (1280,720) على الشاشة الحقيقية
        self.assertEqual(seen, [{"x": 1280, "y": 720}])

    def test_step_sends_image_dims_and_vision_flag(self):
        with mock.patch.object(task_loop, "capture_screen_compressed", lambda *a, **k: {**fake_shot(), "screenshot_b64": "QUJD"}):
            tid = self.api.create_task("click here")["task_id"]
            self.runner.run_task_to_completion(tid)
        self.assertEqual(self.api.last_step_payload["screen_width"], 1280)
        self.assertTrue(self.api.last_step_payload["use_vision_model"])

    def test_idle_when_no_task(self):
        with mock.patch.object(task_loop, "capture_screen_compressed", fake_shot):
            res = self.runner.run_task_to_completion("task_missing_but_none_active")
        self.assertIn(res["status"], ("IDLE", "PAUSED_OFFLINE", "CANCELLED", "FAILED"))

    def test_emergency_stop_cancels(self):
        with mock.patch.object(task_loop, "capture_screen_compressed", fake_shot):
            tid = self.api.create_task("click here")["task_id"]
            orig_reset = self.gate.reset_emergency_stop
            self.gate.reset_emergency_stop = lambda: (orig_reset(), self.gate.trigger_emergency_stop())
            res = self.runner.run_task_to_completion(tid)
        self.assertEqual(res["status"], "CANCELLED")


class TestRegexFixes(unittest.TestCase):
    def test_format_drive_blocked_on_both_sides(self):
        self.assertTrue(BLOCKED_LOCAL_RE.search("format c: now"))
        self.assertTrue(ryn_agent.BLOCKED_CONTENT_RE.search("please format c: now"))
        self.assertIsNone(BLOCKED_LOCAL_RE.search("format a table in Word"))

    def test_local_block_list_actually_matches(self):
        # كان الملف يحتوي رموز Backspace بدل \\b فلم يكن يمنع أي شي
        for bad in ("rm -rf /", "del /f x.txt", "shutdown now", "enter password here", "open regedit"):
            self.assertTrue(BLOCKED_LOCAL_RE.search(bad), bad)
        self.assertIsNone(BLOCKED_LOCAL_RE.search("type hello world"))

    def test_back_key_allowed(self):
        self.assertIn("back", ryn_agent.ALLOWED_KEYS)

    def test_stop_cancels_pending_approval_commands(self):
        with tempfile.TemporaryDirectory() as d:
            core = ryn_agent.RynAgentCoreManager(db_path=os.path.join(d, "x.db"))
            code = core.start_pairing("u2")["pairing_code"]
            tok = core.complete_pairing(code, "pc")["device_token"]
            dev = core.authenticate_device(tok)
            t = core.create_task("u2", "delete the file", device_id=dev["id"])
            core.propose_next_step(tok, t["task_id"], 1, "", 1000, 800, "")
            core.stop_task("u2", t["task_id"])
            with core._conn() as conn:
                st = [r[0] for r in conn.execute("SELECT approval_status FROM commands WHERE task_id=?", (t["task_id"],))]
            self.assertNotIn("pending_approval", st)


class TestChatCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = ryn_agent.RynAgentCoreManager(db_path=os.path.join(self.tmp.name, "c.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _pair(self, uid="u9", platform="android"):
        code = self.core.start_pairing(uid)["pairing_code"]
        return self.core.complete_pairing(code, "phone", platform)["device_token"]

    def test_no_device_means_normal_chat(self):
        self.assertIsNone(self.core.handle_chat_agent_command("u9", "افتح واتساب وارسل لأمي مرحبا"))

    def test_natural_command_creates_pending_task(self):
        tok = self._pair()
        self.core.authenticate_device(tok)  # يحدّث last_seen فيصير متصل
        reply = self.core.handle_chat_agent_command("u9", "افتح واتساب وارسل لأمي مرحبا")
        self.assertIn("🚀", reply)
        pend = self.core.get_pending_task(tok)
        self.assertTrue(pend["pending"])
        self.assertIn("واتساب", pend["goal"])

    def test_regular_chat_not_hijacked(self):
        tok = self._pair()
        self.core.authenticate_device(tok)
        for txt in ("ارسل لي الكود بالتفصيل", "افتح ملف الاختبار واشرحلي", "اكتب لي قصيدة", "شغل هالكود بايثون"):
            self.assertIsNone(self.core.handle_chat_agent_command("u9", txt), txt)

    def test_offline_device_message(self):
        tok = self._pair()
        with self.core._conn() as conn:
            conn.execute("UPDATE devices SET last_seen_at = ?", (1.0,))
        reply = self.core.handle_chat_agent_command("u9", "افتح تطبيق الصور")
        self.assertIn("مو متصل", reply)
        self.assertFalse(self.core.get_pending_task(tok)["pending"])

    def test_pending_cleared_after_step(self):
        tok = self._pair()
        self.core.authenticate_device(tok)
        self.core.handle_chat_agent_command("u9", "افتح تطبيق الصور")
        pend = self.core.get_pending_task(tok)
        self.core.propose_next_step(tok, pend["task_id"], 1, "", 1080, 2400, "")
        self.assertFalse(self.core.get_pending_task(tok)["pending"])


if __name__ == "__main__":
    unittest.main()
