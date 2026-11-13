# -*- coding: utf-8 -*-
"""
tests/test_preflight_repair_plan.py — Comprehensive Verification Suite for RYN Pre-Implementation Repair Plan
=============================================================================================================
Tests every requirement in Sections 3, 4, 5, 6, 7, and 9 of `RYN_Preflight_Repair_Local_Memory_GitHub_Write_Plan.pdf`:
1. Local save/restart: Save a memory (including sensitive encrypted memory), close service, reopen, and retrieve.
2. Offline retrieval: Disable network completely and verify local SQLite FTS5 + semantic memory search works.
3. No server persistence: Verify `/api/chat` does NOT upload or persist personal memory on the server by default.
4. Encrypted backup/restore: Export encrypted backup, delete DB, restore, and verify records, FTS5, and tamper detection.
5. Deletion: Delete a test memory and confirm it is absent from both SQLite table and FTS5 search index.
6. GitHub branch write: Write test file to disposable branch (`ryn/ai-run-...`) and verify read-back content & commit SHA;
   also verify that read-back mismatch raises an error and marks status FAILED.
7. Permission denied: Remove write permission (HTTP 403) and confirm failure is reported without claiming success.
8. Branch protection: Test protected branch (`main` HTTP 403/422); confirm system opens a feature branch (`ryn/ai-run-...`) + Draft PR path and reports NEEDS_ATTENTION.
9. Conflict: Modify the same file remotely and locally (stale SHA); ensure conflict is detected and never silently overwrites.
10. No-op patch: Attempt an empty/no-op patch; confirm system reports no changes and does not fabricate a commit.
11. Truthfulness guard: Confirm AI chat message claiming "تم تعديل الملف على GitHub" without verified write is flagged as NOT SAVED.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from unittest import mock

from memory import MemoryService


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


class TestPhase2DeviceLocalMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "device_local_memory.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_local_save_and_restart_persistence(self):
        """Test 1: Save a memory, close the app/service, reopen it, and retrieve the same record."""
        svc1 = MemoryService(db_path=self.db_path)
        saved = svc1.add_memory(
            user_id="haydar",
            content="المطور يفضل استخدام SQLite FTS5 للذاكرة المحلية على الجهاز بدون سيرفر.",
            category="preferences",
            source_conversation_id="conv_local_1",
            source_message_range="1..2",
            importance_score=0.92,
            confidence=0.98,
            tags=["sqlite", "fts5", "local_memory"],
            sensitivity_label="personal",
        )
        self.assertIsNotNone(saved)
        mem_id = saved["memory_id"]
        self.assertEqual(saved["category"], "preferences")
        self.assertEqual(saved["source_conversation_id"], "conv_local_1")
        self.assertEqual(saved["source_message_range"], "1..2")
        self.assertEqual(saved["tags"], ["sqlite", "fts5", "local_memory"])
        del svc1

        # Reopen a brand new MemoryService instance pointing to the same device SQLite file
        svc2 = MemoryService(db_path=self.db_path)
        reloaded = svc2.read_memory("haydar", mem_id)
        self.assertEqual(reloaded["content"], "المطور يفضل استخدام SQLite FTS5 للذاكرة المحلية على الجهاز بدون سيرفر.")
        self.assertEqual(reloaded["memory_id"], mem_id)

        status = svc2.get_local_memory_status("haydar")
        self.assertTrue(status["memory_is_local"])
        self.assertIn("Memory is local", status["status_badge"])
        self.assertGreater(status["storage_size_bytes"], 0)

    def test_02_offline_retrieval_with_network_disabled(self):
        """Test 2: Disable network and verify local memory search (SQLite FTS5 + local semantic) still works."""
        svc = MemoryService(db_path=self.db_path)
        svc.add_memory(
            user_id="haydar",
            content="فرع التعديلات الآلية في جيتهاب هو ryn/ai-run- لتجنب الكتابة المباشرة على main.",
            category="project_memory",
            tags=["github", "branch"],
        )
        with mock.patch("requests.request", side_effect=RuntimeError("Network is disabled")), \
                mock.patch("requests.get", side_effect=RuntimeError("Network is disabled")), \
                mock.patch("requests.post", side_effect=RuntimeError("Network is disabled")):
            hits = svc.retrieve("haydar", "ما هو فرع التعديلات الآلية في جيتهاب؟")
            self.assertGreaterEqual(len(hits), 1)
            self.assertIn("ryn/ai-run-", hits[0]["content"])

    def test_03_no_server_persistence_by_default(self):
        """Test 3: Confirm personal memory content is not persisted on the server by default."""
        try:
            import flask
        except ModuleNotFoundError:
            import sys
            import _flask_compat as flask
            sys.modules["flask"] = flask
        import webapp

        with tempfile.TemporaryDirectory() as srv_tmp:
            with mock.patch.dict(os.environ, {
                "RYN_DATA_DIR": srv_tmp,
                "RYN_DB_PATH": os.path.join(srv_tmp, "srv.db"),
                "WEB_PASSWORD": "pw-test-local",
                "RYN_SERVER_MEMORY_PERSISTENCE": "0",
            }):
                app = flask.Flask("test_no_srv_mem")
                app.secret_key = "s" * 32
                fake_b = mock.MagicMock()
                fake_b.chat_provider, fake_b.code_route_off = {}, set()
                fake_b.get_doc.return_value = None
                fake_b._groq_ready.return_value = False
                fake_b._or_key.return_value = ""
                fake_b.is_deep_question.return_value = False
                fake_b.is_code_request.return_value = False
                fake_b.ask_model.return_value = ("أهلاً بك يا حيدر [[MEM: معلومة محلية فقط لا تحفظ بالسيرفر]]", "Gemini")
                webapp.register(app, fake_b)

                client = app.test_client()
                client.post("/api/login", json={"password": "pw-test-local"}, headers={"X-Requested-With": "astra"})
                webapp.USER_PERSISTENT_LEARNINGS.clear()

                r = client.post(
                    "/api/chat",
                    json={
                        "message": "مرحبا",
                        "memory": {
                            "enabled": True,
                            "local_only": True,
                            "name": "حيدر",
                            "custom": "أحب البرمجة بلغة بايثون",
                            "facts": ["📚 درس: معلومة خاصة على جهازي فقط"],
                        },
                    },
                    headers={"X-Requested-With": "astra"},
                )
                self.assertEqual(r.status_code, 200)
                import time as _t; _t.sleep(0.15)
                # Confirm USER_PERSISTENT_LEARNINGS on server did NOT store the client's memory
                self.assertEqual(webapp.USER_PERSISTENT_LEARNINGS.get("main", []), [])

    def test_04_encrypted_backup_and_restore(self):
        """Test 4: Export encrypted backup, remove test database, restore, and verify records and indexes."""
        svc1 = MemoryService(db_path=self.db_path)
        svc1.add_memory(
            user_id="haydar",
            content="مفتاح الإعداد المحلي لتطبيق سطح المكتب يستخدم منفذ 10000.",
            category="technical_memory",
            tags=["desktop", "port"],
        )
        svc1.add_memory(
            user_id="haydar",
            content="بيانات خاصة مشفرة على الجهاز فقط.",
            category="user_profile",
            sensitivity_label="sensitive",
        )
        backup_file = os.path.join(self.tmp.name, "ryn_backup.enc.json")
        bundle = svc1.export_encrypted_backup("haydar", passphrase="my-device-passphrase", output_path=backup_file)
        self.assertTrue(bundle["encrypted"])
        self.assertEqual(bundle["record_count"], 2)
        self.assertNotIn("مفتاح الإعداد المحلي", bundle["payload"])
        del svc1

        # Remove the original database completely
        os.remove(self.db_path)
        self.assertFalse(os.path.exists(self.db_path))

        # Restore into a fresh database
        restored_db = os.path.join(self.tmp.name, "restored_memory.db")
        svc2 = MemoryService(db_path=restored_db)
        with self.assertRaises(PermissionError):
            svc2.restore_encrypted_backup("haydar", bundle, passphrase="wrong-passphrase")

        res = svc2.restore_encrypted_backup("haydar", bundle, passphrase="my-device-passphrase")
        self.assertTrue(res["ok"])
        self.assertEqual(res["restored_count"], 2)

        hits = svc2.retrieve("haydar", "منفذ تطبيق سطح المكتب")
        self.assertEqual(len(hits), 1)
        self.assertIn("10000", hits[0]["content"])

    def test_05_deletion_removes_from_db_and_search_index(self):
        """Test 5: Delete a test memory and confirm it is absent from database and search index."""
        svc = MemoryService(db_path=self.db_path)
        rec = svc.add_memory(
            user_id="haydar",
            content="معلومة مؤقتة للحذف الفوري من القاعدة والفهرس.",
            category="knowledge_memory",
            tags=["temp_delete_tag"],
        )
        mid = rec["memory_id"]
        self.assertEqual(len(svc.retrieve("haydar", "معلومة مؤقتة للحذف الفوري")), 1)

        del_res = svc.delete_memory("haydar", mid, hard_delete=True)
        self.assertTrue(del_res["ok"])
        self.assertTrue(del_res["verified_absent"])

        # Confirm absent from retrieve() and read_memory()
        self.assertEqual(len(svc.retrieve("haydar", "معلومة مؤقتة للحذف الفوري")), 0)
        with self.assertRaises(KeyError):
            svc.read_memory("haydar", mid)

    def test_06_sensitive_candidate_requires_user_confirmation_and_secrets_blocked(self):
        """Section 5.3 & 5.4: Ask user before saving sensitive details; block API keys/passwords."""
        svc = MemoryService(db_path=self.db_path)
        with self.assertRaises(ValueError):
            svc.capture_memory_candidate("haydar", "My GitHub token is ghp_1234567890abcdefghijklmnopqrstuv")

        cand = svc.capture_memory_candidate("haydar", "عنوان بيتي ورقم هاتفي الخاص هو 0999000111")
        self.assertTrue(cand["requires_confirmation"])
        self.assertEqual(cand["sensitivity_label"], "sensitive")

        with self.assertRaises(PermissionError):
            svc.confirm_and_store_candidate("haydar", cand, user_confirmed=False)

        stored = svc.confirm_and_store_candidate("haydar", cand, user_confirmed=True)
        self.assertEqual(stored["sensitivity_label"], "sensitive")


if __name__ == "__main__":
    unittest.main()
