# -*- coding: utf-8 -*-
"""
اختبارات تعديل الصور والسيناريوهات 6 إلى 9 من القسم 13 + نموذج `timbrooks/instruct-pix2pix` + قيد No Silent Downgrade:
6. Edit lighting only (face, clothing, pose, background remain stable)
7. Change shirt color only (folds, fabric texture, body, background remain consistent)
8. Remove a background object (reconstruct background perspective & texture)
9. Restore an old image (damage reduced without inventing facial features or changing identity)
+ InstructPix2Pix (`timbrooks/instruct-pix2pix`) routing & execution test
"""
import os
import sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import base64
from visual_brain import get_visual_brain, get_provider_capabilities
from visual_brain.brain import UnsupportedEditOperationError
from image_providers import InstructPix2PixProvider

VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestVisualEditing(unittest.TestCase):
    def setUp(self):
        self.brain = get_visual_brain()

    def test_instruct_pix2pix_registered_and_routed_for_edits(self):
        caps = get_provider_capabilities({"instruct_pix2pix": True, "gemini": True, "flux": True})
        self.assertIn("instruct_pix2pix", caps)
        self.assertEqual(caps["instruct_pix2pix"].model_id, "timbrooks/instruct-pix2pix")
        self.assertTrue(caps["instruct_pix2pix"].image_to_image)
        self.assertFalse(caps["instruct_pix2pix"].text_to_image)

        plan = self.brain.build_plan(
            "غير الخلفية لمكتبة خشبية",
            user_id="u_p2p",
            attached_images_bytes=[VALID_PNG],
            available_providers={"instruct_pix2pix": True, "gemini": True, "flux": True},
        )
        self.assertEqual(plan.recommended_provider, "instruct_pix2pix")
        self.assertIn("gemini", plan.fallback_providers)
        self.assertTrue(bool(plan.edit_plan.instruct_pix2pix_instruction_en))

        from types import SimpleNamespace
        fake_part = SimpleNamespace(inline_data=SimpleNamespace(data=VALID_PNG, mime_type="image/png"))
        p2p_prov = InstructPix2PixProvider(
            custom_runner_fn=lambda prompt_en, _refs: (VALID_PNG, "image/png", "timbrooks/instruct-pix2pix")
        )
        res = self.brain.execute_plan(
            plan,
            user_id="u_p2p",
            provider_runners={"instruct_pix2pix": p2p_prov.generate},
            ref_parts=[fake_part],
        )
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["model"], "timbrooks/instruct-pix2pix")

    def test_scenario_6_edit_lighting_only(self):
        plan = self.brain.build_plan(
            "Edit lighting only to soft morning light",
            user_id="s6",
            attached_images_bytes=[VALID_PNG],
        )
        self.assertTrue(plan.edit_plan.is_edit)
        self.assertTrue(plan.identity_lock.preserve_face_structure)
        self.assertTrue(plan.identity_lock.preserve_clothing)
        self.assertTrue(plan.identity_lock.preserve_background)
        self.assertTrue(plan.identity_lock.preserve_body_pose)

    def test_scenario_7_change_shirt_color_only(self):
        plan = self.brain.build_plan(
            "Change shirt color only to navy blue",
            user_id="s7",
            attached_images_bytes=[VALID_PNG],
        )
        self.assertEqual(plan.task_type, "inpaint")
        self.assertTrue(plan.identity_lock.preserve_face_structure)
        self.assertTrue(plan.identity_lock.preserve_background)
        self.assertIn("garment fabric micro-texture, existing folds, seams", " ".join(plan.must_keep))

    def test_scenario_8_remove_background_object(self):
        plan = self.brain.build_plan(
            "احذف الكاسة من الخلفية",
            user_id="s8",
            attached_images_bytes=[VALID_PNG],
        )
        self.assertEqual(plan.task_type, "remove_object")
        self.assertTrue(plan.edit_plan.requires_mask)
        self.assertIn("reconstruct background consistently", plan.edit_plan.allowed_edit_area)

    def test_scenario_9_restore_old_image(self):
        plan = self.brain.build_plan(
            "Restore this old image and remove scratches",
            user_id="s9",
            attached_images_bytes=[VALID_PNG],
        )
        self.assertEqual(plan.task_type, "restore")
        self.assertIn("without inventing facial features or changing identity", plan.edit_plan.allowed_edit_area)

    def test_no_silent_downgrade_when_edit_provider_missing(self):
        plan = self.brain.build_plan(
            "Change shirt color only to green",
            user_id="u_nodowngrade",
            attached_images_bytes=[VALID_PNG],
            available_providers={"flux": True, "gemini": False, "instruct_pix2pix": False, "pollinations": True},
        )
        self.assertEqual(plan.recommended_provider, "unsupported")
        with self.assertRaises(UnsupportedEditOperationError):
            self.brain.execute_plan(
                plan,
                user_id="u_nodowngrade",
                provider_runners={"flux": lambda p, r=None: (VALID_PNG, "image/png", "flux")},
            )


if __name__ == "__main__":
    unittest.main()
