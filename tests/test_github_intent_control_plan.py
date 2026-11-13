import tempfile
import unittest
from pathlib import Path

from github_intent_control import (
    GitHubControlPlane,
    parse_task_spec,
)


class MockGitHubControlClient:
    """In-memory mock GitHub API supporting all 12 Section 13 acceptance scenarios."""

    def __init__(self):
        self.repos = {}  # (owner, name) -> meta dict
        self.files = {}  # (owner, name, branch, path) -> content
        self.branches = {}  # (owner, name) -> set(branch)
        self.commits = {}  # (owner, name, commit_sha) -> branch
        self.next_id = 100
        self.write_log = []  # records every write target
        self.fail_on_path = None
        self.tamper_readback_path = None
        self.wrong_owner_on_get = None

    def seed_repo(self, owner: str, name: str, default_branch: str = "main", files=None):
        self.next_id += 1
        self.repos[(owner, name)] = {
            "id": self.next_id,
            "name": name,
            "owner": {"login": owner},
            "html_url": f"https://github.com/{owner}/{name}",
            "visibility": "private",
            "default_branch": default_branch,
            "created_at": "2026-11-13T00:00:00Z",
        }
        self.branches[(owner, name)] = {default_branch}
        for p, content in (files or {}).items():
            self.files[(owner, name, default_branch, p)] = content

    def get_repo(self, owner: str, name: str):
        meta = self.repos.get((owner, name))
        if meta is None:
            return None
        if self.wrong_owner_on_get:
            copy = dict(meta)
            copy["owner"] = {"login": self.wrong_owner_on_get}
            return copy
        return dict(meta)

    def create_repo(self, owner: str, name: str, visibility: str = "private"):
        self.next_id += 1
        meta = {
            "id": self.next_id,
            "name": name,
            "owner": {"login": owner},
            "html_url": f"https://github.com/{owner}/{name}",
            "visibility": visibility,
            "default_branch": "main",
            "created_at": "2026-11-13T00:00:00Z",
        }
        self.repos[(owner, name)] = meta
        self.branches[(owner, name)] = {"main"}
        return dict(meta)

    def ensure_branch(self, owner: str, name: str, branch: str, base_branch: str = "main"):
        self.branches.setdefault((owner, name), {"main"}).add(branch)
        return {"ref": f"refs/heads/{branch}"}

    def upload_file(self, owner: str, name: str, branch: str, path: str, content: str, message: str):
        self.write_log.append((owner, name, branch, path))
        if self.fail_on_path == path:
            return {"ok": False, "status_code": 500}
        self.files[(owner, name, branch, path)] = content
        commit_sha = f"c_{len(self.write_log):04d}"
        self.commits[(owner, name, commit_sha)] = branch
        return {"ok": True, "commit_sha": commit_sha}

    def read_file(self, owner: str, name: str, branch: str, path: str):
        if self.tamper_readback_path == path:
            return "# TAMPERED REMOTE CONTENT"
        return self.files.get((owner, name, branch, path))

    def verify_commit(self, owner: str, name: str, branch: str, commit_sha: str) -> bool:
        return self.commits.get((owner, name, commit_sha)) == branch

    def check_permissions(self, owner: str, name: str) -> bool:
        return True


