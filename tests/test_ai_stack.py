# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryn_ai_stack import (
    ModelQuotaTracker,
    ChromaSemanticMemory,
    html_to_clean_markdown,
    InstructorStructuredEngine,
    PromptfooEvalEngine,
    TreeSitterASTAnalyzer,
    DSPyPromptOptimizer,
    LangGraphStateEngine,
    run_full_stack_diagnostics,
)


class TestRynAIStack(unittest.TestCase):
    def test_1_crawl4ai_html_to_markdown(self):
        html = """
        <html><head><title>Ryn Docs</title><script>evil()</script></head>
        <body>
          <nav>Menu</nav>
          <h1>Welcome to Ryn</h1>
          <p>This is a <b>powerful</b> AI assistant. Visit <a href="https://example.com">Example</a>.</p>
          <ul><li>LiteLLM Router</li><li>Chroma Memory</li></ul>
          <pre>print("hello")</pre>
        </body></html>
        """
        title, md = html_to_clean_markdown(html)
        self.assertEqual(title, "Ryn Docs")
        self.assertIn("# Welcome to Ryn", md)
        self.assertIn("**powerful**", md)
        self.assertIn("[Example](https://example.com)", md)
        self.assertIn("- LiteLLM Router", md)
        self.assertIn('```\nprint("hello")\n```', md)
        self.assertNotIn("evil()", md)

    def test_2_chroma_semantic_memory(self):
        tmp = tempfile.mktemp(suffix=".json")
        chroma = ChromaSemanticMemory(storage_path=tmp)
        chroma.add_interaction("u1", "أنا أبرمج تطبيق بايثون باستخدام Flask و SQLite", "ممتاز، سأساعدك في كود Flask وقاعدة بيانات SQLite.")
        chroma.add_interaction("u1", "أحب شرب القهوة في الصباح", "صحة وهنا!")
        ctx = chroma.query_relevant_context("u1", "كيف أربط قاعدة بيانات في مشروع بايثون Flask؟", top_k=2)
        self.assertIn("Flask", ctx)
        self.assertIn("ذاكرة Chroma الدلالية", ctx)

    def test_3_instructor_structured_engine(self):
        inst = InstructorStructuredEngine()
        calls = []

        def flaky_llm(_p):
            calls.append(1)
            if len(calls) == 1:
                return '{"title": "Only title"}'
            return '```json\n{"title": "Fixed", "steps": ["step1", "step2"]}\n```'

        out = inst.create_structured(
            "Build a plan",
            required_keys=["title", "steps"],
            call_llm_fn=flaky_llm,
            max_retries=2,
        )
        self.assertEqual(out["title"], "Fixed")
        self.assertEqual(len(out["steps"]), 2)
        self.assertEqual(len(calls), 2)

    def test_4_per_model_quota_tracker(self):
        tmp = tempfile.mktemp(suffix=".json")
        qt = ModelQuotaTracker(storage_path=tmp)
        qt.record_usage("u_test", "gemini", requests_delta=3, tokens_delta=1500)
        qt.record_usage("u_test", "gpt-oss-120b", requests_delta=2, tokens_delta=900)
        res = qt.get_user_model_quotas("u_test", img_override_used=5)
        by_id = {m["id"]: m for m in res["models"]}
        self.assertEqual(by_id["gemini"]["requests_used"], 3)
        self.assertEqual(by_id["gpt_oss"]["requests_used"], 2)
        self.assertEqual(by_id["visual_brain"]["requests_used"], 5)
        for k in ("litellm", "chroma", "crawl4ai", "instructor", "promptfoo", "treesitter", "dspy", "langgraph"):
            self.assertTrue(res["stack"][k])

    def test_5_promptfoo_eval_engine(self):
        pf = PromptfooEvalEngine()
        res_ok = pf.evaluate_output("def calc(a, b):\n    return a + b\n", assertions=[
            {"type": "python-syntax-valid"},
            {"type": "no-secret-leak"},
            {"type": "contains", "value": "return"},
        ])
        self.assertTrue(res_ok["passed"])
        self.assertEqual(res_ok["score"], 1.0)

        res_bad = pf.evaluate_output("key = 'sk-proj-1234567890abcdefghijklmno'", assertions=[{"type": "no-secret-leak"}])
        self.assertFalse(res_bad["passed"])

    def test_6_treesitter_ast_analyzer(self):
        ts = TreeSitterASTAnalyzer()
        py_res = ts.parse_code_structure("python", "import os\nclass App:\n    def start(self):\n        return os.getcwd()\n")
        self.assertTrue(py_res["valid"])
        self.assertEqual(len(py_res["classes"]), 1)
        self.assertEqual(py_res["functions"][0]["name"], "start")

        js_bad = ts.parse_code_structure("javascript", "function broken(a, b { return a + b; }")
        self.assertFalse(js_bad["valid"])
        self.assertTrue(len(js_bad["error_nodes"]) >= 1)

    def test_7_dspy_prompt_optimizer(self):
        dspy_opt = DSPyPromptOptimizer()
        attempts = []

        def mock_llm(p):
            attempts.append(p)
            return "bad" if len(attempts) == 1 else "valid_sql_query"

        res = dspy_opt.optimize_with_metric(
            "question -> sql_query",
            {"question": "count users"},
            call_llm_fn=mock_llm,
            metric_fn=lambda out: (out == "valid_sql_query", "must output valid_sql_query"),
            max_bootstraps=2,
        )
        self.assertTrue(res["passed_metric"])
        self.assertEqual(res["steps_run"], 2)

    def test_8_langgraph_state_engine_and_diagnostics(self):
        lg = LangGraphStateEngine(entry_point="investigate")
        lg.add_node("investigate", lambda st: {"root_cause": "found", "attempt": st.get("attempt", 0) + 1})
        lg.add_node("fix", lambda st: {"fixed": st.get("attempt", 0) >= 2})
        lg.add_edge("investigate", "fix")
        lg.add_conditional_edges("fix", lambda st: LangGraphStateEngine.END if st.get("fixed") else "investigate")
        out_st = lg.invoke({"attempt": 0})
        self.assertTrue(out_st["fixed"])
        self.assertEqual(out_st["_langgraph_trace"], ["investigate", "fix", "investigate", "fix"])

        diag = run_full_stack_diagnostics()
        self.assertTrue(diag["passed_all"])
        self.assertEqual(diag["passed_count"], 9)


if __name__ == "__main__":
    unittest.main()
