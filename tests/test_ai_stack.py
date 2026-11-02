import os
import tempfile
import unittest
from ryn_ai_stack import (
    LiteLLMRouter,
    ChromaSemanticMemory,
    html_to_clean_markdown,
    InstructorStructuredEngine,
    PromptfooEvalEngine,
    TreeSitterASTAnalyzer,
    DSPyPromptOptimizer,
    LangGraphStateEngine,
    GitHubFileManager,
    ModelQuotaTracker,
    run_full_stack_diagnostics,
)


class TestRynAIStack(unittest.TestCase):
    def test_quota_tracker_and_litellm(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q_path = os.path.join(tmpdir, "quotas.json")
            qt = ModelQuotaTracker(storage_path=q_path)
            qt.record_usage("u1", "gemini", requests_delta=3, tokens_delta=450)
            snap = qt.get_user_model_quotas("u1")
            gem = [m for m in snap["models"] if m["id"] == "gemini"][0]
            self.assertEqual(gem["requests_used"], 3)
            self.assertEqual(gem["tokens_used"], 450)
            router = LiteLLMRouter(quota_tracker=qt)
            self.assertIsNotNone(router)

    def test_chroma_semantic_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "chroma.json")
            mem = ChromaSemanticMemory(storage_path=db_path)
            mem.add_interaction("u1", "اسمي حيدر وأعمل على تطوير بوت Ryn بالبايثون وفلاسك", "أهلاً بك يا حيدر في تطوير Ryn")
            ctx = mem.query_relevant_context("u1", "مطور بوت Ryn بايثون وفلاسك")
            self.assertIn("Chroma", ctx)
            self.assertIn("حيدر", ctx)

    def test_crawl4ai_markdown(self):
        title, md = html_to_clean_markdown("<html><head><title>Test Page</title></head><body><h1>Hello</h1><p>World <b>Bold</b></p></body></html>")
        self.assertEqual(title, "Test Page")
        self.assertIn("# Hello", md)
        self.assertIn("**Bold**", md)

    def test_instructor_structured_engine(self):
        inst = InstructorStructuredEngine()
        sample = 'إليك النتيجة:\n```json\n{"title": "Ryn Bot", "summary": "AI Assistant"}\n```'
        parsed = inst.extract_json_dict(sample)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["title"], "Ryn Bot")

    def test_promptfoo_eval_engine(self):
        pf = PromptfooEvalEngine()
        res = pf.evaluate_output(
            "def add(a, b):\n    return a + b\n",
            assertions=[
                {"type": "python-syntax-valid"},
                {"type": "no-secret-leak"},
                {"type": "contains", "value": "return"},
            ],
        )
        self.assertTrue(res["passed"])
        self.assertEqual(res["score"], 1.0)

    def test_tree_sitter_ast_analyzer(self):
        ts = TreeSitterASTAnalyzer()
        py_res = ts.parse_code_structure("python", "class Bot:\n    def run(self, x):\n        return x + 1\n")
        self.assertTrue(py_res["valid"])
        self.assertEqual(py_res["classes"][0]["name"], "Bot")
        self.assertEqual(py_res["functions"][0]["name"], "run")

    def test_dspy_prompt_optimizer(self):
        dspy = DSPyPromptOptimizer()
        comp = dspy.compile_chain_of_thought_prompt("request -> code", {"request": "fibonacci"})
        self.assertIn("DSPy ChainOfThought Signature", comp)

    def test_langgraph_state_engine(self):
        lg = LangGraphStateEngine(entry_point="plan")
        lg.add_node("plan", lambda st: {"done": True})
        lg.add_edge("plan", LangGraphStateEngine.END)
        out = lg.invoke({})
        self.assertTrue(out["done"])
        self.assertEqual(out["_langgraph_trace"], ["plan"])

    def test_github_owner_only_env_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.environ.get("GITHUB_TOKEN")
            os.environ["GITHUB_TOKEN"] = "ghp_envtoken1234567890abcdef"
            try:
                gh = GitHubFileManager(storage_path=os.path.join(tmpdir, "gh.json"))
                # المالك (WEB_PASSWORD) ياخد التوكن من البيئة
                cfg_owner = gh.get_config("main")
                self.assertTrue(cfg_owner["is_owner"])
                self.assertTrue(cfg_owner["has_token"])
                self.assertEqual(cfg_owner["_token"], "ghp_envtoken1234567890abcdef")
                # حساب ثاني: فاضي ومنفصل، وما بياخد توكن البيئة
                cfg_ali = gh.get_config("ali")
                self.assertFalse(cfg_ali["has_token"])
                self.assertEqual(cfg_ali["repo"], "")
                with self.assertRaises(PermissionError):
                    gh.list_files("ali")
                self.assertIn("توكنك", gh.handle_direct_chat_command("ali", "عدلي ملف bot.py على جيتهاب"))
                with self.assertRaises(PermissionError):
                    gh.set_config("ali", repo="haeeedar6-ops/my-ai-bot", token="ghp_1234567890abcdefghijklmnop")
                # ali يحط توكنه من الشات
                r = gh.handle_direct_chat_command("ali", "اربط جيتهاب ghp_1234567890abcdefghijklmnop مستودع ali-dev/my-app")
                self.assertIn("ali-dev/my-app", r)
                self.assertEqual(gh.get_config("ali")["_token"], "ghp_1234567890abcdefghijklmnop")
                # حساب ثالث منفصل عن ali
                self.assertFalse(gh.get_config("omar")["has_token"])
                # المالك: التوكن من البيئة فقط
                self.assertIn("GITHUB_TOKEN", gh.handle_direct_chat_command("main", "اربط جيتهاب ghp_1234567890abcdefghijklmnop"))
                gh.set_config("main", repo="x/y", token="ghp_zzzzzzzzzzzzzzzzzzzzzzzz")
                self.assertEqual(gh.get_config("main")["_token"], "ghp_envtoken1234567890abcdef")
                # حذف توكن ali
                gh.clear_token("ali")
                self.assertFalse(gh.get_config("ali")["has_token"])
            finally:
                if old is None:
                    os.environ.pop("GITHUB_TOKEN", None)
                else:
                    os.environ["GITHUB_TOKEN"] = old

        diag = run_full_stack_diagnostics()
        self.assertTrue(diag["passed_all"])
        self.assertEqual(diag["passed_count"], 10)
        self.assertEqual(diag["total"], 10)


if __name__ == "__main__":
    unittest.main()
