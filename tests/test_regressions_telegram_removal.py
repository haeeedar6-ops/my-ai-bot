"""اختبارات انحدار: حذف تيليجرام + إصلاحات الويندوز/الأندرويد/الحارس (بدون شبكة ولا واجهة)."""
import ctypes
import os
import re
import struct
import sys
import unittest
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ryn-desktop"))

import ryn_guard as G  # noqa: E402
from app import action_executor as ae  # noqa: E402
from app import api_client as ac  # noqa: E402
from app import task_loop  # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _png(w, h):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class TestTelegramRemoved(unittest.TestCase):
    def test_bot_py_has_no_telegram_code(self):
        src = _read("bot.py")
        for word in ("telebot", "BOT_TOKEN", "ENABLE_TELEGRAM", "message_handler", "infinity_polling"):
            self.assertNotIn(word, src)

    def test_requirements_have_no_telegram_but_have_pillow(self):
        req = _read("requirements.txt").lower()
        self.assertNotIn("telegram", req)
        self.assertIn("pillow", req)  # الصور (upscale/outpaint/quality) بتعتمد عليه

    def test_bot_py_still_mounts_web_at_import_time(self):
        src = _read("bot.py")
        self.assertIn("def mount_web()", src)
        self.assertRegex(src, r"(?m)^mount_web\(\)\s*$")

    def test_workflow_is_in_dot_github_and_points_to_existing_script(self):
        self.assertFalse(os.path.isdir(os.path.join(ROOT, "github")))
        wf = _read(".github", "workflows", "main.yml")
        self.assertIn("run_desktop.py", wf)
        self.assertNotIn("pyperclip", wf)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "ryn-desktop", "run_desktop.py")))
        spec = _read("ryn-desktop", "build.spec")
        self.assertIn("run_desktop.py", spec)

    def test_no_nested_zip_in_project(self):
        zips = [f for f in os.listdir(ROOT) if f.lower().endswith(".zip")]
        self.assertEqual(zips, [])

    def test_desktop_requirements_ascii_and_complete(self):
        with open(os.path.join(ROOT, "ryn-desktop", "requirements.txt"), "rb") as f:
            raw = f.read()
        raw.decode("ascii")  # pip على ويندوز ممكن يقرأها بترميز محلي
        self.assertIn(b"pynput", raw)


class TestImageUploadGuard(unittest.TestCase):
    def test_accepts_normal_png(self):
        r = G.validate_image_upload(_png(320, 200))
        self.assertTrue(r["ok"])
        self.assertEqual((r["format"], r["width"], r["height"]), ("png", 320, 200))

    def test_rejects_pixel_bomb(self):
        r = G.validate_image_upload(_png(60000, 60000))
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "image_huge")

    def test_rejects_empty_oversized_and_non_image(self):
        self.assertEqual(G.validate_image_upload(b"")["code"], "empty")
        self.assertEqual(G.validate_image_upload(_png(8, 8), max_bytes=10)["code"], "too_big")
        self.assertEqual(G.validate_image_upload(b"hello, not an image at all!!")["code"], "not_image")

    def test_rejects_png_without_ihdr(self):
        self.assertEqual(G.validate_image_upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)["code"], "bad_image")

    def test_unknown_jpeg_dimensions_are_accepted(self):
        self.assertTrue(G.validate_image_upload(b"\xff\xd8\xff\xe0" + b"\x00" * 40)["ok"])

    def test_webp_vp8x_dimensions(self):
        body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (99).to_bytes(3, "little") + (49).to_bytes(3, "little")
        data = b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body
        r = G.validate_image_upload(data)
        self.assertTrue(r["ok"])
        self.assertEqual((r["width"], r["height"]), (100, 50))


class _Resp:
    def __init__(self, status, body=b"{}"):
        self.status_code = status
        self.content = body

    def json(self):
        import json
        return json.loads(self.content.decode("utf-8"))


