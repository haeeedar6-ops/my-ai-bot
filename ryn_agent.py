"""
ryn_agent.py — منظومة RYN Agent المركزية (RYN Core Agent Subsystem — Sections 1..18)
======================================================================================
تنفيذ كامل وهندسي لخارطة الطريق (RYN_Agent_Implementation_Roadmap.pdf):
1) المبدأ الأساسي (Core Principle):
   السيرفر على Render هو المخطط وبوابة السياسات والأمان (Planner & Policy Gate)،
   وتطبيقات Windows و Android هي عملاء تنفيذ محدودين ومفحوصين (Limited Execution Clients).
2) قاعدة البيانات الدائمة (Section 12 — 7 جداول SQLite/PostgreSQL):
   users, devices, pairing_codes, tasks, commands, action_events, usage_daily.
   تُخزّن جميع رموز الإقران وتوكنات الأجهزة كـ SHA-256 Hashes فقط (بدون أي توكن خام على القرص أو السجلات).
3) مسارات الـ API القياسية المرقّمة (Section 5 — /api/v1/agent/*):
   - POST /api/v1/agent/pair/start
   - POST /api/v1/agent/pair/complete
   - POST /api/v1/agent/tasks
   - POST /api/v1/agent/step
   - POST /api/v1/agent/result
   - POST /api/v1/agent/voice
   - POST /api/v1/agent/stop
   - GET  /api/v1/agent/status
   - GET  /api/v1/agent/logs
   - POST /api/v1/agent/device/revoke
   - POST /api/v1/agent/approve
   - POST /api/v1/agent/killswitch
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("ryn_agent")

# ==============================================================================
# 1. الثوابت وقواعد التحقق الصارمة (Section 5, 7, 8, 11)
# ==============================================================================
ALLOWED_ACTIONS = (
    "observe",
    "click",
    "double_click",
    "type_text",
    "key_press",
    "scroll",
    "wait",
    "done",
    "ask_user",
)

ALLOWED_PLATFORMS = ("windows", "android", "linux", "macos")

TASK_STATES = (
    "CREATED",
    "PLANNING",
    "WAITING_FOR_DEVICE",
    "ACTION_PROPOSED",
    "WAITING_FOR_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "NEEDS_USER_INPUT",
)

ALLOWED_KEYS = {
    "enter", "return", "tab", "escape", "esc", "backspace", "delete", "space",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "ctrl+a", "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+s", "ctrl+z", "ctrl+f",
    "alt+tab", "f1", "f2", "f5", "f11", "f12", "back",
}

ALLOWED_ACTION_FIELDS = {
    "action",
    "coordinates",
    "text",
    "key",
    "scroll_dy",
    "wait_seconds",
    "reason",
    "risk",
    "requires_confirmation",
    "question",
}

# أنماط محظورة افتراضياً في الإصدار الأول (Section 2 & Section 8: Blocked by default)
BLOCKED_CONTENT_RE = re.compile(
    r"(?i)(\b(?:rm\s+-rf|del\s+/[fsq]|format\s+[a-z](?=:)|powershell|cmd\.exe|regedit|bcdedit|"
    r"netsh\s+advfirewall|shutdown\s+/[sr]|taskkill\s+/f|sudo\s+rm|chmod\s+777|curl\s+.*\|\s*(?:sh|bash))\b|"
    r"\b(?:credit\s*card|cvv|iban|swift\s*code|bank\s*transfer|wire\s*transfer|password\s*manager|"
    r"bitwarden|1password|keepass|metamask|seed\s*phrase|private\s*key)\b|"
    r"تحويل\s*بنكي|بطاقة\s*ائتمان|مدير\s*كلمات\s*المرور|فرمتة\s*القرص|حذف\s*النظام)",
)

HIGH_RISK_RE = re.compile(
    r"(?i)(\b(?:delete|remove|uninstall|install|setup\.exe|\.msi|submit|purchase|checkout|pay|send\s+email)\b|"
    r"حذف\s*ملف|إزالة|تثبيت\s*برنامج|إرسال\s*نموذج|دفع|شراء)",
)

MEDIUM_RISK_RE = re.compile(
    r"(?i)(\b(?:type_text|key_press|mkdir|new\s+folder|save\s+as|rename)\b|"
    r"كتابة|إنشاء\s*مجلد|إعادة\s*تسمية|حفظ)",
)

MAX_TEXT_LEN = 500
MAX_SCROLL_DISTANCE = 2000
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 12 * 1024 * 1024
COMMAND_TTL_SECONDS = 120
PAIRING_TTL_SECONDS = 300
MAX_PAIRING_ATTEMPTS = 5
DEFAULT_USER_DAILY_LIMIT = int(os.environ.get("AGENT_USER_DAILY_LIMIT", "300"))
DEFAULT_DEVICE_DAILY_LIMIT = int(os.environ.get("AGENT_DEVICE_DAILY_LIMIT", "250"))


def _sha256_hex(val: str) -> str:
    return hashlib.sha256((val or "").encode("utf-8")).hexdigest()


def _iso_utc(ts: Optional[float] = None) -> str:
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ==============================================================================
# 2. مدقق مخطط الأوامر ومحرك سياسات الأمان (Action Schema Validator & Policy Engine)
# ==============================================================================
class ActionPolicyError(ValueError):
    """خطأ عند مخالفة الأمر المقترح لمخطط الأمان أو حدود الشاشة أو السياسات."""


class ActionSchemaValidator:
    """
    ينفذ قواعد التحقق الصارمة في القسم 5 و 8:
    - رفض أي اسم عملية غير معروف أو حقول غير معروفة.
    - التحقق من حدود الإحداثيات (x, y) ضمن أبعاد الشاشة الفعلية.
    - تقييد طول النص ومسافة التمرير وأسماء المفاتيح ومدة صلاحية الأمر.
    - تصنيف المخاطر (low / medium / high / blocked) وفرض موافقة المستخدم عند اللزوم.
    """
    @classmethod
    def validate_and_enforce_policy(
        cls,
        raw_proposal: Dict[str, Any],
        screen_width: int = 1920,
        screen_height: int = 1080,
        user_goal: str = "",
        require_approval_all: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(raw_proposal, dict):
            raise ActionPolicyError("مخرجات النموذج ليست كائن JSON صالحاً.")

        unknown_keys = set(raw_proposal.keys()) - ALLOWED_ACTION_FIELDS
        if unknown_keys:
            raise ActionPolicyError(f"حقول غير مسموحة في أمر الوكيل: {sorted(unknown_keys)}")

        action = str(raw_proposal.get("action") or "").strip().lower()
        if action not in ALLOWED_ACTIONS:
            raise ActionPolicyError(f"العملية '{action}' غير مسموحة في الإصدار الأول. المسموح: {ALLOWED_ACTIONS}")

        reason = str(raw_proposal.get("reason") or "").strip()[:300] or f"Execute {action}"
        text_val = str(raw_proposal.get("text") or "")
        key_val = str(raw_proposal.get("key") or "").strip().lower()

        # فحص الأنماط المحظورة أمنياً (Blocked by default)
        combined_txt = f"{reason} {text_val} {key_val} {user_goal}"
        if BLOCKED_CONTENT_RE.search(combined_txt):
            raise ActionPolicyError("⛔ تم حظر العملية تلقائياً بواسطة حارس السياسات (عمليات بنكية/كلمات مرور/أوامر نظام محظورة).")

        w_bound = max(100, min(10000, int(screen_width or 1920)))
        h_bound = max(100, min(10000, int(screen_height or 1080)))

        coords_out: Optional[Dict[str, int]] = None
        if action in ("click", "double_click"):
            coords = raw_proposal.get("coordinates")
            if not isinstance(coords, dict) or "x" not in coords or "y" not in coords:
                raise ActionPolicyError(f"العملية '{action}' تتطلب إحداثيات صريحة {{'x': int, 'y': int}}.")
            try:
                cx = int(round(float(coords["x"])))
                cy = int(round(float(coords["y"])))
            except (ValueError, TypeError):
                raise ActionPolicyError("إحداثيات النقر يجب أن تكون أرقاماً صحيحة.")
            if not (0 <= cx <= w_bound and 0 <= cy <= h_bound):
                raise ActionPolicyError(f"الإحداثيات ({cx}, {cy}) خارج حدود الشاشة ({w_bound}x{h_bound}).")
            coords_out = {"x": cx, "y": cy}

        if action == "type_text":
            if not text_val:
                raise ActionPolicyError("العملية 'type_text' تتطلب حقل 'text' غير فارغ.")
            if len(text_val) > MAX_TEXT_LEN:
                raise ActionPolicyError(f"النص المطلوب كتابته يتجاوز الحد المسموح ({MAX_TEXT_LEN} حرف).")

        if action == "key_press":
            if not key_val or key_val not in ALLOWED_KEYS:
                raise ActionPolicyError(f"المفتاح '{key_val}' غير مدرج في قائمة المفاتيح الآمنة.")

        scroll_dy = 0
        if action == "scroll":
            try:
                scroll_dy = int(raw_proposal.get("scroll_dy", -300))
            except (ValueError, TypeError):
                raise ActionPolicyError("قيمة scroll_dy غير صالحة.")
            if abs(scroll_dy) > MAX_SCROLL_DISTANCE:
                raise ActionPolicyError(f"مسافة التمرير ({scroll_dy}) تتجاوز الحد الأقصى ({MAX_SCROLL_DISTANCE}).")

        wait_sec = 1.0
        if action == "wait":
            try:
                wait_sec = max(0.2, min(15.0, float(raw_proposal.get("wait_seconds", 2.0))))
            except (ValueError, TypeError):
                wait_sec = 2.0

        # تصنيف المخاطر وفرض الموافقة المسبقة (Desktop & Android Safety Policy)
        model_risk = str(raw_proposal.get("risk") or "low").strip().lower()
        if HIGH_RISK_RE.search(f"{reason} {text_val}") or model_risk == "high":
            risk = "high"
        elif action in ("type_text", "key_press") or MEDIUM_RISK_RE.search(reason) or model_risk == "medium":
            risk = "medium"
        else:
            risk = "low"

        requires_conf = bool(
            require_approval_all
            or raw_proposal.get("requires_confirmation")
            or risk == "high"
        )

        validated: Dict[str, Any] = {
            "action": action,
            "coordinates": coords_out,
            "text": text_val if action == "type_text" else "",
            "key": key_val if action == "key_press" else "",
            "scroll_dy": scroll_dy if action == "scroll" else 0,
            "wait_seconds": wait_sec if action == "wait" else 0.0,
            "reason": reason,
            "risk": risk,
            "requires_confirmation": requires_conf,
        }
        if action == "ask_user":
            validated["question"] = str(raw_proposal.get("question") or reason)[:300]
        return validated


# ==============================================================================
# 3. مدير قاعدة البيانات والوكيل المركزي (RYN Core Agent Manager — Section 5..12)
# ==============================================================================
class RynAgentCoreManager:
    """
    يدير دورة حياة الأجهزة المقترنة، المهام، الأوامر المتسلسلة، سجل التدقيق الأمني،
    الحصص اليومية، ومفتاح الإيقاف الطارئ (Kill Switch).
    """
    def __init__(self, db_path: Optional[str] = None):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        self.db_path = db_path or os.path.join(base_dir, "ryn_agent_core.db")
        self._lock = threading.Lock()
        self._rate_buckets: Dict[str, List[float]] = {}
        self._kill_switch_global = False
        self._killed_devices: set = set()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    status TEXT DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    revoked_at REAL DEFAULT NULL
                );
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    attempts INTEGER DEFAULT 0,
                    consumed_at REAL DEFAULT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT DEFAULT '',
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step INTEGER DEFAULT 0,
                    require_approval_all INTEGER DEFAULT 0,
                    memory_json TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    completed_at REAL DEFAULT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    executed_at REAL DEFAULT NULL,
                    UNIQUE(task_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS action_events (
                    id TEXT PRIMARY KEY,
                    command_id TEXT DEFAULT '',
                    task_id TEXT DEFAULT '',
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    result TEXT DEFAULT '',
                    error_code TEXT DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    request_count INTEGER DEFAULT 0,
                    image_bytes INTEGER DEFAULT 0,
                    audio_seconds REAL DEFAULT 0.0,
                    model_tokens INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                );
                """
            )

    # ---------- سجل التدقيق الأمني والحصص ومحدد السرعة ----------
    def _log_event(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        event_type: str,
        result: str = "",
        task_id: str = "",
        command_id: str = "",
        error_code: str = "",
    ) -> None:
        conn.execute(
            "INSERT INTO action_events (id, command_id, task_id, user_id, event_type, result, error_code, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt_" + uuid.uuid4().hex[:12],
                command_id,
                task_id,
                user_id or "main",
                event_type,
                str(result or "")[:400],
                str(error_code or "")[:80],
                time.time(),
            ),
        )

    def check_rate_limit(self, key: str, max_per_minute: int = 45) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._rate_buckets.get(key, []) if now - t < 60.0]
            if len(hits) >= max_per_minute:
                raise RuntimeError("⏳ تم تجاوز الحد المسموح للطلبات في الدقيقة (Rate Limit). انتظر قليلاً.")
            hits.append(now)
            self._rate_buckets[key] = hits

    def _record_usage(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        req_delta: int = 1,
        img_bytes: int = 0,
        audio_sec: float = 0.0,
        tokens: int = 0,
    ) -> None:
        today = time.strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT request_count FROM usage_daily WHERE user_id = ? AND date = ?",
            (user_id, today),
        ).fetchone()
        cur_reqs = int(row["request_count"]) if row else 0
        if cur_reqs + req_delta > DEFAULT_USER_DAILY_LIMIT:
            raise RuntimeError(f"⏳ وصلت للحد اليومي لطلبات الوكيل ({DEFAULT_USER_DAILY_LIMIT} طلب).")
        conn.execute(
            """
            INSERT INTO usage_daily (user_id, date, request_count, image_bytes, audio_seconds, model_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                request_count = request_count + excluded.request_count,
                image_bytes = image_bytes + excluded.image_bytes,
                audio_seconds = audio_seconds + excluded.audio_seconds,
                model_tokens = model_tokens + excluded.model_tokens
            """,
            (user_id, today, req_delta, img_bytes, audio_sec, tokens),
        )

    # ---------- 1. الإقران والمصادقة (Section 6: Pairing & Authentication) ----------
    def start_pairing(self, user_id: str = "main") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        self.check_rate_limit(f"pair_start:{u}", max_per_minute=10)
        # توليد كود عشوائي من 6 أرقام صالح لـ 5 دقائق
        code = f"{secrets.randbelow(900000) + 100000:06d}"
        c_hash = _sha256_hex(code)
        now = time.time()
        exp = now + PAIRING_TTL_SECONDS
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, created_at, status) VALUES (?, ?, 'active')",
                (u, now),
            )
            # تنظيف الأكواد المنتهية
            conn.execute("DELETE FROM pairing_codes WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT OR REPLACE INTO pairing_codes (code_hash, user_id, expires_at, attempts, consumed_at) "
                "VALUES (?, ?, ?, 0, NULL)",
                (c_hash, u, exp),
            )
            self._log_event(conn, u, "pair_start", "تم توليد كود إقران سداسي صالح لـ 5 دقائق")
        return {
            "ok": True,
            "pairing_code": code,
            "expires_in_seconds": PAIRING_TTL_SECONDS,
            "expires_at": _iso_utc(exp),
        }

    def complete_pairing(self, code: str, device_name: str = "My Device", platform: str = "windows") -> Dict[str, Any]:
        clean_code = str(code or "").strip()
        plat = str(platform or "windows").strip().lower()
        if plat not in ALLOWED_PLATFORMS:
            raise ValueError(f"المنصة غير مدعومة ({plat}). المسموح: {ALLOWED_PLATFORMS}")
        if not re.fullmatch(r"\d{6}", clean_code):
            raise ValueError("كود الإقران يجب أن يتكون من 6 أرقام.")

        self.check_rate_limit(f"pair_complete:{plat}", max_per_minute=15)
        c_hash = _sha256_hex(clean_code)
        now = time.time()

        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT code_hash, user_id, expires_at, attempts, consumed_at FROM pairing_codes WHERE code_hash = ?",
                (c_hash,),
            ).fetchone()
            if not row:
                # زيادة عدد المحاولات على الأكواد النشطة لمنع التخمين (Brute-force protection)
                conn.execute("UPDATE pairing_codes SET attempts = attempts + 1 WHERE consumed_at IS NULL")
                raise PermissionError("كود الإقران غير صحيح أو منتهي الصلاحية.")
            if row["consumed_at"] is not None:
                raise PermissionError("تم استخدام كود الإقران هذا مسبقاً (يُستخدم لمرة واحدة فقط).")
            if int(row["attempts"]) >= MAX_PAIRING_ATTEMPTS:
                raise PermissionError("تم تجاوز الحد الأقصى لمحاولات هذا الكود. اطلب كوداً جديداً.")
            if float(row["expires_at"]) < now:
                raise PermissionError("انتهت صلاحية كود الإقران (5 دقائق). اطلب كوداً جديداً من لوحة التحكم.")

            user_id = row["user_id"]
            conn.execute(
                "UPDATE pairing_codes SET consumed_at = ?, attempts = attempts + 1 WHERE code_hash = ?",
                (now, c_hash),
            )

            device_id = "dev_" + uuid.uuid4().hex[:14]
            raw_token = "ryn_dev_" + secrets.token_urlsafe(32)
            tok_hash = _sha256_hex(raw_token)
            clean_name = (device_name or f"{plat.capitalize()} Client").strip()[:80]

            conn.execute(
                "INSERT INTO devices (id, user_id, name, platform, token_hash, created_at, last_seen_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (device_id, user_id, clean_name, plat, tok_hash, now, now),
            )
            self._log_event(conn, user_id, "device_paired", f"Paired {clean_name} ({plat}) -> {device_id}")

        return {
            "ok": True,
            "device_id": device_id,
            "device_token": raw_token,
            "user_id": user_id,
            "platform": plat,
            "name": clean_name,
        }

    def authenticate_device(self, raw_token: str) -> Dict[str, Any]:
        tok = (raw_token or "").strip()
        if tok.lower().startswith("bearer "):
            tok = tok[7:].strip()
        if not tok or not tok.startswith("ryn_dev_"):
            raise PermissionError("توكن الجهاز مفقود أو غير صالح.")
        if self._kill_switch_global:
            raise PermissionError("🛑 مفتاح الإيقاف الطارئ الشامل (Kill Switch) مفعّل حالياً.")

        tok_hash = _sha256_hex(tok)
        now = time.time()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id, user_id, name, platform, created_at, last_seen_at, revoked_at FROM devices WHERE token_hash = ?",
                (tok_hash,),
            ).fetchone()
            if not row:
                raise PermissionError("توكن الجهاز غير معروف.")
            if row["revoked_at"] is not None or row["id"] in self._killed_devices:
                raise PermissionError("⛔ تم إلغاء صلاحية هذا الجهاز (Revoked) من لوحة التحكم.")
            conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
            return dict(row)

    def revoke_device(self, user_id: str, device_id: str) -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        did = str(device_id or "").strip()
        now = time.time()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT id, user_id, name FROM devices WHERE id = ?", (did,)).fetchone()
            if not row or row["user_id"] != u:
                raise ValueError("الجهاز غير موجود أو لا يتبع لحسابك.")
            conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ?", (now, did))
            # إيقاف أي مهام جارية على هذا الجهاز فوراً
            conn.execute(
                "UPDATE tasks SET status = 'CANCELLED', completed_at = ? WHERE device_id = ? AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')",
                (now, did),
            )
            self._killed_devices.add(did)
            self._log_event(conn, u, "device_revoked", f"Revoked device {did} ({row['name']})")
        return {"ok": True, "device_id": did, "status": "revoked"}

    def set_kill_switch(self, user_id: str, active: bool = True) -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        now = time.time()
        with self._lock, self._conn() as conn:
            self._kill_switch_global = bool(active)
            if active:
                conn.execute(
                    "UPDATE tasks SET status = 'CANCELLED', completed_at = ? WHERE user_id = ? AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')",
                    (now, u),
                )
                conn.execute(
                    "UPDATE commands SET approval_status = 'cancelled' WHERE approval_status IN ('pending', 'pending_approval', 'approved')",
                )
            self._log_event(conn, u, "kill_switch", f"Kill switch set to {active}")
        return {"ok": True, "kill_switch": self._kill_switch_global}

    # ---------- 2. إدارة المهام ودورة الحياة (Section 7: Task Lifecycle & Memory) ----------
    def create_task(
        self,
        user_id: str,
        goal: str,
        device_id: str = "",
        require_approval_all: bool = False,
    ) -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        if self._kill_switch_global:
            raise PermissionError("⛔ مفتاح الإيقاف الفوري الشامل (Kill-Switch) مفعّل حالياً.")
        clean_goal = str(goal or "").strip()
        if not clean_goal:
            raise ValueError("اكتب الهدف أو المهمة المطلوب تنفيذها.")
        if BLOCKED_CONTENT_RE.search(clean_goal):
            raise ActionPolicyError("⛔ الهدف المطلوب يحتوي على عمليات محظورة أمنياً.")

        now = time.time()
        task_id = "task_" + uuid.uuid4().hex[:14]
        with self._lock, self._conn() as conn:
            self._record_usage(conn, u, req_delta=1)
            if not device_id:
                dev_row = conn.execute(
                    "SELECT id FROM devices WHERE user_id = ? AND revoked_at IS NULL ORDER BY last_seen_at DESC LIMIT 1",
                    (u,),
                ).fetchone()
                if dev_row:
                    device_id = dev_row["id"]

            init_status = "WAITING_FOR_DEVICE" if device_id else "CREATED"
            mem = {
                "goal": clean_goal,
                "observations": [],
                "actions_attempted": [],
                "action_results": [],
                "errors": [],
                "consecutive_failures": 0,
            }
            conn.execute(
                "INSERT INTO tasks (id, user_id, device_id, goal, status, current_step, require_approval_all, memory_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (task_id, u, device_id or "", clean_goal[:1000], init_status, 1 if require_approval_all else 0, json.dumps(mem, ensure_ascii=False), now),
            )
            self._log_event(conn, u, "task_created", f"Goal: {clean_goal[:120]}", task_id=task_id)

        return {
            "ok": True,
            "task_id": task_id,
            "user_id": u,
            "device_id": device_id or "",
            "goal": clean_goal,
            "status": init_status,
            "current_step": 0,
            "require_approval_all": bool(require_approval_all),
            "created_at": _iso_utc(now),
        }

    def stop_task(self, user_id: str, task_id: str = "") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        now = time.time()
        with self._lock, self._conn() as conn:
            if task_id:
                conn.execute(
                    "UPDATE tasks SET status = 'CANCELLED', completed_at = ? WHERE id = ? AND user_id = ?",
                    (now, task_id, u),
                )
                conn.execute(
                    "UPDATE commands SET approval_status = 'cancelled' WHERE task_id = ? AND approval_status IN ('pending', 'pending_approval', 'approved')",
                    (task_id,),
                )
                self._log_event(conn, u, "task_stopped", f"Cancelled task {task_id}", task_id=task_id)
            else:
                conn.execute(
                    "UPDATE tasks SET status = 'CANCELLED', completed_at = ? WHERE user_id = ? AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')",
                    (now, u),
                )
                self._log_event(conn, u, "all_tasks_stopped", "Cancelled all running tasks")
        return {"ok": True, "task_id": task_id or "all", "status": "CANCELLED"}

    def approve_command(self, user_id: str, command_id: str, approve: bool = True) -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        cid = str(command_id or "").strip()
        with self._lock, self._conn() as conn:
            cmd = conn.execute(
                "SELECT c.id, c.task_id, c.sequence, c.action, c.payload_json, c.approval_status, t.user_id "
                "FROM commands c JOIN tasks t ON c.task_id = t.id WHERE c.id = ?",
                (cid,),
            ).fetchone()
            if not cmd or cmd["user_id"] != u:
                raise ValueError("الأمر غير موجود أو لا يتبع لحسابك.")
            new_st = "approved" if approve else "rejected"
            conn.execute("UPDATE commands SET approval_status = ? WHERE id = ?", (new_st, cid))
            task_st = "ACTION_PROPOSED" if approve else "CANCELLED"
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (task_st, cmd["task_id"]))
            self._log_event(conn, u, f"command_{new_st}", f"Command {cid} ({cmd['action']}) {new_st}", task_id=cmd["task_id"], command_id=cid)
        return {"ok": True, "command_id": cid, "approval_status": new_st, "task_status": task_st}

    # ---------- 3. اقتراح الخطوة التالية والتحقق (Section 5 & 7: Step & Result Loop) ----------
    def _default_planner_heuristic(self, goal: str, step_num: int, memory: Dict[str, Any], screen_w: int, screen_h: int) -> Dict[str, Any]:
        """مخطط احتياطي حتمي وآمن إذا لم يتوفر نموذج الرؤية أو للاختبارات الدخانية (Notepad Smoke Test)."""
        g_low = (goal or "").lower()
        attempted = [a.get("action") for a in (memory.get("actions_attempted") or [])]
        if step_num >= 6 or ("done" in attempted):
            return {"action": "done", "reason": "تم إكمال خطوات المهمة والتحقق من النتيجة بنجاح", "risk": "low"}
        if ("notepad" in g_low or "المفكرة" in g_low or "اكتب" in g_low) and "type_text" not in attempted:
            m_txt = re.search(r"(?:type|اكتب)\s+[\"'«]?([^\"'»\n]{1,120})", goal, re.I)
            txt_to_type = m_txt.group(1).strip() if m_txt else "Hello from RYN Agent"
            return {"action": "type_text", "text": txt_to_type, "reason": "كتابة النص المطلوب في النافذة النشطة", "risk": "medium"}
        if ("click" in g_low or "اضغط" in g_low or "انقر" in g_low) and "click" not in attempted:
            return {
                "action": "click",
                "coordinates": {"x": screen_w // 2, "y": screen_h // 2},
                "reason": "النقر على العنصر المطلوب في وسط الشاشة",
                "risk": "low",
            }
        if not attempted:
            return {"action": "observe", "reason": "فحص الشاشة الحالية وتحديد موقع العناصر", "risk": "low"}
        return {"action": "done", "reason": "اكتملت المهمة وتم التحقق من الشاشة", "risk": "low"}

    def propose_next_step(
        self,
        device_token: str,
        task_id: str = "",
        sequence: int = 1,
        screenshot_b64: str = "",
        screen_width: int = 1920,
        screen_height: int = 1080,
        observation: str = "",
        vision_planner_fn: Optional[Callable[[str, bytes, Dict[str, Any], int, int], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        dev = self.authenticate_device(device_token)
        user_id = dev["user_id"]
        device_id = dev["id"]
        self.check_rate_limit(f"step:{device_id}", max_per_minute=30)

        img_bytes = b""
        if screenshot_b64:
            clean_b64 = screenshot_b64.split(",", 1)[-1].strip()
            try:
                img_bytes = base64.b64decode(clean_b64)
            except Exception:
                raise ValueError("لقطة الشاشة المرسلة ليست base64 صالحة.")
            if len(img_bytes) > MAX_SCREENSHOT_BYTES:
                raise ValueError("حجم لقطة الشاشة يتجاوز الحد المسموح (8MB).")

        now = time.time()
        with self._lock, self._conn() as conn:
            self._record_usage(conn, user_id, req_delta=1, img_bytes=len(img_bytes), tokens=350)

            if task_id:
                t_row = conn.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
            else:
                t_row = conn.execute(
                    "SELECT * FROM tasks WHERE user_id = ? AND (device_id = ? OR device_id = '') "
                    "AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') ORDER BY created_at DESC LIMIT 1",
                    (user_id, device_id),
                ).fetchone()

            if not t_row:
                return {"ok": True, "status": "idle", "message": "لا توجد مهمة نشطة حالياً لهذا الجهاز."}

            tid = t_row["id"]
            t_status = t_row["status"]
            if t_status in ("COMPLETED", "FAILED", "CANCELLED"):
                return {"ok": True, "task_id": tid, "status": t_status}

            # ربط الجهاز بالمهمة إن لم يكن محدداً
            if not t_row["device_id"]:
                conn.execute("UPDATE tasks SET device_id = ? WHERE id = ?", (device_id, tid))

            # التحقق مما إذا كان هناك أمر معلّق ينتظر موافقة المستخدم أو تم اعتماده للتو
            existing_cmd = conn.execute(
                "SELECT * FROM commands WHERE task_id = ? AND executed_at IS NULL ORDER BY sequence DESC LIMIT 1",
                (tid,),
            ).fetchone()
            if existing_cmd:
                if float(existing_cmd["expires_at"]) < now:
                    conn.execute("UPDATE commands SET approval_status = 'expired' WHERE id = ?", (existing_cmd["id"],))
                elif existing_cmd["approval_status"] == "pending_approval":
                    payload = json.loads(existing_cmd["payload_json"])
                    return {
                        "ok": True,
                        "status": "WAITING_FOR_APPROVAL",
                        **payload,
                    }
                elif existing_cmd["approval_status"] == "approved":
                    payload = json.loads(existing_cmd["payload_json"])
                    payload["requires_confirmation"] = False
                    conn.execute("UPDATE tasks SET status = 'EXECUTING' WHERE id = ?", (tid,))
                    return {"ok": True, "status": "ACTION_PROPOSED", **payload}

            cur_step = int(t_row["current_step"] or 0)
            expected_seq = cur_step + 1
            if int(sequence) != expected_seq:
                raise ValueError(f"رقم التسلسل غير متطابق (أُرسل {sequence}، المتوقع {expected_seq}). يُرفض إعادة تشغيل الأوامر القديمة أو خارج الترتيب.")

            mem = json.loads(t_row["memory_json"] or "{}")
            if observation:
                obs_list = mem.setdefault("observations", [])
                obs_list.append({"step": cur_step, "text": str(observation)[:300], "ts": _iso_utc(now)})
                del obs_list[:-6]  # ذاكرة مدمجة ومحدودة بالمهمة فقط (Section 7)

            # إذا تكرر الفشل 3 مرات متتالية، نطلب تدخل المستخدم بدلاً من التكرار اللانهائي
            if int(mem.get("consecutive_failures", 0)) >= 3:
                conn.execute("UPDATE tasks SET status = 'NEEDS_USER_INPUT' WHERE id = ?", (tid,))
                return {
                    "ok": True,
                    "task_id": tid,
                    "status": "NEEDS_USER_INPUT",
                    "action": "ask_user",
                    "question": "تعذر التحقق من نجاح الخطوة بعد 3 محاولات. كيف تفضل المتابعة؟",
                }

            raw_prop: Optional[Dict[str, Any]] = None
            if callable(vision_planner_fn):
                try:
                    raw_prop = vision_planner_fn(t_row["goal"], img_bytes, mem, screen_width, screen_height)
                except Exception as _ve:
                    log.warning("Vision planner fallback to safe heuristic: %s", _ve)
            if not raw_prop:
                raw_prop = self._default_planner_heuristic(t_row["goal"], expected_seq, mem, screen_width, screen_height)

            validated = ActionSchemaValidator.validate_and_enforce_policy(
                raw_prop,
                screen_width=screen_width,
                screen_height=screen_height,
                user_goal=t_row["goal"],
                require_approval_all=bool(t_row["require_approval_all"]),
            )

            cmd_id = "cmd_" + uuid.uuid4().hex[:14]
            exp_ts = now + COMMAND_TTL_SECONDS
            cmd_contract = {
                "task_id": tid,
                "command_id": cmd_id,
                "sequence": expected_seq,
                "action": validated["action"],
                "coordinates": validated["coordinates"],
                "text": validated["text"],
                "key": validated["key"],
                "scroll_dy": validated["scroll_dy"],
                "wait_seconds": validated["wait_seconds"],
                "reason": validated["reason"],
                "risk": validated["risk"],
                "requires_confirmation": validated["requires_confirmation"],
                "expires_at": _iso_utc(exp_ts),
            }
            if validated.get("question"):
                cmd_contract["question"] = validated["question"]

            approval_st = "pending_approval" if validated["requires_confirmation"] else "approved"
            next_task_st = (
                "COMPLETED" if validated["action"] == "done"
                else ("NEEDS_USER_INPUT" if validated["action"] == "ask_user"
                      else ("WAITING_FOR_APPROVAL" if validated["requires_confirmation"] else "ACTION_PROPOSED"))
            )

            conn.execute(
                "INSERT INTO commands (id, task_id, sequence, action, payload_json, risk, approval_status, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cmd_id,
                    tid,
                    expected_seq,
                    validated["action"],
                    json.dumps(cmd_contract, ensure_ascii=False),
                    validated["risk"],
                    approval_st,
                    exp_ts,
                ),
            )
            mem.setdefault("actions_attempted", []).append({
                "sequence": expected_seq,
                "command_id": cmd_id,
                "action": validated["action"],
                "reason": validated["reason"],
            })
            del mem["actions_attempted"][:-8]

            conn.execute(
                "UPDATE tasks SET status = ?, current_step = ?, memory_json = ?, completed_at = ? WHERE id = ?",
                (
                    next_task_st,
                    expected_seq if validated["action"] == "done" else cur_step,
                    json.dumps(mem, ensure_ascii=False),
                    now if validated["action"] == "done" else None,
                    tid,
                ),
            )
            self._log_event(
                conn,
                user_id,
                "action_proposed",
                f"seq={expected_seq} action={validated['action']} risk={validated['risk']} confirm={validated['requires_confirmation']}",
                task_id=tid,
                command_id=cmd_id,
            )

        return {"ok": True, "status": next_task_st, **cmd_contract}

    def report_step_result(
        self,
        device_token: str,
        task_id: str,
        command_id: str,
        sequence: int,
        status: str = "succeeded",
        error: str = "",
        verification_note: str = "",
        screenshot_b64: str = "",
    ) -> Dict[str, Any]:
        """
        يستقبل نتيجة التنفيذ الفعلي من العميل مع لقطة التحقق (Verification Loop — Section 3 & 7):
        لا يفترض السيرفر أبداً أن العملية نجحت بمجرد إرسالها، بل يحدث الحالة فقط بعد تقرير العميل.
        """
        dev = self.authenticate_device(device_token)
        user_id = dev["user_id"]
        now = time.time()

        with self._lock, self._conn() as conn:
            cmd = conn.execute(
                "SELECT * FROM commands WHERE id = ? AND task_id = ?",
                (command_id, task_id),
            ).fetchone()
            if not cmd:
                raise ValueError("معرّف الأمر (command_id) غير موجود لهذه المهمة.")
            if int(cmd["sequence"]) != int(sequence):
                raise ValueError("تسلسل الأمر (sequence) غير مطابق.")
            if cmd["executed_at"] is not None:
                raise ValueError("تم الإبلاغ عن نتيجة هذا الأمر مسبقاً (يُرفض تكرار التنفيذ).")
            if float(cmd["expires_at"]) < now:
                raise ValueError("انتهت صلاحية هذا الأمر (Expired Command).")
            if cmd["approval_status"] not in ("approved",):
                raise PermissionError(f"لا يمكن تنفيذ أمر حالته '{cmd['approval_status']}' بدون اعتماد المستخدم.")

            t_row = conn.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).fetchone()
            if not t_row:
                raise ValueError("المهمة غير موجودة.")
            if t_row["status"] in ("CANCELLED", "FAILED", "COMPLETED"):
                raise ValueError(f"المهمة في حالة '{t_row['status']}' ولا تقبل نتائج جديدة.")

            ok_exec = str(status or "").lower() in ("succeeded", "success", "ok", "done")
            conn.execute("UPDATE commands SET executed_at = ?, approval_status = ? WHERE id = ?", (now, "executed" if ok_exec else "failed", command_id))

            mem = json.loads(t_row["memory_json"] or "{}")
            mem.setdefault("action_results", []).append({
                "sequence": int(sequence),
                "command_id": command_id,
                "action": cmd["action"],
                "status": "succeeded" if ok_exec else "failed",
                "verification": str(verification_note or "")[:240],
                "error": str(error or "")[:240],
            })
            del mem["action_results"][:-8]

            if ok_exec:
                mem["consecutive_failures"] = 0
                new_task_st = "COMPLETED" if cmd["action"] == "done" else "VERIFYING"
                new_step = int(sequence)
            else:
                mem["consecutive_failures"] = int(mem.get("consecutive_failures", 0)) + 1
                mem.setdefault("errors", []).append(str(error or "Action execution failed")[:200])
                new_task_st = "NEEDS_USER_INPUT" if mem["consecutive_failures"] >= 3 else "PLANNING"
                new_step = int(sequence)

            conn.execute(
                "UPDATE tasks SET status = ?, current_step = ?, memory_json = ?, completed_at = ? WHERE id = ?",
                (new_task_st, new_step, json.dumps(mem, ensure_ascii=False), now if new_task_st == "COMPLETED" else None, task_id),
            )
            self._log_event(
                conn,
                user_id,
                "action_result",
                f"seq={sequence} action={cmd['action']} status={status} {verification_note}",
                task_id=task_id,
                command_id=command_id,
                error_code="" if ok_exec else (error or "exec_failed"),
            )

        return {
            "ok": True,
            "task_id": task_id,
            "command_id": command_id,
            "sequence": int(sequence),
            "task_status": new_task_st,
            "verified": ok_exec,
        }

    # ---------- 4. الاستعلام عن الحالة والسجلات (Status & Audit Logs) ----------
    def get_status(self, user_id: str = "main") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        today = time.strftime("%Y-%m-%d")
        with self._lock, self._conn() as conn:
            devs = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "platform": r["platform"],
                    "created_at": _iso_utc(r["created_at"]),
                    "last_seen_at": _iso_utc(r["last_seen_at"]),
                    "revoked": r["revoked_at"] is not None,
                }
                for r in conn.execute(
                    "SELECT id, name, platform, created_at, last_seen_at, revoked_at FROM devices WHERE user_id = ? ORDER BY last_seen_at DESC",
                    (u,),
                ).fetchall()
            ]
            tasks = []
            for r in conn.execute(
                "SELECT id, device_id, goal, status, current_step, require_approval_all, created_at, completed_at "
                "FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT 12",
                (u,),
            ).fetchall():
                pending_cmd = conn.execute(
                    "SELECT id, sequence, action, payload_json, risk, approval_status FROM commands "
                    "WHERE task_id = ? AND approval_status = 'pending_approval' ORDER BY sequence DESC LIMIT 1",
                    (r["id"],),
                ).fetchone()
                p_cmd_dict = json.loads(pending_cmd["payload_json"]) if pending_cmd else None
                tasks.append({
                    "id": r["id"],
                    "device_id": r["device_id"],
                    "goal": r["goal"],
                    "status": r["status"],
                    "current_step": r["current_step"],
                    "require_approval_all": bool(r["require_approval_all"]),
                    "pending_command": p_cmd_dict,
                    "created_at": _iso_utc(r["created_at"]),
                    "completed_at": _iso_utc(r["completed_at"]) if r["completed_at"] else None,
                })
            u_row = conn.execute(
                "SELECT request_count, image_bytes, audio_seconds, model_tokens FROM usage_daily WHERE user_id = ? AND date = ?",
                (u, today),
            ).fetchone()
            usage = {
                "date": today,
                "request_count": int(u_row["request_count"]) if u_row else 0,
                "request_limit": DEFAULT_USER_DAILY_LIMIT,
                "image_bytes": int(u_row["image_bytes"]) if u_row else 0,
                "audio_seconds": float(u_row["audio_seconds"]) if u_row else 0.0,
                "model_tokens": int(u_row["model_tokens"]) if u_row else 0,
            }
        return {
            "ok": True,
            "user_id": u,
            "kill_switch": self._kill_switch_global,
            "devices": devs,
            "tasks": tasks,
            "usage": usage,
        }

    def get_logs(self, user_id: str = "main", limit: int = 40) -> List[Dict[str, Any]]:
        u = str(user_id or "main").strip() or "main"
        lim = max(1, min(200, int(limit or 40)))
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT id, command_id, task_id, event_type, result, error_code, created_at "
                "FROM action_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (u, lim),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "command_id": r["command_id"],
                    "task_id": r["task_id"],
                    "event_type": r["event_type"],
                    "result": r["result"],
                    "error_code": r["error_code"],
                    "created_at": _iso_utc(r["created_at"]),
                }
                for r in rows
            ]

    # ---------- 5. التفاعل المباشر من محادثة Ryn (Chat Command Integration) ----------
    _PAIR_CHAT_RE = re.compile(
        r"^(?:اربط\s+جهاز|إقران\s+جهاز|اقتران\s+جهاز|كود\s+إقران|كود\s+اقتران|كود\s+ربط\s+الجهاز|pair\s+device)\s*[.!؟?]*$",
        re.I,
    )
    _AGENT_TASK_CHAT_RE = re.compile(
        r"^(?:نفذ\s+على\s+(?:الكمبيوتر|الجهاز|ويندوز|الهاتف|أندرويد)|يا\s*وكيل\s+نفذ|شغل\s+الوكيل\s*:?)\s*(.+)$",
        re.I | re.S,
    )
    _AGENT_STATUS_CHAT_RE = re.compile(
        r"^(?:حالة\s+الوكيل|حالة\s+الأجهزة|أجهزتي\s+المتصلة|سجل\s+الوكيل|agent\s+status)\s*[.!؟?]*$",
        re.I,
    )

    def handle_chat_agent_command(self, user_id: str, text: str) -> Optional[str]:
        t = (text or "").strip()
        if not t:
            return None
        if self._PAIR_CHAT_RE.match(t):
            res = self.start_pairing(user_id)
            return (
                f"🤖 **كود إقران جهاز RYN Agent الجديد**:\n"
                f"• **الكود السداسي**: `{res['pairing_code']}` *(صالح لمدة 5 دقائق ويُستخدم لمرة واحدة فقط)*\n"
                f"• افتح تطبيق **RYN Desktop (Windows)** أو **RYN Mobile (Android)** وأدخل هذا الكود لإتمام الربط الآمن."
            )
        m_task = self._AGENT_TASK_CHAT_RE.match(t)
        if m_task:
            goal_txt = m_task.group(1).strip()
            try:
                tr = self.create_task(user_id, goal=goal_txt)
                return (
                    f"🚀 **تم إنشاء مهمة جديدة لوكيل RYN Agent!**\n"
                    f"• **معرّف المهمة**: `{tr['task_id']}`\n"
                    f"• **الهدف**: {tr['goal']}\n"
                    f"• **الحالة**: `{tr['status']}` *(سيقوم الجهاز المقترن بالتقاط الشاشة وتنفيذ خطوة واحدة كل مرة مع التحقق)*"
                )
            except Exception as e:
                return f"⛔ **تعذر إنشاء مهمة الوكيل**: {e}"
        if self._AGENT_STATUS_CHAT_RE.match(t):
            st = self.get_status(user_id)
            active_devs = [d for d in st["devices"] if not d["revoked"]]
            lines = [
                f"🤖 **حالة منظومة RYN Agent (الأجهزة المقترنة: {len(active_devs)})**:",
                f"• **مفتاح الطوارئ (Kill Switch)**: {'🛑 مفعّل' if st['kill_switch'] else '🟢 طبيعي'}",
                f"• **استهلاك اليوم**: `{st['usage']['request_count']} / {st['usage']['request_limit']}` طلب",
            ]
            for d in active_devs[:5]:
                lines.append(f"  - 💻 **{d['name']}** (`{d['platform']}` · `{d['id']}`) — آخر ظهور: `{d['last_seen_at']}`")
            for tk in st["tasks"][:4]:
                lines.append(f"  - 📋 مهمة `{tk['id']}`: **{tk['goal'][:60]}** ← `{tk['status']}` (الخطوة {tk['current_step']})")
            return "\n".join(lines)
        return None


_GLOBAL_AGENT_CORE: Optional[RynAgentCoreManager] = None


def get_agent_core() -> RynAgentCoreManager:
    global _GLOBAL_AGENT_CORE
    if _GLOBAL_AGENT_CORE is None:
        _GLOBAL_AGENT_CORE = RynAgentCoreManager()
    return _GLOBAL_AGENT_CORE
