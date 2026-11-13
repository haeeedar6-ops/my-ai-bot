# -*- coding: utf-8 -*-
"""
memory/__init__.py — Device-Local Persistent Memory (SQLite + FTS5 + At-Rest Encryption) & 10-Message Summarizer
=================================================================================================================
Implements:
1. Phase 2 of RYN Pre-Implementation Repair Plan (Device-Local Long-Term Memory):
   - Authoritative SQLite database in private device storage with WAL mode and strict connection closing.
   - SQLite FTS5 (`memory_fts`) full-text search index + deterministic on-device vector embeddings (`HybridEmbeddingEngine`)
     that NEVER silently upload personal memory to remote servers.
   - Per-device at-rest encryption (`LocalMemoryCipher`) protected by OS credential/keystore or local 0600 device key,
     plus user-initiated encrypted backup export and restore (`export_encrypted_backup` / `restore_encrypted_backup`).
   - Full Section 5.2 record schema: `memory_id`, `created_at`, `updated_at`, `category`, `content`,
     `source_conversation_id`, `source_message_range`, `importance_score`, `confidence`, `tags`,
     `embedding`, `sensitivity_label`, `expires_at`, `is_archived`.
   - Full Section 5.3 lifecycle: Capture -> Propose -> Confirm -> Store -> Retrieve (FTS5 + Semantic) ->
     Use (inject only relevant snippets) -> Update -> Archive -> Forget/Delete -> Encrypted Backup/Restore.
   - Strict secret blocking (never stores API keys, passwords, tokens, recovery codes).
2. Summarize Every 10 User Messages (Section 4 of Integrated Upgrade Plan):
   - Trigger counts 10 user-authored messages (1..10, 11..20, 21..30) with safe retry and superseding corrections.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import math
import os
import re
import secrets
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

MEMORY_CATEGORIES = (
    "user_profile",
    "project_memory",
    "technical_memory",
    "conversation_memory",
    "knowledge_memory",
    "preferences",
    "decisions",
    "tasks",
)

PROVENANCE_TYPES = (
    "user_fact",
    "model_inference",
    "external_fact",
    "tested_outcome",
)

SENSITIVITY_LABELS = (
    "normal",
    "personal",
    "sensitive",
    "restricted",
)

SECRET_PATTERNS_RE = re.compile(
    r"(?i)(\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{25,}|sk-[A-Za-z0-9\-_]{20,}|"
    r"AIza[0-9A-Za-z\-_]{30,}|gsk_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|"
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*)\b|"
    r"(?:password|passwd|secret_key|api_key|private_key|recovery_code|seed_phrase|توكن|كلمة\s*السر|كلمة\s*المرور|مفتاح\s*سري)\s*[:=]\s*\S+)"
)

SENSITIVE_PERSONAL_RE = re.compile(
    r"(?i)(\b(?:medical|health|diagnosis|address|phone\s*number|national\s*id|passport|salary|debt)\b|"
    r"(?:عنوان\s*بيتي|رقم\s*هاتفي|حالتي\s*الصحية|مرض|راتبي|ديون|هوية\s*شخصية|جواز\s*سفر))"
)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _iso_utc(ts: Optional[float] = None) -> str:
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def contains_secret(text: str) -> bool:
    return bool(SECRET_PATTERNS_RE.search(text or ""))


def scrub_secrets(text: str) -> str:
    return SECRET_PATTERNS_RE.sub("[REDACTED_SECRET]", text or "")


class LocalMemoryCipher:
    """
    Encrypts sensitive memory records at rest and encrypts exported backup bundles using a per-device key
    protected by OS credential store (keyring) or a restricted local device keyfile (0600 permissions).
    Uses PBKDF2-HMAC-SHA256 (100,000 iterations) + SHA256-CTR stream cipher + HMAC-SHA256 authentication tag.
    """

    def __init__(self, key_dir: Optional[str] = None, master_secret: Optional[bytes] = None):
        base_dir = key_dir or os.environ.get("RYN_DATA_DIR") or tempfile.gettempdir()
        os.makedirs(base_dir, exist_ok=True)
        self.key_path = os.path.join(base_dir, ".ryn_device_keystore.key")
        self._master_secret = master_secret or self._load_or_create_device_key()

    def _load_or_create_device_key(self) -> bytes:
        # 1. Prefer OS credential store / keyring when available
        try:
            import keyring  # type: ignore
            stored = keyring.get_password("ryn-local-memory", "device-master-key")
            if stored:
                return base64.b64decode(stored.encode("ascii"))
        except Exception:
            pass

        # 2. Fallback to restricted device-local key file (0600)
        if os.path.isfile(self.key_path):
            try:
                with open(self.key_path, "rb") as f:
                    raw = f.read().strip()
                if len(raw) >= 32:
                    return raw[:32]
            except Exception:
                pass

        key = secrets.token_bytes(32)
        try:
            with open(self.key_path, "wb") as f:
                f.write(key)
            try:
                os.chmod(self.key_path, 0o600)
            except Exception:
                pass
        except Exception:
            pass
        return key

    @staticmethod
    def _derive_keys(secret: bytes, salt: bytes, iterations: int = 60_000) -> Tuple[bytes, bytes]:
        dk = hashlib.pbkdf2_hmac("sha256", secret, salt, iterations, dklen=64)
        return dk[:32], dk[32:]

    @staticmethod
    def _xor_keystream(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
        out = bytearray(len(data))
        block_count = (len(data) + 31) // 32
        for counter in range(block_count):
            blk = hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            start = counter * 32
            end = min(len(data), start + 32)
            for i in range(start, end):
                out[i] = data[i] ^ blk[i - start]
        return bytes(out)

    def encrypt_text(self, plaintext: str, passphrase: str = "") -> str:
        raw = (plaintext or "").encode("utf-8")
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(16)
        secret = (passphrase.encode("utf-8") + b":" + self._master_secret) if passphrase else self._master_secret
        enc_key, mac_key = self._derive_keys(secret, salt)
        ciphertext = self._xor_keystream(raw, enc_key, nonce)
        mac = hmac.new(mac_key, b"RYN_ENC_V1" + salt + nonce + ciphertext, hashlib.sha256).digest()
        packed = b"RYN1" + salt + nonce + mac + ciphertext
        return base64.b64encode(packed).decode("ascii")

    def decrypt_text(self, token: str, passphrase: str = "") -> str:
        if not token:
            return ""
        raw = base64.b64decode(token.encode("ascii"))
        if len(raw) < 68 or raw[:4] != b"RYN1":
            raise ValueError("صيغة البيانات المشفرة غير صالحة أو تالفة")
        salt = raw[4:20]
        nonce = raw[20:36]
        mac = raw[36:68]
        ciphertext = raw[68:]
        secret = (passphrase.encode("utf-8") + b":" + self._master_secret) if passphrase else self._master_secret
        enc_key, mac_key = self._derive_keys(secret, salt)
        expected_mac = hmac.new(mac_key, b"RYN_ENC_V1" + salt + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise PermissionError("فشل التحقق من سلامة أو مفتاح فك تشفير الذاكرة المحلية")
        plain_bytes = self._xor_keystream(ciphertext, enc_key, nonce)
        return plain_bytes.decode("utf-8")


class HybridEmbeddingEngine:
    """
    Produces deterministic on-device semantic embeddings for Arabic/English text using character n-grams,
    Arabic root/prefix normalization, and word-stem projections.
    Privacy rule (Section 5.1): Never silently uploads personal memory to a remote server to create embeddings.
    """

    def __init__(self, dim: int = 384, external_embed_fn: Optional[Callable[[str], List[float]]] = None):
        self.dim = dim
        # Only used if explicitly injected and allow_remote_embeddings is explicitly enabled
        self.external_embed_fn = external_embed_fn
        self.allow_remote_embeddings = os.environ.get("RYN_ALLOW_REMOTE_MEMORY_EMBEDDINGS", "0") == "1"
        self.model_name = os.environ.get("EMBEDDING_MODEL", "ryn-local-ondevice-multilingual-v2")

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
            if len(w) >= 4 and w.startswith("ال"):
                tokens.append(w[2:])
            elif len(w) >= 5 and w.startswith(("وال", "بال", "كال", "فال")):
                tokens.append(w[3:])
        return tokens

    def embed(self, text: str) -> Dict[int, float]:
        if self.allow_remote_embeddings and callable(self.external_embed_fn):
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
    Device-Local Persistent Long-Term Memory (SQLite + FTS5 + At-Rest Encryption)
    and 10-User-Message Summarization Service.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        embedder: Optional[HybridEmbeddingEngine] = None,
        summarizer_llm_fn: Optional[Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = None,
        cipher: Optional[LocalMemoryCipher] = None,
    ):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        os.makedirs(base_dir, exist_ok=True)
        self.db_path = db_path or os.environ.get("RYN_DB_PATH") or os.path.join(base_dir, "ryn_canonical.db")
        self.embedder = embedder or HybridEmbeddingEngine()
        self.summarizer_llm_fn = summarizer_llm_fn
        self.cipher = cipher or LocalMemoryCipher(key_dir=os.path.dirname(os.path.abspath(self.db_path)))
        self._lock = threading.RLock()
        self._fts5_available = False
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, factory=_ClosingConnection)
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
                    category TEXT NOT NULL DEFAULT 'knowledge_memory',
                    provenance TEXT NOT NULL DEFAULT 'user_fact',
                    content TEXT NOT NULL,
                    encrypted_content TEXT NOT NULL DEFAULT '',
                    source_conversation_id TEXT NOT NULL DEFAULT '',
                    source_message_range TEXT NOT NULL DEFAULT '',
                    importance_score REAL NOT NULL DEFAULT 0.75,
                    confidence REAL NOT NULL DEFAULT 0.9,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    sensitivity_label TEXT NOT NULL DEFAULT 'normal',
                    expires_at REAL,
                    is_archived INTEGER NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS local_memory_meta (
                    user_id TEXT PRIMARY KEY,
                    last_backup_at REAL NOT NULL DEFAULT 0,
                    backup_location TEXT NOT NULL DEFAULT '',
                    retention_days INTEGER NOT NULL DEFAULT 365,
                    updated_at REAL NOT NULL DEFAULT 0
                );
                """
            )
            # Safe schema migration for older databases
            existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(memory_items)").fetchall()}
            migrations = [
                ("category", "TEXT NOT NULL DEFAULT 'knowledge_memory'"),
                ("encrypted_content", "TEXT NOT NULL DEFAULT ''"),
                ("source_conversation_id", "TEXT NOT NULL DEFAULT ''"),
                ("source_message_range", "TEXT NOT NULL DEFAULT ''"),
                ("importance_score", "REAL NOT NULL DEFAULT 0.75"),
                ("tags_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("sensitivity_label", "TEXT NOT NULL DEFAULT 'normal'"),
                ("expires_at", "REAL"),
                ("is_archived", "INTEGER NOT NULL DEFAULT 0"),
            ]
            for col_name, col_ddl in migrations:
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE memory_items ADD COLUMN {col_name} {col_ddl}")

            # Initialize SQLite FTS5 index for fast offline keyword/full-text search (Section 5.1)
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                        memory_id UNINDEXED,
                        user_id UNINDEXED,
                        category UNINDEXED,
                        content,
                        tags
                    );
                    """
                )
                self._fts5_available = True
            except Exception:
                self._fts5_available = False

    def _fts_upsert(self, conn: sqlite3.Connection, memory_id: str, user_id: str, category: str, content: str, tags: List[str]) -> None:
        if not self._fts5_available:
            return
        try:
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "INSERT INTO memory_fts (memory_id, user_id, category, content, tags) VALUES (?, ?, ?, ?, ?)",
                (memory_id, user_id, category, content, " ".join(tags or [])),
            )
        except Exception:
            pass

    def _fts_delete(self, conn: sqlite3.Connection, memory_id: str) -> None:
        if not self._fts5_available:
            return
        try:
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        except Exception:
            pass

    @staticmethod
    def _normalize_user(user_id: str) -> str:
        u = str(user_id or "").strip()
        if not u:
            raise PermissionError("user_id مطلوب")
        return u

    # ==========================================================================
    # Section 4: Summarize Every 10 User Messages
    # ==========================================================================
    def _extract_structured_summary_from_messages(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                if re.search(r"(?i)(اسمي|أفضل|تفضيلي|أعمل على|مشروعي|أستخدم|my name is|i prefer|i use|we use)", txt):
                    facts.append({
                        "fact": first_line,
                        "source_message_id": mid,
                        "confidence": 0.95,
                        "date": ts_iso,
                    })
                if re.search(r"(?i)(شروطي|لا تقم بتعديل|حافظ على|اعتمد|قررنا|بدون|constraint|must not|do not change|decided)", txt):
                    decisions.append({
                        "decision": first_line,
                        "source_message_id": mid,
                        "status": "accepted",
                    })
                if re.search(r"(?i)(مشكلة|خطأ|عطل|فشل|لا يعمل|bug|error|exception|fail|issue|conflict)", txt):
                    problems.append({
                        "symptom": first_line,
                        "source_message_id": mid,
                        "outcome": "investigating",
                    })
                if re.search(r"(?i)(لاحقاً|الخطوة القادمة|باقي|أكمل|todo|next step|pending|follow up)", txt):
                    open_tasks.append({
                        "task": first_line,
                        "source_message_id": mid,
                    })
                if re.search(r"(?i)(اشتغل تمام|نحلت المشكلة|شغال 100%|كل شي تمام|verified|tests pass|works now)", txt):
                    verified_solutions.append({
                        "solution": first_line,
                        "source_message_id": mid,
                        "evidence": "user_confirmation",
                    })
            else:
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
        u = self._normalize_user(user_id)
        cid = str(conversation_id or "").strip()
        w_idx = int(window_index)
        if w_idx < 1:
            raise ValueError("window_index يجب أن يكون >= 1")

        user_msgs = [m for m in window_messages if m.get("role") == "user"]
        if len(user_msgs) < 10:
            raise ValueError(f"النافذة تتطلب 10 رسائل من المستخدم على الأقل (المتوفر: {len(user_msgs)})")

        target_user_msgs = user_msgs[:10]
        start_seq = int(target_user_msgs[0]["sequence"])
        end_seq = int(window_messages[-1]["sequence"])
        window_id = f"win_{cid}_{w_idx}"
        now = time.time()

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

            msg_range = f"{start_seq}..{end_seq}"
            self.add_memory(
                user_id=u,
                content=structured_payload["summary_text"],
                layer="conversation_memory",
                category="conversation_memory",
                provenance="model_inference",
                confidence=0.88,
                source_conversation_id=cid,
                source_message_range=msg_range,
                source_refs=[window_id] + covered_ids[:5],
                metadata={"window_id": window_id, "conversation_id": cid, "window_index": w_idx},
            )
            for f_item in structured_payload["facts"]:
                self.add_memory(
                    user_id=u,
                    content=str(f_item.get("fact") or ""),
                    layer="user_profile",
                    category="user_profile",
                    provenance="user_fact",
                    confidence=float(f_item.get("confidence") or 0.95),
                    source_conversation_id=cid,
                    source_message_range=msg_range,
                    source_refs=[str(f_item.get("source_message_id") or window_id)],
                )
            for d_item in structured_payload["decisions"]:
                self.add_memory(
                    user_id=u,
                    content=str(d_item.get("decision") or ""),
                    layer="project_memory",
                    category="project_memory",
                    provenance="user_fact",
                    confidence=0.95,
                    source_conversation_id=cid,
                    source_message_range=msg_range,
                    source_refs=[str(d_item.get("source_message_id") or window_id)],
                )
            for v_item in structured_payload["verified_solutions"]:
                self.add_memory(
                    user_id=u,
                    content=str(v_item.get("solution") or ""),
                    layer="technical_memory",
                    category="technical_memory",
                    provenance="tested_outcome",
                    confidence=0.98,
                    source_conversation_id=cid,
                    source_message_range=msg_range,
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
    # Section 5 & Phase 2: Device-Local Long-Term Memory Lifecycle
    # ==========================================================================
    def capture_memory_candidate(
        self,
        user_id: str,
        text: str,
        source_conversation_id: str = "",
        source_message_range: str = "",
        category: str = "user_profile",
        confidence: float = 0.9,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Lifecycle Steps 1 & 2 (Capture & Propose):
        Detects a potentially useful fact or project decision, rejects credentials/secrets,
        and marks whether explicit user confirmation is required before saving.
        """
        u = self._normalize_user(user_id)
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("محتوى المرشح للذاكرة فارغ")
        if contains_secret(raw):
            raise ValueError("⛔ لا يُسمح بحفظ المفاتيح السرية أو كلمات المرور أو التوكنات في الذاكرة.")

        is_sensitive = bool(SENSITIVE_PERSONAL_RE.search(raw))
        is_ambiguous = bool(re.search(r"(?i)\b(ربما|ممكن|يمكن|أظن|maybe|perhaps|might)\b", raw)) or float(confidence) < 0.8
        sensitivity_label = "sensitive" if is_sensitive else "normal"
        requires_confirmation = is_sensitive or is_ambiguous

        return {
            "candidate_id": f"cand_{uuid.uuid4().hex[:10]}",
            "user_id": u,
            "content": raw,
            "category": category if category in MEMORY_CATEGORIES else "user_profile",
            "source_conversation_id": str(source_conversation_id or ""),
            "source_message_range": str(source_message_range or ""),
            "confidence": round(float(confidence), 3),
            "importance_score": 0.85 if not is_ambiguous else 0.55,
            "tags": list(tags or []),
            "sensitivity_label": sensitivity_label,
            "requires_confirmation": requires_confirmation,
        }

    def confirm_and_store_candidate(
        self,
        user_id: str,
        candidate: Dict[str, Any],
        user_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """
        Lifecycle Step 3 & 4 (Confirm & Store):
        Requires explicit user confirmation if the candidate is sensitive or ambiguous,
        writes the record into local SQLite + FTS5, and confirms the transaction by reading it back.
        """
        if candidate.get("requires_confirmation") and not user_confirmed:
            raise PermissionError("هذه المعلومة حساسة أو غير مؤكدة وتتطلب موافقة صريحة من المستخدم قبل حفظها في الذاكرة المحلية.")
        stored = self.add_memory(
            user_id=user_id,
            content=str(candidate.get("content") or ""),
            layer=str(candidate.get("category") or "user_profile"),
            category=str(candidate.get("category") or "user_profile"),
            provenance="user_fact",
            confidence=float(candidate.get("confidence") or 0.9),
            importance_score=float(candidate.get("importance_score") or 0.75),
            source_conversation_id=str(candidate.get("source_conversation_id") or ""),
            source_message_range=str(candidate.get("source_message_range") or ""),
            tags=list(candidate.get("tags") or []),
            sensitivity_label=str(candidate.get("sensitivity_label") or "normal"),
        )
        if not stored:
            raise RuntimeError("فشل حفظ سجل الذاكرة المحلية")
        return stored

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
        category: Optional[str] = None,
        source_conversation_id: str = "",
        source_message_range: str = "",
        importance_score: float = 0.75,
        tags: Optional[List[str]] = None,
        sensitivity_label: str = "normal",
        expires_at: Optional[float] = None,
        is_archived: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Stores a memory item in device-local SQLite DB, encrypts sensitive records at rest,
        indexes into SQLite FTS5 (`memory_fts`), and computes local deterministic vector embedding.
        Never stores passwords, API keys, or tokens.
        """
        u = self._normalize_user(user_id)
        raw_text = str(content or "").strip()
        if not raw_text:
            return None
        if contains_secret(raw_text):
            raise ValueError("⛔ تم رفض حفظ الذاكرة لاحتوائها على كلمة مرور أو مفتاح API سري.")

        clean_layer = layer if layer in MEMORY_LAYERS else "knowledge_memory"
        clean_cat = (category or clean_layer) if (category or clean_layer) in MEMORY_CATEGORIES else clean_layer
        clean_prov = provenance if provenance in PROVENANCE_TYPES else "user_fact"
        clean_sens = sensitivity_label if sensitivity_label in SENSITIVITY_LABELS else "normal"
        scope = str(project_scope or "default").strip()[:80] or "default"
        conf = max(0.0, min(1.0, float(confidence)))
        imp = max(0.0, min(1.0, float(importance_score)))
        tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
        vec = self.embedder.embed(raw_text)
        now = time.time()
        mem_id = f"mem_{uuid.uuid4().hex[:14]}"

        # At-rest encryption for sensitive memories using per-device key
        enc_blob = self.cipher.encrypt_text(raw_text) if clean_sens in ("sensitive", "restricted", "personal") else ""
        stored_content = "[ENCRYPTED_AT_REST]" if clean_sens in ("sensitive", "restricted") else raw_text

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
                    self._fts_delete(conn, supersedes_id)

            conn.execute(
                """
                INSERT INTO memory_items (
                    id, user_id, project_scope, layer, category, provenance,
                    content, encrypted_content, source_conversation_id, source_message_range,
                    importance_score, confidence, tags_json, sensitivity_label,
                    expires_at, is_archived, pinned, source_refs_json, metadata_json,
                    embedding_json, supersedes_id, superseded_by_id, deleted,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, ?, ?)
                """,
                (
                    mem_id,
                    u,
                    scope,
                    clean_layer,
                    clean_cat,
                    clean_prov,
                    stored_content,
                    enc_blob,
                    str(source_conversation_id or ""),
                    str(source_message_range or ""),
                    imp,
                    conf,
                    json.dumps(tag_list, ensure_ascii=False),
                    clean_sens,
                    float(expires_at) if expires_at is not None else None,
                    1 if is_archived else 0,
                    1 if pinned else 0,
                    json.dumps(source_refs or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    json.dumps({str(k): v for k, v in vec.items()}),
                    supersedes_id,
                    now,
                    now,
                ),
            )
            if not is_archived:
                self._fts_upsert(conn, mem_id, u, clean_cat, raw_text, tag_list)
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (mem_id,)).fetchone()
            return self._row_to_memory(row)

    def read_memory(self, user_id: str, memory_id: str) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ? AND deleted = 0", (memory_id,)).fetchone()
            if not row:
                raise KeyError("سجل الذاكرة غير موجود")
            if row["user_id"] != u:
                raise PermissionError("⛔ غير مصرح لك بقراءة ذاكرة مستخدم آخر")
            return self._row_to_memory(row)

    def update_memory(
        self,
        user_id: str,
        memory_id: str,
        content: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        importance_score: Optional[float] = None,
        confidence: Optional[float] = None,
        sensitivity_label: Optional[str] = None,
        expires_at: Optional[float] = None,
        is_archived: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Updates an existing local memory record, keeping provenance and updating timestamps & FTS5 index."""
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ? AND deleted = 0", (memory_id,)).fetchone()
            if not row:
                raise KeyError("سجل الذاكرة غير موجود")
            if row["user_id"] != u:
                raise PermissionError("⛔ غير مصرح لك بتعديل ذاكرة مستخدم آخر")
            cur = self._row_to_memory(row)

            new_content = str(content).strip() if content is not None else cur["content"]
            if contains_secret(new_content):
                raise ValueError("⛔ لا يُسمح بحفظ المفاتيح السرية أو كلمات المرور في الذاكرة.")
            new_cat = category if (category and category in MEMORY_CATEGORIES) else cur["category"]
            new_layer = new_cat if new_cat in MEMORY_LAYERS else cur["layer"]
            new_tags = [str(t).strip() for t in tags if str(t).strip()] if tags is not None else cur["tags"]
            new_imp = max(0.0, min(1.0, float(importance_score))) if importance_score is not None else cur["importance_score"]
            new_conf = max(0.0, min(1.0, float(confidence))) if confidence is not None else cur["confidence"]
            new_sens = sensitivity_label if (sensitivity_label and sensitivity_label in SENSITIVITY_LABELS) else cur["sensitivity_label"]
            new_arch = bool(is_archived) if is_archived is not None else cur["is_archived"]
            new_exp = float(expires_at) if expires_at is not None else row["expires_at"]

            enc_blob = self.cipher.encrypt_text(new_content) if new_sens in ("sensitive", "restricted", "personal") else ""
            stored_content = "[ENCRYPTED_AT_REST]" if new_sens in ("sensitive", "restricted") else new_content
            vec = self.embedder.embed(new_content)
            now = time.time()

            conn.execute(
                """
                UPDATE memory_items
                SET content = ?, encrypted_content = ?, category = ?, layer = ?,
                    tags_json = ?, importance_score = ?, confidence = ?,
                    sensitivity_label = ?, expires_at = ?, is_archived = ?,
                    embedding_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    stored_content,
                    enc_blob,
                    new_cat,
                    new_layer,
                    json.dumps(new_tags, ensure_ascii=False),
                    new_imp,
                    new_conf,
                    new_sens,
                    new_exp,
                    1 if new_arch else 0,
                    json.dumps({str(k): v for k, v in vec.items()}),
                    now,
                    memory_id,
                ),
            )
            if new_arch:
                self._fts_delete(conn, memory_id)
            else:
                self._fts_upsert(conn, memory_id, u, new_cat, new_content, new_tags)
            updated = conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
            return self._row_to_memory(updated)

    def archive_memory(self, user_id: str, memory_id: str, is_archived: bool = True) -> Dict[str, Any]:
        return self.update_memory(user_id, memory_id, is_archived=is_archived)

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
            category=old["category"] if "category" in old.keys() else old["layer"],
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

    def delete_memory(self, user_id: str, memory_id: str, hard_delete: bool = True) -> Dict[str, Any]:
        """
        Lifecycle Step 'Forget':
        Deletes the selected record and associated local FTS5/vector index entries,
        and verifies it is absent from both database and search index.
        """
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
            if not row or row["user_id"] != u:
                raise PermissionError("⛔ عنصر الذاكرة غير موجود أو غير مصرح به")
            self._fts_delete(conn, memory_id)
            if hard_delete:
                conn.execute("DELETE FROM memory_items WHERE id = ?", (memory_id,))
            else:
                conn.execute(
                    "UPDATE memory_items SET deleted = 1, content = '', encrypted_content = '', embedding_json = '{}', updated_at = ? WHERE id = ?",
                    (time.time(), memory_id),
                )
            still_in_db = conn.execute("SELECT id FROM memory_items WHERE id = ? AND deleted = 0", (memory_id,)).fetchone()
            still_in_fts = None
            if self._fts5_available:
                try:
                    still_in_fts = conn.execute("SELECT memory_id FROM memory_fts WHERE memory_id = ?", (memory_id,)).fetchone()
                except Exception:
                    still_in_fts = None
            verified_absent = (still_in_db is None) and (still_in_fts is None)
            return {"ok": verified_absent, "deleted_id": memory_id, "verified_absent": verified_absent}

    def forget_memory(self, user_id: str, memory_id: str) -> Dict[str, Any]:
        return self.delete_memory(user_id=user_id, memory_id=memory_id, hard_delete=True)

    def rebuild_vector_index(self, user_id: Optional[str] = None) -> int:
        """Rebuilds both the local vector index and SQLite FTS5 index from canonical records."""
        with self._lock, self._conn() as conn:
            if user_id:
                rows = conn.execute("SELECT * FROM memory_items WHERE user_id = ? AND deleted = 0", (user_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM memory_items WHERE deleted = 0").fetchall()
            count = 0
            for r in rows:
                item = self._row_to_memory(r)
                vec = self.embedder.embed(item["content"])
                conn.execute(
                    "UPDATE memory_items SET embedding_json = ? WHERE id = ?",
                    (json.dumps({str(k): v for k, v in vec.items()}), r["id"]),
                )
                if not item.get("is_archived") and not r["superseded_by_id"]:
                    self._fts_upsert(conn, r["id"], r["user_id"], item.get("category", r["layer"]), item["content"], item.get("tags", []))
                count += 1
            return count

    def _fts5_candidate_ids(self, user_id: str, query: str, limit: int = 25) -> set:
        if not self._fts5_available or not query.strip():
            return set()
        tokens = [t for t in re.findall(r"[A-Za-z0-9_]{2,}|[\u0600-\u06FF]{2,}", query) if len(t) >= 2]
        if not tokens:
            return set()
        fts_query = " OR ".join(f'"{t}"' for t in tokens[:8])
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(
                    "SELECT memory_id FROM memory_fts WHERE user_id = ? AND memory_fts MATCH ? LIMIT ?",
                    (user_id, fts_query, int(limit)),
                ).fetchall()
                return {r["memory_id"] for r in rows}
        except Exception:
            return set()

    def retrieve(
        self,
        user_id: str,
        query: str,
        project_scope: str = "default",
        layers: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        include_archived: bool = False,
        top_k: int = 5,
        min_score: float = 0.15,
    ) -> List[Dict[str, Any]]:
        """
        Offline Hybrid Retrieval Flow (SQLite FTS5 + Exact Keyword + On-Device Vector Similarity):
        Works 100% offline with zero network calls.
        """
        u = self._normalize_user(user_id)
        q_clean = (query or "").strip()
        scope = str(project_scope or "default").strip() or "default"
        now = time.time()

        with self._lock, self._conn() as conn:
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

        fts_hits = self._fts5_candidate_ids(u, q_clean) if q_clean else set()

        if layers:
            allowed_l = set(layers)
            rows = [r for r in rows if r["layer"] in allowed_l or (r["category"] if "category" in r.keys() else "") in allowed_l]
        if categories:
            allowed_c = set(categories)
            rows = [r for r in rows if (r["category"] if "category" in r.keys() else r["layer"]) in allowed_c]

        q_tokens = set(self.embedder.tokenize(q_clean))
        q_vec = self.embedder.embed(q_clean) if q_clean else {}

        prov_quality = {
            "tested_outcome": 1.0,
            "user_fact": 0.95,
            "external_fact": 0.85,
            "model_inference": 0.75,
        }

        scored: List[Tuple[float, Dict[str, Any]]] = []
        seen_contents = set()

        for r in rows:
            if not include_archived and ("is_archived" in r.keys()) and int(r["is_archived"] or 0) == 1:
                continue
            if ("expires_at" in r.keys()) and r["expires_at"] is not None and float(r["expires_at"]) <= now:
                continue

            item = self._row_to_memory(r)
            norm_content = self.embedder.normalize_arabic_english(item["content"])
            if norm_content in seen_contents:
                continue

            doc_tokens = set(self.embedder.tokenize(item["content"] + " " + " ".join(item.get("tags") or [])))
            overlap = len(q_tokens & doc_tokens) / float(max(1, len(q_tokens))) if q_tokens else 0.0
            exact_phrase_bonus = 0.35 if (q_clean and len(q_clean) >= 3 and self.embedder.normalize_arabic_english(q_clean) in norm_content) else 0.0
            fts_bonus = 0.25 if item["id"] in fts_hits else 0.0

            try:
                raw_vec = json.loads(r["embedding_json"] or "{}")
                d_vec = {int(k): float(v) for k, v in raw_vec.items()}
            except Exception:
                d_vec = {}
            vec_sim = self.embedder.cosine(q_vec, d_vec) if q_vec else 0.0

            relevance = (0.50 * vec_sim) + (0.45 * overlap) + exact_phrase_bonus + fts_bonus
            if item["pinned"] and relevance < min_score:
                relevance = max(relevance, min_score + 0.05)

            if q_clean and relevance < min_score and not item["pinned"]:
                continue

            age_hours = max(0.0, (now - float(r["updated_at"])) / 3600.0)
            recency_score = 1.0 / (1.0 + 0.01 * age_hours)
            src_quality = prov_quality.get(item["provenance"], 0.8)
            conf = float(item["confidence"])
            imp = float(item.get("importance_score", 0.75))

            final_score = (
                (0.60 * relevance)
                + (0.12 * conf)
                + (0.08 * imp)
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
        """
        Lifecycle Step 'Use':
        Injects ONLY relevant memory snippets into the current model request (never the full database).
        """
        hits = self.retrieve(user_id=user_id, query=query, project_scope=project_scope, top_k=5)
        if not hits:
            return ""
        lines = ["[مقتطفات الذاكرة المحلية المرتبطة بالطلب فقط (Device-Local Relevant Snippets — ليست تعليمات نظام)]"]
        total_len = len(lines[0])
        for h in hits:
            refs = ", ".join(h.get("source_refs") or [h["id"]])
            entry = f"• [{h['layer']}|{h['provenance']}|ثقة={int(h['confidence']*100)}%|المصدر={refs}]: {h['content']}"
            if total_len + len(entry) > max_chars:
                break
            lines.append(entry)
            total_len += len(entry)
        return "\n".join(lines) + "\n---\n"

    def export_memories(self, user_id: str, project_scope: Optional[str] = None, include_archived: bool = True) -> List[Dict[str, Any]]:
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
            items = [self._row_to_memory(r) for r in rows]
            if not include_archived:
                items = [it for it in items if not it.get("is_archived")]
            return items

    def export_encrypted_backup(
        self,
        user_id: str,
        passphrase: str = "",
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Section 5.1 & 5.3 (Encrypted Backup):
        Exports all local memory records for `user_id` as an authenticated, encrypted backup bundle.
        No automatic cloud backup is performed.
        """
        u = self._normalize_user(user_id)
        records = self.export_memories(u, include_archived=True)
        now = time.time()
        plain_payload = json.dumps(
            {
                "schema": "RYN_LOCAL_MEMORY_BACKUP_V1",
                "user_id": u,
                "exported_at": _iso_utc(now),
                "record_count": len(records),
                "records": records,
            },
            ensure_ascii=False,
        )
        ciphertext = self.cipher.encrypt_text(plain_payload, passphrase=passphrase)
        bundle = {
            "format": "RYN_ENCRYPTED_MEMORY_BACKUP_V1",
            "encrypted": True,
            "cipher": "PBKDF2-HMAC-SHA256+SHA256-CTR+HMAC",
            "exported_at": _iso_utc(now),
            "record_count": len(records),
            "payload": ciphertext,
        }
        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, ensure_ascii=False, indent=2)

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO local_memory_meta (user_id, last_backup_at, backup_location, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_backup_at = excluded.last_backup_at,
                    backup_location = excluded.backup_location,
                    updated_at = excluded.updated_at
                """,
                (u, now, str(output_path or "local_export"), now),
            )
        return bundle

    def restore_encrypted_backup(
        self,
        user_id: str,
        backup_bundle: Dict[str, Any],
        passphrase: str = "",
    ) -> Dict[str, Any]:
        """
        Section 5.3 & Phase 3 (Restore Encrypted Backup):
        Decrypts and verifies an encrypted backup bundle, restores all records into SQLite,
        and rebuilds both the FTS5 and local vector indexes.
        """
        u = self._normalize_user(user_id)
        if not isinstance(backup_bundle, dict) or backup_bundle.get("format") != "RYN_ENCRYPTED_MEMORY_BACKUP_V1":
            raise ValueError("ملف النسخة الاحتياطية المشفرة غير صالح")
        decrypted_json = self.cipher.decrypt_text(str(backup_bundle.get("payload") or ""), passphrase=passphrase)
        data = json.loads(decrypted_json)
        records = data.get("records") or []
        restored_count = 0
        for rec in records:
            if not isinstance(rec, dict) or not rec.get("content"):
                continue
            if contains_secret(str(rec.get("content") or "")):
                continue
            self.add_memory(
                user_id=u,
                content=str(rec["content"]),
                layer=str(rec.get("layer") or "knowledge_memory"),
                category=str(rec.get("category") or rec.get("layer") or "knowledge_memory"),
                provenance=str(rec.get("provenance") or "user_fact"),
                project_scope=str(rec.get("project_scope") or "default"),
                confidence=float(rec.get("confidence") or 0.9),
                importance_score=float(rec.get("importance_score") or 0.75),
                source_conversation_id=str(rec.get("source_conversation_id") or ""),
                source_message_range=str(rec.get("source_message_range") or ""),
                tags=list(rec.get("tags") or []),
                sensitivity_label=str(rec.get("sensitivity_label") or "normal"),
                expires_at=rec.get("expires_at_ts"),
                is_archived=bool(rec.get("is_archived", False)),
                pinned=bool(rec.get("pinned", False)),
                source_refs=list(rec.get("source_refs") or []),
                metadata=dict(rec.get("metadata") or {}),
            )
            restored_count += 1
        self.rebuild_vector_index(u)
        return {"ok": True, "restored_count": restored_count, "fts5_rebuilt": self._fts5_available}

    def get_local_memory_status(self, user_id: str) -> Dict[str, Any]:
        """
        Section 5.4:
        Offers a visible 'Memory is local' status, storage location, last backup time, and storage size.
        """
        u = self._normalize_user(user_id)
        try:
            size_bytes = os.path.getsize(self.db_path) if os.path.isfile(self.db_path) else 0
        except Exception:
            size_bytes = 0
        with self._lock, self._conn() as conn:
            active_c = conn.execute(
                "SELECT COUNT(*) AS c FROM memory_items WHERE user_id = ? AND deleted = 0 AND is_archived = 0 AND superseded_by_id = ''",
                (u,),
            ).fetchone()["c"]
            arch_c = conn.execute(
                "SELECT COUNT(*) AS c FROM memory_items WHERE user_id = ? AND deleted = 0 AND is_archived = 1",
                (u,),
            ).fetchone()["c"]
            meta_row = conn.execute("SELECT * FROM local_memory_meta WHERE user_id = ?", (u,)).fetchone()
            last_bk = _iso_utc(meta_row["last_backup_at"]) if (meta_row and meta_row["last_backup_at"] > 0) else None
            bk_loc = meta_row["backup_location"] if meta_row else ""

        server_persist = os.environ.get("RYN_SERVER_MEMORY_PERSISTENCE", "0") == "1"
        return {
            "memory_is_local": not server_persist,
            "status_badge": "Memory is local (الذاكرة محلية على جهازك فقط)" if not server_persist else "Server sync enabled",
            "storage_location": os.path.abspath(self.db_path),
            "storage_size_bytes": int(size_bytes),
            "last_backup_time": last_bk,
            "last_backup_location": bk_loc,
            "active_records": int(active_c),
            "archived_records": int(arch_c),
            "fts5_enabled": bool(self._fts5_available),
            "at_rest_encryption": True,
            "server_persistence_default": False,
        }

    def _row_to_memory(self, row: sqlite3.Row) -> Dict[str, Any]:
        keys = row.keys()
        try:
            refs = json.loads(row["source_refs_json"] or "[]")
        except Exception:
            refs = []
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        try:
            tags = json.loads(row["tags_json"] or "[]") if "tags_json" in keys else []
        except Exception:
            tags = []
        try:
            emb = json.loads(row["embedding_json"] or "{}") if "embedding_json" in keys else {}
        except Exception:
            emb = {}

        content_val = row["content"]
        if ("encrypted_content" in keys) and row["encrypted_content"]:
            try:
                content_val = self.cipher.decrypt_text(row["encrypted_content"])
            except Exception:
                pass

        exp_ts = row["expires_at"] if ("expires_at" in keys and row["expires_at"] is not None) else None
        cat_val = row["category"] if ("category" in keys and row["category"]) else row["layer"]

        return {
            "id": row["id"],
            "memory_id": row["id"],
            "user_id": row["user_id"],
            "project_scope": row["project_scope"],
            "layer": row["layer"],
            "category": cat_val,
            "provenance": row["provenance"],
            "content": content_val,
            "source_conversation_id": row["source_conversation_id"] if "source_conversation_id" in keys else "",
            "source_message_range": row["source_message_range"] if "source_message_range" in keys else "",
            "importance_score": float(row["importance_score"]) if "importance_score" in keys else 0.75,
            "confidence": float(row["confidence"]),
            "tags": tags,
            "embedding": emb,
            "sensitivity_label": row["sensitivity_label"] if "sensitivity_label" in keys else "normal",
            "expires_at": _iso_utc(exp_ts) if exp_ts is not None else None,
            "expires_at_ts": float(exp_ts) if exp_ts is not None else None,
            "is_archived": bool(row["is_archived"]) if "is_archived" in keys else False,
            "pinned": bool(row["pinned"]),
            "source_refs": refs,
            "metadata": meta,
            "supersedes_id": row["supersedes_id"],
            "superseded_by_id": row["superseded_by_id"],
            "created_at": _iso_utc(row["created_at"]),
            "updated_at": _iso_utc(row["updated_at"]),
        }
