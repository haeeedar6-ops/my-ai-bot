# -*- coding: utf-8 -*-
"""اختبار على مستوى الـ API: ما في كتابة/حذف على GitHub بدون موافقة صريحة."""
import os, sys, tempfile, types, unittest
from unittest import mock


class TestGithubRouteGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RYN_DATA_DIR"] = cls.tmp.name
        os.environ["RYN_DB_PATH"] = os.path.join(cls.tmp.name, "r.db")
        os.environ["WEB_PASSWORD"] = "pw-test-123"
        os.environ.setdefault("FLASK_SECRET_KEY", "x" * 32)
        import importlib
        try:
            import flask
        except ModuleNotFoundError:
            import _flask_compat as flask
            sys.modules["flask"] = flask
        webapp = importlib.import_module("webapp")
        cls.app = flask.Flask("t")
        cls.app.secret_key = "k" * 32
        fake_b = mock.MagicMock()
        fake_b.chat_provider, fake_b.code_route_off = {}, set()
        fake_b.get_doc.return_value = None
        fake_b._groq_ready.return_value = False
        fake_b._or_key.return_value = ""
        webapp.register(cls.app, fake_b)
        cls.AIStack = webapp.AIStack

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.c = self.app.test_client()
        r = self.c.post("/api/login", json={"password": "pw-test-123"}, headers={"X-Requested-With": "astra"})
        self.assertEqual(r.status_code, 200)
        self.h = {"X-Requested-With": "astra"}

    def test_direct_save_does_not_push(self):
        mgr = self.AIStack.get_github_manager()
        with mock.patch.object(type(mgr), "save_file", side_effect=AssertionError("push بدون موافقة!")) as sf:
            r = self.c.post("/api/github/save", json={"path": "a.py", "content": "x=1\n", "repo": "o/r"}, headers=self.h)
            self.assertEqual(r.status_code, 428, r.get_data(as_text=True)[:300])
            self.assertTrue(r.get_json()["approval_required"])
            sf.assert_not_called()

    def test_direct_delete_does_not_delete(self):
        mgr = self.AIStack.get_github_manager()
        with mock.patch.object(type(mgr), "delete_file", side_effect=AssertionError("حذف بدون موافقة!")) as df:
            r = self.c.post("/api/github/delete", json={"path": "a.py", "repo": "o/r"}, headers=self.h)
            self.assertEqual(r.status_code, 428, r.get_data(as_text=True)[:300])
            df.assert_not_called()

    def test_commit_route_refuses_without_approval(self):
        r = self.c.post("/api/github/save", json={"path": "b.py", "content": "y=2\n", "repo": "o/r"}, headers=self.h)
        rid = r.get_json()["change_request"]["id"]
        mgr = self.AIStack.get_github_manager()
        with mock.patch.object(type(mgr), "save_file", side_effect=AssertionError("push!")):
            r2 = self.c.post(f"/api/v2/github/changes/{rid}/commit", json={}, headers=self.h)
        self.assertEqual(r2.status_code, 403)
        self.assertTrue(r2.get_json().get("approval_required"))

    def test_audit_route_lists_events(self):
        self.c.post("/api/github/save", json={"path": "c.py", "content": "z=3\n", "repo": "o/r"}, headers=self.h)
        r = self.c.get("/api/v2/github/audit")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(e["action"].startswith("create") for e in r.get_json()["audit"]))

    def test_mix_plan_is_dry_run(self):
        r = self.c.post("/api/v2/mix/plan", json={"text": "احذف الملف القديم"}, headers=self.h)
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(j["dry_run"])
        self.assertIn("destructive", j["intent"]["risks"])

    def test_mix_audit_ok(self):
        self.assertEqual(self.c.get("/api/v2/mix/audit").status_code, 200)

    def test_dialects_route_honest(self):
        j = self.c.get("/api/v2/voice/dialects").get_json()
        self.assertEqual(len(j["dialects"]), 9)
        self.assertEqual(j["default"], "syrian")
        fb = {d["dialect"]: d for d in j["dialects"]}["moroccan"]
        self.assertTrue(fb["approximate"] and fb["note"])
        self.assertFalse(j["paid_providers_enabled"])

    def test_live_status_route_reports_reasons(self):
        r = self.c.get("/api/v2/live/status")
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("available", j)
        self.assertEqual(j["default_voice"], "Charon")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertTrue(any("GEMINI_API_KEY" in x for x in self.c.get("/api/v2/live/status").get_json()["reasons"]))

    def test_live_status_requires_auth(self):
        self.assertEqual(self.app.test_client().get("/api/v2/live/status").status_code, 401)

    def test_index_html_has_live_client_and_no_placeholders(self):
        html = self.c.get("/").get_data(as_text=True)
        self.assertNotIn("__RYN_", html)
        self.assertIn("window.RynLive", html)
        self.assertIn("startGeminiLive", html)
        self.assertIn("RynDuplex", html)

    def test_floating_buttons_layout(self):
        html = self.c.get("/").get_data(as_text=True)
        i_g, i_l = html.index("__gemini_live"), html.index("__live_voice")
        self.assertLess(i_g, i_l)                       # Gemini Live فوق Live Voice
        self.assertIn("__gh_edit", html)                # زر GitHub مكان زر الكود
        self.assertNotIn('data-m="__code"', html)
        self.assertNotIn("/api/code'", html)

    def test_github_edit_mode_toggle_and_models_flag(self):
        r = self.c.post("/api/github/edit-mode", json={"on": False}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["enabled"])
        self.assertFalse(self.c.get("/api/github/edit-mode").get_json()["enabled"])
        m = self.c.get("/api/models").get_json()
        self.assertFalse(m["github_edit"])
        self.assertFalse(m["code_auto"])                # توجيه الكود محذوف
        self.c.post("/api/github/edit-mode", json={"on": True}, headers=self.h)
        self.assertTrue(self.c.get("/api/github/edit-mode").get_json()["enabled"])

    def test_commit_blocked_when_edit_mode_off(self):
        r = self.c.post("/api/github/save", json={"path": "z.py", "content": "q=1\n", "repo": "o/r"}, headers=self.h)
        rid = r.get_json()["change_request"]["id"]
        self.c.post(f"/api/v2/github/changes/{rid}/approve", json={"approve": True}, headers=self.h)
        self.c.post("/api/github/edit-mode", json={"on": False}, headers=self.h)
        mgr = self.AIStack.get_github_manager()
        with mock.patch("ryn_ai_stack.requests") as rq:
            r2 = self.c.post(f"/api/v2/github/changes/{rid}/commit", json={}, headers=self.h)
            rq.put.assert_not_called(); rq.post.assert_not_called()
        self.assertGreaterEqual(r2.status_code, 400)
        self.c.post("/api/github/edit-mode", json={"on": True}, headers=self.h)

    def test_live_token_endpoint_never_leaks_key_without_permission(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaTESTKEY" + "k" * 30, "LIVE_ALLOW_RAW_KEY": "0"}):
            with mock.patch("voice.live_bridge.create_direct_credentials", side_effect=PermissionError("raw key blocked")):
                r = self.c.post("/api/v2/live/token", json={}, headers=self.h)
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("k" * 30, r.get_data(as_text=True))

    def test_live_token_endpoint_returns_session_config(self):
        creds = {"ephemeral": True, "auth_param": "access_token", "token": "auth_tokens/x", "endpoint": "wss://e"}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaTESTKEY" + "k" * 30}):
            with mock.patch("voice.live_bridge.create_direct_credentials", return_value=creds), \
                    mock.patch("voice.live_bridge.GeminiLiveFactory.candidate_models", return_value=["m1", "m2"]):
                r = self.c.post("/api/v2/live/token", json={"memory": "اسمي حيدر", "history": [{"role": "user", "text": "هلا"}], "voice": "Orus"}, headers=self.h)
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(j["token"], "auth_tokens/x")
        self.assertEqual(j["voice"], "Orus")
        self.assertEqual(j["models"], ["m1", "m2"])
        self.assertIn("haydar_xe", j["system_instruction"])
        self.assertIn("اسمي حيدر", j["system_instruction"])
        self.assertIn("المستخدم: هلا", j["system_instruction"])

    def test_live_token_requires_auth_and_csrf_header(self):
        self.assertEqual(self.app.test_client().post("/api/v2/live/token", json={}, headers=self.h).status_code, 401)
        self.assertEqual(self.c.post("/api/v2/live/token", json={}).status_code, 403)

    def test_project_profile_route(self):
        mgr = self.AIStack.get_github_manager()
        fake = {"total_files": 2, "languages": [("Python", 2)], "frameworks": ["Flask"], "package_managers": ["pip"], "entry_points": ["bot.py"],
                "has_tests": False, "test_locations": [], "ci": [], "deploy": [], "top_dirs": [], "npm_scripts": [], "readme_head": ""}
        with mock.patch.object(type(mgr), "project_profile", return_value=fake):
            j = self.c.get("/api/github/project-profile").get_json()
        self.assertTrue(j["ok"])
        self.assertIn("Flask", j["summary"])

    def test_unauthenticated_blocked(self):
        r = self.app.test_client().post("/api/github/save", json={"path": "a"}, headers=self.h)
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