class TestGitHubIntentControlPlan(unittest.TestCase):
    def setUp(self):
        self.client = MockGitHubControlClient()
        self.client.seed_repo("haeeedar6-ops", "my-ai-bot", files={"bot.py": "print('old')"})
        self.tmp = tempfile.TemporaryDirectory()
        self.cp = GitHubControlPlane(audit_log_path=str(Path(self.tmp.name) / "audit.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_new_repository_leaves_old_unchanged(self):
        res = self.cp.execute_task(
            user_request="Create a new GitHub repository named pot-ai with render.yaml, package.json, server.js",
            staged_files={"render.yaml": "services: []", "package.json": '{"name": "pot-ai"}', "server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
            connected_old_repo="haeeedar6-ops/my-ai-bot",
        )
        self.assertEqual(res["status"], "VERIFIED_SUCCESS")
        self.assertFalse(res["old_repository_touched"])
        self.assertIn(("haeeedar6-ops", "pot-ai"), self.client.repos)
        self.assertEqual(self.client.files[("haeeedar6-ops", "my-ai-bot", "main", "bot.py")], "print('old')")
        self.assertTrue(all(w[1] == "pot-ai" for w in self.client.write_log))

    def test_02_name_collision_stops_and_asks(self):
        self.client.seed_repo("haeeedar6-ops", "pot-ai")
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with server.js",
            staged_files={"server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "NAME_COLLISION")
        self.assertEqual(res["writes_performed"], 0)
        self.assertEqual(self.client.write_log, [])

    def test_03_three_files_all_uploaded_and_verified(self):
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with render.yaml, package.json, server.js",
            staged_files={"render.yaml": "services: []", "package.json": '{"name":"pot-ai"}', "server.js": "console.log('ok')"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "VERIFIED_SUCCESS")
        self.assertEqual(sorted(res["verified_paths"]), ["package.json", "render.yaml", "server.js"])

    def test_04_missing_file_fails_preflight(self):
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with render.yaml, package.json, server.js",
            staged_files={"render.yaml": "services: []", "package.json": '{"name":"pot-ai"}'},  # missing server.js
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "PREFLIGHT_FAILED")
        self.assertEqual(res["writes_performed"], 0)
        self.assertIn("5_manifest_check", res["preflight"]["failed_checks"])

    def test_05_foreign_config_contamination_flagged_before_upload(self):
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with render.yaml, package.json, server.js",
            staged_files={
                "render.yaml": "services:\n  - name: family-expense-tracker",
                "package.json": '{"name":"pot-ai"}',
                "server.js": "console.log(1);",
            },
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "PREFLIGHT_FAILED")
        self.assertEqual(res["writes_performed"], 0)
        self.assertIn("6_content_check", res["preflight"]["failed_checks"])

    def test_06_ambiguous_target_asks_and_performs_no_writes(self):
        res = self.cp.execute_task(
            user_request="Update my project and fix bot.py",
            staged_files={"bot.py": "print('new')"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
            known_repositories=["haeeedar6-ops/my-ai-bot", "haeeedar6-ops/pot-ai"],
        )
        self.assertEqual(res["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(res["writes_performed"], 0)
        self.assertTrue(any("my project" in q.lower() for q in res["questions"]))

    def test_07_explicit_prohibition_zero_writes_to_old_repo(self):
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with server.js; do not touch old project",
            staged_files={"server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
            connected_old_repo="haeeedar6-ops/my-ai-bot",
        )
        self.assertEqual(res["status"], "VERIFIED_SUCCESS")
        self.assertIn("do_not_touch_old_repository", res["task_spec"]["constraints"])
        self.assertFalse(res["old_repository_touched"])

    def test_08_wrong_api_target_aborts_immediately(self):
        self.client.wrong_owner_on_get = "wrong-owner"
        res = self.cp.execute_task(
            user_request="Edit bot.py in haeeedar6-ops/my-ai-bot",
            staged_files={"bot.py": "print('x')"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "ABORTED_WRONG_TARGET")
        self.assertEqual(res["writes_performed"], 0)

    def test_09_partial_upload_marks_partial_never_success(self):
        self.client.fail_on_path = "server.js"
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with render.yaml, package.json, server.js",
            staged_files={"render.yaml": "services: []", "package.json": '{"name":"pot-ai"}', "server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "PARTIAL")
        self.assertIn("server.js", res["report"]["failed"])

    def test_10_hash_mismatch_on_readback_fails_verification(self):
        self.client.tamper_readback_path = "server.js"
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with server.js",
            staged_files={"server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "FAILED")
        self.assertIn("server.js", res["report"]["unverified"])

    def test_11_unexpected_fourth_file_quarantined_and_blocked(self):
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with render.yaml, package.json, server.js",
            staged_files={
                "render.yaml": "services: []",
                "package.json": '{"name":"pot-ai"}',
                "server.js": "console.log(1);",
                "unexpected_helper.py": "print('sneaky')",
            },
            github_client=self.client,
            default_owner="haeeedar6-ops",
        )
        self.assertEqual(res["status"], "PREFLIGHT_FAILED")
        self.assertIn("7_scope_check", res["preflight"]["failed_checks"])
        self.assertIn("unexpected_helper.py", res["manifest"]["unexpected_files"])
        self.assertEqual(self.client.write_log, [])

    def test_12_memory_conflict_explicit_request_wins(self):
        res = self.cp.execute_task(
            user_request="Create a new repository named pot-ai with server.js",
            staged_files={"server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
            memory_repo_hint="my-ai-bot",
            connected_old_repo="haeeedar6-ops/my-ai-bot",
        )
        self.assertEqual(res["status"], "VERIFIED_SUCCESS")
        self.assertEqual(res["identity"]["repository_name"], "pot-ai")
        self.assertFalse(res["old_repository_touched"])


    def test_13_arabic_create_new_project_poi_ai_with_attachments(self):
        prompt = "افتح مشروع جديد على GitHub وسميه poi-ai وارفع هدول ملفات عليه"
        spec = parse_task_spec(
            prompt,
            default_owner="haeeedar6-ops",
            attached_filenames=["render.yaml", "package.json", "server.js"],
        )
        self.assertEqual(spec.operation, "CREATE_REPOSITORY")
        self.assertTrue(spec.repository_must_be_new)
        self.assertEqual(spec.repository_name, "poi-ai")
        self.assertEqual(spec.confidence, "high")

        res = self.cp.execute_task(
            user_request=prompt,
            staged_files={
                "render.yaml": "services: []",
                "package.json": '{"name": "poi-ai"}',
                "server.js": "console.log('poi-ai');",
            },
            github_client=self.client,
            default_owner="haeeedar6-ops",
            connected_old_repo="haeeedar6-ops/my-ai-bot",
        )
        self.assertEqual(res["status"], "VERIFIED_SUCCESS")
        self.assertEqual(res["identity"]["repository_name"], "poi-ai")
        self.assertFalse(res["old_repository_touched"])
        self.assertEqual(sorted(res["verified_paths"]), ["package.json", "render.yaml", "server.js"])


    def test_14_inshaa_mashrou_jadid_variations(self):
        # 1. "انشاء مشروع جديد" with name
        p1 = "انشاء مشروع جديد على GitHub وسميه poi-ai وارفع هدول ملفات عليه"
        res1 = self.cp.execute_task(
            user_request=p1,
            staged_files={"server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
            connected_old_repo="haeeedar6-ops/my-ai-bot",
        )
        self.assertEqual(res1["status"], "VERIFIED_SUCCESS")
        self.assertEqual(res1["identity"]["repository_name"], "poi-ai")
        self.assertFalse(res1["old_repository_touched"])

        # 2. "انشاء مشروع جديد" without name -> must stop & ask, NEVER touch old repo
        p2 = "انشاء مشروع جديد وارفع الملفات عليه"
        res2 = self.cp.execute_task(
            user_request=p2,
            staged_files={"server.js": "console.log(1);"},
            github_client=self.client,
            default_owner="haeeedar6-ops",
            connected_old_repo="haeeedar6-ops/my-ai-bot",
        )
        self.assertEqual(res2["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(res2["writes_performed"], 0)
        self.assertFalse(res2["old_repository_touched"])


if __name__ == "__main__":
    unittest.main()
