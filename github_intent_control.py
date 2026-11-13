"""RYN GitHub Intent, Repository & File Control Plan (Version 1.0).

Implements the strict execution specification for:
1. Intent Understanding Engine (TaskSpec & Confidence Policy)
2. Canonical Repository Identity Resolution & New-vs-Existing Guard
3. File Manifest & Scope Control (including cross-project contamination checks)
4. Mandatory 9-Check Preflight Write Gate
5. Safe Execution, Task Branches, & Partial Failure Handling
6. Independent Read-After-Write Verification
7. Redacted Audit Logging & User-Facing Preview
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants & Enums
# ---------------------------------------------------------------------------

VALID_OPERATIONS = {
    "CREATE_REPOSITORY",
    "UPDATE_REPOSITORY",
    "UPLOAD_FILES",
    "EDIT_FILES",
    "INSPECT_ONLY",
}

VALID_FILE_ACTIONS = {"CREATE", "REPLACE", "PATCH", "DELETE", "MOVE"}

TOKEN_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{12,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{12,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9\-_]{16,}"),
    re.compile(r"(?i)(token|password|secret|api_key)\s*[:=]\s*['\"]?([^\s'\",;]{6,})"),
)

FOREIGN_SERVICE_PATTERNS = (
    "family-expense-tracker",
    "tartusbudgetbot",
    "tartus-budget",
    "my-old-unrelated-app",
)


def redact_secrets(text: Any) -> str:
    s = str(text or "")
    for pat in TOKEN_PATTERNS:
        s = pat.sub("[REDACTED]", s)
    return s


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def normalize_repo_path(path: str) -> str:
    p = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not p or ".." in p.split("/") or "\x00" in p:
        raise ValueError(f"Invalid repository path: {path!r}")
    return p


# ---------------------------------------------------------------------------
# Section 3: Intent Understanding Engine (TaskSpec)
# ---------------------------------------------------------------------------

@dataclass
class TaskSpec:
    raw_request: str
    operation: str  # CREATE_REPOSITORY | UPDATE_REPOSITORY | UPLOAD_FILES | EDIT_FILES | INSPECT_ONLY
    repository_owner: str = "unresolved"
    repository_name: str = "unresolved"
    repository_must_be_new: bool = False
    branch: str = ""
    files_expected: List[str] = field(default_factory=list)
    file_actions: List[str] = field(default_factory=list)
    content_source: str = "generated content"
    constraints: List[str] = field(default_factory=list)
    approval_required: bool = True
    confidence: str = "low"  # high | medium | low
    ambiguities: List[str] = field(default_factory=list)
    visibility: str = "private"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _extract_repo_owner_and_name(text: str, default_owner: str = "") -> Tuple[str, str]:
    # 1. Explicit GitHub URL: https://github.com/owner/repo
    m_url = re.search(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git|/|$|\s)", text)
    if m_url:
        return m_url.group(1), m_url.group(2)

    # 2. Explicit owner/repo pattern (avoiding file paths like app/main.py)
    m_slash = re.search(r"\b([A-Za-z0-9][A-Za-z0-9_-]{1,38})/([A-Za-z0-9][A-Za-z0-9_.-]{1,100})\b", text)
    if m_slash:
        cand_owner, cand_name = m_slash.group(1), m_slash.group(2)
        if "." not in cand_name or cand_name.endswith(".js") is False:
            if cand_owner.lower() not in {"app", "src", "tests", "audit", "memory", "code_intelligence", "ryn"}:
                return cand_owner, cand_name

    # 3. Named repository: "named pot-ai", "called pot-ai", "سميه poi-ai", "وسميه poi-ai", "باسم pot-ai"
    m_named = re.search(
        r"(?i)(?:named|called|name\s+is|repo(?:sitory)?(?:\s+named)?|وسميه|سميه|سمّيه|اسمه|اسمو|باسم)\s+[`'\"]?([A-Za-z0-9][A-Za-z0-9_.-]{1,64})[`'\"]?",
        text,
    )
    if m_named:
        return (default_owner or "unresolved"), m_named.group(1)

    return (default_owner or "unresolved"), "unresolved"


def _extract_expected_files(text: str) -> List[str]:
    # Match filenames like render.yaml, package.json, server.js, app/main.py
    matches = re.findall(
        r"\b([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:py|js|ts|jsx|tsx|json|ya?ml|md|txt|html|css|kt|java|toml|ini|sh|spec))\b",
        text,
    )
    seen: List[str] = []
    for m in matches:
        if m not in seen:
            seen.append(m)
    return seen


def parse_task_spec(
    user_request: str,
    *,
    default_owner: str = "",
    known_repositories: Optional[List[str]] = None,
    memory_repo_hint: str = "",
    has_attachments: bool = False,
    attached_filenames: Optional[List[str]] = None,
) -> TaskSpec:
    """Parse a natural language request into a structured TaskSpec (Section 3)."""
    text = str(user_request or "").strip()
    low = text.lower()
    known_repositories = list(known_repositories or [])

    # Detect constraints
    constraints: List[str] = []
    if any(p in low for p in (
        "do not touch old", "don't touch old", "do not modify old", "leave old",
        "preserve existing", "لا تعدل القديم", "لا تلمس القديم", "بدون تعديل القديم"
    )):
        constraints.append("do_not_touch_old_repository")
    if any(p in low for p in ("preserve", "keep existing", "لا تحذف")):
        constraints.append("preserve_existing_work")

    # Classify operation (Section 3.2)
    is_inspect = any(p in low for p in ("inspect", "show ", "check ", "list files", "read ", "افحص", "اعرض", "تحقق")) and not any(
        p in low for p in ("create", "upload", "edit", "update", "fix", "patch", "delete", "انشئ", "ارفع", "عدل")
    )
    is_create_repo = any(p in low for p in (
        "create a new repo", "create new repo", "new github repo", "new repository",
        "create a new project", "new github project", "open a new project",
        "open a new repo", "start a new project", "create repo",
        "انشئ مستودع جديد", "أنشئ مستودع جديد", "سوي مستودع جديد", "مستودع جديد",
        "افتح مشروع جديد", "افتح مستودع جديد", "افتح ريبو جديد", "سوي مشروع جديد",
        "انشئ مشروع جديد", "أنشئ مشروع جديد", "مشروع جديد على github", "مشروع جديد على غيتهاب"
    ))
    is_upload = any(p in low for p in ("upload", "push these files", "ارفع الملفات", "رفع ملفات"))
    is_edit = any(p in low for p in ("edit", "patch", "replace", "delete", "rename", "fix", "update", "عدل", "حدث", "احذف"))

    if is_inspect:
        operation = "INSPECT_ONLY"
    elif is_create_repo:
        operation = "CREATE_REPOSITORY"
    elif is_upload:
        operation = "UPLOAD_FILES"
    elif is_edit:
        operation = "EDIT_FILES" if "file" in low or _extract_expected_files(text) else "UPDATE_REPOSITORY"
    else:
        operation = "INSPECT_ONLY"

    owner, repo_name = _extract_repo_owner_and_name(text, default_owner=default_owner)
    files_expected = _extract_expected_files(text)
    if attached_filenames and not files_expected:
        files_expected = [str(f).strip().lstrip("/") for f in attached_filenames if str(f).strip()]
    if attached_filenames:
        has_attachments = True

    # Branch detection
    m_branch = re.search(r"(?i)(?:branch|فرع)\s+[`'\"]?([A-Za-z0-9_./-]+)[`'\"]?", text)
    branch = m_branch.group(1) if m_branch else ""

    # Visibility
    visibility = "public" if "public" in low or "عام" in low else "private"

    # File actions
    file_actions: List[str] = []
    if operation in ("CREATE_REPOSITORY", "UPLOAD_FILES"):
        file_actions = ["CREATE"]
    elif "delete" in low or "احذف" in low:
        file_actions = ["DELETE"]
    elif "replace" in low or "استبدل" in low:
        file_actions = ["REPLACE"]
    elif "rename" in low or "move" in low:
        file_actions = ["MOVE"]
    elif operation in ("EDIT_FILES", "UPDATE_REPOSITORY"):
        file_actions = ["PATCH"]

    content_source = "attachment" if has_attachments else ("existing repository" if operation == "INSPECT_ONLY" else "generated content")

    # Ambiguity & confidence evaluation (Section 3.3)
    ambiguities: List[str] = []
    ambiguous_my_project = any(p in low for p in ("my project", "this project", "مشروعي", "هذا المشروع"))
    if repo_name == "unresolved":
        if ambiguous_my_project and len(known_repositories) > 1:
            ambiguities.append(
                f"Ambiguous target 'my project': multiple repositories match ({', '.join(known_repositories[:5])}). Please specify which repository."
            )
        elif operation != "INSPECT_ONLY":
            ambiguities.append("Target repository name is unresolved. Please specify owner/repository.")
    if owner == "unresolved" and operation != "INSPECT_ONLY":
        ambiguities.append("Repository owner/account is unresolved.")

    # Note: memory_repo_hint NEVER overrides an explicit repository name (Section 3.3 & 11)
    if repo_name != "unresolved" and memory_repo_hint and repo_name != memory_repo_hint:
        constraints.append(f"explicit_request_overrides_memory:{memory_repo_hint}")

    if ambiguities:
        confidence = "low" if (repo_name == "unresolved" or len(ambiguities) > 1) else "medium"
    elif operation in VALID_OPERATIONS and (operation == "INSPECT_ONLY" or (owner != "unresolved" and repo_name != "unresolved")):
        confidence = "high"
    else:
        confidence = "medium"

    approval_required = bool(
        operation in ("CREATE_REPOSITORY", "UPDATE_REPOSITORY")
        or any(a in ("REPLACE", "DELETE", "MOVE") for a in file_actions)
        or visibility == "public"
    )

    return TaskSpec(
        raw_request=text,
        operation=operation,
        repository_owner=owner,
        repository_name=repo_name,
        repository_must_be_new=(operation == "CREATE_REPOSITORY"),
        branch=branch,
        files_expected=files_expected,
        file_actions=file_actions,
        content_source=content_source,
        constraints=constraints,
        approval_required=approval_required,
        confidence=confidence,
        ambiguities=ambiguities,
        visibility=visibility,
    )


# ---------------------------------------------------------------------------
# Section 4: Repository Identity Resolution
# ---------------------------------------------------------------------------

@dataclass
class CanonicalRepositoryIdentity:
    owner: str
    repository_name: str
    repository_id: int
    canonical_url: str
    visibility: str
    default_branch: str
    task_run_id: str
    created_at: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repository_name}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["full_name"] = self.full_name
        return d


class RepositoryResolutionError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


# ---------------------------------------------------------------------------
# Section 5: File Manifest & Scope Control
# ---------------------------------------------------------------------------

@dataclass
class ManifestEntry:
    path: str
    action: str  # CREATE | REPLACE | PATCH | DELETE | MOVE
    source: str
    required: bool = True
    expected_sha256: str = ""
    overwrite_allowed: bool = False
    content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "source": self.source,
            "required": self.required,
            "expected_sha256": self.expected_sha256,
            "overwrite_allowed": self.overwrite_allowed,
        }


@dataclass
class FileManifest:
    run_id: str
    entries: List[ManifestEntry] = field(default_factory=list)
    unexpected_files: List[str] = field(default_factory=list)
    contamination_flags: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "required_count": sum(1 for e in self.entries if e.required),
            "staged_count": len(self.entries),
            "entries": [e.to_dict() for e in self.entries],
            "unexpected_files": list(self.unexpected_files),
            "contamination_flags": list(self.contamination_flags),
            "validation_errors": list(self.validation_errors),
        }


def build_and_validate_manifest(
    *,
    run_id: str,
    expected_paths: List[str],
    staged_files: Dict[str, str],
    action: str = "CREATE",
    source: str = "approved source",
    overwrite_allowed: bool = False,
    target_repo_name: str = "",
    allowed_project_aliases: Optional[List[str]] = None,
) -> FileManifest:
    """Build FileManifest and run Section 5 scope & contamination checks."""
    manifest = FileManifest(run_id=run_id)
    normalized_expected: List[str] = []
    seen_lower: set = set()

    for raw_p in expected_paths:
        p = normalize_repo_path(raw_p)
        if p.lower() in seen_lower:
            manifest.validation_errors.append(f"Duplicate manifest path (case-insensitive): {p}")
            continue
        seen_lower.add(p.lower())
        normalized_expected.append(p)

    normalized_staged: Dict[str, str] = {}
    for raw_p, content in (staged_files or {}).items():
        p = normalize_repo_path(raw_p)
        normalized_staged[p] = content

    # 1. Check required expected files against staged files
    for p in normalized_expected:
        if p not in normalized_staged:
            manifest.validation_errors.append(f"Missing required staged file: {p}")
            continue
        content = normalized_staged[p]
        if not isinstance(content, str):
            manifest.validation_errors.append(f"Invalid non-text content for {p}")
            continue
        # Basic size check (10 MB limit per file)
        raw_bytes = content.encode("utf-8")
        if len(raw_bytes) > 10 * 1024 * 1024:
            manifest.validation_errors.append(f"File exceeds 10MB limit: {p}")
            continue
        manifest.entries.append(
            ManifestEntry(
                path=p,
                action=action if action in VALID_FILE_ACTIONS else "CREATE",
                source=source,
                required=True,
                expected_sha256=sha256_bytes(raw_bytes),
                overwrite_allowed=overwrite_allowed,
                content=content,
            )
        )

    # 2. Quarantine unexpected files (Section 5 & 10)
    expected_set = set(normalized_expected)
    for p in sorted(normalized_staged.keys()):
        if p not in expected_set:
            manifest.unexpected_files.append(p)

    # 3. Cross-project contamination check (Section 5)
    aliases = {a.lower() for a in (allowed_project_aliases or []) if a}
    if target_repo_name:
        aliases.add(target_repo_name.lower())

    for entry in manifest.entries:
        body_low = (entry.content or "").lower()
        for foreign_name in FOREIGN_SERVICE_PATTERNS:
            if foreign_name in body_low and foreign_name not in aliases:
                manifest.contamination_flags.append(
                    f"Cross-project contamination detected in '{entry.path}': references foreign service '{foreign_name}'."
                )

    return manifest


# ---------------------------------------------------------------------------
# Section 6: Mandatory 9-Check Preflight Write Gate
# ---------------------------------------------------------------------------

def run_preflight_gate(
    *,
    spec: TaskSpec,
    identity: Optional[CanonicalRepositoryIdentity],
    manifest: FileManifest,
    repo_exists_on_github: Optional[bool],
    token_permissions_ok: bool = True,
    target_branch: str = "",
) -> Dict[str, Any]:
    """Run all 9 mandatory preflight checks (Section 6). Any failure blocks writes."""
    checks: Dict[str, Dict[str, Any]] = {}

    # 1. Intent check
    intent_ok = spec.operation in VALID_OPERATIONS and spec.confidence == "high"
    checks["1_intent_check"] = {
        "passed": intent_ok,
        "detail": "Intent parsed cleanly" if intent_ok else ("; ".join(spec.ambiguities) or f"Low/medium confidence ({spec.confidence})"),
    }

    # 2. Target check
    target_ok = bool(
        identity is not None
        and identity.owner
        and identity.owner != "unresolved"
        and identity.repository_name
        and identity.repository_name != "unresolved"
        and identity.repository_id > 0
    )
    checks["2_target_check"] = {
        "passed": target_ok,
        "detail": f"Resolved {identity.full_name} (id={identity.repository_id})" if target_ok else "Target repository identity unresolved",
    }

    # 3. New/existing check
    if spec.repository_must_be_new:
        state_ok = repo_exists_on_github is False
        state_detail = "Repository does not exist yet (ready to create)" if state_ok else "Name collision: repository already exists"
    else:
        state_ok = repo_exists_on_github is True
        state_detail = "Existing repository verified" if state_ok else "Target repository does not exist"
    checks["3_new_existing_check"] = {"passed": state_ok, "detail": state_detail}

    # 4. Branch check
    resolved_branch = target_branch or (identity.default_branch if identity else "") or spec.branch
    branch_ok = bool(resolved_branch)
    checks["4_branch_check"] = {
        "passed": branch_ok,
        "detail": f"Branch '{resolved_branch}' validated" if branch_ok else "Target branch missing",
    }

    # 5. Manifest check
    manifest_ok = len(manifest.validation_errors) == 0 and len(manifest.entries) == len(spec.files_expected or [e.path for e in manifest.entries])
    checks["5_manifest_check"] = {
        "passed": manifest_ok,
        "detail": f"{len(manifest.entries)} required files staged" if manifest_ok else "; ".join(manifest.validation_errors),
    }

    # 6. Content check (approved source & no cross-project contamination)
    content_ok = len(manifest.contamination_flags) == 0
    checks["6_content_check"] = {
        "passed": content_ok,
        "detail": "Approved sources & no contamination" if content_ok else "; ".join(manifest.contamination_flags),
    }

    # 7. Scope check (no unexpected files or unapproved overwrites)
    scope_ok = len(manifest.unexpected_files) == 0
    checks["7_scope_check"] = {
        "passed": scope_ok,
        "detail": "Exact scope only" if scope_ok else f"Unexpected unrequested files quarantined: {', '.join(manifest.unexpected_files)}",
    }

    # 8. Permission check
    checks["8_permission_check"] = {
        "passed": bool(token_permissions_ok),
        "detail": "Token permissions verified" if token_permissions_ok else "Insufficient or mismatched token permissions",
    }

    # 9. Preview (Section 9 format)
    preview = {
        "action": "Create a new repository" if spec.repository_must_be_new else spec.operation,
        "destination": identity.full_name if identity else f"{spec.repository_owner}/{spec.repository_name}",
        "existing_repository_modified": "No" if spec.repository_must_be_new else "Yes",
        "files": [e.path for e in manifest.entries],
        "unexpected_files": manifest.unexpected_files if manifest.unexpected_files else ["None"],
        "commit": "one initial commit" if spec.repository_must_be_new else f"task branch {resolved_branch}",
    }
    checks["9_preview"] = {"passed": True, "detail": preview}

    all_passed = all(v["passed"] for v in checks.values())
    failed_checks = [k for k, v in checks.items() if not v["passed"]]
    return {
        "passed": all_passed,
        "failed_checks": failed_checks,
        "checks": checks,
        "preview": preview,
    }


# ---------------------------------------------------------------------------
# Sections 7, 8, 10, 11: Control Plane Orchestrator, Verifier & Audit Log
# ---------------------------------------------------------------------------

class GitHubControlPlane:
    """Orchestrates Intent -> Identity -> Manifest -> Preflight -> Execute -> Verify -> Audit."""

    def __init__(self, audit_log_path: Optional[str] = None):
        self._audit_path = Path(audit_log_path) if audit_log_path else None
        self._audit_logs: List[Dict[str, Any]] = []

    def _record_audit(self, entry: Dict[str, Any]) -> None:
        safe_entry = json.loads(redact_secrets(json.dumps(entry, ensure_ascii=False)))
        self._audit_logs.append(safe_entry)
        if self._audit_path is not None:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(safe_entry, ensure_ascii=False) + "\n")

    def list_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self._audit_logs[-limit:])

    def execute_task(
        self,
        *,
        user_request: str,
        staged_files: Dict[str, str],
        github_client: Any,
        default_owner: str = "",
        known_repositories: Optional[List[str]] = None,
        memory_repo_hint: str = "",
        allowed_project_aliases: Optional[List[str]] = None,
        connected_old_repo: str = "",
    ) -> Dict[str, Any]:
        """Execute a full controlled GitHub workflow (Sections 3–13).

        `github_client` must implement:
          - get_repo(owner, name) -> Optional[dict] (None if 404)
          - create_repo(owner, name, visibility) -> dict
          - ensure_branch(owner, name, branch, base_branch) -> dict
          - upload_file(owner, name, branch, path, content, message) -> dict (with commit_sha)
          - read_file(owner, name, branch, path) -> Optional[str]
          - verify_commit(owner, name, branch, commit_sha) -> bool
          - check_permissions(owner, name) -> bool
        """
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        spec = parse_task_spec(
            user_request,
            default_owner=default_owner,
            known_repositories=known_repositories,
            memory_repo_hint=memory_repo_hint,
        )

        # Stop immediately on ambiguity / low confidence (Section 2 & 3.3)
        if spec.confidence != "high" or spec.repository_name == "unresolved" or spec.repository_owner == "unresolved":
            result = {
                "status": "NEEDS_CLARIFICATION",
                "run_id": run_id,
                "writes_performed": 0,
                "old_repository_touched": False,
                "questions": spec.ambiguities or ["Please specify the exact owner/repository and files."],
                "task_spec": spec.to_dict(),
            }
            self._record_audit({"run_id": run_id, "raw_request": user_request, "task_spec": spec.to_dict(), "final_status": result["status"]})
            return result

        # Inspect-only fast path (Section 3.2)
        if spec.operation == "INSPECT_ONLY":
            repo_meta = github_client.get_repo(spec.repository_owner, spec.repository_name)
            result = {
                "status": "INSPECTED",
                "run_id": run_id,
                "writes_performed": 0,
                "old_repository_touched": False,
                "repository": repo_meta,
                "task_spec": spec.to_dict(),
            }
            self._record_audit({"run_id": run_id, "raw_request": user_request, "task_spec": spec.to_dict(), "final_status": "INSPECTED"})
            return result

        # Section 4: Repository Identity Resolution
        existing_meta = github_client.get_repo(spec.repository_owner, spec.repository_name)
        repo_exists = existing_meta is not None

        if spec.repository_must_be_new and repo_exists:
            # Section 4.2 & 13 (Name collision): Stop and ask; never reuse silently!
            result = {
                "status": "NAME_COLLISION",
                "run_id": run_id,
                "writes_performed": 0,
                "old_repository_touched": False,
                "error": f"Repository '{spec.repository_owner}/{spec.repository_name}' already exists. Use existing or choose another name?",
                "task_spec": spec.to_dict(),
            }
            self._record_audit({"run_id": run_id, "raw_request": user_request, "task_spec": spec.to_dict(), "final_status": "NAME_COLLISION"})
            return result

        if not spec.repository_must_be_new and not repo_exists:
            result = {
                "status": "FAILED",
                "run_id": run_id,
                "writes_performed": 0,
                "error": f"Target repository '{spec.repository_owner}/{spec.repository_name}' does not exist.",
                "task_spec": spec.to_dict(),
            }
            self._record_audit({"run_id": run_id, "raw_request": user_request, "task_spec": spec.to_dict(), "final_status": "FAILED"})
            return result

        # Build & validate FileManifest BEFORE creating repo or writing files (Section 5 & 6)
        expected_paths = spec.files_expected or sorted(staged_files.keys())
        manifest = build_and_validate_manifest(
            run_id=run_id,
            expected_paths=expected_paths,
            staged_files=staged_files,
            action=spec.file_actions[0] if spec.file_actions else "CREATE",
            source=spec.content_source,
            overwrite_allowed=not spec.repository_must_be_new,
            target_repo_name=spec.repository_name,
            allowed_project_aliases=allowed_project_aliases,
        )

        # Pre-check identity placeholder for preflight gate if new repo
        pre_identity = CanonicalRepositoryIdentity(
            owner=spec.repository_owner,
            repository_name=spec.repository_name,
            repository_id=int(existing_meta["id"]) if existing_meta else 1,
            canonical_url=(existing_meta or {}).get("html_url", f"https://github.com/{spec.repository_owner}/{spec.repository_name}"),
            visibility=(existing_meta or {}).get("visibility", spec.visibility),
            default_branch=(existing_meta or {}).get("default_branch", "main"),
            task_run_id=run_id,
        )
        # Validate returned owner/name for existing repos (Section 13: Wrong API target)
        if existing_meta is not None:
            ret_owner = (existing_meta.get("owner") or {}).get("login", "")
            if ret_owner and ret_owner.lower() != spec.repository_owner.lower():
                result = {
                    "status": "ABORTED_WRONG_TARGET",
                    "run_id": run_id,
                    "writes_performed": 0,
                    "error": f"API returned owner '{ret_owner}' instead of expected '{spec.repository_owner}'. Aborting.",
                }
                self._record_audit({"run_id": run_id, "final_status": "ABORTED_WRONG_TARGET", "error": result["error"]})
                return result

        target_branch = (
            spec.branch
            or (pre_identity.default_branch if spec.repository_must_be_new else f"ryn/task-{run_id}")
        )
        perms_ok = bool(github_client.check_permissions(spec.repository_owner, spec.repository_name))

        preflight = run_preflight_gate(
            spec=spec,
            identity=pre_identity,
            manifest=manifest,
            repo_exists_on_github=repo_exists,
            token_permissions_ok=perms_ok,
            target_branch=target_branch,
        )
        if not preflight["passed"]:
            result = {
                "status": "PREFLIGHT_FAILED",
                "run_id": run_id,
                "writes_performed": 0,
                "old_repository_touched": False,
                "preflight": preflight,
                "manifest": manifest.to_dict(),
                "task_spec": spec.to_dict(),
            }
            self._record_audit({
                "run_id": run_id,
                "raw_request": user_request,
                "task_spec": spec.to_dict(),
                "manifest": manifest.to_dict(),
                "preflight": preflight,
                "final_status": "PREFLIGHT_FAILED",
            })
            return result

        # Section 7.2: Create repository if needed & verify metadata read-back
        if spec.repository_must_be_new:
            created_meta = github_client.create_repo(spec.repository_owner, spec.repository_name, spec.visibility)
            readback_meta = github_client.get_repo(spec.repository_owner, spec.repository_name)
            if (
                not readback_meta
                or int(readback_meta.get("id", -1)) != int(created_meta.get("id", -2))
                or (readback_meta.get("owner") or {}).get("login", "").lower() != spec.repository_owner.lower()
                or readback_meta.get("name", "").lower() != spec.repository_name.lower()
            ):
                result = {
                    "status": "ABORTED_WRONG_TARGET",
                    "run_id": run_id,
                    "writes_performed": 0,
                    "error": "Repository metadata read-back mismatch after creation. Aborting immediately.",
                }
                self._record_audit({"run_id": run_id, "final_status": "ABORTED_WRONG_TARGET", "error": result["error"]})
                return result
            identity = CanonicalRepositoryIdentity(
                owner=spec.repository_owner,
                repository_name=spec.repository_name,
                repository_id=int(readback_meta["id"]),
                canonical_url=readback_meta.get("html_url", ""),
                visibility=readback_meta.get("visibility", spec.visibility),
                default_branch=readback_meta.get("default_branch", "main"),
                task_run_id=run_id,
                created_at=readback_meta.get("created_at", datetime.now(timezone.utc).isoformat()),
            )
            target_branch = spec.branch or identity.default_branch
        else:
            identity = pre_identity
            # Ensure task branch for updates (Section 7.1)
            if target_branch != identity.default_branch:
                github_client.ensure_branch(identity.owner, identity.repository_name, target_branch, identity.default_branch)

        # Upload ONLY manifest-approved files (Section 7.2)
        report_buckets: Dict[str, List[str]] = {
            "created": [],
            "uploaded": [],
            "modified": [],
            "skipped": list(manifest.unexpected_files),
            "failed": [],
            "unverified": [],
        }
        commit_shas: List[str] = []

        for entry in manifest.entries:
            # Revalidate identity before write
            if connected_old_repo and identity.full_name.lower() == connected_old_repo.lower() and spec.repository_must_be_new:
                report_buckets["failed"].append(entry.path)
                continue
            try:
                up = github_client.upload_file(
                    identity.owner,
                    identity.repository_name,
                    target_branch,
                    entry.path,
                    entry.content or "",
                    f"RYN {run_id}: {entry.action} {entry.path}",
                )
                if not up or not up.get("ok"):
                    report_buckets["failed"].append(entry.path)
                    continue
                if up.get("commit_sha"):
                    commit_shas.append(str(up["commit_sha"]))
                report_buckets["uploaded"].append(entry.path)
                if entry.action == "CREATE":
                    report_buckets["created"].append(entry.path)
                else:
                    report_buckets["modified"].append(entry.path)
            except Exception:
                report_buckets["failed"].append(entry.path)

        # Section 8: Independent Read-Back Verification
        verified_paths: List[str] = []
        for entry in manifest.entries:
            if entry.path in report_buckets["failed"]:
                continue
            remote_content = github_client.read_file(identity.owner, identity.repository_name, target_branch, entry.path)
            if remote_content is None or sha256_text(remote_content) != entry.expected_sha256:
                report_buckets["unverified"].append(entry.path)
            else:
                verified_paths.append(entry.path)

        commit_ok = True
        if commit_shas:
            commit_ok = bool(github_client.verify_commit(identity.owner, identity.repository_name, target_branch, commit_shas[-1]))

        if report_buckets["failed"] and verified_paths:
            final_status = "PARTIAL"
        elif report_buckets["failed"] or report_buckets["unverified"] or not commit_ok or len(verified_paths) != len(manifest.entries):
            final_status = "FAILED"
        else:
            final_status = "VERIFIED_SUCCESS"

        result = {
            "status": final_status,
            "run_id": run_id,
            "identity": identity.to_dict(),
            "branch": target_branch,
            "commit_shas": commit_shas,
            "verified_paths": verified_paths,
            "report": report_buckets,
            "writes_performed": len(report_buckets["uploaded"]),
            "old_repository_touched": bool(connected_old_repo and identity.full_name.lower() == connected_old_repo.lower()),
            "preview": preflight["preview"],
            "task_spec": spec.to_dict(),
        }
        self._record_audit({
            "run_id": run_id,
            "raw_request": user_request,
            "task_spec": spec.to_dict(),
            "identity": identity.to_dict(),
            "manifest": manifest.to_dict(),
            "branch": target_branch,
            "commit_shas": commit_shas,
            "verification": {"verified_paths": verified_paths, "commit_ok": commit_ok, "report": report_buckets},
            "final_status": final_status,
        })
        return result
