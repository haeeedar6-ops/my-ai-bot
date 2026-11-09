# -*- coding: utf-8 -*-
import os, sqlite3, tempfile, time, unittest
from unittest import mock

from code_intelligence import (
    ApprovalExpiredError, ApprovalRequiredError, GitHubEngineeringService, StaleSHAConflictError,
    change_digest, redact_secrets, validate_repo_path,
)

REPO = "owner/repo"
ORIG = "def a():\n    return 1\n"
NEW = "def a():\n    return 2\n"


class FakeRemote:
    def __init__(self, content=ORIG, sha="sha1"):
        self.files = {"f.py": {"content": content, "sha": sha}} if content is not None else {}
        self.writes, self.deletes = [], []

    def read(self, _r, _b, path):
        return dict(self.files.get(path, {"content": "", "sha": ""}))

    def write(self, _r, _b, path, content, _sha):
        self.writes.append(path)
        self.files[path] = {"content": content, "sha": "sha_new"}
        return {"commit_sha": "c" * 40}

    def delete(self, _r, _b, path, _sha):
        self.deletes.append(path)
        self.files.pop(path, None)
        return {"commit_sha": "d" * 40}


class TestGithubHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = GitHubEngineeringService(db_path=os.path.join(self.tmp.name, "g.db"))
        self.u = "u1"

    def tearDown(self):
        self.tmp.cleanup()

    def _ccr(self, **kw):
        args = dict(user_id=self.u, repo=REPO, file_path="f.py", original_content=ORIG,
                    proposed_content=NEW, original_file_sha="sha1", base_branch="main", request_text="t")
        args.update(kw)
        return self.svc.create_change_request(**args)

    def test_always_pending_even_if_env_disables_approval(self):
        with mock.patch.dict(os.environ, {"GITHUB_REQUIRE_APPROVAL": "0"}):
            svc = GitHubEngineeringService(db_path=os.path.join(self.tmp.name, "g2.db"))
            c = svc.create_change_request(self.u, REPO, "f.py", ORIG, NEW, "sha1", base_branch="dev")
        self.assertEqual(c["approval_status"], "pending")

    def test_no_push_without_approval(self):
        c = self._ccr()
        r = FakeRemote()
        with self.assertRaises(ApprovalRequiredError):
            self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        self.assertEqual(r.writes, [])

    def test_approved_commit_succeeds_and_is_verified(self):
        c = self._ccr()
        self.svc.approve_change_request(self.u, c["id"])
        r = FakeRemote()
        out = self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        self.assertEqual(out["status"], "committed")
        self.assertTrue(out["verified_reread"])

    def test_approval_bound_to_exact_change(self):
        c = self._ccr()
        self.svc.approve_change_request(self.u, c["id"])
        with sqlite3.connect(self.svc.db_path) as conn:  # تلاعب بالمحتوى بعد الموافقة
            conn.execute("UPDATE code_change_requests SET proposed_content = ? WHERE id = ?", (NEW + "evil()\n", c["id"]))
        r = FakeRemote()
        with self.assertRaises(ApprovalExpiredError):
            self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        self.assertEqual(r.writes, [])

    def test_approval_expires(self):
        c = self._ccr()
        self.svc.approve_change_request(self.u, c["id"])
        self.svc.approval_ttl_sec = 30.0
        with sqlite3.connect(self.svc.db_path) as conn:
            conn.execute("UPDATE code_change_requests SET approved_at = ? WHERE id = ?", (time.time() - 3600, c["id"]))
        r = FakeRemote()
        with self.assertRaises(ApprovalExpiredError):
            self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")

    def test_delete_needs_exact_path_confirmation(self):
        c = self._ccr(operation="delete", proposed_content="")
        with self.assertRaises(ApprovalRequiredError):
            self.svc.approve_change_request(self.u, c["id"])
        with self.assertRaises(ApprovalRequiredError):
            self.svc.approve_change_request(self.u, c["id"], confirm_delete_path="other.py")
        self.svc.approve_change_request(self.u, c["id"], confirm_delete_path="f.py")

    def test_delete_flow_verified_and_never_via_write_fn(self):
        c = self._ccr(operation="delete", proposed_content="")
        self.svc.approve_change_request(self.u, c["id"], confirm_delete_path="f.py")
        r = FakeRemote()
        with self.assertRaises(RuntimeError):  # بدون remote_delete_fn
            self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        self.assertEqual(r.writes, [])
        out = self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x", remote_delete_fn=r.delete)
        self.assertEqual(r.deletes, ["f.py"])
        self.assertEqual(out["status"], "committed")

    def test_create_blocked_if_file_exists(self):
        c = self.svc.create_change_request(self.u, REPO, "f.py", "", NEW, "", operation="create")
        self.svc.approve_change_request(self.u, c["id"])
        r = FakeRemote()
        with self.assertRaises(StaleSHAConflictError):
            self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")

    def test_create_new_file_ok(self):
        c = self.svc.create_change_request(self.u, REPO, "new_mod.py", "", NEW, "", operation="create")
        self.svc.approve_change_request(self.u, c["id"])
        r = FakeRemote(content=None)
        out = self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        self.assertEqual(out["status"], "committed")

    def test_stale_sha_still_blocks(self):
        c = self._ccr()
        self.svc.approve_change_request(self.u, c["id"])
        r = FakeRemote(sha="someone_else")
        with self.assertRaises(StaleSHAConflictError):
            self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        self.assertEqual(r.writes, [])

    def test_other_user_cannot_approve_or_read(self):
        c = self._ccr()
        with self.assertRaises(PermissionError):
            self.svc.approve_change_request("intruder", c["id"])
        with self.assertRaises(PermissionError):
            self.svc.get_change_request("intruder", c["id"])

    def test_audit_log_records_actions_without_secrets(self):
        c = self._ccr(request_text="deploy with token ghp_" + "A" * 30)
        self.svc.approve_change_request(self.u, c["id"])
        r = FakeRemote()
        self.svc.commit_and_verify_change_request(self.u, c["id"], r.read, r.write, target_branch="ryn-work/x")
        log = self.svc.list_audit_log(self.u)
        actions = [e["action"] for e in log]
        for a in ("create:edit", "approve", "commit:edit"):
            self.assertIn(a, actions)
        blob = repr(log)
        self.assertNotIn("ghp_" + "A" * 30, blob)
        entry = [e for e in log if e["action"] == "commit:edit"][0]
        self.assertEqual(entry["files"], ["f.py"])
        self.assertEqual(entry["branch"], "ryn-work/x")
        self.assertTrue(entry["approval_ref"])

    def test_audit_is_per_user(self):
        self._ccr()
        self.assertEqual(self.svc.list_audit_log("other"), [])


class TestPathAndSecrets(unittest.TestCase):
    def test_bad_paths_rejected(self):
        for bad in ("../x", "a/../../b", "a\\b", ".git/config", "a//b", "", "a/./b", "C:/x", "a\x00b", "./x"):
            with self.assertRaises(ValueError, msg=bad):
                validate_repo_path(bad)

    def test_good_paths(self):
        self.assertEqual(validate_repo_path("/src/app.py"), "src/app.py")
        self.assertEqual(validate_repo_path(".github/workflows/main.yml"), ".github/workflows/main.yml")

    def test_redaction(self):
        for secret in ("ghp_" + "x" * 30, "github_pat_" + "y" * 30, "sk-" + "z" * 30, "token=abcdef123456"):
            self.assertNotIn(secret, redact_secrets(f"err {secret} end"))

    def test_digest_changes_with_content(self):
        a = change_digest("r", "main", "w", "f", "edit", "x")
        self.assertNotEqual(a, change_digest("r", "main", "w", "f", "edit", "y"))
        self.assertNotEqual(a, change_digest("r", "main", "w", "f", "delete", "x"))


if __name__ == "__main__":
    unittest.main()
