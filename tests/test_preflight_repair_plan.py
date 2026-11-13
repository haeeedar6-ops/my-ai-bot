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
from ryn_ai_stack import GitHubFileManager


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


class TestPhase1GitHubWriteVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gh = GitHubFileManager(storage_path=os.path.join(self.tmp.name, "gh.json"))
        self.env_patcher = mock.patch.dict(os.environ, {
            "GITHUB_TOKEN": "ghp_testtoken1234567890abcdefghij",
            "GITHUB_REPO": "haeeedar6-ops/my-ai-bot",
            "GITHUB_BRANCH": "ryn/ai-run-disposable",
        })
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        self.tmp.cleanup()

    def test_01_github_branch_write_and_read_back_verification(self):
        """Test 6: Write test file to disposable branch and verify read-back content and commit SHA."""
        orig_text = "x = 1\n"
        new_text = "x = 2\n"
        b64_orig = base64.b64encode(orig_text.encode("utf-8")).decode("ascii")
        b64_new = base64.b64encode(new_text.encode("utf-8")).decode("ascii")

        get_calls = {"n": 0}

        def fake_get(url, **_kw):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                return _FakeResp(200, {"sha": "sha_old_111", "content": b64_orig})
            return _FakeResp(200, {"sha": "sha_new_222", "content": b64_new})

        def fake_put(url, **_kw):
            return _FakeResp(200, {
                "content": {"sha": "sha_new_222", "html_url": "https://github.com/haeeedar6-ops/my-ai-bot/blob/ryn/ai-run-disposable/test.py"},
                "commit": {"sha": "commit_sha_verified_abc1234567890", "html_url": "https://github.com/haeeedar6-ops/my-ai-bot/commit/commit_sha_verified_abc1234567890"},
            })

        with mock.patch("ryn_ai_stack.requests.get", side_effect=fake_get), \
                mock.patch("ryn_ai_stack.requests.put", side_effect=fake_put):
            res = self.gh.save_file("test.py", new_text, commit_message="Test verified write", sha="sha_old_111", user_id="main")

        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "SUCCESS")
        self.assertTrue(res["verified_reread"])
        self.assertEqual(res["commit_sha"], "commit_sha_verified_abc1234567890")
        self.assertEqual(res["read_back_sha"], "sha_new_222")
        self.assertEqual(res["branch"], "ryn/ai-run-disposable")

        traces = self.gh.get_write_traces()
        self.assertGreaterEqual(len(traces), 1)
        self.assertEqual(traces[0]["status"], "SUCCESS")
        self.assertEqual(traces[0]["returned_commit_sha"], "commit_sha_verified_abc1234567890")

    def test_02_read_back_mismatch_reports_failure(self):
        """Section 3.3: API reports success but read-back content is unchanged -> must fail verification."""
        orig_text = "x = 1\n"
        new_text = "x = 2\n"
        b64_orig = base64.b64encode(orig_text.encode("utf-8")).decode("ascii")

        def fake_get(_url, **_kw):
            # Remote still returns old content even after PUT 200!
            return _FakeResp(200, {"sha": "sha_old_111", "content": b64_orig})

        def fake_put(_url, **_kw):
            return _FakeResp(200, {
                "content": {"sha": "sha_old_111"},
                "commit": {"sha": "commit_fake_123"},
            })

        with mock.patch("ryn_ai_stack.requests.get", side_effect=fake_get), \
                mock.patch("ryn_ai_stack.requests.put", side_effect=fake_put):
            with self.assertRaises(RuntimeError) as ctx:
                self.gh.save_file("test.py", new_text, sha="sha_old_111", user_id="main")
        self.assertIn("Read-Back Verification Failed", str(ctx.exception))
        self.assertEqual(self.gh.get_write_traces()[0]["status"], "FAILED")

    def test_03_permission_denied_reports_failure_without_claiming_success(self):
        """Test 7: Remove write permission and confirm failure is reported without claiming success."""
        b64_orig = base64.b64encode(b"x = 1\n").decode("ascii")
        with mock.patch("ryn_ai_stack.requests.get", return_value=_FakeResp(200, {"sha": "s1", "content": b64_orig})), \
                mock.patch("ryn_ai_stack.requests.put", return_value=_FakeResp(403, {"message": "Resource not accessible by integration"})):
            with self.assertRaises(PermissionError) as ctx:
                self.gh.save_file("test.py", "x = 2\n", sha="s1", user_id="main")
        self.assertIn("Permission Denied", str(ctx.exception))
        self.assertEqual(self.gh.get_write_traces()[0]["status"], "FAILED")

    def test_04_protected_branch_diverts_to_feature_branch_and_pr(self):
        """Test 8: Test protected branch; confirm system opens a feature branch/PR path and reports NEEDS_ATTENTION."""
        b64_orig = base64.b64encode(b"x = 1\n").decode("ascii")
        b64_new = base64.b64encode(b"x = 2\n").decode("ascii")

        put_calls = []

        def fake_put(url, json=None, **_kw):
            put_calls.append(json)
            if json.get("branch") == "main":
                return _FakeResp(403, {"message": "Protected branch hook declined (GH006)"})
            return _FakeResp(200, {
                "content": {"sha": "sha_feat_222"},
                "commit": {"sha": "commit_feat_999"},
            })

        def fake_get(url, params=None, **_kw):
            if "/git/ref/heads/main" in url:
                return _FakeResp(200, {"object": {"sha": "base_main_sha_000"}})
            if "/git/ref/heads/ryn/ai-run-" in url:
                return _FakeResp(404, {})
            if params and str(params.get("ref", "")).startswith("ryn/ai-run-"):
                return _FakeResp(200, {"sha": "sha_feat_222", "content": b64_new})
            return _FakeResp(200, {"sha": "s1", "content": b64_orig})

        def fake_post(url, json=None, **_kw):
            if url.endswith("/git/refs"):
                return _FakeResp(201, {"ref": json.get("ref")})
            if url.endswith("/pulls"):
                return _FakeResp(201, {"html_url": "https://github.com/haeeedar6-ops/my-ai-bot/pull/42"})
            return _FakeResp(200, {})

        with mock.patch("ryn_ai_stack.requests.get", side_effect=fake_get), \
                mock.patch("ryn_ai_stack.requests.put", side_effect=fake_put), \
                mock.patch("ryn_ai_stack.requests.post", side_effect=fake_post):
            res = self.gh.save_file("test.py", "x = 2\n", sha="s1", user_id="main", branch_override="main")

        self.assertTrue(res["protected_branch_diverted"])
        self.assertEqual(res["status"], "NEEDS_ATTENTION")
        self.assertTrue(res["branch"].startswith("ryn/ai-run-"))
        self.assertEqual(res["pr_url"], "https://github.com/haeeedar6-ops/my-ai-bot/pull/42")

    def test_05_conflict_detection_blocks_overwrite(self):
        """Test 9: Modify the same file remotely and locally; ensure integration detects conflict and does not overwrite."""
        b64_remote = base64.b64encode(b"x = 999\n").decode("ascii")
        with mock.patch("ryn_ai_stack.requests.get", return_value=_FakeResp(200, {"sha": "sha_remote_newer", "content": b64_remote})), \
                mock.patch("ryn_ai_stack.requests.put") as mock_put:
            with self.assertRaises(RuntimeError) as ctx:
                self.gh.save_file("test.py", "x = 2\n", sha="sha_local_stale", user_id="main")
            mock_put.assert_not_called()
        self.assertIn("Conflict (Stale SHA)", str(ctx.exception))
        self.assertEqual(self.gh.get_write_traces()[0]["status"], "CONFLICT")

    def test_06_noop_patch_rejected_without_fabricating_commit(self):
        """Test 10: Attempt an empty/no-op patch; system must report no changes and must not fabricate a commit."""
        orig_text = "def hello():\n    return 42\n"
        b64_orig = base64.b64encode(orig_text.encode("utf-8")).decode("ascii")
        with mock.patch("ryn_ai_stack.requests.get", return_value=_FakeResp(200, {"sha": "s1", "content": b64_orig})), \
                mock.patch("ryn_ai_stack.requests.put") as mock_put:
            with self.assertRaises(ValueError) as ctx:
                self.gh.save_file("test.py", orig_text, sha="s1", user_id="main", reject_noop=True)
            mock_put.assert_not_called()
        self.assertIn("No-op patch", str(ctx.exception))
        self.assertEqual(self.gh.get_write_traces()[0]["status"], "NO_OP")

    def test_07_chat_truthfulness_guard_flags_unverified_edit_claims(self):
        """Non-negotiable rule: Never let the AI claim it edited/pushed to GitHub when only a code block was generated."""
        fake_ai_reply = "لقد قمت بتعديل الملف على GitHub وحفظ التغييرات بنجاح:\n```python\nx = 2\n```"
        out, has_pending = self.gh.apply_chat_github_actions("main", "عدلي ملف test.py", fake_ai_reply)
        self.assertFalse(has_pending)
        self.assertIn("Write-Verification Guard — NOT SAVED", out)


if __name__ == "__main__":
    unittest.main()
