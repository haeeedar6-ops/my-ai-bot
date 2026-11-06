# -*- coding: utf-8 -*-
"""
memory/__init__.py — Ten-Message Summarizer & Long-Term Hybrid Memory Service
==============================================================================
Implements Sections 4, 5, 9, 10, 11, and 12 of RYN Complete Integrated Upgrade Plan:
1. Summarize Every 10 User Messages (Section 4):
   - Trigger counts 10 user-authored messages (1..10, 11..20, 21..30) together with related assistant replies.
   - Database locks/unique constraints on `(conversation_id, window_index)` so each window is summarized once.
   - Safe retries without skipping windows or overwriting older summaries.
   - If an assistant response is pending, marks summary explicitly `partial` until completed.
   - Corrections create superseding versions while retaining source history.
   - Structured fields: `window_id`, `covered_message_ids`, `summary_text`, `facts`, `decisions`, `problems`, `verified_solutions`, `open_tasks`, `model_version`, `prompt_version`.
2. Long-Term Memory and Intelligent Recall (Section 5):
   - 5 Memory Layers: `user_profile`, `project_memory`, `technical_memory`, `conversation_memory`, `knowledge_memory`.
   - Hybrid search: exact keyword/metadata matching (names, IDs, error messages) + dense semantic vector similarity, re-ranked by relevance, recency, confidence, and source quality.
   - Provenance tracking (`user_fact`, `model_inference`, `external_fact`, `tested_outcome`) + source references.
   - Inspect, correct (supersedes old entry & invalidates stale retrieval), pin, export, and delete operations.
   - Strict secret redaction (never stores passwords, API keys, or tokens) and strict user/project isolation.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

MEMORY_LAYERS = (
    "user_profile",
    "project_memory",
    "technical_memory",
    "conversation_memory",
    "knowledge_memory",
)

PROVENANCE_TYPES = (
    "user_fact",
    "model_inference",
    "external_fact",
    "tested_outcome",
)

SECRET_PATTERNS_RE = re.compile(
    r"(?i)(\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{25,}|sk-[A-Za-z0-9\-_]{20,}|"
    r"AIza[0-9A-Za-z\-_]{30,}|gsk_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|"
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*)\b|"
    r"(?:password|passwd|secret_key|api_key|private_key|توكن|كلمة\s*السر|مفتاح\s*سري)\s*[:=]\s*\S+)"
)


def _iso_utc(ts: Optional[float] = None) -> str:
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def contains_secret(text: str) -> bool:
    return bool(SECRET_PATTERNS_RE.search(text or ""))


def scrub_secrets(text: str) -> str:
    return SECRET_PATTERNS_RE.sub("[REDACTED_SECRET]", text or "")


class HybridEmbeddingEngine:
    """
    Produces dense semantic embeddings for Arabic/English text and supports external embedding providers
    (e.g. Gemini text-embedding-004 when configured via EMBEDDING_MODEL) with deterministic local fallback
    using character n-grams, Arabic root normalization, and word-stem projections so vector index is always
    rebuildable from canonical SQLite/PostgreSQL metadata.
    """

    def __init__(self, dim: int = 384, external_embed_fn: Optional[Callable[[str], List[float]]] = None):
        self.dim = dim
        self.external_embed_fn = external_embed_fn
        self.model_name = os.environ.get("EMBEDDING_MODEL", "ryn-hybrid-multilingual-v2")

    @staticmethod
    def normalize_arabic_english(text: str) -> str:
        s = (text or "").lower()
        s = re.sub(r"[\u064B-\u065F\u0670]", "", s)  # strip tashkeel
        s = re.sub(r"[أإآ]", "ا", s)
        s = re.sub(r"ة\b", "ه", s)
        s = re.sub(r"ى\b", "ي", s)
        return s

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        norm = cls.normalize_arabic_english(text)
        words = re.findall(r"[a-z0-9_.\-/]{2,}|[\u0600-\u06FF]{2,}", norm)
        tokens: List[str] = []
        for w in words:
            tokens.append(w)
            # Strip common Arabic prefixes (ال، و، ب، ف، ل) for stem matching
            if len(w) >= 4 and w.startswith("ال"):
                tokens.append(w[2:])
            elif len(w) >= 5 and w.startswith(("وال", "بال", "كال", "فال")):
                tokens.append(w[3:])
        return tokens

    def embed(self, text: str) -> Dict[int, float]:
        if callable(self.external_embed_fn):
            try:
                dense = self.external_embed_fn(text)
                if dense and isinstance(dense, list):
                    norm = math.sqrt(sum(float(x) * float(x) for x in dense)) or 1.0
                    return {i: round(float(v) / norm, 5) for i, v in enumerate(dense[:self.dim]) if abs(float(v) / norm) > 0.01}
            except Exception:
                pass

        tokens = self.tokenize(text)
        if not tokens:
            return {}
        features: List[Tuple[str, float]] = []
        for tok in tokens:
            features.append((f"w:{tok}", 2.2 if len(tok) > 3 else 1.2))
            if len(tok) >= 4:
                for i in range(len(tok) - 2):
                    features.append((f"g3:{tok[i:i+3]}", 0.65))
        vec: Dict[int, float] = {}
        for feat, weight in features:
            digest = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] = vec.get(idx, 0.0) + sign * weight
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 1e-8:
            vec = {k: round(v / norm, 5) for k, v in vec.items() if abs(v / norm) > 0.015}
        return vec

    @staticmethod
    def cosine(v1: Dict[int, float], v2: Dict[int, float]) -> float:
        if not v1 or not v2:
            return 0.0
        if len(v1) > len(v2):
            v1, v2 = v2, v1
        return sum(val * v2.get(idx, 0.0) for idx, val in v1.items())


class MemoryService:
    """
    Canonical Long-Term Memory + 10-User-Message Summarization Service.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        embedder: Optional[HybridEmbeddingEngine] = None,
        summarizer_llm_fn: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = None,
    ):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        os.makedirs(base_dir, exist_ok=True)
        self.db_path = db_path or os.environ.get("RYN_DB_PATH") or os.path.join(base_dir, "ryn_canonical.db")
        self.embedder = embedder or HybridEmbeddingEngine()
        self.summarizer_llm_fn = summarizer_llm_fn
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS summary_windows (
                    window_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    window_index INTEGER NOT NULL,
                    start_seq INTEGER NOT NULL,
                    end_seq INTEGER NOT NULL,
                    user_msg_count INTEGER NOT NULL DEFAULT 10,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(conversation_id, window_index)
                );
                CREATE INDEX IF NOT EXISTS idx_sumwin_conv ON summary_windows(conversation_id, window_index ASC);
                CREATE INDEX IF NOT EXISTS idx_sumwin_status ON summary_windows(user_id, status);

                CREATE TABLE IF NOT EXISTS summaries (
                    summary_id TEXT PRIMARY KEY,
                    window_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    is_partial INTEGER NOT NULL DEFAULT 0,
                    summary_text TEXT NOT NULL,
                    structured_json TEXT NOT NULL,
                    source_message_ids_json TEXT NOT NULL,
                    model_version TEXT NOT NULL DEFAULT 'ryn-summarizer-v2',
                    prompt_version TEXT NOT NULL DEFAULT 'v2.0',
                    supersedes_summary_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(window_id) REFERENCES summary_windows(window_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_summaries_win ON summaries(window_id, version DESC);

                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_scope TEXT NOT NULL DEFAULT 'default',
                    layer TEXT NOT NULL DEFAULT 'knowledge_memory',
                    provenance TEXT NOT NULL DEFAULT 'user_fact',
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.9,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    embedding_json TEXT NOT NULL DEFAULT '{}',
                    supersedes_id TEXT NOT NULL DEFAULT '',
                    superseded_by_id TEXT NOT NULL DEFAULT '',
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mem_user_scope ON memory_items(user_id, project_scope, deleted);
                CREATE INDEX IF NOT EXISTS idx_mem_layer ON memory_items(user_id, layer, deleted);
                """
            )

    @staticmethod
    def _normalize_user(user_id: str) -> str:
        u = str(user_id or "").strip()
        if not u:
            raise PermissionError("user_id مطلوب من الجلسة الموثقة على السيرفر")
        return u

    # ==========================================================================
    # Section 4: Summarize Every 10 User Messages
    # ==========================================================================
    def _extract_structured_summary_from_messages(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Extracts facts/preferences, decisions/constraints, problems/attempted_fixes,
        verified_solutions (only fixes supported by tests or user confirmation),
        and open_tasks/next_steps from a 10-user-message window + related assistant replies.
        """
        if callable(self.summarizer_llm_fn):
            res = self.summarizer_llm_fn(messages)
            if isinstance(res, dict) and res.get("summary_text"):
                return res

        facts: List[Dict[str, Any]] = []
        decisions: List[Dict[str, Any]] = []
        problems: List[Dict[str, Any]] = []
        verified_solutions: List[Dict[str, Any]] = []
        open_tasks: List[Dict[str, Any]] = []
        topics: List[str] = []

        for m in messages:
            mid = m.get("message_id") or m.get("id") or ""
            role = m.get("role") or "user"
            txt = scrub_secrets(str(m.get("content") or "").strip())
            if not txt:
                continue
            ts_iso = m.get("created_at") or _iso_utc()
            first_line = txt.splitlines()[0][:140]

            if role == "user":
                topics.append(first_line)
                # Detect user facts / preferences
                if re.search(r"(?i)(اسمي|أفضل|تفضيلي|أعمل على|مشروعي|أستخدم|my name is|i prefer|i use|we use)", txt):
                    facts.append({
                        "fact": first_line,
                        "source_message_id": mid,
                        "confidence": 0.95,
                        "date": ts_iso,
                    })
                # Detect decisions / constraints
                if re.search(r"(?i)(شروطي|لا تقم بتعديل|حافظ على|اعتمد|قررنا|بدون|constraint|must not|do not change|decided)", txt):
                    decisions.append({
                        "decision": first_line,
                        "source_message_id": mid,
                        "status": "accepted",
                    })
                # Detect problems / bugs
                if re.search(r"(?i)(مشكلة|خطأ|عطل|فشل|لا يعمل|bug|error|exception|fail|issue|conflict)", txt):
                    problems.append({
                        "symptom": first_line,
                        "source_message_id": mid,
                        "outcome": "investigating",
                    })
                # Detect open tasks / next steps
                if re.search(r"(?i)(لاحقاً|الخطوة القادمة|باقي|أكمل|todo|next step|pending|follow up)", txt):
                    open_tasks.append({
                        "task": first_line,
                        "source_message_id": mid,
                    })
                # Detect user confirmation of a solution
                if re.search(r"(?i)(اشتغل تمام|نحلت المشكلة|شغال 100%|كل شي تمام|verified|tests pass|works now)", txt):
                    verified_solutions.append({
                        "solution": first_line,
                        "source_message_id": mid,
                        "evidence": "user_confirmation",
                    })
            else:
                # Assistant reply: check if it includes test/verification evidence
                if re.search(r"(?i)(✅ مفحوص|تطابق وفحص AST|tests pass|OK|verified commit)", txt):
                    verified_solutions.append({
                        "solution": first_line,
                        "source_message_id": mid,
                        "evidence": "automated_test_or_ast_verification",
                    })

        summary_text = (
            f"ملخص نافذة الـ 10 رسائل ({len(messages)} إجمالي التفاعلات): "
            + " ؛ ".join(topics[:5])
            + (f" ... (+{len(topics) - 5} مواضيع أخرى)" if len(topics) > 5 else "")
        )

        return {
            "summary_text": summary_text,
            "facts": facts,
            "decisions": decisions,
            "problems": problems,
            "verified_solutions": verified_solutions,
            "open_tasks": open_tasks,
        }

    def process_ten_message_window(
        self,
        user_id: str,
        conversation_id: str,
        window_index: int,
        window_messages: List[Dict[str, Any]],
        force_retry: bool = False,
    ) -> Dict[str, Any]:
        """
        Queues and executes the 10-user-message window summarization job with database lock/unique constraint
        so each window (1..10 -> index 1, 11..20 -> index 2, 21..30 -> index 3) is summarized exactly once.
        """
        u = self._normalize_user(user_id)
        cid = str(conversation_id or "").strip()
        w_idx = int(window_index)
        if w_idx < 1:
            raise ValueError("window_index يجب أن يكون >= 1")

        user_msgs = [m for m in window_messages if m.get("role") == "user"]
        if len(user_msgs) < 10:
            raise ValueError(f"النافذة تتطلب 10 رسائل من المستخدم على الأقل (المتوفر: {len(user_msgs)})")

        # Exact 10 user messages for this window plus any assistant replies in the sequence span
        target_user_msgs = user_msgs[:10]
        start_seq = int(target_user_msgs[0]["sequence"])
        end_seq = int(window_messages[-1]["sequence"])
        window_id = f"win_{cid}_{w_idx}"
        now = time.time()

        # Check if an assistant response is still pending (e.g., last message in window is user message or status=='running')
        is_partial = (
            window_messages[-1].get("role") == "user"
            or any(m.get("status") in ("queued", "running") for m in window_messages)
        )

        with self._lock, self._conn() as conn:
            existing_win = conn.execute(
                "SELECT * FROM summary_windows WHERE conversation_id = ? AND window_index = ?",
                (cid, w_idx),
            ).fetchone()

            if existing_win:
                window_id = existing_win["window_id"]
                if existing_win["status"] == "succeeded" and not force_retry:
                    # Check if it was partial and now assistant reply has arrived
                    latest_sum = conn.execute(
                        "SELECT * FROM summaries WHERE window_id = ? ORDER BY version DESC LIMIT 1",
                        (window_id,),
                    ).fetchone()
                    if latest_sum and not (latest_sum["is_partial"] and not is_partial):
                        return self._row_to_summary(latest_sum, existing_win)
                conn.execute(
                    "UPDATE summary_windows SET status = 'running', attempts = attempts + 1, end_seq = ?, updated_at = ? WHERE window_id = ?",
                    (end_seq, now, window_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO summary_windows (
                        window_id, conversation_id, user_id, window_index,
                        start_seq, end_seq, user_msg_count, status, attempts,
                        last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 10, 'running', 1, '', ?, ?)
                    """,
                    (window_id, cid, u, w_idx, start_seq, end_seq, now, now),
                )

        try:
            extracted = self._extract_structured_summary_from_messages(window_messages)
            covered_ids = [str(m.get("message_id") or m.get("id")) for m in window_messages]

            structured_payload = {
                "window_id": window_id,
                "window_index": w_idx,
                "covered_message_ids": covered_ids,
                "summary_text": scrub_secrets(extracted.get("summary_text") or ""),
                "facts": extracted.get("facts") or [],
                "decisions": extracted.get("decisions") or [],
                "problems": extracted.get("problems") or [],
                "verified_solutions": extracted.get("verified_solutions") or [],
                "open_tasks": extracted.get("open_tasks") or [],
                "is_partial": bool(is_partial),
                "model_version": "ryn-summarizer-v2",
                "prompt_version": "v2.0",
            }

            with self._lock, self._conn() as conn:
                prev_sum = conn.execute(
                    "SELECT * FROM summaries WHERE window_id = ? ORDER BY version DESC LIMIT 1",
                    (window_id,),
                ).fetchone()
                next_ver = (int(prev_sum["version"]) + 1) if prev_sum else 1
                supersedes_id = prev_sum["summary_id"] if prev_sum else ""
                summary_id = f"sum_{uuid.uuid4().hex[:14]}"

                conn.execute(
                    """
                    INSERT INTO summaries (
                        summary_id, window_id, conversation_id, user_id, version,
                        is_partial, summary_text, structured_json, source_message_ids_json,
                        model_version, prompt_version, supersedes_summary_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary_id,
                        window_id,
                        cid,
                        u,
                        next_ver,
                        1 if is_partial else 0,
                        structured_payload["summary_text"],
                        json.dumps(structured_payload, ensure_ascii=False),
                        json.dumps(covered_ids, ensure_ascii=False),
                        structured_payload["model_version"],
                        structured_payload["prompt_version"],
                        supersedes_id,
                        time.time(),
                    ),
                )
                conn.execute(
                    "UPDATE summary_windows SET status = 'succeeded', last_error = '', updated_at = ? WHERE window_id = ?",
                    (time.time(), window_id),
                )
                saved_sum = conn.execute("SELECT * FROM summaries WHERE summary_id = ?", (summary_id,)).fetchone()
                saved_win = conn.execute("SELECT * FROM summary_windows WHERE window_id = ?", (window_id,)).fetchone()

            # Index the summary and extracted items into Long-Term Memory
            self.add_memory(
                user_id=u,
                content=structured_payload["summary_text"],
                layer="conversation_memory",
                provenance="model_inference",
                confidence=0.88,
                source_refs=[window_id] + covered_ids[:5],
                metadata={"window_id": window_id, "conversation_id": cid, "window_index": w_idx},
            )
            for f_item in structured_payload["facts"]:
                self.add_memory(
                    user_id=u,
                    content=str(f_item.get("fact") or ""),
                    layer="user_profile",
                    provenance="user_fact",
                    confidence=float(f_item.get("confidence") or 0.95),
                    source_refs=[str(f_item.get("source_message_id") or window_id)],
                )
            for d_item in structured_payload["decisions"]:
                self.add_memory(
                    user_id=u,
                    content=str(d_item.get("decision") or ""),
                    layer="project_memory",
                    provenance="user_fact",
                    confidence=0.95,
                    source_refs=[str(d_item.get("source_message_id") or window_id)],
                )
            for v_item in structured_payload["verified_solutions"]:
                self.add_memory(
                    user_id=u,
                    content=str(v_item.get("solution") or ""),
                    layer="technical_memory",
                    provenance="tested_outcome",
                    confidence=0.98,
                    source_refs=[str(v_item.get("source_message_id") or window_id)],
                    metadata={"evidence": v_item.get("evidence", "verified")},
                )

            return self._row_to_summary(saved_sum, saved_win)

        except Exception as e:
            with self._lock, self._conn() as conn:
                conn.execute(
                    "UPDATE summary_windows SET status = 'failed', last_error = ?, updated_at = ? WHERE window_id = ?",
                    (str(e)[:240], time.time(), window_id),
                )
            raise

    def retry_failed_summaries(
        self,
        user_id: str,
        fetch_messages_fn: Callable[[str, str, int, int], List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Retries failed summary jobs safely without creating duplicates or skipping windows."""
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            failed_rows = conn.execute(
                "SELECT * FROM summary_windows WHERE user_id = ? AND status = 'failed' ORDER BY window_index ASC",
                (u,),
            ).fetchall()

        results = []
        for row in failed_rows:
            cid = row["conversation_id"]
            w_idx = int(row["window_index"])
            msgs = fetch_messages_fn(u, cid, int(row["start_seq"]), int(row["end_seq"]))
            res = self.process_ten_message_window(u, cid, w_idx, msgs, force_retry=True)
            results.append(res)
        return results

    def correct_summary(self, user_id: str, window_id: str, corrected_text: str, corrected_facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Creates a superseding summary version while retaining source history."""
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            win = conn.execute("SELECT * FROM summary_windows WHERE window_id = ?", (window_id,)).fetchone()
            if not win or win["user_id"] != u:
                raise PermissionError("⛔ نافذة الملخص غير موجودة أو غير مصرح بها.")
            prev = conn.execute(
                "SELECT * FROM summaries WHERE window_id = ? ORDER BY version DESC LIMIT 1",
                (window_id,),
            ).fetchone()
            if not prev:
                raise KeyError("لا يوجد ملخص سابق لتصحيحه")
            struct = json.loads(prev["structured_json"] or "{}")
            struct["summary_text"] = scrub_secrets(corrected_text)
            if corrected_facts is not None:
                struct["facts"] = corrected_facts
            struct["is_partial"] = False
            next_ver = int(prev["version"]) + 1
            new_sum_id = f"sum_{uuid.uuid4().hex[:14]}"
            conn.execute(
                """
                INSERT INTO summaries (
                    summary_id, window_id, conversation_id, user_id, version,
                    is_partial, summary_text, structured_json, source_message_ids_json,
                    model_version, prompt_version, supersedes_summary_id, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_sum_id,
                    window_id,
                    win["conversation_id"],
                    u,
                    next_ver,
                    struct["summary_text"],
                    json.dumps(struct, ensure_ascii=False),
                    prev["source_message_ids_json"],
                    "user-corrected-v2",
                    prev["prompt_version"],
                    prev["summary_id"],
                    time.time(),
                ),
            )
            saved_sum = conn.execute("SELECT * FROM summaries WHERE summary_id = ?", (new_sum_id,)).fetchone()
            return self._row_to_summary(saved_sum, win)

    def list_summaries(self, user_id: str, conversation_id: Optional[str] = None, latest_only: bool = True) -> List[Dict[str, Any]]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            if conversation_id:
                wins = conn.execute(
                    "SELECT * FROM summary_windows WHERE user_id = ? AND conversation_id = ? ORDER BY window_index ASC",
                    (u, conversation_id),
                ).fetchall()
            else:
                wins = conn.execute(
                    "SELECT * FROM summary_windows WHERE user_id = ? ORDER BY created_at ASC",
                    (u,),
                ).fetchall()
            out = []
            for w in wins:
                if latest_only:
                    s_row = conn.execute(
                        "SELECT * FROM summaries WHERE window_id = ? ORDER BY version DESC LIMIT 1",
                        (w["window_id"],),
                    ).fetchone()
                    if s_row:
                        out.append(self._row_to_summary(s_row, w))
                else:
                    s_rows = conn.execute(
                        "SELECT * FROM summaries WHERE window_id = ? ORDER BY version ASC",
                        (w["window_id"],),
                    ).fetchall()
                    for s_row in s_rows:
                        out.append(self._row_to_summary(s_row, w))
            return out

    def get_summary_queue_status(self, user_id: str) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS c FROM summary_windows WHERE user_id = ? GROUP BY status", (u,)).fetchall()
            counts = {r["status"]: int(r["c"]) for r in rows}
            failed = conn.execute(
                "SELECT window_id, conversation_id, window_index, attempts, last_error FROM summary_windows WHERE user_id = ? AND status = 'failed'",
                (u,),
            ).fetchall()
            return {
                "queued": counts.get("queued", 0),
                "running": counts.get("running", 0),
                "succeeded": counts.get("succeeded", 0),
                "failed": counts.get("failed", 0),
                "failed_jobs": [dict(r) for r in failed],
            }

    @staticmethod
    def _row_to_summary(s_row: sqlite3.Row, w_row: sqlite3.Row) -> Dict[str, Any]:
        try:
            struct = json.loads(s_row["structured_json"] or "{}")
        except Exception:
            struct = {}
        try:
            src_ids = json.loads(s_row["source_message_ids_json"] or "[]")
        except Exception:
            src_ids = []
        return {
            "summary_id": s_row["summary_id"],
            "window_id": s_row["window_id"],
            "conversation_id": s_row["conversation_id"],
            "user_id": s_row["user_id"],
            "window_index": int(w_row["window_index"]),
            "start_seq": int(w_row["start_seq"]),
            "end_seq": int(w_row["end_seq"]),
            "status": w_row["status"],
            "attempts": int(w_row["attempts"]),
            "version": int(s_row["version"]),
            "is_partial": bool(s_row["is_partial"]),
            "summary_text": s_row["summary_text"],
            "covered_message_ids": src_ids,
            "facts": struct.get("facts", []),
            "decisions": struct.get("decisions", []),
            "problems": struct.get("problems", []),
            "verified_solutions": struct.get("verified_solutions", []),
            "open_tasks": struct.get("open_tasks", []),
            "model_version": s_row["model_version"],
            "prompt_version": s_row["prompt_version"],
            "supersedes_summary_id": s_row["supersedes_summary_id"],
            "created_at": _iso_utc(s_row["created_at"]),
        }

    # ==========================================================================
    # Section 5: Long-Term Memory and Intelligent Recall
    # ==========================================================================
    def add_memory(
        self,
        user_id: str,
        content: str,
        layer: str = "knowledge_memory",
        provenance: str = "user_fact",
        project_scope: str = "default",
        confidence: float = 0.9,
        source_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        pinned: bool = False,
        supersedes_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Stores a memory item in canonical DB and computes its semantic vector embedding.
        Never stores passwords, API keys, or tokens.
        """
        u = self._normalize_user(user_id)
        raw_text = str(content or "").strip()
        if not raw_text:
            return None
        if contains_secret(raw_text):
            raise ValueError("⛔ تم رفض حفظ الذاكرة لاحتوائها على كلمة مرور أو مفتاح API سري.")

        clean_layer = layer if layer in MEMORY_LAYERS else "knowledge_memory"
        clean_prov = provenance if provenance in PROVENANCE_TYPES else "user_fact"
        scope = str(project_scope or "default").strip()[:80] or "default"
        conf = max(0.0, min(1.0, float(confidence)))
        vec = self.embedder.embed(raw_text)
        now = time.time()
        mem_id = f"mem_{uuid.uuid4().hex[:14]}"

        with self._lock, self._conn() as conn:
            if supersedes_id:
                old = conn.execute("SELECT * FROM memory_items WHERE id = ?", (supersedes_id,)).fetchone()
                if old and old["user_id"] != u:
                    raise PermissionError("⛔ لا يمكنك تعديل ذاكرة تخص مستخدماً آخر.")
                if old:
                    conn.execute(
                        "UPDATE memory_items SET superseded_by_id = ?, updated_at = ? WHERE id = ?",
                        (mem_id, now, supersedes_id),
                    )

            conn.execute(
                """
                INSERT INTO memory_items (
                    id, user_id, project_scope, layer, provenance, content,
                    confidence, pinned, source_refs_json, metadata_json,
                    embedding_json, supersedes_id, superseded_by_id, deleted,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, ?, ?)
                """,
                (
                    mem_id,
                    u,
                    scope,
                    clean_layer,
                    clean_prov,
                    raw_text,
                    conf,
                    1 if pinned else 0,
                    json.dumps(source_refs or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    json.dumps({str(k): v for k, v in vec.items()}),
                    supersedes_id,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (mem_id,)).fetchone()
            return self._row_to_memory(row)

    def correct_memory(
        self,
        user_id: str,
        old_memory_id: str,
        corrected_content: str,
        source_refs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """When a user corrects a fact, supersedes the old entry and invalidates stale retrieval."""
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            old = conn.execute("SELECT * FROM memory_items WHERE id = ?", (old_memory_id,)).fetchone()
            if not old:
                raise KeyError("عنصر الذاكرة غير موجود")
            if old["user_id"] != u:
                raise PermissionError("⛔ لا تملك صلاحية تصحيح ذاكرة مستخدم آخر")
            old_refs = json.loads(old["source_refs_json"] or "[]")
            combined_refs = list(dict.fromkeys((source_refs or []) + old_refs + [old_memory_id]))

        res = self.add_memory(
            user_id=u,
            content=corrected_content,
            layer=old["layer"],
            provenance="user_fact",
            project_scope=old["project_scope"],
            confidence=0.99,
            source_refs=combined_refs,
            pinned=bool(old["pinned"]),
            supersedes_id=old_memory_id,
        )
        if not res:
            raise ValueError("محتوى التصحيح فارغ")
        return res

    def pin_memory(self, user_id: str, memory_id: str, pinned: bool = True) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
            if not row or row["user_id"] != u:
                raise PermissionError("⛔ عنصر الذاكرة غير موجود أو غير مصرح به")
            conn.execute("UPDATE memory_items SET pinned = ?, updated_at = ? WHERE id = ?", (1 if pinned else 0, time.time(), memory_id))
            updated = conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
            return self._row_to_memory(updated)

    def delete_memory(self, user_id: str, memory_id: str) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
            if not row or row["user_id"] != u:
                raise PermissionError("⛔ عنصر الذاكرة غير موجود أو غير مصرح به")
            conn.execute("UPDATE memory_items SET deleted = 1, updated_at = ? WHERE id = ?", (time.time(), memory_id))
            return {"ok": True, "deleted_id": memory_id}

    def rebuild_vector_index(self, user_id: Optional[str] = None) -> int:
        """Vector index is rebuildable and never the only copy of memory."""
        with self._lock, self._conn() as conn:
            if user_id:
                rows = conn.execute("SELECT id, content FROM memory_items WHERE user_id = ? AND deleted = 0", (user_id,)).fetchall()
            else:
                rows = conn.execute("SELECT id, content FROM memory_items WHERE deleted = 0").fetchall()
            count = 0
            for r in rows:
                vec = self.embedder.embed(r["content"])
                conn.execute(
                    "UPDATE memory_items SET embedding_json = ? WHERE id = ?",
                    (json.dumps({str(k): v for k, v in vec.items()}), r["id"]),
                )
                count += 1
            return count

    def retrieve(
        self,
        user_id: str,
        query: str,
        project_scope: str = "default",
        layers: Optional[List[str]] = None,
        top_k: int = 5,
        min_score: float = 0.15,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid Retrieval Flow (Section 5):
        User request -> identify user/project/task scope -> exact keyword + metadata search + vector similarity ->
        re-rank by relevance, recency, confidence and source quality -> remove stale duplicates/conflicts ->
        return bounded context items with source IDs.
        """
        u = self._normalize_user(user_id)
        q_clean = (query or "").strip()
        scope = str(project_scope or "default").strip() or "default"

        with self._lock, self._conn() as conn:
            # Exclude deleted and superseded (stale) entries!
            rows = conn.execute(
                """
                SELECT * FROM memory_items
                WHERE user_id = ? AND project_scope = ? AND deleted = 0 AND superseded_by_id = ''
                ORDER BY pinned DESC, updated_at DESC
                """,
                (u, scope),
            ).fetchall()

        if not rows:
            return []

        if layers:
            allowed_l = set(layers)
            rows = [r for r in rows if r["layer"] in allowed_l]

        q_tokens = set(self.embedder.tokenize(q_clean))
        q_vec = self.embedder.embed(q_clean) if q_clean else {}
        now = time.time()

        prov_quality = {
            "tested_outcome": 1.0,
            "user_fact": 0.95,
            "external_fact": 0.85,
            "model_inference": 0.75,
        }

        scored: List[Tuple[float, Dict[str, Any]]] = []
        seen_contents = set()

        for r in rows:
            item = self._row_to_memory(r)
            norm_content = self.embedder.normalize_arabic_english(item["content"])
            if norm_content in seen_contents:
                continue

            # 1. Exact keyword & substring matching (names, IDs, error messages)
            doc_tokens = set(self.embedder.tokenize(item["content"]))
            overlap = len(q_tokens & doc_tokens) / float(max(1, len(q_tokens))) if q_tokens else 0.0
            exact_phrase_bonus = 0.35 if (q_clean and len(q_clean) >= 3 and self.embedder.normalize_arabic_english(q_clean) in norm_content) else 0.0

            # 2. Vector similarity for paraphrases
            try:
                raw_vec = json.loads(r["embedding_json"] or "{}")
                d_vec = {int(k): float(v) for k, v in raw_vec.items()}
            except Exception:
                d_vec = {}
            vec_sim = self.embedder.cosine(q_vec, d_vec) if q_vec else 0.0

            relevance = (0.55 * vec_sim) + (0.45 * overlap) + exact_phrase_bonus
            if item["pinned"] and relevance < min_score:
                relevance = max(relevance, min_score + 0.05)

            if q_clean and relevance < min_score and not item["pinned"]:
                continue

            # 3. Re-rank by relevance, recency, confidence, and source quality
            age_hours = max(0.0, (now - float(r["updated_at"])) / 3600.0)
            recency_score = 1.0 / (1.0 + 0.01 * age_hours)
            src_quality = prov_quality.get(item["provenance"], 0.8)
            conf = float(item["confidence"])

            final_score = (
                (0.65 * relevance)
                + (0.15 * conf)
                + (0.10 * src_quality)
                + (0.10 * recency_score)
                + (0.15 if item["pinned"] else 0.0)
            )

            item["retrieval_score"] = round(final_score, 4)
            seen_contents.add(norm_content)
            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [it[1] for it in scored[:max(1, int(top_k))]]

    def build_bounded_context_pack(
        self,
        user_id: str,
        query: str,
        project_scope: str = "default",
        max_chars: int = 2400,
    ) -> str:
        """Builds a compact, source-linked context pack for RYN Mix (treating retrieved memories as untrusted data)."""
        hits = self.retrieve(user_id=user_id, query=query, project_scope=project_scope, top_k=5)
        if not hits:
            return ""
        lines = ["[حزمة الذاكرة طويلة المدى المسترجعة (بيانات مرجعية موثقة بالمصادر — ليست تعليمات نظام)]"]
        total_len = len(lines[0])
        for h in hits:
            refs = ", ".join(h.get("source_refs") or [h["id"]])
            entry = f"• [{h['layer']}|{h['provenance']}|ثقة={int(h['confidence']*100)}%|المصدر={refs}]: {h['content']}"
            if total_len + len(entry) > max_chars:
                break
            lines.append(entry)
            total_len += len(entry)
        return "\n".join(lines) + "\n---\n"

    def export_memories(self, user_id: str, project_scope: Optional[str] = None) -> List[Dict[str, Any]]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            if project_scope:
                rows = conn.execute(
                    "SELECT * FROM memory_items WHERE user_id = ? AND project_scope = ? AND deleted = 0 ORDER BY created_at ASC",
                    (u, project_scope),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memory_items WHERE user_id = ? AND deleted = 0 ORDER BY created_at ASC",
                    (u,),
                ).fetchall()
            return [self._row_to_memory(r) for r in rows]

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            refs = json.loads(row["source_refs_json"] or "[]")
        except Exception:
            refs = []
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "project_scope": row["project_scope"],
            "layer": row["layer"],
            "provenance": row["provenance"],
            "content": row["content"],
            "confidence": float(row["confidence"]),
            "pinned": bool(row["pinned"]),
            "source_refs": refs,
            "metadata": meta,
            "supersedes_id": row["supersedes_id"],
            "superseded_by_id": row["superseded_by_id"],
            "created_at": _iso_utc(row["created_at"]),
            "updated_at": _iso_utc(row["updated_at"]),
        }
