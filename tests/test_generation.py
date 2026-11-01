# -*- coding: utf-8 -*-
"""
اختبارات توليد الصور والسيناريوهات الخمسة الأولى من القسم 13 + اختبار بريميو الطلب (PremiumRequestBrief):
1. Photorealistic hand holding a cup (5 digits when visible, natural grip & contact)
2. Person crossing a river (balance, water contact, wet clothing only where appropriate, reflections/shadows)
3. Child with a dog in a forest (subject count, animal anatomy, scale, fur, hand-to-dog contact)
4. Close-up portrait (natural eyes, skin, hair, teeth, ears; NO random dirt/mud added; hands not forced)
5. Dirty clothing after a muddy walk (mud follows contact/splash logic; no random dirt on clean surfaces)
+ Ambiguous request handling & Premium Brief verification
"""
import os
import sys
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
from visual_brain import get_visual_brain


class TestVisualGeneration(unittest.TestCase):
    def setUp(self):
        self.brain = get_visual_brain()

    def test_scenario_1_hand_holding_cup(self):
        plan = self.brain.build_plan("Photorealistic hand holding a warm ceramic cup on a wooden table", user_id="s1")
        self.assertEqual(plan.task_type, "text_to_image")
        self.assertTrue(plan.scene.has_visible_hands)
        self.assertIn("five digits per hand including the thumb", plan.final_prompt_en)
        self.assertIn("grip", plan.final_prompt_en)
        self.assertTrue(bool(plan.premium_brief.brief_summary_ar))
        self.assertIn("بريميو الطلب", plan.premium_brief.brief_summary_ar)

    def test_scenario_2_person_crossing_river(self):
        plan = self.brain.build_plan("Person crossing a shallow river in a forest", user_id="s2")
        self.assertTrue(plan.scene.has_humans)
        self.assertTrue(plan.scene.has_liquids_or_glass)
        self.assertIn("wet darkened clothing/skin strictly where water contact", plan.final_prompt_en)
        self.assertIn("reflections", plan.final_prompt_en)

    def test_scenario_3_child_with_dog_in_forest(self):
        plan = self.brain.build_plan("A child with a dog in a forest, touching the dog's fur", user_id="s3")
        self.assertTrue(plan.scene.has_humans)
        self.assertTrue(plan.scene.has_animals)
        self.assertIn("plausible animal anatomy", plan.final_prompt_en)
        self.assertIn("fur", plan.final_prompt_en)

    def test_scenario_4_closeup_portrait_clean_no_dirt(self):
        plan = self.brain.build_plan("Close-up portrait of a young woman in studio light", user_id="s4")
        self.assertTrue(plan.scene.is_clean_studio_or_portrait)
        self.assertFalse(plan.scene.has_dirt_sweat_or_wear)
        self.assertNotIn("muddy walk", plan.final_prompt_en)
        self.assertIn("do not force hands into the frame", plan.final_prompt_en)
        self.assertIn("subsurface scattering", plan.final_prompt_en)

    def test_scenario_5_dirty_clothing_after_muddy_walk(self):
        plan = self.brain.build_plan("Hiker with dirty clothing after a muddy walk in the rain", user_id="s5")
        self.assertTrue(plan.scene.has_dirt_sweat_or_wear)
        self.assertIn("splash trajectory", plan.final_prompt_en)
        self.assertIn("without random dirt on unrelated clean surfaces", plan.final_prompt_en)

    def test_scenario_10_ambiguous_request(self):
        plan = self.brain.build_plan("ارسم صورة", user_id="s10")
        self.assertTrue(len(plan.uncertainties) >= 1)
        self.assertTrue(len(plan.safe_assumptions) >= 1)
        self.assertIsNotNone(plan.clarification_question)

    def test_non_photo_style_and_conflict_resolution(self):
        p_anime = self.brain.build_plan("ارسم شخصية انمي محارب في الفضاء", user_id="s_anime")
        self.assertEqual(p_anime.scene.style, "anime")
        p_conf = self.brain.build_plan("صورة في ليل دامس ومظلم مع شمس ساطعة وشعاع شمس قوي", user_id="s_conf")
        self.assertTrue(len(p_conf.conflicts_detected) >= 1)


if __name__ == "__main__":
    unittest.main()
