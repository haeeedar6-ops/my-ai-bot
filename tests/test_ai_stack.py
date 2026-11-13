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

if __name__ == "__main__":
    unittest.main()
