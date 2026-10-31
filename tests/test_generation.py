# -*- coding: utf-8 -*-
"""اختبارات توليد الصور، فهم المشهد، كشف التعارضات، وتوجيه النماذج."""
import os, sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from visual_brain import get_visual_brain


class TestVisualGeneration(unittest.TestCase):
    def setUp(self):
        self.brain = get_visual_brain()

    def test_arabic_human_cafe_scene_plan(self):
        plan = self.brain.build_plan(
            "ارسم شاب ثلاثيني بلحية خفيفة ونظارة ولابس كنزة صوف كحلية قاعد بمقهى وعم يشرب شاي سخن بكاسة زجاج",
            user_id="test_gen_u1",
        )
        self.assertEqual(plan.intent, "generate")
        self.assertEqual(plan.scene.style, "photorealistic")
        self.assertTrue(plan.scene.has_humans)
        self.assertTrue(plan.scene.has_clothing)
        self.assertTrue(plan.scene.has_liquids_or_glass)
        self.assertIn("navy blue", plan.scene.colors)
        self.assertIn("material_engine", plan.prompt_modules)
        self.assertIn("lighting_engine", plan.prompt_modules)
        self.assertIn("camera_engine", plan.prompt_modules)
        self.assertEqual(plan.recommended_provider, "flux")

    def test_non_photo_style_routing(self):
        plan = self.brain.build_plan("ارسم شخصية انمي محارب في الفضاء", user_id="test_gen_u2")
        self.assertEqual(plan.scene.style, "anime")
        self.assertNotIn("material_engine", plan.prompt_modules)
        self.assertIn("anime", plan.final_prompt_en.lower())

    def test_conflict_detection_and_resolution(self):
        plan = self.brain.build_plan("صورة في ليل دامس ومظلم مع شمس ساطعة وشعاع شمس قوي", user_id="test_gen_u3")
        self.assertTrue(len(plan.conflicts_detected) >= 1)
        self.assertTrue(len(plan.conflict_resolutions) >= 1)


if __name__ == "__main__":
    unittest.main()
