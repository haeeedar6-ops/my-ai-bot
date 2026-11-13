import os, sys, tempfile, types, unittest
from unittest import mock


class TestWebRoutesAndLiveGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RYN_DATA_DIR"] = cls.tmp.name
        os.environ["RYN_DB_PATH"] = os.path.join(cls.tmp.name, "test.db")
        os.environ["WEB_PASSWORD"] = "secret123"
        os.environ["GEMINI_API_KEY"] = "AIzaSyFAKEKEY_0123456789abcdefghijk"
        try:
            import flask
        except ImportError:
            import _flask_compat as flask
            sys.modules["flask"] = flask
        import webapp
        cls.webapp = webapp
        cls.app = flask.Flask("test_ryn")
        cls.app.secret_key = "test_secret"
        b = types.SimpleNamespace(
            client=None,
            types=types.SimpleNamespace(Content=lambda **kw: kw, Part=types.SimpleNamespace(from_bytes=lambda **kw: kw)),
            FAST_MODELS=["gemini-2.5-flash"],
            DEEP_MODELS=["gemini-2.5-pro"],
            chat_provider={},
            code_route_off=set(),
            doc_ctx={},
            _groq_ready=lambda: False,
            _or_ready=lambda: False,
            get_doc=lambda w: None,
            set_doc=lambda w, n, p: None,
        )
        webapp.register(cls.app, b)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _authed(self):
        self.client.post("/api/login", json={"password": "secret123"}, headers={"X-Requested-With": "astra"})
        return {"X-Requested-With": "astra"}

    def test_github_and_agent_routes_removed(self):
        h = self._authed()
        for dead_route in ("/api/github/config", "/api/github/save", "/api/v1/agent/status", "/api/v2/github/changes"):
            r = self.client.get(dead_route, headers=h)
            self.assertEqual(r.status_code, 404, dead_route)

    def test_mix_plan_is_dry_run(self):
        h = self._authed()
        r = self.client.post("/api/v2/mix/plan", json={"text": "اكتب دالة بايثون"}, headers=h)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["dry_run"])
        self.assertEqual(d["intent"]["intent"], "code")

    def test_mix_audit_ok(self):
        r = self.client.get("/api/v2/mix/audit", headers=self._authed())
        self.assertEqual(r.status_code, 200)

    def test_dialects_route_honest(self):
        r = self.client.get("/api/v2/voice/dialects", headers=self._authed())
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["default"], "syrian")
        self.assertEqual(len(d["dialects"]), 9)

    def test_live_status_route_reports_reasons(self):
        r = self.client.get("/api/v2/live/status", headers=self._authed())
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("available", d)
        self.assertIn("voices", d)

    def test_live_status_requires_auth(self):
        self.client.post("/api/logout", headers={"X-Requested-With": "astra"})
        r = self.client.get("/api/v2/live/status")
        self.assertEqual(r.status_code, 401)

    def test_index_html_has_live_client_and_no_placeholders(self):
        r = self.client.get("/")
        html = r.get_data(as_text=True)
        self.assertIn("RynLive", html)
        self.assertNotIn('id="agentModal"', html)
        self.assertNotIn('id="dAgentBtn"', html)
        self.assertNotIn("__gh_edit", html)

    def test_floating_buttons_layout(self):
        r = self.client.get("/")
        html = r.get_data(as_text=True)
        self.assertIn("__gemini_live", html)
        self.assertIn("__live_voice", html)

    def test_live_token_endpoint_never_leaks_key_without_permission(self):
        h = self._authed()
        with mock.patch.dict(os.environ, {"RYN_LIVE_ALLOW_RAW_KEY": "0"}):
            with mock.patch("voice.live_bridge.create_direct_credentials", side_effect=PermissionError("no raw key")):
                r = self.client.post("/api/v2/live/token", json={}, headers=h)
                self.assertIn(r.status_code, (403, 502))
                self.assertNotIn("AIzaSyFAKEKEY", r.get_data(as_text=True))

    def test_live_token_endpoint_returns_session_config(self):
        h = self._authed()
        with mock.patch("voice.live_bridge.create_direct_credentials", return_value={"token": "auth_tokens/ephem_123", "auth_param": "access_token", "ephemeral": True}):
            r = self.client.post("/api/v2/live/token", json={"dialect": "syrian"}, headers=h)
            self.assertEqual(r.status_code, 200)
            d = r.get_json()
            self.assertTrue(d["ok"])
            self.assertEqual(d["token"], "auth_tokens/ephem_123")

    def test_live_token_requires_auth_and_csrf_header(self):
        self.client.post("/api/logout", headers={"X-Requested-With": "astra"})
        r = self.client.post("/api/v2/live/token", json={})
        self.assertEqual(r.status_code, 401)

    def test_unauthenticated_blocked(self):
        self.client.post("/api/logout", headers={"X-Requested-With": "astra"})
        r = self.client.get("/api/v2/mix/audit")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
