# -*- coding: utf-8 -*-
"""
tests/test_ryn_ultra.py — Comprehensive tests for RYN Ultra (Sections 1..15).
"""

import os
import sys
import tempfile
import types
import unittest

from core.ryn_ultra import (
    RynUltraEngine,
    DEFAULT_ULTRA_AGENTS,
    ROBOT_STATES,
)


class TestRynUltraSystem(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "ultra.db")
        self.engine = RynUltraEngine(db_path=self.db_path)
        self.callers = {
            "gemini": lambda p, s: "I analyzed the request. We should establish the architecture and identify required components.",
            "gpt": lambda p, s: "I disagree with a monolithic layer; however, we need a separate orchestration layer with clear boundaries.",
            "qwen": lambda p, s: "Here is the implementation patch:\n```python\ndef orchestrate():\n    return True\n```",
            "deepseek": lambda p, s: "We must enforce max rounds and token limits to prevent infinite loops.",
            "kimi": lambda p, s: "Based on existing requirements, preserve current RYN modules without duplication.",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_five_team_members_and_robot_states(self):
        self.assertEqual(len(DEFAULT_ULTRA_AGENTS), 5)
        ids = [a["id"] for a in DEFAULT_ULTRA_AGENTS]
        self.assertEqual(ids, ["gemini", "gpt", "qwen", "deepseek", "kimi"])
        for st in ("idle", "thinking", "speaking", "listening", "reviewing", "error", "offline"):
            self.assertIn(st, ROBOT_STATES)

    def test_02_multi_round_live_discussion_and_disagreement_tracking(self):
        m = self.engine.create_meeting(
            user_id="haydar",
            request_text="Design RYN Ultra multi-agent architecture",
            max_rounds=2,
        )
        done = self.engine.run_meeting(m["meeting_id"], self.callers)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["current_round"], 2)
        agent_msgs = [x for x in done["messages"] if x["sender_id"] != "user"]
        self.assertEqual(len(agent_msgs), 10)  # 5 agents * 2 rounds
        # Disagreement from GPT is preserved in shared_memory and final_report
        self.assertTrue(len(done["shared_memory"]["disagreements"]) >= 1)
        rep = done["final_report"]
        self.assertEqual(rep["original_user_request"], "Design RYN Ultra multi-agent architecture")
        self.assertTrue(len(rep["main_disagreements"]) >= 1)
        self.assertIn("independently_verified_facts", rep["distinction"])

    def test_03_code_analysis_read_only_until_explicit_approval(self):
        m = self.engine.create_meeting(
            user_id="haydar",
            request_text="Fix bug in bot.py",
            project_files={"bot.py": "print('hello')\n"},
            max_rounds=1,
        )
        # In Round 1 with project files, Kimi runs first (Repository Mapping)
        done = self.engine.run_meeting(m["meeting_id"], self.callers)
        first_agent_msg = [x for x in done["messages"] if x["sender_id"] != "user"][0]
        self.assertEqual(first_agent_msg["sender_id"], "kimi")

        # Must wait for explicit approval before applying proposed code changes
        self.assertEqual(done["status"], "waiting_approval")
        self.assertFalse(done["plan_approved"])
        self.assertEqual(done["shared_memory"]["approved_file_changes"], {})
        self.assertTrue(len(done["shared_memory"]["proposed_file_changes"]) >= 1)

        # Approve explicitly
        approved = self.engine.handle_control_action(
            m["meeting_id"],
            "approve_changes",
            provider_callers=self.callers,
        )
        self.assertEqual(approved["status"], "completed")
        self.assertTrue(approved["plan_approved"])
        self.assertEqual(
            approved["final_report"]["code_changes"]["approval_status"],
            "APPROVED",
        )

    def test_04_user_controls_pause_resume_interrupt_private_explain_update_extra_round(self):
        m = self.engine.create_meeting(
            user_id="haydar",
            request_text="Analyze caching strategy",
            max_rounds=1,
        )
        mid = m["meeting_id"]

        # Pause & Resume
        paused = self.engine.handle_control_action(mid, "pause")
        self.assertEqual(paused["status"], "paused")
        resumed = self.engine.handle_control_action(mid, "resume", provider_callers=self.callers)
        self.assertEqual(resumed["status"], "completed")

        # Private instruction to DeepSeek only
        priv = self.engine.handle_control_action(
            mid,
            "private_instruction",
            payload={"agent_id": "deepseek", "message": "Focus on SQLite concurrency"},
            provider_callers=self.callers,
        )
        self.assertIn("deepseek", priv["shared_memory"]["private_instructions"])

        # Explain reasoning
        exp = self.engine.handle_control_action(
            mid,
            "explain_reasoning",
            payload={"agent_id": "gpt", "message": "Why separate orchestration?"},
            provider_callers=self.callers,
        )
        self.assertTrue(any(x.get("tag") == "Reasoning Explanation" for x in exp["messages"]))

        # Update task mid-meeting preserves original_request
        upd = self.engine.handle_control_action(
            mid,
            "update_task",
            payload={"task": "Use SQLite WAL mode instead of Redis", "run_round": False},
        )
        self.assertEqual(upd["original_request"], "Analyze caching strategy")
        self.assertEqual(upd["current_task"], "Use SQLite WAL mode instead of Redis")

        # Request extra discussion round
        extra = self.engine.handle_control_action(
            mid,
            "extra_round",
            provider_callers=self.callers,
        )
        self.assertEqual(extra["current_round"], 2)

        # Interrupt & Stop
        inter = self.engine.handle_control_action(mid, "interrupt", payload={"agent_id": "qwen"})
        self.assertTrue(any(x.get("tag") == "Interrupt" for x in inter["messages"]))
        stopped = self.engine.handle_control_action(mid, "stop", provider_callers=self.callers)
        self.assertEqual(stopped["status"], "stopped")

    def test_05_admin_settings_and_web_routes(self):
        cfg = self.engine.update_settings("haydar", {
            "max_rounds": 4,
            "reduced_motion": True,
            "agents": [{"id": "kimi", "enabled": False, "role": "Lead Context Auditor"}],
        })
        self.assertEqual(cfg["max_rounds"], 4)
        self.assertTrue(cfg["reduced_motion"])
        kimi_cfg = next(a for a in cfg["agents"] if a["id"] == "kimi")
        self.assertFalse(kimi_cfg["enabled"])
        self.assertEqual(kimi_cfg["role"], "Lead Context Auditor")

        # Verify web UI & routes in webapp.py
        try:
            import flask
        except ImportError:
            import _flask_compat as flask
            sys.modules["flask"] = flask
        import webapp
        app = flask.Flask("test_ultra_web")
        app.secret_key = "test_secret"
        os.environ["WEB_PASSWORD"] = "secret123"
        b = types.SimpleNamespace(
            client=None,
            types=types.SimpleNamespace(Content=lambda **kw: kw, Part=types.SimpleNamespace(from_bytes=lambda **kw: kw, text="")),
            FAST_MODELS=["gemini-2.5-flash"],
            DEEP_MODELS=["gemini-2.5-pro"],
            chat_provider={},
            code_route_off=set(),
            doc_ctx={},
            _groq_ready=lambda: False,
            _or_ready=lambda: False,
            _or_key=lambda: "",
            get_doc=lambda w: None,
            set_doc=lambda w, n, p: None,
        )
        webapp.register(app, b)
        client = app.test_client()
        client.post("/api/login", json={"password": "secret123"}, headers={"X-Requested-With": "astra"})
        h = {"X-Requested-With": "astra"}

        # Check HTML has #ultraBtn, __ryn_ultra, and #ultraModal
        html = client.get("/").get_data(as_text=True)
        self.assertIn('id="ultraBtn"', html)
        self.assertIn('__ryn_ultra', html)
        self.assertIn('id="ultraModal"', html)

        # Create & run meeting via API
        r_create = client.post("/api/v2/ultra/meetings", json={"request": "Build a clean API router", "max_rounds": 1, "async_run": False}, headers=h)
        self.assertEqual(r_create.status_code, 200)
        m_data = r_create.get_json()["meeting"]
        self.assertEqual(m_data["status"], "completed")
        self.assertTrue(len(m_data["messages"]) >= 6)


if __name__ == "__main__":
    unittest.main()