class TestDesktopFixes(unittest.TestCase):
    def test_api_client_retries_5xx_then_succeeds(self):
        calls = []

        def fake_request(**kw):
            calls.append(kw["url"])
            return _Resp(503, b"<html>waking up</html>") if len(calls) < 3 else _Resp(200, b'{"ok": true}')

        with mock.patch.object(ac.requests, "request", side_effect=lambda **kw: fake_request(**kw)), \
                mock.patch.object(ac.time, "sleep"):
            out = ac.RynAgentApiClient("http://x").request_json("GET", "/api/v1/agent/pending")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 3)

    def test_api_client_4xx_is_not_retried(self):
        n = {"c": 0}

        def fake_request(**kw):
            n["c"] += 1
            return _Resp(403, b'{"error": "forbidden"}')

        with mock.patch.object(ac.requests, "request", side_effect=lambda **kw: fake_request(**kw)), \
                mock.patch.object(ac.time, "sleep"):
            with self.assertRaises(RuntimeError) as cm:
                ac.RynAgentApiClient("http://x").request_json("GET", "/p")
        self.assertIn("forbidden", str(cm.exception))
        self.assertEqual(n["c"], 1)

    def _run(self, step_resp):
        from app.safety import LocalSafetyGate
        api = mock.Mock()
        api.request_next_step.return_value = step_resp
        shot = {"screenshot_b64": "", "image_width": 100, "image_height": 100, "screen_width": 100, "screen_height": 100}
        runner = task_loop.DesktopTaskRunner(api, LocalSafetyGate(), mock.Mock())
        with mock.patch.object(task_loop, "capture_screen_compressed", return_value=shot):
            return runner.run_task_to_completion("t1", max_steps=3), api

    def test_lowercase_idle_ends_loop_cleanly(self):
        # السيرفر بيرجّع "idle" بحروف صغيرة: قبل الإصلاح كان ينعدّ فشل خطوة (FAILED)
        res, api = self._run({"status": "idle"})
        self.assertEqual(res["status"], "IDLE")
        api.report_result.assert_not_called()

    def test_needs_user_input_stops_without_executing(self):
        res, api = self._run({"status": "NEEDS_USER_INPUT", "action": "ask_user", "question": "أي ملف؟"})
        self.assertEqual(res["status"], "NEEDS_USER_INPUT")
        api.report_result.assert_not_called()

    def test_unicode_typing_structs_match_win64_layout(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(ae._INPUT), 40)
            self.assertEqual(ctypes.sizeof(ae._KEYBDINPUT), 24)

    def test_non_ascii_text_never_goes_to_pyautogui_write(self):
        fake = mock.Mock()
        d = ae.StrictActionDispatcher(dry_run=True)
        d._pyautogui = fake
        d.dry_run = False
        with mock.patch.object(ae, "type_unicode_text", return_value=True) as tut:
            d._do_type_text({"text": "مرحبا بالعالم"})
        fake.write.assert_not_called()
        tut.assert_called_once()
        with mock.patch.object(ae, "type_unicode_text", return_value=False):
            with self.assertRaises(RuntimeError):
                d._do_type_text({"text": "مرحبا"})
        d._do_type_text({"text": "hello"})
        fake.write.assert_called_once()

    def test_arabic_sensitive_words_are_blocked_locally(self):
        self.assertTrue(ae.__name__)  # الاستيراد سليم
        from app.safety import BLOCKED_LOCAL_RE
        self.assertTrue(BLOCKED_LOCAL_RE.search("اكتب كلمة المرور"))
        self.assertTrue(BLOCKED_LOCAL_RE.search("open password manager"))


class TestAndroidVoiceRegression(unittest.TestCase):
    def test_fallback_tts_is_skipped_when_server_audio_started(self):
        kt = _read("ryn-android", "app", "src", "main", "java", "com", "ryn", "agent", "VoiceController.kt")
        self.assertIn("var served = false", kt)
        self.assertIn("served = true", kt)
        self.assertRegex(kt, r"if \(!served && token == playbackToken\.get\(\)")


if __name__ == "__main__":
    unittest.main()
