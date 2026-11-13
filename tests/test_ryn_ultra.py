import io
import os
import sys
import time
import json
import tempfile
import types
import unittest
import zipfile
from unittest import mock


class FakeBot:
    def __init__(self):
        self.client = object()
        self.GEMINI_API_KEY = "configured"
        self.PROVIDERS = {
            "chatgpt": {"backends": [{"name": "OpenRouter", "base": "https://example.invalid", "key_env": "OPENROUTER_API_KEY", "timeout_fast": 5, "timeout_deep": 5, "prov": "chatgpt", "match": ("openai/", "gpt-oss")}]},
            "qwen": {"backends": [{"name": "OpenRouter", "base": "https://example.invalid", "key_env": "OPENROUTER_API_KEY", "timeout_fast": 5, "timeout_deep": 5, "prov": "qwen", "match": ("qwen",)}]},
            "deepseek": {"backends": [{"name": "OpenRouter", "base": "https://example.invalid", "key_env": "OPENROUTER_API_KEY", "timeout_fast": 5, "timeout_deep": 5, "prov": "deepseek", "match": ("deepseek",)}]},
            "kimi": {"backends": [{"name": "OpenRouter", "base": "https://example.invalid", "key_env": "OPENROUTER_API_KEY", "timeout_fast": 5, "timeout_deep": 5, "prov": "kimi", "match": ("kimi",)}]},
        }

    @staticmethod
    def _or_key():
        return "configured-openrouter-key"

    @staticmethod
    def _groq_ready():
        return False

    @staticmethod
    def or_models_for(provider, match, deep=False):
        return {"chatgpt": ["openai/gpt-oss-test"], "qwen": ["qwen/test"],
                "deepseek": ["deepseek/test"], "kimi": ["moonshotai/kimi-test"]}.get(provider, [])


class TestRynUltra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import flask
        except ImportError:
            import _flask_compat as flask
            sys.modules["flask"] = flask
        cls.flask = flask

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "RYN_DATA_DIR": self.tmp.name,
            "RYN_ULTRA_DB_PATH": os.path.join(self.tmp.name, "ultra-test.db"),
            "WEB_PASSWORD": "ultra-test-password",
            "WEB_USERS": "",
            "GEMINI_API_KEY": "fake-test-key",
            "OPENROUTER_API_KEY": "fake-openrouter-key",
        })
        self.env.start()
        import webapp
        import ryn_ultra
        self.webapp = webapp
        self.ultra = ryn_ultra
        self.app = self.flask.Flask("test_ryn_ultra")
        self.app.secret_key = "test-secret"
        self.bot = FakeBot()
        # webapp.register expects these optional bot attributes to exist at registration time.
        self.bot.types = types.SimpleNamespace(Content=lambda **kw: kw, Part=lambda **kw: kw)
        self.bot.chat_provider = {}
        self.bot.code_route_off = set()
        self.bot.doc_ctx = {}
        self.bot._or_ready = lambda: True
        self.bot.get_doc = lambda _w: None
        self.bot.set_doc = lambda *_a, **_kw: None
        self.webapp.register(self.app, self.bot)
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def login(self):
        r = self.client.post("/api/login", json={"password": "ultra-test-password"},
                             headers={"X-Requested-With": "astra"})
        self.assertEqual(r.status_code, 200)
        return {"X-Requested-With": "astra"}

    def test_ultra_requires_existing_web_login(self):
        self.assertEqual(self.client.get("/ultra").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/ultra/status").status_code, 401)

    def test_ultra_page_and_five_agent_status(self):
        h = self.login()
        page = self.client.get("/ultra", headers=h)
        self.assertEqual(page.status_code, 200)
        self.assertIn("RYN ULTRA", page.get_data(as_text=True))
        self.assertIn("data-agent=\"kimi\"", page.get_data(as_text=True))
        status = self.client.get("/api/v1/ultra/status", headers=h)
        self.assertEqual(status.status_code, 200)
        data = status.get_json()
        self.assertEqual(len(data["agents"]), 5)
        self.assertEqual(data["online_count"], 5)
        self.assertFalse(data["features"]["file_writes"])

    def test_start_requires_at_least_two_real_agents(self):
        h = self.login()
        self.bot.client = None
        self.bot._or_key = lambda: ""
        self.bot._groq_ready = lambda: False
        response = self.client.post("/api/v1/ultra/start", json={"task": "Analyze this project"}, headers=h)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Available: 0/5", response.get_json()["error"])

    def test_real_session_pipeline_records_agent_events_and_report(self):
        h = self.login()
        prompts = []
        def fake_invoke(_bot, agent, prompt, deep=True):
            prompts.append((agent["id"], prompt, deep))
            return (f"{agent['name']} real provider response for the test task.", f"test-model-{agent['id']}", "mocked provider")
        def wait_completed(sid):
            deadline = time.time() + 6
            data = None
            while time.time() < deadline:
                check = self.client.get("/api/v1/ultra/session/" + sid, headers=h)
                self.assertEqual(check.status_code, 200)
                data = check.get_json()
                if data["session"]["status"] in ("completed", "error"):
                    return data
                time.sleep(0.05)
            return data
        with mock.patch.object(self.ultra, "_invoke_agent", side_effect=fake_invoke):
            response = self.client.post("/api/v1/ultra/start",
                                        json={"task": "Review the current RYN architecture", "max_rounds": 1},
                                        headers=h)
            self.assertEqual(response.status_code, 202)
            sid = response.get_json()["session_id"]
            data = wait_completed(sid)
            self.assertEqual(data["session"]["status"], "completed")
            messages = [e for e in data["events"] if e["type"] == "agent_message"]
            self.assertEqual({e["agent_id"] for e in messages}, {"gemini", "gpt", "qwen", "deepseek", "kimi"})
            self.assertEqual(len(messages), 5)
            self.assertTrue(data["session"]["final_report"])
            self.assertTrue(any(e["type"] == "agent_final" for e in data["events"]))

            extra = self.client.post("/api/v1/ultra/control/" + sid, json={"action": "another_round"}, headers=h)
            self.assertEqual(extra.status_code, 200)
            data = wait_completed(sid)
            self.assertEqual(data["session"]["status"], "completed")
            messages = [e for e in data["events"] if e["type"] == "agent_message"]
            self.assertEqual(len(messages), 10)
            self.assertTrue(any("DISCUSSION SO FAR" in prompt for _agent, prompt, _deep in prompts))

    def test_project_zip_inspection_skips_secrets_and_redacts_inline_keys(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("sample/main.py", 'API_KEY = "sk-supersecretvalue123456789"\nprint("hello")\n')
            zf.writestr("sample/.env", "TOKEN=do-not-read-this")
            zf.writestr("sample/node_modules/pkg/index.js", "not part of project")
        result = self.ultra._zip_project_context(buf.getvalue(), "sample.zip")
        self.assertEqual(result["text_files_included"], 1)
        self.assertIn("main.py", result["project_context"])
        self.assertNotIn("supersecretvalue", result["project_context"])
        self.assertNotIn("do-not-read-this", result["project_context"])
        self.assertNotIn("node_modules", result["project_context"])

    def test_session_access_is_user_scoped(self):
        h = self.login()
        response = self.client.get("/api/v1/ultra/session/ultra_nonexistent", headers=h)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
