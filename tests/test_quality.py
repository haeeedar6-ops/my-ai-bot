# -*- coding: utf-8 -*-
"""اختبارات فاحص الجودة السباعي وكتابة ملاحظات المراجع لنموذج توليد الصور (Section 8)."""
import os
import sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import base64
import tempfile
from visual_brain import VisualBrain
from visual_brain.visual_memory import VisualMemoryStore
from visual_brain.quality_inspector import inspect_generated_image

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
        self.assertTrue(len(bad_rep.defects) >= 1)
        self.assertTrue(len(bad_rep.critic_notes_ar) >= 1)
        self.assertIn("QUALITY CORRECTION & CRITIC NOTES FOR IMAGE GENERATOR:", bad_rep.generator_feedback_en)

        good_rep = inspect_generated_image(VALID_1X1_PNG, plan)
        self.assertTrue(good_rep.passed)
        self.assertEqual(good_rep.format, "png")

    def test_vision_critic_writes_notes_to_generator_on_defect(self):
        plan = self.brain.build_plan("بورتريه شاب يمسك كوب قهوة", user_id="u_q_critic")
        calls = []
        audit_rounds = []

        def mock_generator(prompt_en, ref_parts=None):
            calls.append(prompt_en)
            return VALID_1X1_PNG, "image/png", "mock-flux"

        def mock_vision_critic(_img_bytes, _audit_q):
            audit_rounds.append(1)
            if len(audit_rounds) == 1:
                return "FAIL: anatomy | right_hand | extra sixth finger on cup | render strictly five natural fingers including thumb gripping the ceramic cup"
            return "OK"

        res = self.brain.execute_plan(
            plan,
            user_id="u_q_critic",
            provider_runners={"flux": mock_generator},
            max_quality_retries=2,
            vision_audit_fn=mock_vision_critic,
        )
        self.assertEqual(res["status"], "done")
        self.assertEqual(len(calls), 2)
        self.assertIn("render strictly five natural fingers including thumb", calls[1])
        self.assertEqual(len(res["review_history"]), 2)
        self.assertFalse(res["review_history"][0]["passed"])
        self.assertTrue(res["review_history"][1]["passed"])


if __name__ == "__main__":
    unittest.main()
