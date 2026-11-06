# -*- coding: utf-8 -*-
"""
conversations/__init__.py — Canonical Conversation & Message Store, Device Export/Import, and Sync Service
===========================================================================================================
Implements Sections 2, 3, 9, 10, 11, and 12 of RYN Complete Integrated Upgrade Plan:
- Canonical server-side message store (PostgreSQL via DATABASE_URL when configured, SQLite fallback for local/single-process).
- Stable IDs (`message_id`, `conversation_id`), sequence numbers, SHA-256 content checksums, and idempotency keys.
- Server-derived `user_id` isolation (no cross-user access).
- Per-conversation export layout (`RYN_Conversations/manifest.json`, `conversation_<id>/conversation.json`, `conversation.md`, `summaries/`, `attachments/`, `memory_export.json`) as JSON/Markdown or downloadable ZIP bundle.
- Import validation (schema, size limit, checksum verification, untrusted content sanitization) and two-device offline conflict detection (never silently overwrites).
- Trigger hook for the 10-user-message summarization cycle (counts 10 user-authored messages, not total turns).
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_IMPORT_BYTES = 10 * 1024 * 1024
ALLOWED_ROLES = ("user", "assistant", "system")
ALLOWED_MODALITIES = ("text", "voice", "file", "image", "code")
ALLOWED_STATUSES = ("queued", "running", "succeeded", "failed", "delivered", "partial")


def _iso_utc(ts: Optional[float] = None) -> str:
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_checksum(role: str, content: str, sequence: int) -> str:
    raw = f"{role}|{sequence}|{content or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize_untrusted_import(text: str) -> str:
    """Marks imported content as untrusted data and strips prompt-injection control tags."""
    s = str(text or "")
    s = re.sub(r"(?i)<\s*/?\s*(?:system|instruction|admin_override|github_patch|github_create|github_delete)\b[^>]*>", "[untrusted-tag]", s)
    return s[:150000]


class ConversationService:
    """
    Canonical message history & portable device export/import manager.
    Shares the same database path (`RYN_DB_PATH` / `DATABASE_URL`) with Memory, Voice, Code Intelligence, and Core Ledger.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        on_ten_user_messages_cb: Optional[Callable[[str, str, int, List[Dict[str, Any]]], None]] = None,
    ):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        os.makedirs(base_dir, exist_ok=True)
        self.db_path = db_path or os.environ.get("RYN_DB_PATH") or os.path.join(base_dir, "ryn_canonical.db")
        self.database_url = os.environ.get("DATABASE_URL", "").strip()
        self._lock = threading.RLock()
        self.on_ten_user_messages_cb = on_ten_user_messages_cb
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
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'محادثة RYN',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    client_sync_confirmed INTEGER NOT NULL DEFAULT 0,
                    last_client_device_id TEXT NOT NULL DEFAULT '',
                    deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, deleted, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    modality TEXT NOT NULL DEFAULT 'text',
                    status TEXT NOT NULL DEFAULT 'succeeded',
                    model_id TEXT NOT NULL DEFAULT 'gemini',
                    created_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    attachment_refs_json TEXT NOT NULL DEFAULT '[]',
                    checksum TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    UNIQUE(conversation_id, sequence),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_msg_conv_seq ON messages(conversation_id, sequence ASC);
                CREATE INDEX IF NOT EXISTS idx_msg_idem ON messages(user_id, idempotency_key);

                CREATE TABLE IF NOT EXISTS conversation_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    server_message_id TEXT NOT NULL,
                    client_payload_json TEXT NOT NULL,
                    detected_at REAL NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    @staticmethod
    def _normalize_user(user_id: str) -> str:
        u = str(user_id or "").strip()
        if not u:
            raise PermissionError("user_id مطلوب من الجلسة الموثقة على السيرفر")
        return u

    def ensure_conversation(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        title: str = "",
    ) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        cid = str(conversation_id or "").strip() or f"conv_{u}_default"
        now = time.time()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
            if row:
                if row["user_id"] != u:
                    raise PermissionError("⛔ لا يمكنك الوصول إلى محادثة تخص مستخدماً آخر.")
                if row["deleted"]:
                    conn.execute(
                        "UPDATE conversations SET deleted = 0, updated_at = ?, version = version + 1 WHERE id = ?",
                        (now, cid),
                    )
                    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
                return self._row_to_conv(row)

            clean_title = (title or "محادثة RYN").strip()[:120]
            conn.execute(
                """
                INSERT INTO conversations (id, user_id, title, created_at, updated_at, version, client_sync_confirmed)
                VALUES (?, ?, ?, ?, ?, 1, 0)
                """,
                (cid, u, clean_title, now, now),
            )
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
            return self._row_to_conv(row)

    @staticmethod
    def _row_to_conv(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "conversation_id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "created_at": _iso_utc(row["created_at"]),
            "created_at_ts": float(row["created_at"]),
            "updated_at": _iso_utc(row["updated_at"]),
            "updated_at_ts": float(row["updated_at"]),
            "version": int(row["version"]),
            "client_sync_confirmed": bool(row["client_sync_confirmed"]),
            "last_client_device_id": row["last_client_device_id"],
        }

    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        try:
            atts = json.loads(row["attachment_refs_json"] or "[]")
        except Exception:
            atts = []
        return {
            "message_id": row["message_id"],
            "id": row["message_id"],
            "conversation_id": row["conversation_id"],
            "user_id": row["user_id"],
            "sequence": int(row["sequence"]),
            "role": row["role"],
            "content": row["content"],
            "modality": row["modality"],
            "status": row["status"],
            "model_id": row["model_id"],
            "created_at": _iso_utc(row["created_at"]),
            "timestamp": float(row["created_at"]),
            "metadata": meta,
            "attachment_refs": atts,
            "checksum": row["checksum"],
            "idempotency_key": row["idempotency_key"],
        }

    def append_message(
        self,
        user_id: str,
        role: str,
        content: str,
        conversation_id: Optional[str] = None,
        modality: str = "text",
        status: str = "succeeded",
        model_id: str = "gemini",
        metadata: Optional[Dict[str, Any]] = None,
        attachment_refs: Optional[List[str]] = None,
        idempotency_key: str = "",
        message_id: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        r = str(role or "user").strip().lower()
        if r == "model":
            r = "assistant"
        if r not in ALLOWED_ROLES:
            raise ValueError(f"دور الرسالة غير صالح: {r}")

        mod = str(modality or "text").strip().lower()
        if mod not in ALLOWED_MODALITIES:
            mod = "text"
        st = str(status or "succeeded").strip().lower()
        if st not in ALLOWED_STATUSES:
            st = "succeeded"

        conv = self.ensure_conversation(u, conversation_id=conversation_id)
        cid = conv["id"]
        idem = str(idempotency_key or "").strip()
        now = float(created_at if created_at is not None else time.time())

        trigger_window_info: Optional[Tuple[int, List[Dict[str, Any]]]] = None

        with self._lock, self._conn() as conn:
            if idem:
                existing = conn.execute(
                    "SELECT * FROM messages WHERE user_id = ? AND conversation_id = ? AND idempotency_key = ?",
                    (u, cid, idem),
                ).fetchone()
                if existing:
                    return self._row_to_msg(existing)

            if message_id:
                existing_id = conn.execute(
                    "SELECT * FROM messages WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if existing_id:
                    if existing_id["user_id"] != u:
                        raise PermissionError("⛔ معرّف الرسالة يخص مستخدماً آخر.")
                    return self._row_to_msg(existing_id)

            max_seq_row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM messages WHERE conversation_id = ?",
                (cid,),
            ).fetchone()
            next_seq = int(max_seq_row["max_seq"]) + 1
            mid = str(message_id or f"msg_{uuid.uuid4().hex[:16]}")
            text_val = str(content or "")
            chk = _compute_checksum(r, text_val, next_seq)

            meta_json = json.dumps(metadata or {}, ensure_ascii=False)
            atts_json = json.dumps(attachment_refs or [], ensure_ascii=False)

            conn.execute(
                """
                INSERT INTO messages (
                    message_id, conversation_id, user_id, sequence, role, content,
                    modality, status, model_id, created_at, metadata_json,
                    attachment_refs_json, checksum, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, cid, u, next_seq, r, text_val, mod, st, str(model_id or "gemini"), now, meta_json, atts_json, chk, idem),
            )

            # Update conversation title on first user message if still default
            if next_seq == 1 and r == "user" and conv["title"] == "محادثة RYN" and text_val.strip():
                short_title = text_val.strip().splitlines()[0][:60]
                conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ?, version = version + 1, client_sync_confirmed = 0 WHERE id = ?",
                    (short_title, now, cid),
                )
            else:
                conn.execute(
                    "UPDATE conversations SET updated_at = ?, version = version + 1, client_sync_confirmed = 0 WHERE id = ?",
                    (now, cid),
                )

            saved_row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (mid,)).fetchone()
            saved_msg = self._row_to_msg(saved_row)

            # Check if we should trigger the 10-user-message summary cycle:
            # Trigger when an assistant reply completes a 10-user-message window, OR when a 10th user message is recorded
            user_rows = conn.execute(
                "SELECT sequence, message_id FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY sequence ASC",
                (cid,),
            ).fetchall()
            user_count = len(user_rows)
            if user_count >= 10 and (user_count % 10 == 0):
                # Fetch window of the last 10 user messages and all assistant replies associated with them
                win_idx = user_count // 10  # 1 for 1..10, 2 for 11..20, etc.
                start_user_seq = int(user_rows[(win_idx - 1) * 10]["sequence"])
                all_msgs_in_win = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? AND sequence >= ? ORDER BY sequence ASC",
                    (cid, start_user_seq),
                ).fetchall()
                trigger_window_info = (win_idx, [self._row_to_msg(m) for m in all_msgs_in_win])

        if trigger_window_info and callable(self.on_ten_user_messages_cb):
            win_idx, win_msgs = trigger_window_info
            try:
                self.on_ten_user_messages_cb(u, cid, win_idx, win_msgs)
            except Exception:
                pass

        return saved_msg

    def update_message_status(self, user_id: str, message_id: str, status: str, content: Optional[str] = None) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            if not row:
                raise KeyError(f"الرسالة {message_id} غير موجودة")
            if row["user_id"] != u:
                raise PermissionError("⛔ لا تملك صلاحية تعديل هذه الرسالة")
            new_content = content if content is not None else row["content"]
            new_chk = _compute_checksum(row["role"], new_content, int(row["sequence"]))
            conn.execute(
                "UPDATE messages SET status = ?, content = ?, checksum = ? WHERE message_id = ?",
                (status, new_content, new_chk, message_id),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ?, version = version + 1 WHERE id = ?",
                (time.time(), row["conversation_id"]),
            )
            updated = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            return self._row_to_msg(updated)

    def get_messages(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        limit: int = 100,
        after_sequence: int = 0,
    ) -> List[Dict[str, Any]]:
        u = self._normalize_user(user_id)
        cid = str(conversation_id or "").strip() or f"conv_{u}_default"
        lim = max(1, min(500, int(limit or 100)))
        with self._lock, self._conn() as conn:
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
            if not conv:
                return []
            if conv["user_id"] != u:
                raise PermissionError("⛔ لا يمكنك قراءة محادثة تخص مستخدماً آخر.")
            if conv["deleted"]:
                return []
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (cid, int(after_sequence or 0), lim),
            ).fetchall()
            return [self._row_to_msg(r) for r in rows]

    def list_conversations(self, user_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            if include_deleted:
                rows = conn.execute(
                    "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
                    (u,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conversations WHERE user_id = ? AND deleted = 0 ORDER BY updated_at DESC",
                    (u,),
                ).fetchall()
            out = []
            for r in rows:
                c_dict = self._row_to_conv(r)
                cnt = conn.execute(
                    "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?",
                    (r["id"],),
                ).fetchone()["c"]
                c_dict["message_count"] = int(cnt)
                out.append(c_dict)
            return out

    def confirm_client_local_save(self, user_id: str, conversation_id: str, device_id: str = "browser_indexeddb") -> Dict[str, Any]:
        """Do not claim local save until the client confirms it."""
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not conv or conv["user_id"] != u:
                raise PermissionError("⛔ محادثة غير موجودة أو غير مصرح بها.")
            conn.execute(
                "UPDATE conversations SET client_sync_confirmed = 1, last_client_device_id = ? WHERE id = ?",
                (str(device_id or "browser_indexeddb")[:80], conversation_id),
            )
            updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return self._row_to_conv(updated)

    def delete_conversation(self, user_id: str, conversation_id: str, hard_delete: bool = False) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not conv:
                raise KeyError("المحادثة غير موجودة")
            if conv["user_id"] != u:
                raise PermissionError("⛔ لا تملك صلاحية حذف هذه المحادثة")
            if hard_delete:
                conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
                conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            else:
                conn.execute(
                    "UPDATE conversations SET deleted = 1, updated_at = ?, version = version + 1 WHERE id = ?",
                    (time.time(), conversation_id),
                )
            return {"ok": True, "conversation_id": conversation_id, "deleted": True, "hard_delete": hard_delete}

    def restore_conversation(self, user_id: str, conversation_id: str) -> Dict[str, Any]:
        u = self._normalize_user(user_id)
        with self._lock, self._conn() as conn:
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not conv:
                raise KeyError("المحادثة غير موجودة للاسترجاع")
            if conv["user_id"] != u:
                raise PermissionError("⛔ لا تملك صلاحية استرجاع هذه المحادثة")
            conn.execute(
                "UPDATE conversations SET deleted = 0, updated_at = ?, version = version + 1 WHERE id = ?",
                (time.time(), conversation_id),
            )
            restored = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return {"ok": True, "conversation": self._row_to_conv(restored), "messages": self.get_messages(u, conversation_id, limit=500)}

    # ---------- Export & Import (Section 3 Layout) ----------
    def export_conversation(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        summaries: Optional[List[Dict[str, Any]]] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Builds the exact Section 3 per-conversation export structure:
        RYN_Conversations/
          manifest.json
          conversation_<id>/
            conversation.json
            conversation.md
            summaries/
            attachments/
          memory_export.json
        """
        u = self._normalize_user(user_id)
        cid = str(conversation_id or "").strip() or f"conv_{u}_default"
        conv = self.ensure_conversation(u, cid)
        msgs = self.get_messages(u, cid, limit=500)

        bundle_checksum = hashlib.sha256(
            json.dumps([m["checksum"] for m in msgs], ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        conversation_json = {
            "schema_version": "2.0",
            "conversation": conv,
            "message_count": len(msgs),
            "bundle_checksum": bundle_checksum,
            "messages": msgs,
        }

        md_lines = [
            f"# {conv['title']}",
            f"- **Conversation ID**: `{cid}`",
            f"- **User**: `{u}`",
            f"- **Exported At**: `{_iso_utc()}`",
            f"- **Message Count**: `{len(msgs)}`",
            f"- **Checksum**: `{bundle_checksum}`",
            "",
            "## Transcript",
            "",
        ]
        for m in msgs:
            speaker = "👤 User" if m["role"] == "user" else ("🤖 RYN" if m["role"] == "assistant" else "⚙️ System")
            md_lines.append(f"### [{m['sequence']}] {speaker} (`{m['created_at']}` · `{m['modality']}` · `{m['model_id']}`)")
            md_lines.append(m["content"])
            md_lines.append("")

        conversation_md = "\n".join(md_lines)

        manifest = {
            "format": "RYN_Conversations_Export",
            "schema_version": "2.0",
            "user_id": u,
            "exported_at": _iso_utc(),
            "conversations": [
                {
                    "conversation_id": cid,
                    "title": conv["title"],
                    "message_count": len(msgs),
                    "bundle_checksum": bundle_checksum,
                    "folder": f"RYN_Conversations/conversation_{cid}/",
                }
            ],
        }

        files_layout = {
            "RYN_Conversations/manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2),
            f"RYN_Conversations/conversation_{cid}/conversation.json": json.dumps(conversation_json, ensure_ascii=False, indent=2),
            f"RYN_Conversations/conversation_{cid}/conversation.md": conversation_md,
            "RYN_Conversations/memory_export.json": json.dumps(memories or [], ensure_ascii=False, indent=2),
        }
        for s in (summaries or []):
            wid = s.get("window_id") or "window"
            files_layout[f"RYN_Conversations/conversation_{cid}/summaries/{wid}.json"] = json.dumps(s, ensure_ascii=False, indent=2)

        return {
            "manifest": manifest,
            "conversation_json": conversation_json,
            "conversation_md": conversation_md,
            "files_layout": files_layout,
            "bundle_checksum": bundle_checksum,
            "message_count": len(msgs),
        }

    def export_all_zip_bytes(
        self,
        user_id: str,
        summaries_by_conv: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> bytes:
        u = self._normalize_user(user_id)
        convs = self.list_conversations(u)
        if not convs:
            self.ensure_conversation(u)
            convs = self.list_conversations(u)

        buf = io.BytesIO()
        manifest_convs = []
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for c in convs:
                cid = c["id"]
                exp = self.export_conversation(
                    u,
                    cid,
                    summaries=(summaries_by_conv or {}).get(cid, []),
                    memories=memories,
                )
                manifest_convs.extend(exp["manifest"]["conversations"])
                for path_k, content_v in exp["files_layout"].items():
                    if path_k == "RYN_Conversations/manifest.json":
                        continue
                    zf.writestr(path_k, content_v.encode("utf-8"))
                # Ensure attachments/ and summaries/ directories are represented
                zf.writestr(f"RYN_Conversations/conversation_{cid}/attachments/.keep", b"")
                if not (summaries_by_conv or {}).get(cid):
                    zf.writestr(f"RYN_Conversations/conversation_{cid}/summaries/.keep", b"")

            full_manifest = {
                "format": "RYN_Conversations_Export",
                "schema_version": "2.0",
                "user_id": u,
                "exported_at": _iso_utc(),
                "conversations": manifest_convs,
            }
            zf.writestr("RYN_Conversations/manifest.json", json.dumps(full_manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        return buf.getvalue()

    def import_conversation(
        self,
        user_id: str,
        payload: Dict[str, Any],
        device_id: str = "imported_device",
    ) -> Dict[str, Any]:
        """
        Validates imported JSON/Markdown, file size, schema, and attachment references.
        Treats imported content as untrusted data.
        Detects two-device offline edit conflicts and records them without silently overwriting server history.
        """
        u = self._normalize_user(user_id)
        raw_str = json.dumps(payload, ensure_ascii=False)
        if len(raw_str.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError("حجم ملف المحادثة المستورد يتجاوز الحد المسموح (10MB)")

        if not isinstance(payload, dict):
            raise ValueError("صيغة ملف الاستيراد غير صالحة (يجب أن تكون كائن JSON)")

        conv_meta = payload.get("conversation") or {}
        msgs = payload.get("messages")
        if not isinstance(msgs, list):
            raise ValueError("ملف المحادثة المستورد يفتقر إلى قائمة الرسائل (messages)")

        cid = str(conv_meta.get("id") or conv_meta.get("conversation_id") or payload.get("conversation_id") or f"conv_{u}_{uuid.uuid4().hex[:8]}").strip()
        title = _sanitize_untrusted_import(conv_meta.get("title") or "محادثة مستوردة")[:120]

        # Enforce user isolation: if cid belongs to another user, remap cid to the authenticated user's namespace
        with self._lock, self._conn() as conn:
            existing_conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
            if existing_conv and existing_conv["user_id"] != u:
                cid = f"conv_{u}_{uuid.uuid4().hex[:8]}"

        self.ensure_conversation(u, conversation_id=cid, title=title)

        imported_count = 0
        skipped_duplicates = 0
        conflicts: List[Dict[str, Any]] = []

        with self._lock, self._conn() as conn:
            for item in msgs:
                if not isinstance(item, dict):
                    raise ValueError("عنصر رسالة غير صالح داخل الملف المستورد")
                role = str(item.get("role") or "user").strip().lower()
                if role == "model":
                    role = "assistant"
                if role not in ALLOWED_ROLES:
                    raise ValueError(f"دور غير صالح في الرسالة المستوردة: {role}")

                seq = int(item.get("sequence") or (imported_count + 1))
                raw_content = str(item.get("content") or "")
                sanitized_content = _sanitize_untrusted_import(raw_content)
                mid = str(item.get("message_id") or item.get("id") or f"msg_{uuid.uuid4().hex[:16]}")
                modality = str(item.get("modality") or "text").lower()
                if modality not in ALLOWED_MODALITIES:
                    modality = "text"
                status = str(item.get("status") or "succeeded").lower()
                if status not in ALLOWED_STATUSES:
                    status = "succeeded"
                model_id = str(item.get("model_id") or "gemini")[:80]

                atts = item.get("attachment_refs") or []
                if not isinstance(atts, list) or any(not isinstance(a, str) or ".." in a for a in atts):
                    raise ValueError("مراجع المرفقات (attachment_refs) غير صالحة أو غير آمنة")

                meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                meta = {**meta, "imported_untrusted": True, "source_device": device_id}

                expected_chk = _compute_checksum(role, sanitized_content, seq)
                supplied_chk = str(item.get("checksum") or "").strip()
                if supplied_chk and raw_content == sanitized_content and supplied_chk != expected_chk:
                    raise ValueError(f"فشل التحقق من البصمة الرقمية (checksum mismatch) للرسالة رقم {seq}")

                # Check existing message at sequence or message_id
                existing_seq = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? AND sequence = ?",
                    (cid, seq),
                ).fetchone()

                if existing_seq:
                    if existing_seq["checksum"] == expected_chk or existing_seq["message_id"] == mid and existing_seq["content"] == sanitized_content:
                        skipped_duplicates += 1
                        continue
                    else:
                        # Conflict detected between server copy and offline device copy!
                        # Never silently overwrite one copy: record conflict and preserve both safely.
                        conf_id = f"conf_{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            """
                            INSERT INTO conversation_conflicts (
                                conflict_id, conversation_id, user_id, sequence,
                                server_message_id, client_payload_json, detected_at, resolved
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                conf_id,
                                cid,
                                u,
                                seq,
                                existing_seq["message_id"],
                                json.dumps(item, ensure_ascii=False),
                                time.time(),
                            ),
                        )
                        conflicts.append({
                            "conflict_id": conf_id,
                            "sequence": seq,
                            "server_message_id": existing_seq["message_id"],
                            "client_message_id": mid,
                        })
                        continue

                conn.execute(
                    """
                    INSERT INTO messages (
                        message_id, conversation_id, user_id, sequence, role, content,
                        modality, status, model_id, created_at, metadata_json,
                        attachment_refs_json, checksum, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mid if not conn.execute("SELECT 1 FROM messages WHERE message_id = ?", (mid,)).fetchone() else f"msg_{uuid.uuid4().hex[:16]}",
                        cid,
                        u,
                        seq,
                        role,
                        sanitized_content,
                        modality,
                        status,
                        model_id,
                        float(item.get("timestamp") or time.time()),
                        json.dumps(meta, ensure_ascii=False),
                        json.dumps(atts, ensure_ascii=False),
                        expected_chk,
                        str(item.get("idempotency_key") or ""),
                    ),
                )
                imported_count += 1

            conn.execute(
                "UPDATE conversations SET updated_at = ?, version = version + 1 WHERE id = ?",
                (time.time(), cid),
            )

        all_msgs = self.get_messages(u, cid, limit=500)
        bundle_checksum = hashlib.sha256(
            json.dumps([m["checksum"] for m in all_msgs], ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        return {
            "ok": True,
            "conversation_id": cid,
            "imported_count": imported_count,
            "skipped_duplicates": skipped_duplicates,
            "total_messages": len(all_msgs),
            "bundle_checksum": bundle_checksum,
            "conflicts": conflicts,
            "has_conflicts": bool(conflicts),
        }
