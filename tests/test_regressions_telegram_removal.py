import os
import re
import struct
import unittest
import zlib
from pathlib import Path

import ryn_guard as G

ROOT = Path(__file__).resolve().parent.parent


def _make_png(w: int, h: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    c_ihdr = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    raw = b"\x00" + b"\x00\x00\x00" * w
    idat = zlib.compress(raw * min(h, 4))
    c_idat = struct.pack(">I", len(idat)) + b"IDAT" + idat + struct.pack(">I", zlib.crc32(b"IDAT" + idat) & 0xFFFFFFFF)
    c_iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + c_ihdr + c_idat + c_iend


class TestTelegramGithubAndAgentsRemoved(unittest.TestCase):
    def test_bot_py_has_no_telegram_code(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        for forbidden in ("import telebot", "BOT_TOKEN", "ENABLE_TELEGRAM", "message_handler", "infinity_polling", "run_polling"):
            self.assertNotIn(forbidden, src)

    def test_requirements_have_no_telegram_but_have_pillow(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("telebot", req)
        self.assertNotIn("pytelegrambotapi", req)
        self.assertIn("pillow", req)

    def test_bot_py_still_mounts_web_at_import_time(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("def mount_web():", src)
        self.assertRegex(src, r"(?m)^mount_web\(\)\s*$")

    def test_no_nested_zip_in_project(self):
        zips = list(ROOT.glob("*.zip"))
        self.assertEqual(zips, [])

    def test_phone_pc_agents_and_github_editing_removed(self):
        self.assertFalse((ROOT / "ryn-android").exists())
        self.assertFalse((ROOT / "ryn-desktop").exists())
        self.assertFalse((ROOT / "ryn_agent.py").exists())
        self.assertFalse((ROOT / "code_intelligence").exists())
        self.assertFalse((ROOT / "github_intent_control.py").exists())
        stack_src = (ROOT / "ryn_ai_stack.py").read_text(encoding="utf-8")
        self.assertNotIn("class GitHubFileManager", stack_src)
        self.assertNotIn("def get_agent_core", stack_src)
        web_src = (ROOT / "webapp.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/github/", web_src)
        self.assertNotIn("/api/v1/agent/", web_src)
        self.assertNotIn('id="agentModal"', web_src)


class TestImageUploadGuard(unittest.TestCase):
    def test_accepts_normal_png(self):
        res = G.validate_image_upload(_make_png(64, 64), "ok.png")
        self.assertTrue(res["ok"])
        self.assertEqual(res["format"], "png")
        self.assertEqual((res["width"], res["height"]), (64, 64))

    def test_rejects_pixel_bomb(self):
        res = G.validate_image_upload(_make_png(10000, 10000), "bomb.png")
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "image_huge")

    def test_rejects_empty_oversized_and_non_image(self):
        self.assertEqual(G.validate_image_upload(b"", "a.png")["code"], "empty")
        self.assertEqual(G.validate_image_upload(b"x" * 100, "a.png", max_bytes=50)["code"], "too_big")
        self.assertEqual(G.validate_image_upload(b"<html>not an image</html>", "a.png")["code"], "not_image")

    def test_rejects_png_without_ihdr(self):
        bad = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dXXXX" + b"0" * 30
        self.assertEqual(G.validate_image_upload(bad, "bad.png")["code"], "bad_image")

    def test_unknown_jpeg_dimensions_are_accepted(self):
        minimal_jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
        res = G.validate_image_upload(minimal_jpg, "tiny.jpg")
        self.assertTrue(res["ok"])
        self.assertEqual(res["format"], "jpeg")

    def test_webp_vp8x_dimensions(self):
        payload = b"WEBP" + b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (319).to_bytes(3, "little") + (239).to_bytes(3, "little")
        riff = b"RIFF" + struct.pack("<I", len(payload)) + payload
        res = G.validate_image_upload(riff, "t.webp")
        self.assertTrue(res["ok"])
        self.assertEqual((res["width"], res["height"]), (320, 240))


if __name__ == "__main__":
    unittest.main()
