# -*- coding: utf-8 -*-
"""اختبارات قفل الهوية، حفظ الصورة الأصلية بدون الكتابة فوقها، وعزل ذاكرة المستخدمين."""
import os
import sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import tempfile
import base64
from visual_brain import VisualBrain
from visual_brain.visual_memory import VisualMemoryStore

ORIG_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
EDIT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class TestIdentityAndMemory(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="ryn_vmem_test_")
        self.store = VisualMemoryStore(storage_dir=self.tmp_dir)
        self.brain = VisualBrain(memory_store=self.store)

    def test_immutable_original_retained_on_edit(self):
        plan = self.brain.build_plan(
            "غير الخلفية فقط لحديقة",
            user_id="user_imm",
            attached_images_bytes=[ORIG_PNG],
            available_providers={"instruct_pix2pix": True, "gemini": True},
        )
        from types import SimpleNamespace
        fake_part = SimpleNamespace(inline_data=SimpleNamespace(data=ORIG_PNG, mime_type="image/png"))
        res = self.brain.execute_plan(
            plan,
            user_id="user_imm",
            provider_runners={"instruct_pix2pix": lambda _p, _r=None: (EDIT_PNG, "image/png", "timbrooks/instruct-pix2pix")},
            ref_parts=[fake_part],
        )
        hist = self.store.list_history("user_imm")
        self.assertEqual(len(hist), 2)
        self.assertTrue(hist[0]["is_original_upload"])
        self.assertFalse(hist[1]["is_original_upload"])
        self.assertEqual(res["parent_version_id"], hist[0]["version_id"])
        self.assertEqual(res["original_source_id"], hist[0]["version_id"])

        rev = self.store.revert_version("user_imm", target="first")
        self.assertEqual(rev.version_id, hist[0]["version_id"])
        self.assertTrue(rev.is_original_upload)


if __name__ == "__main__":
    unittest.main()
