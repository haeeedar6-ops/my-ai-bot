# -*- coding: utf-8 -*-
"""
tests/test_engineering_team_plan.py — Section 18 Acceptance Test Scenarios
for RYN_AI_Software_Engineering_Team_Plan.pdf (Version 1.0).

Tests all 10 mandatory acceptance scenarios:
1. A vague request is detected and the workflow asks a clarification rather than inventing requirements.
2. A plan that changes an unapproved file is rejected before implementation.
3. Qwen's patch cannot modify .env, secrets, or protected deployment files.
4. A failing unit test routes to DeepSeek and does not pass directly to release.
5. Kimi identifies a deliberately introduced defect and blocks the candidate.
6. Final QA reports a test as "not_run" when the execution environment is unavailable.
7. A stale patch based on an older commit is rejected.
8. A provider timeout resumes without duplicating a committed change.
9. A production deployment request pauses for human approval.
10. The final report identifies exact commit, changed files, test evidence, unresolved risks, and approval status.
"""

import os
import tempfile
import unittest

from core.engineering_team import (
    RynEngineeringTeamOrchestrator,
)


class TestRynEngineeringTeamAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "team.db")
        self.art_root = os.path.join(self.tmp.name, "runs")
        self.orch = RynEngineeringTeamOrchestrator(db_path=self.db_path, artifact_root=self.art_root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_vague_request_asks_clarification(self):
        run = self.orch.create_run(
            request_text="صلحه",
            project_files={},
        )
        res = self.orch.execute_workflow(run["run_id"])
        self.assertEqual(res["state"], "BLOCKED")
        self.assertTrue(bool(res["data"].get("clarification_question")))
        self.assertNotIn("implementation", res["data"].get("committed_stages", []))

    def test_02_plan_changing_unapproved_file_rejected_before_implementation(self):
        run = self.orch.create_run(
            request_text="Add logging helper to src/app.py",
            project_files={"src/app.py": "def run():\n    return 1\n"},
            allowed_paths=["src/"],
        )
        qwen_called = {"called": False}

        def bad_architect(_r):
            return {
                "status": "completed",
                "architecture_plan": "Touch unapproved system config",
                "task_list": ["Modify /etc/passwd"],
                "file_impact_map": ["src/app.py", "deploy/prod_config.yaml"],
                "test_strategy": "unit",
                "decision_log": [],
            }

        def qwen_spy(_r):
            qwen_called["called"] = True
            return {"files": {"src/app.py": "def run():\n    return 2\n"}}

        res = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"gpt_architect": bad_architect, "qwen_implementer": qwen_spy},
        )
        self.assertEqual(res["state"], "BLOCKED")
        self.assertFalse(qwen_called["called"])
        self.assertIn("deploy/prod_config.yaml", res["data"]["blocker_reason"])

    def test_03_qwen_patch_cannot_modify_env_or_secrets(self):
        run = self.orch.create_run(
            request_text="Update configuration in src/app.py",
            project_files={"src/app.py": "x = 1\n"},
            allowed_paths=["src/", ".env"],
        )

        def sneaky_qwen(r):
            return {
                "files": {"src/app.py": "x = 2\n", ".env": "SECRET_KEY=hacked\n"},
                "changed_files": ["src/app.py", ".env"],
                "base_commit": r["candidate_commit"],
            }

        res = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"qwen_implementer": sneaky_qwen},
        )
        self.assertEqual(res["state"], "BLOCKED")
        self.assertIn("forbidden", res["data"]["blocker_reason"].lower())
        self.assertNotIn(".env", res["data"]["working_files"])

    def test_04_failing_unit_test_routes_to_deepseek_and_repairs(self):
        run = self.orch.create_run(
            request_text="Implement add(a, b) in src/math_util.py",
            project_files={"src/math_util.py": "def add(a, b):\n    return 0\n"},
            allowed_paths=["src/"],
        )
        deepseek_calls = []

        def buggy_qwen(r):
            return {
                "files": {"src/math_util.py": "def add(a, b):\n    return a - b\n"},
                "changed_files": ["src/math_util.py"],
                "base_commit": r["candidate_commit"],
            }

        def deepseek_fix(_r):
            deepseek_calls.append(1)
            return {
                "root_cause_analysis": "Qwen used subtraction instead of addition.",
                "files": {"src/math_util.py": "def add(a, b):\n    return a + b\n"},
            }

        def unit_runner(files_map):
            ns = {}
            exec(files_map["src/math_util.py"], ns)
            ok = ns["add"](2, 3) == 5
            return {"passed": ok, "exit_code": 0 if ok else 1, "log": "add(2,3)==5" if ok else "AssertionError: 2+3 != 5"}

        res = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"qwen_implementer": buggy_qwen, "deepseek_debugger": deepseek_fix},
            unit_test_fn=unit_runner,
        )
        self.assertEqual(len(deepseek_calls), 1)
        self.assertEqual(res["state"], "READY_TO_DEPLOY")
        self.assertEqual(res["data"]["qa_report"]["go_no_go"], "GO")

    def test_05_kimi_identifies_defect_and_blocks_candidate(self):
        run = self.orch.create_run(
            request_text="Update src/service.py",
            project_files={"src/service.py": "def process(x):\n    return x * 2\n"},
            allowed_paths=["src/"],
        )

        def strict_kimi(_r):
            return {
                "verdict": "changes_requested",
                "findings": [
                    {"severity": "critical", "file": "src/service.py", "problem": "Race condition on shared state"}
                ],
                "summary": "Critical concurrency defect found.",
            }

        res = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"kimi_reviewer": strict_kimi},
            unit_test_fn=lambda _f: {"passed": True, "exit_code": 0},
        )
        self.assertEqual(res["state"], "BLOCKED")
        self.assertIn("Kimi", res["data"]["blocker_reason"])

    def test_06_final_qa_reports_test_as_not_run_when_env_unavailable(self):
        run = self.orch.create_run(
            request_text="Add helper in src/app.py",
            project_files={"src/app.py": "def helper():\n    return 'ok'\n"},
            allowed_paths=["src/"],
        )
        res = self.orch.execute_workflow(
            run["run_id"],
            execution_env_available=False,
        )
        self.assertEqual(res["state"], "READY_TO_DEPLOY")
        qa = res["data"]["qa_report"]
        self.assertEqual(qa["unit_tests_status"], "not_run")
        self.assertEqual(qa["test_matrix"]["unit_tests"]["status"], "not_run")
        self.assertFalse(qa["test_matrix"]["unit_tests"]["executed"])

    def test_07_stale_patch_based_on_older_commit_is_rejected(self):
        run = self.orch.create_run(
            request_text="Update src/app.py",
            project_files={"src/app.py": "v = 1\n"},
            allowed_paths=["src/"],
            base_commit="c_current_200",
        )

        def stale_qwen(_r):
            return {
                "files": {"src/app.py": "v = 2\n"},
                "changed_files": ["src/app.py"],
                "base_commit": "c_old_stale_100",
            }

        res = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"qwen_implementer": stale_qwen},
        )
        self.assertEqual(res["state"], "BLOCKED")
        self.assertIn("stale patch", res["data"]["blocker_reason"].lower())

    def test_08_provider_timeout_resumes_without_duplicating_committed_change(self):
        run = self.orch.create_run(
            request_text="Update src/app.py cleanly",
            project_files={"src/app.py": "v = 1\n"},
            allowed_paths=["src/"],
        )
        attempts = {"qwen": 0, "analyst": 0}

        def analyst_cb(_r):
            attempts["analyst"] += 1
            return {
                "status": "completed",
                "requirements_brief": "Update v to 2",
                "ambiguity_list": [],
                "acceptance_criteria": ["AC-1"],
                "risk_notes": [],
            }

        def flaky_qwen(r):
            attempts["qwen"] += 1
            if attempts["qwen"] == 1:
                raise TimeoutError("OpenRouter Qwen 504 gateway timeout")
            return {
                "files": {"src/app.py": "v = 2\n"},
                "changed_files": ["src/app.py"],
                "base_commit": r["candidate_commit"],
            }

        # First call times out during Qwen implementation
        res1 = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"gemini_analyst": analyst_cb, "qwen_implementer": flaky_qwen},
            unit_test_fn=lambda _f: {"passed": True, "exit_code": 0},
        )
        self.assertEqual(res1["state"], "IMPLEMENTING")
        self.assertIn("analysis", res1["data"]["committed_stages"])
        self.assertIn("plan", res1["data"]["committed_stages"])

        # Second call resumes from IMPLEMENTING without re-running analyst or duplicating commits
        res2 = self.orch.execute_workflow(
            run["run_id"],
            agent_callbacks={"gemini_analyst": analyst_cb, "qwen_implementer": flaky_qwen},
            unit_test_fn=lambda _f: {"passed": True, "exit_code": 0},
        )
        self.assertEqual(attempts["analyst"], 1)
        self.assertEqual(attempts["qwen"], 2)
        self.assertEqual(res2["state"], "READY_TO_DEPLOY")

    def test_09_production_deployment_pauses_for_human_approval(self):
        run = self.orch.create_run(
            request_text="Update src/app.py and deploy to production",
            project_files={"src/app.py": "v = 1\n"},
            allowed_paths=["src/"],
            request_production_deploy=True,
        )
        res = self.orch.execute_workflow(
            run["run_id"],
            unit_test_fn=lambda _f: {"passed": True, "exit_code": 0},
        )
        self.assertEqual(res["state"], "RELEASE_APPROVAL")
        self.assertEqual(res["data"]["qa_report"]["approval_status"], "PENDING_HUMAN_RELEASE_APPROVAL")

        # After human approval, transitions to READY_TO_DEPLOY
        approved = self.orch.perform_user_action(run["run_id"], "approve_release", note="Approved by haydar_xe")
        self.assertEqual(approved["state"], "READY_TO_DEPLOY")

    def test_10_final_report_identifies_commit_files_evidence_risks_and_approval(self):
        run = self.orch.create_run(
            request_text="Add feature to src/app.py",
            project_files={"src/app.py": "def feat():\n    return 42\n"},
            allowed_paths=["src/"],
        )
        res = self.orch.execute_workflow(
            run["run_id"],
            unit_test_fn=lambda _f: {"passed": True, "exit_code": 0, "log": "1 passed in 0.01s"},
        )
        qa = res["data"]["qa_report"]
        self.assertTrue(qa["tested_commit"].startswith("c_"))
        self.assertEqual(qa["changed_files"], ["src/app.py"])
        self.assertEqual(qa["test_matrix"]["unit_tests"]["status"], "passed")
        self.assertIn("Preserve existing public interfaces and routes", qa["unresolved_risks"])
        self.assertEqual(qa["approval_status"], "APPROVED")
        self.assertEqual(qa["requested_model_label"], "Gemini 4 Argon")
        self.assertTrue(len(qa["fallback_disclosures"]) >= 1)


if __name__ == "__main__":
    unittest.main()
