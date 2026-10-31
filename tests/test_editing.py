# -*- coding: utf-8 -*-
"""اختبارات عمليات التعديل العشر وتوجيه مهام Inpainting / Outpainting / Merge."""
import os, sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from visual_brain import get_visual_brain


class TestVisualEditing(unittest.TestCase):
    def setUp(self):
        self.brain = get_visual_brain()

    def test_ten_edit_operations_detection(self):
        cases = [
            ("غير الخلفية وخليها شاطئ بحر", "background_only"),
            ("بدل الملابس وخليه لابس بدلة رسمية", "clothing_only"),
            ("غير لون العيون للأخضر", "eye_color"),
            ("عدل الإضاءة وخليها وقت الغروب", "lighting_adjustment"),
            ("احذف الكاسة من على الطاولة", "add_remove_object"),
            ("حسن جودة الصورة ووضح التفاصيل", "enhance_upscale"),
            ("وسع الصورة خارج حدودها لتصير عريضة", "outpainting"),
            ("غير زاوية الكاميرا لتصير من فوق", "camera_reframe"),
            ("ادمج الصورتين مع بعض بمشهد واحد", "multi_image_merge"),
        ]
        for prompt_txt, expected_op in cases:
            plan = self.brain.build_plan(prompt_txt, user_id="test_edit_u", attached_images_bytes=[b"fake"])
            self.assertTrue(plan.edit_plan.is_edit, f"Failed is_edit for {prompt_txt}")
            self.assertEqual(plan.edit_plan.operation_type, expected_op, f"Mismatch for {prompt_txt}: got {plan.edit_plan.operation_type}")

    def test_edit_routes_to_reference_capable_model(self):
        plan = self.brain.build_plan(
            "غير الخلفية لمكتبة خشبية",
            user_id="test_edit_route",
            attached_images_bytes=[b"fake_img"],
            available_providers={"flux": True, "nano_banana": True},
        )
        self.assertEqual(plan.recommended_provider, "nano_banana")
        self.assertTrue(plan.edit_plan.requires_mask)


if __name__ == "__main__":
    unittest.main()
