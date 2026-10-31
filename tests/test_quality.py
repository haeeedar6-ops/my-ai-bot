# -*- coding: utf-8 -*-
"""اختبارات فاحص جودة الصور وإعادة المحاولة عند فشل الصورة."""
import unittest
import base64
from visual_brain import VisualBrain
from visual_brain.visual_memory import VisualMemoryStore
from visual_brain.quality_inspector import inspect_generated_image
import tempfile


VALID_1X1_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestQualityInspector(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="ryn_qual_test_")
        self.brain = VisualBrain(memory_store=VisualMemoryStore(storage_dir=self.tmp_dir))

    def test_corrupted_bytes_rejected_and_retried(self):
        plan = self.brain.build_plan("ارسم منظر طبيعي لجبل وبحيرة", user_id="u_q1")
        bad_rep = inspect_generated_image(b"not_an_image", plan)
        self.assertFalse(bad_rep.passed)
        self.assertTrue(bad_rep.retry_recommended)

        good_rep = inspect_generated_image(VALID_1X1_PNG, plan)
        self.assertTrue(good_rep.passed)
        self.assertEqual(good_rep.format, "png")

    def test_auto_retry_on_corrupted_first_attempt(self):
        plan = self.brain.build_plan("ارسم بورتريه شاب", user_id="u_q2")
        calls = []

        def flaky_runner(prompt_en, ref_parts=None):
            calls.append(prompt_en)
            if len(calls) == 1:
                return b"corrupted", "image/png", "mock-model"
            return VALID_1X1_PNG, "image/png", "mock-model"

        res = self.brain.execute_plan(
            plan,
            user_id="u_q2",
            provider_runners={"flux": flaky_runner},
            max_quality_retries=2,
        )
        self.assertEqual(res["status"], "done")
        self.assertEqual(len(calls), 2)
        self.assertIn("QUALITY CORRECTION:", calls[1])


if __name__ == "__main__":
    unittest.main()
