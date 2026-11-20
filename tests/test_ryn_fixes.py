"""اختبارات التصليحات (مراجعة RYN Ultra + أمان تسجيل الدخول + النماذج + ذاكرة الجهاز لكل الموقع).

كل اختبار هون بيغطي خطأ انكشف بتجربة فعلية، فإذا رجع الخطأ الاختبار بيفشل:
  - الاختبارات القديمة كانت بتستعمل بوت وهمي وما كانت تجرّب مشروع كبير ولا ضغطات متزامنة ولا انقطاع اتصال.
"""
import ast
import builtins
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types as pytypes
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import flask  # noqa: E402

import webapp  # noqa: E402
import ryn_ultra as u  # noqa: E402

H = {"X-Requested-With": "astra"}
PASSWORD = "pw-test-123456"
OR_MODELS = {"chatgpt": ["openai/gpt-oss-test"], "qwen": ["qwen/test-a", "qwen/test-b", "qwen/test-c", "qwen/test-d", "qwen/test-e"],
             "deepseek": ["deepseek/test"], "kimi": ["moonshotai/kimi-test"], "nemotron": ["nvidia/nemotron-test"]}


def wait_for(pred, timeout=20.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


class FakeBot:
    """بوت وهمي: Gemini عبر ask_gemini والباقي عبر _call_backend، مع تسجيل الطلبات يلي وصلت."""

    def __init__(self, offline=(), gate=None, fail_all=False):
        self.client = object()
        self.GEMINI_API_KEY = "configured"
        self.offline = set(offline)
        self.gate = gate
        self.fail_all = fail_all
        self.synth_started = threading.Event()
        self.prompts = []          # (model, text)
        self.models_args = []      # عدد الموديلات المرسلة لكل _call_backend
        self._lock = threading.Lock()

        def be(prov, match):
            return {"backends": [{"name": "OpenRouter", "base": "https://example.invalid", "key_env": "OPENROUTER_API_KEY",
                                  "timeout_fast": 5, "timeout_deep": 5, "prov": prov, "match": match}]}
        self.PROVIDERS = {"chatgpt": be("chatgpt", ("openai/", "gpt-oss")), "qwen": be("qwen", ("qwen",)),
                          "deepseek": be("deepseek", ("deepseek",)), "kimi": be("kimi", ("kimi",)), "nemotron": be("nemotron", ("nemotron",))}
        self.types = pytypes.SimpleNamespace(Content=lambda **kw: kw, Part=lambda **kw: kw)
        self.chat_provider = {}
        self.code_route_off = set()
        self.doc_ctx = {}
        self._or_ready = lambda: True
        self.get_doc = lambda _w: None
        self.set_doc = lambda *_a, **_k: None

    @staticmethod
    def _or_key():
        return "k"

    @staticmethod
    def _groq_ready():
        return False

    def or_models_for(self, provider, match, deep=False):
        if provider in self.offline:
            return []
        return list(OR_MODELS.get(provider, []))

    def _record(self, model, text):
        with self._lock:
            self.prompts.append((model, text))

    def ask_gemini(self, contents, deep=False, system=None, json_mode=False, preferred_model=None):
        text = contents[0]["parts"][0]["text"]
        self._record("gemini", text)
        if self.fail_all:
            raise RuntimeError("quota exhausted")
        return "gemini says something substantial about the task. " * 3

    def _call_backend(self, backend, contents, deep, json_mode, models=None, out=None):
        text = contents[0]["parts"][0]["text"]
        self._record(models[0], text)
        with self._lock:
            self.models_args.append(len(models or []))
        if self.fail_all:
            raise RuntimeError("quota exhausted")
        if "Prepare RYN Ultra's final team report" in text:
            self.synth_started.set()
            if self.gate is not None:
                self.gate.wait(30)
        out["model"] = models[0]
        return "reply from " + models[0] + " with enough detail. " * 3


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = {"RYN_DATA_DIR": self.tmp, "RYN_ULTRA_DB_PATH": os.path.join(self.tmp, "u.db"), "WEB_PASSWORD": PASSWORD,
               "WEB_USERS": "", "GEMINI_API_KEY": "fake", "OPENROUTER_API_KEY": "fake", "WEB_SECURE_COOKIE": "0"}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        os.environ.pop("WEB_SECRET", None)
        for k in ("RYN_ULTRA_AGENT_TIMEOUT", "RYN_ULTRA_SESSION_CALL_LIMIT", "TRUSTED_PROXY_HOPS", "LOGIN_GLOBAL_MAX_FAILS"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_app(self, bot=None, login=True):
        app = flask.Flask("t")
        bot = bot or FakeBot()
        webapp.register(app, bot)
        c = app.test_client()
        if login:
            self.assertEqual(c.post("/api/login", json={"password": PASSWORD}, headers=H).status_code, 200)
        return app, c, bot

    def start(self, c, **body):
        body.setdefault("task", "Review the architecture of my project and find risks")
        body.setdefault("max_rounds", 1)
        r = c.post("/api/v1/ultra/start", json=body, headers=H)
        self.assertIn(r.status_code, (200, 202), r.get_data(as_text=True))
        return r.get_json()["session_id"]

    def session(self, c, sid):
        return c.get(f"/api/v1/ultra/session/{sid}").get_json()

    def wait_status(self, c, sid, statuses=("completed", "error", "stopped"), timeout=25):
        return wait_for(lambda: self.session(c, sid)["session"]["status"] in statuses, timeout)


# ===================== أمان تسجيل الدخول =====================
class LoginSecurity(Base):
    def _bad(self, c, xff):
        h = dict(H)
        if xff:
            h["X-Forwarded-For"] = xff
        return c.post("/api/login", json={"password": "wrong"}, headers=h).status_code

    def test_spoofed_xff_cannot_bypass_throttle(self):
        _app, c, _b = self.make_app(login=False)
        with mock.patch.object(webapp.time, "sleep", lambda _s: None):
            codes = [self._bad(c, f"66.66.66.{i}, 9.9.9.9") for i in range(5)]
            self.assertEqual(codes, [401] * 5)
            # IP جديد مزوّر بأول الهيدر، بس البروكسي الموثوق (آخر قيمة) نفسو → لازم يتحظر
            self.assertEqual(self._bad(c, "77.77.77.1, 9.9.9.9"), 429)

    def test_trusted_hops_two_separates_real_clients(self):
        os.environ["TRUSTED_PROXY_HOPS"] = "2"
        _app, c, _b = self.make_app(login=False)
        with mock.patch.object(webapp.time, "sleep", lambda _s: None):
            for _ in range(5):
                self.assertEqual(self._bad(c, "5.5.5.5, 9.9.9.9"), 401)
            self.assertEqual(self._bad(c, "5.5.5.5, 9.9.9.9"), 429)      # نفس العميل
            self.assertEqual(self._bad(c, "6.6.6.6, 9.9.9.9"), 401)      # عميل تاني مو محظور

    def test_global_cap_stops_distributed_guessing(self):
        os.environ["TRUSTED_PROXY_HOPS"] = "2"
        os.environ["LOGIN_GLOBAL_MAX_FAILS"] = "5"
        _app, c, _b = self.make_app(login=False)
        with mock.patch.object(webapp.time, "sleep", lambda _s: None):
            for i in range(5):
                self.assertEqual(self._bad(c, f"10.0.0.{i}, 9.9.9.9"), 401)
            self.assertEqual(self._bad(c, "10.0.0.99, 9.9.9.9"), 429)

    def test_correct_password_still_works(self):
        _app, c, _b = self.make_app(login=False)
        self.assertEqual(c.post("/api/login", json={"password": PASSWORD}, headers=H).status_code, 200)

    def test_session_secret_is_random_persistent_and_not_derived_from_passwords(self):
        a1, _c1, _ = self.make_app(login=False)
        a2, _c2, _ = self.make_app(login=False)
        self.assertGreaterEqual(len(a1.secret_key), 32)
        self.assertEqual(a1.secret_key, a2.secret_key)                 # محفوظ بملف: نفس المفتاح بعد إعادة التشغيل
        old = hashlib.sha256(("astra-web|" + PASSWORD + "|").encode()).hexdigest()
        self.assertNotEqual(a1.secret_key, old)                        # مو مشتق من كلمة السر
        path = os.path.join(self.tmp, "ryn_web_secret.key")
        self.assertTrue(os.path.exists(path))
        if os.name == "posix":
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_web_secret_env_wins(self):
        os.environ["WEB_SECRET"] = "x" * 40
        app, _c, _b = self.make_app(login=False)
        self.assertEqual(app.secret_key, "x" * 40)

    def test_preflight_status_is_computed_and_warns(self):
        _app, c, _b = self.make_app()
        os.environ.pop("RYN_DATA_DIR", None)
        d = c.get("/api/v2/preflight/status").get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["project_health"]["baseline_test_status"], "PASSED")   # من audit/baseline_tests.json
        self.assertTrue(d["project_health"]["baseline_is_recorded_not_live"])
        joined = " ".join(d["warnings"])
        self.assertIn("WEB_SECRET", joined)
        self.assertIn("RYN_DATA_DIR", joined)


# ===================== منع الأسماء غير المعرّفة (NameError مبلوع) =====================
def undefined_names(path):
    with open(path, encoding="utf-8") as fh:
        t = ast.parse(fh.read())
    b = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__builtins__", "__class__", "__spec__", "__path__"}
    defined, loads = set(), []
    for n in ast.walk(t):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
            if not isinstance(n, ast.ClassDef):
                a = n.args
                for x in a.args + a.kwonlyargs + a.posonlyargs:
                    defined.add(x.arg)
                for x in (a.vararg, a.kwarg):
                    if x:
                        defined.add(x.arg)
        elif isinstance(n, ast.Lambda):
            a = n.args
            for x in a.args + a.kwonlyargs + a.posonlyargs:
                defined.add(x.arg)
            for x in (a.vararg, a.kwarg):
                if x:
                    defined.add(x.arg)
        elif isinstance(n, ast.Name):
            if isinstance(n.ctx, (ast.Store, ast.Del)):
                defined.add(n.id)
            else:
                loads.append((n.id, n.lineno))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            defined.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            defined.update(n.names)
    return sorted({(nm, ln) for nm, ln in loads if nm not in defined and nm not in b})


class StaticChecks(unittest.TestCase):
    def test_no_undefined_names(self):
        for f in ("webapp.py", "ryn_ultra.py", "bot.py", "ryn_ai_stack.py", "ryn_guard.py"):
            self.assertEqual(undefined_names(os.path.join(ROOT, f)), [], f)

    def test_bot_imports_without_google_genai(self):
        import bot
        self.assertTrue(hasattr(bot, "ask_gemini"))


# ===================== RYN Ultra =====================
class UltraPromptBudget(Base):
    def _transcript(self):
        names = [("Gemini", "Chief Strategist"), ("GPT", "Chief Architect"), ("Qwen", "Lead Developer"),
                 ("DeepSeek", "Critical Reviewer"), ("Kimi", "Context Analyst")]
        return "\n\n".join(f"[Round 1 · {n} ({r})]\n" + ("تحليل مفصل. " * 420)[:5000] for n, r in names)

    def test_big_project_never_pushes_out_the_debate(self):
        project = ("\n--- FILE: x.py ---\n" + "a" * 100) * 600          # ~ 69 ألف حرف
        for agent_id in ("gemini", "gpt", "qwen", "deepseek", "kimi"):
            ag = u.AGENT_BY_ID[agent_id]
            budget = u._input_budget() - len(u._agent_system(ag)) - 400
            prompt = u._build_agent_prompt("راجع هالمشروع", "proj", project, 2, self._transcript(), [], ag, memory_context="- بحب القهوة")
            self.assertLessEqual(len(prompt), budget)
            self.assertIn("LIVE TEAM DEBATE", prompt)
            self.assertIn("DISCUSSION SO FAR", prompt)
            self.assertIn("[Round 1 · Kimi", prompt)                      # آخر رسالة بالنقاش موجودة
            self.assertIn("Your assigned responsibility", prompt)         # الذيل ما انقصّ
            self.assertIn("USER DEVICE MEMORY", prompt)
            self.assertIn("shortened to fit", prompt)                     # المشروع هو يلي انقصّ

    def test_small_project_is_kept_whole(self):
        ag = u.AGENT_BY_ID["qwen"]
        prompt = u._build_agent_prompt("task", "proj", "tiny project text", 2, self._transcript(), [], ag)
        self.assertIn("tiny project text", prompt)
        self.assertNotIn("shortened to fit", prompt)

    def test_tiny_budget_is_still_safe(self):
        ag = u.AGENT_BY_ID["gpt"]
        prompt = u._build_agent_prompt("t" * 12000, "proj", "p" * 50000, 3, self._transcript(), ["be brief"], ag, budget=6000)
        self.assertLessEqual(len(prompt), 6000)
        self.assertIn("Your assigned responsibility", prompt)

    def test_bad_env_values_do_not_break_agents(self):
        os.environ["RYN_ULTRA_AGENT_TIMEOUT"] = "90s"
        os.environ["RYN_ULTRA_MAX_OUTPUT_TOKENS"] = "lots"
        os.environ["RYN_ULTRA_MAX_INPUT_CHARS"] = ""
        bot = FakeBot()
        text, model, _prov = u._invoke_agent(bot, u.AGENT_BY_ID["deepseek"], "hello")
        self.assertIn("reply from", text)
        self.assertEqual(u._env_int("RYN_ULTRA_AGENT_TIMEOUT", 90, 15, 180), 90)

    def test_fallback_models_are_capped(self):
        bot = FakeBot()
        u._invoke_agent(bot, u.AGENT_BY_ID["qwen"], "hello")
        self.assertLessEqual(max(bot.models_args), 3)                    # كان لحد 5 محاولات × 90 ثانية

    def test_contents_fallback_matches_what_build_messages_expects(self):
        c = u._contents(pytypes.SimpleNamespace(types=None), "hi")
        self.assertEqual(c[0].parts[0].text, "hi")
        self.assertEqual(c[0].role, "user")


class UltraWorkflow(Base):
    def test_status_is_running_during_meeting_and_extra_round_during_report_is_not_lost(self):
        gate = threading.Event()
        _app, c, bot = self.make_app(FakeBot(gate=gate))
        sid = self.start(c, max_rounds=1)
        self.assertTrue(bot.synth_started.wait(20))
        self.assertEqual(self.session(c, sid)["session"]["status"], "running")      # كان يضل queued
        r = c.post(f"/api/v1/ultra/control/{sid}", json={"action": "another_round"}, headers=H)
        self.assertEqual(r.status_code, 200)
        gate.set()
        self.assertTrue(self.wait_status(c, sid, ("completed", "error", "stopped")))
        rounds = lambda: [e["round"] for e in self.session(c, sid)["events"] if e["type"] == "round_started"]   # noqa: E731
        self.assertTrue(wait_for(lambda: rounds() == [1, 2], 20), rounds())      # الجولة الإضافية اشتغلت (كانت بتضيع)
        s = self.session(c, sid)
        self.assertEqual(s["session"]["status"], "completed")
        self.assertEqual(s["session"]["max_rounds"], 2)
        finals = [e for e in s["events"] if e["type"] == "agent_final"]
        self.assertEqual(len(finals), 2)                                          # تقرير نهائي بعد كل مرحلة

    def test_another_round_after_completion_runs_exactly_one_round(self):
        gate = threading.Event()
        gate.set()
        _app, c, _bot = self.make_app(FakeBot(gate=gate))
        sid = self.start(c, max_rounds=1)
        self.assertTrue(self.wait_status(c, sid, ("completed",)))
        c.post(f"/api/v1/ultra/control/{sid}", json={"action": "another_round"}, headers=H)
        rounds = lambda: [e["round"] for e in self.session(c, sid)["events"] if e["type"] == "round_started"]   # noqa: E731
        self.assertTrue(wait_for(lambda: rounds() == [1, 2] and self.session(c, sid)["session"]["status"] == "completed", 20))
        time.sleep(0.5)
        self.assertEqual(rounds(), [1, 2])

    def test_stop_during_report_is_not_overwritten(self):
        gate = threading.Event()
        _app, c, bot = self.make_app(FakeBot(gate=gate))
        sid = self.start(c, max_rounds=1)
        self.assertTrue(bot.synth_started.wait(20))
        c.post(f"/api/v1/ultra/control/{sid}", json={"action": "stop"}, headers=H)
        gate.set()
        wait_for(lambda: sid not in u._RUNNING, 10)
        self.assertEqual(self.session(c, sid)["session"]["status"], "stopped")     # كان بينكتب completed فوقها

    def test_all_agents_failing_ends_with_error_and_stream_sends_end_event(self):
        _app, c, _bot = self.make_app(FakeBot(fail_all=True))
        sid = self.start(c, max_rounds=1)
        self.assertTrue(self.wait_status(c, sid, ("error",)))
        body = c.get(f"/api/v1/ultra/events/{sid}?after=0").get_data(as_text=True)
        self.assertIn("event: end", body)
        self.assertIn('"status": "error"', body)

    def test_stream_honours_last_event_id(self):
        gate = threading.Event()
        gate.set()
        _app, c, _bot = self.make_app(FakeBot(gate=gate))
        sid = self.start(c, max_rounds=1)
        self.assertTrue(self.wait_status(c, sid, ("completed",)))
        events = self.session(c, sid)["events"]
        mid = events[len(events) // 2]["id"]
        body = c.get(f"/api/v1/ultra/events/{sid}?after=0", headers={"Last-Event-ID": str(mid)}).get_data(as_text=True)
        ids = [int(x) for x in re.findall(r"^id: (\d+)$", body, re.M)]
        self.assertTrue(ids)
        self.assertTrue(all(i > mid for i in ids), (mid, ids[:5]))               # ما بيعيد القديم
        self.assertEqual(ids[-1], events[-1]["id"])
        self.assertIn("event: end", body)

    def test_budget_exhaustion_finishes_gracefully(self):
        os.environ["RYN_ULTRA_SESSION_CALL_LIMIT"] = "3"
        _app, c, _bot = self.make_app(FakeBot())
        sid = self.start(c, max_rounds=3)
        self.assertTrue(self.wait_status(c, sid, ("completed", "error")))
        s = self.session(c, sid)
        self.assertEqual(s["session"]["status"], "completed")
        self.assertTrue(any(e["status"] == "usage_budget_reached" for e in s["events"] if e["type"] == "warning"))


class UltraCommands(Base):
    def _paused_session(self, uid="main"):
        u._init_db()
        sid = "ultra_cmdtest"
        now = time.time()
        conn = u._conn()
        with conn:
            conn.execute("INSERT INTO ultra_sessions (session_id,user_id,task,status,max_rounds,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                         (sid, uid, "t", "paused", 2, now, now))
        conn.close()
        return sid

    def test_message_to_all_goes_only_to_online_agents_and_offline_target_is_rejected(self):
        _app, c, _bot = self.make_app(FakeBot(offline=("deepseek", "kimi")))
        sid = self._paused_session()
        r = c.post(f"/api/v1/ultra/message/{sid}", json={"message": "use tabs", "target": "all"}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        conn = u._conn()
        targets = {row["target"] for row in conn.execute("SELECT target FROM ultra_commands WHERE session_id=?", (sid,))}
        conn.close()
        self.assertEqual(targets, {"gemini", "gpt", "qwen", "nemotron"})
        r = c.post(f"/api/v1/ultra/message/{sid}", json={"message": "hi", "target": "kimi"}, headers=H)
        self.assertEqual(r.status_code, 409)

    def test_pending_commands_for_other_agents_never_block_the_queue(self):
        u._init_db()
        sid = self._paused_session()
        conn = u._conn()
        with conn:
            for i in range(40):
                conn.execute("INSERT INTO ultra_commands (session_id,user_id,action,content,target,created_at) VALUES (?,?,?,?,?,?)",
                             (sid, "main", "message", f"old{i}", "kimi", time.time()))
            conn.execute("INSERT INTO ultra_commands (session_id,user_id,action,content,target,created_at) VALUES (?,?,?,?,?,?)",
                         (sid, "main", "message", "NEW for qwen", "qwen", time.time()))
            conn.execute("INSERT INTO ultra_commands (session_id,user_id,action,content,target,created_at) VALUES (?,?,?,?,?,?)",
                         (sid, "main", "message", "for nobody", "ghost", time.time()))
        conn.close()
        self.assertEqual(u._take_commands(sid, "main", "qwen"), ["NEW for qwen"])     # كانت ترجّع [] بعد 30 أمر
        self.assertEqual(u._take_commands(sid, "main", "qwen"), [])                  # ما بتنعاد
        conn = u._conn()
        ghost = conn.execute("SELECT processed_at FROM ultra_commands WHERE target='ghost'").fetchone()[0]
        conn.close()
        self.assertGreater(ghost, 0)                                                # العناوين المجهولة بتنتهي


class UltraDeviceMemory(Base):
    def test_memory_reaches_all_five_agents_and_final_report_but_secrets_are_redacted(self):
        gate = threading.Event()
        gate.set()
        bot = FakeBot(gate=gate)
        _app, c, bot = self.make_app(bot)
        mem = {"enabled": True, "name": "حيدر", "custom": "جاوبني باختصار",
               "facts": ["بحب القهوة بدون سكر", "مفتاحي AIza" + "a" * 35]}
        sid = self.start(c, max_rounds=1, memory=mem)
        self.assertTrue(self.wait_status(c, sid, ("completed",)))
        agent_prompts = [(m, t) for m, t in bot.prompts if "Prepare RYN Ultra's final team report" not in t]
        self.assertGreaterEqual(len(agent_prompts), 5)
        for model, text in agent_prompts:
            self.assertIn("USER DEVICE MEMORY", text, model)
            self.assertIn("بحب القهوة بدون سكر", text, model)
            self.assertNotIn("AIza" + "a" * 35, text)
        final = [t for _m, t in bot.prompts if "Prepare RYN Ultra's final team report" in t]
        self.assertTrue(final and "بحب القهوة بدون سكر" in final[0])
        s = self.session(c, sid)
        self.assertTrue(s["session"]["has_memory"])
        self.assertNotIn("memory_context", s["session"])                              # النص ما بيرجع للمتصفح

    def test_memory_disabled_or_missing_adds_nothing(self):
        gate = threading.Event()
        gate.set()
        _app, c, bot = self.make_app(FakeBot(gate=gate))
        sid = self.start(c, max_rounds=1, memory={"enabled": False, "facts": ["secret fact"]})
        self.assertTrue(self.wait_status(c, sid, ("completed",)))
        self.assertTrue(all("USER DEVICE MEMORY" not in t for _m, t in bot.prompts))
        self.assertFalse(self.session(c, sid)["session"]["has_memory"])

    def test_old_database_gets_migrated(self):
        path = os.path.join(self.tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE ultra_sessions (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, task TEXT NOT NULL, "
                     "project_name TEXT NOT NULL DEFAULT '', project_context TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, "
                     "max_rounds INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                     "final_report TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '')")
        conn.commit()
        conn.close()
        with mock.patch.dict(os.environ, {"RYN_ULTRA_DB_PATH": path}):
            u._init_db()
            c2 = u._conn()
            cols = {r[1] for r in c2.execute("PRAGMA table_info(ultra_sessions)")}
            c2.close()
        self.assertIn("memory_context", cols)

    def test_pages_embed_the_shared_memory_library_with_security_headers(self):
        _app, c, _bot = self.make_app()
        r = c.get("/ultra")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("window.RynMem", body)
        self.assertNotIn("__RYN_MEMORY_JS__", body)
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        main = c.get("/").get_data(as_text=True)
        self.assertIn("window.RynMem", main)
        for ph in ("__RYN_MEMORY_JS__", "__RYN_DUPLEX_JS__", "__RYN_LIVE_JS__"):
            self.assertNotIn(ph, main)


# ===================== ذاكرة الجهاز: المتصفح (node) + احتياط السيرفر =====================
@unittest.skipUnless(shutil.which("node"), "node غير متوفر")
class DeviceMemoryJs(unittest.TestCase):
    def test_node_harness(self):
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "memory_harness.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_python_ranking_agrees_with_browser_ranking(self):
        facts = ["أنا بحب القهوة بدون سكر", "اسمي حيدر وبشتغل على خرائط Craftland بالموبايل", "بستعمل لابتوب ويندوز للشغل",
                 "الموقع بيشتغل على Render ومخزّن بـ GitHub", "أختي عندها كوتا صور منفصلة"]
        queries = ["كيف بحب القهوة؟", "خريطة craftland جديدة", "شو نوع اللابتوب؟", "نشر على render", "كوتا الصور لأختي"]
        js = ("const M=require(%s);const f=%s;const qs=%s;"
              "console.log(JSON.stringify(qs.map(q=>{const r=M.search({facts:f},q);return r.length?r[0].text:null})))") % (
            json.dumps(os.path.join(ROOT, "ryn_memory.js")), json.dumps(facts), json.dumps(queries))
        env = dict(os.environ)
        r = subprocess.run(["node", "-e", "global.localStorage={getItem(){return null},setItem(){}};" + js],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        js_top = json.loads(r.stdout.strip().splitlines()[-1])
        py_top = [webapp.mem_rank(facts, q, 12)[0] if webapp.mem_rank(facts, q, 12) else None for q in queries]
        for q, a, b in zip(queries, js_top, py_top):
            self.assertEqual(a, b, q)
            self.assertIsNotNone(a, q)


class ServerMemoryRanking(unittest.TestCase):
    def test_server_ranks_big_memory_by_relevance(self):
        facts = [f"معلومة عشوائية رقم {i}" for i in range(300)] + ["بحب القهوة بدون سكر"]
        top = webapp.mem_rank(facts, "كيف بحب القهوة", 12)
        self.assertEqual(top[0], "بحب القهوة بدون سكر")
        self.assertLessEqual(len(top), 12)

    def test_server_falls_back_to_newest_when_nothing_matches(self):
        facts = ["أ بب", "جج دد", "هه وو", "زز حح"]
        self.assertEqual(webapp.mem_rank(facts, "qqqq zzzz", 12)[:3], facts[:3])


# ===================== النماذج (التحقق من التوفّر 2026-11) =====================
class ModelLists(unittest.TestCase):
    DEAD_GROQ = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3-32b", "qwen/qwen3.6-27b",
                 "moonshotai/kimi-k2-instruct", "moonshotai/kimi-k2-instruct-0905", "deepseek-r1-distill-llama-70b", "gemma2-9b-it",
                 "meta-llama/llama-4-maverick-17b-128e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct"}

    def test_static_lists_have_no_known_dead_models(self):
        import bot
        for m in list(bot.GROQ_CODE_PREF) + list(bot.GROQ_FREE_DEFAULTS):
            self.assertNotIn(m, self.DEAD_GROQ)
        for m in bot.OR_FREE_DEFAULTS:
            self.assertNotIn("deepseek", m)                    # ما عاد في DeepSeek مجاني بـ OpenRouter
        for gone in ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"):      # غير موجود / ملغى عند Google
            self.assertNotIn(gone, list(bot.FAST_MODELS) + list(bot.DEEP_MODELS), gone)
        self.assertEqual(bot.FAST_MODELS[0], os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip())
        with open(os.path.join(ROOT, "webapp.py"), encoding="utf-8") as fh:
            src = fh.read()
        for dead in self.DEAD_GROQ:
            self.assertFalse(('"%s"' % dead) in src, "اسم نموذج ميت لسا بـ webapp.py: " + dead)   # ولا بقوائم الواجهة

    def test_live_models_prefer_current_generation(self):
        import voice.live_bridge as lb
        self.assertEqual(lb.FALLBACK_MODELS[0], "gemini-3.8-live")
        self.assertNotIn("gemini-live-2.5-flash-preview", lb.FALLBACK_MODELS)

    def test_dead_gemini_model_is_remembered_and_skipped(self):
        import bot
        calls = []
        dead = "dead-model"

        class Err(Exception):
            code = 404

        class Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                if model == dead:
                    raise Err("models/%s is not found" % model)
                return pytypes.SimpleNamespace(text="ok from " + model)

        client = pytypes.SimpleNamespace(models=Models())
        with mock.patch.object(bot, "client", client), mock.patch.object(bot, "make_config", lambda *a, **k: None), \
                mock.patch.object(bot, "get_system_prompt", lambda: "sys"), \
                mock.patch.object(bot, "FAST_MODELS", [dead, "gemini-ok"]), mock.patch.object(bot, "DEEP_MODELS", ["gemini-ok"]), \
                mock.patch.dict(bot._gemini_live_cache, {"t": 0.0, "ids": [], "fail_t": time.time()}), \
                mock.patch.dict(bot._gemini_dead, {}, clear=True):
            self.assertEqual(bot.ask_gemini([object()]), "ok from gemini-ok")
            first = list(calls)
            calls.clear()
            self.assertEqual(bot.ask_gemini([object()]), "ok from gemini-ok")
            self.assertIn(dead, first)
            self.assertNotIn(dead, calls)                        # المرة التانية تجاوزو


if __name__ == "__main__":
    unittest.main()


class ModelListAdditions(Base):
    """الموديلات الموجودة ما بتتغيّر؛ بس بتنضاف الناقصة من كتالوج OpenRouter وGemini."""

    def test_existing_models_kept_and_missing_ones_added(self):
        class CatalogBot(FakeBot):
            def openrouter_catalog(self, force=False):
                return [
                    {"id": "deepseek/deepseek-r1:free", "free": True, "ctx": 1000},
                    {"id": "openai/gpt-4o-mini", "free": False, "ctx": 128000},
                    {"id": "openai/whisper-large", "free": False, "ctx": 10},
                ]

            def free_chat_models(self, limit=25):
                return ["deepseek/deepseek-r1:free"]

            def discover_gemini_models(self):
                return ["gemini-3.8-flash", "gemini-4-hydrogen"]

        bot = CatalogBot()
        _app, c, _ = self.make_app(bot)
        models = c.get("/api/models", headers=H).get_json()
        items = models["options"]
        ids = [m["id"] for m in items]
        # الموجود قبل كان ولسا موجود بنفس الـ id
        self.assertIn("gemini:gemini-3.8-flash", ids)
        self.assertIn("or:deepseek/deepseek-r1:free", ids)
        # الناقص انضاف
        self.assertIn("gemini:gemini-4-hydrogen", ids)
        # whisper (صوت مو محادثة) ما بينضاف
        self.assertNotIn("or:openai/whisper-large", ids)
        # المدفوعة ما بتنضاف (المستخدم طلب مجاني فقط)
        self.assertNotIn("or:openai/gpt-4o-mini", ids)
        # ما في تكرار
        self.assertEqual(len(ids), len(set(ids)))


class DrawerLayout(Base):
    def test_counters_button_and_translate_sit_under_chats_and_menu_is_collapsed(self):
        _app, c, _b = self.make_app()
        html = c.get("/").get_data(as_text=True)
        self.assertNotIn('id="menuBtn" aria-label="القائمة"', html.split('id="dList"')[0])   # ⋮ مو بالشريط العلوي
        drawer = html[html.index('id="dList"'):html.index('id="dFoot"')]
        self.assertLess(drawer.index('id="dList"'), drawer.index('class="dsubbar"'))
        self.assertLess(drawer.index('class="dsubbar"'), drawer.index('class="d-actions"'))
        bar = drawer[drawer.index('class="dsubbar"'):drawer.index('class="d-actions"')]
        self.assertIn('id="menuBtn"', bar)
        self.assertIn('id="langBtn"', bar)
        self.assertIn('id="menu" hidden', html)                                               # القائمة مخفية بالأول
        self.assertIn('id="pcLayoutBtn"', html[:html.index('id="drawer"')])
