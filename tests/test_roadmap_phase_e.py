# -*- coding: utf-8 -*-
import os, tempfile, time, unittest

from core.orchestrator import Budget, RynMixOrchestrator, Specialist


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = {"direct": 0}

        def direct(text, ctx):
            self.calls["direct"] += 1
            return "RYN:" + text[:20] + ("|spec" if ctx.get("specialist_outputs") else "")

        self.o = RynMixOrchestrator(direct, budget=Budget(max_seconds=3, max_tool_calls=3, max_retries=1),
                                    db_path=os.path.join(self.tmp.name, "m.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _code_tool(self, fn, **kw):
        self.o.register_specialist(Specialist(name="coder", fn=fn, handles=["code"], **kw))

    def test_plain_chat_goes_direct(self):
        r = self.o.run("u", "كيفك اليوم")
        self.assertEqual(r["route"], "direct")
        self.assertTrue(r["answer"].startswith("RYN:"))

    def test_specialist_used_but_ryn_mix_owns_final_answer(self):
        self._code_tool(lambda p: "patch-suggestion")
        r = self.o.run("u", "اكتب كود python للدالة")
        self.assertEqual(r["route"], "specialist")
        self.assertEqual(r["tools_used"], ["coder"])
        self.assertIn("|spec", r["answer"])  # التوليف بيد RYN Mix
        self.assertEqual(self.calls["direct"], 1)

    def test_specialist_failure_falls_back_safely(self):
        def boom(_p):
            raise RuntimeError("down")
        self._code_tool(boom)
        r = self.o.run("u", "عندي bug بالكود")
        self.assertEqual(r["route"], "direct_fallback")
        self.assertEqual(r["outcome"], "answered_fallback")
        self.assertTrue(r["answer"])

    def test_timeout_falls_back(self):
        self._code_tool(lambda p: time.sleep(2) or "late", timeout_sec=0.2)
        r = self.o.run("u", "كود function")
        self.assertEqual(r["route"], "direct_fallback")

    def test_validator_rejects_bad_output(self):
        self._code_tool(lambda p: "bad", validator=lambda o: o == "good")
        r = self.o.run("u", "اكتب كود")
        self.assertEqual(r["route"], "direct_fallback")

    def test_retry_budget_respected(self):
        n = {"c": 0}
        def flaky(_p):
            n["c"] += 1
            raise RuntimeError("x")
        self._code_tool(flaky)
        self.o.run("u", "bug بالكود")
        self.assertEqual(n["c"], 2)  # محاولة + إعادة وحدة

    def test_destructive_needs_confirmation_and_tool_not_called(self):
        n = {"c": 0}
        self._code_tool(lambda p: n.__setitem__("c", n["c"] + 1) or "ok")
        r = self.o.run("u", "احذف كل الكود القديم")
        self.assertEqual(r["outcome"], "needs_confirmation")
        self.assertIn("destructive", r["needs_confirmation"])
        self.assertEqual(n["c"], 0)
        self.assertEqual(self.calls["direct"], 0)

    def test_confirmed_action_proceeds(self):
        self._code_tool(lambda p: "ok")
        r = self.o.run("u", "احذف كل الكود القديم", confirmed_actions=["destructive"])
        self.assertEqual(r["outcome"], "answered")

    def test_tool_level_confirmation(self):
        self.o.register_specialist(Specialist(name="publisher", fn=lambda p: "done", handles=["writing"], needs_confirmation=True))
        r = self.o.run("u", "اكتب بوست")
        self.assertIn("tool:publisher", r["needs_confirmation"])
        r2 = self.o.run("u", "اكتب بوست", confirmed_actions=["publisher"])
        self.assertEqual(r2["route"], "specialist")

    def test_image_requests_not_touched(self):
        self.o.register_specialist(Specialist(name="anything", fn=lambda p: "x", handles=["image", "chat"]))
        r = self.o.run("u", "ارسم لي صورة قطة")
        self.assertEqual(r["route"], "protected")
        self.assertEqual(r.get("tools_used"), [])

    def test_disabled_specialist_ignored(self):
        self._code_tool(lambda p: "x")
        self.o.set_enabled("coder", False)
        self.assertEqual(self.o.run("u", "اكتب كود")["route"], "direct")

    def test_bad_schema_rejected(self):
        self._code_tool(lambda p: "x", input_schema={"text": str, "must_have": int})
        self.assertEqual(self.o.run("u", "اكتب كود")["route"], "direct_fallback")

    def test_audit_logged_without_secrets_and_per_user(self):
        self.o.run("alice", "كيفك ghp_" + "Q" * 30)
        self.o.run("bob", "مرحبا")
        a = self.o.list_audit("alice")
        self.assertEqual(len(a), 1)
        self.assertNotIn("Q" * 30, repr(a))
        self.assertEqual({s["stage"] for s in a[0]["stages"]} >= {"input", "intent", "plan", "route", "synthesis", "audit"}, True)
        self.assertEqual(len(self.o.list_audit("bob")), 1)

    def test_direct_failure_reported_not_crash(self):
        o = RynMixOrchestrator(lambda t, c: (_ for _ in ()).throw(RuntimeError("llm down")),
                               db_path=os.path.join(self.tmp.name, "m2.db"))
        r = o.run("u", "هلا")
        self.assertEqual(r["outcome"], "error")


if __name__ == "__main__":
    unittest.main()
