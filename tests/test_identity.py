# -*- coding: utf-8 -*-
"""اختبارات محرك الحفاظ على الهوية وعزل ذاكرة الصور بين المستخدمين."""
import unittest
import tempfile
from visual_brain import VisualBrain
from visual_brain.visual_memory import VisualMemoryStore


class TestIdentityAndMemory(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="ryn_vmem_test_")
        self.store = VisualMemoryStore(storage_dir=self.tmp_dir)
        self.brain = VisualBrain(memory_store=self.store)

    def test_selective_identity_locking(self):
        # عند طلب تغيير الخلفية فقط، يجب قفل الوجه والعيون والشعر والملابس والوضعية والزاوية
        plan = self.brain.build_plan(
            "غير الخلفية فقط لحديقة",
            user_id="u_id_1",
            attached_images_bytes=[b"img"],
        )
        lk = plan.identity_lock
        self.assertTrue(lk.enabled)
        self.assertTrue(lk.preserve_face_structure)
        self.assertTrue(lk.preserve_eye_color)
        self.assertTrue(lk.preserve_hair_style)
        self.assertTrue(lk.preserve_clothing)
        self.assertTrue(lk.preserve_body_pose)
        self.assertFalse(lk.preserve_background)
        self.assertIn("IDENTITY & STRUCTURAL LOCK", plan.prompt_modules.get("identity_lock", ""))

    def test_user_isolation_and_version_revert(self):
        uri_u1_v1 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        uri_u1_v2 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        uri_u2_v1 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR42mNk+M9QzwAEjDAGACCcA/18976AAAAAAElFTkSuQmCC"

        v1 = self.store.save_version("user_A", uri_u1_v1, "صورة 1", "prompt 1", operation_type="generate")
        v2 = self.store.save_version("user_A", uri_u1_v2, "تعديل 1", "prompt 2", operation_type="clothing_only")
        v_other = self.store.save_version("user_B", uri_u2_v1, "صورة مستخدم ب", "prompt B", operation_type="generate")

        # التأكد من ربط النسخ وعدم خلط صور المستخدمين
        self.assertEqual(v2.parent_version_id, v1.version_id)
        self.assertEqual(self.store.get_latest_image_uri("user_A"), uri_u1_v2)
        self.assertEqual(self.store.get_latest_image_uri("user_B"), uri_u2_v1)
        self.assertNotEqual(self.store.get_latest_image_uri("user_A"), self.store.get_latest_image_uri("user_B"))

        # التأكد من الرجوع للنسخة السابقة
        reverted = self.store.revert_version("user_A", target="previous")
        self.assertEqual(reverted.version_id, v1.version_id)
        self.assertEqual(self.store.get_latest_image_uri("user_A"), uri_u1_v1)


if __name__ == "__main__":
    unittest.main()
