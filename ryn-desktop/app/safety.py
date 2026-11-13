"""
safety.py — بوابة الأمان المحلية لعميل سطح المكتب (Desktop Safety Policy — Section 8).
العميل يتحقق بشكل مستقل من كل أمر قادم من السيرفر ولا ينفذ أبداً أوامر غير معروفة أو خارج حدود الشاشة.
"""
import re
import threading
from typing import Any, Callable, Dict, Optional

ALLOWED_CLIENT_ACTIONS = (
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

BLOCKED_LOCAL_RE = re.compile(
    r"(?i)(\b(?:rm\s+-rf|del\s+/[fsq]|format\s+[a-z](?=:)|powershell|cmd\.exe|regedit|bcdedit|shutdown|taskkill)\b|"
    r"\b(?:password|credit\s*card|cvv|iban|bank|bitwarden|1password|seed\s*phrase)\b|"
    r"(?:كلمة\s*المرور|كلمه\s*المرور|كلمة\s*السر|كلمه\s*السر|باسورد|بطاقة\s*ائتمان|حساب\s*بنكي|عبارة\s*الاسترداد))",
)


class LocalSafetyGate:
    def __init__(self, confirm_callback: Optional[Callable[[Dict[str, Any]], bool]] = None):
        self._emergency_stop = threading.Event()
        self.confirm_callback = confirm_callback
        self._last_sequence_by_task: Dict[str, int] = {}

    def trigger_emergency_stop(self) -> None:
        self._emergency_stop.set()

    def reset_emergency_stop(self) -> None:
        self._emergency_stop.clear()

    @property
    def is_stopped(self) -> bool:
        return self._emergency_stop.is_set()

    def validate_incoming_command(
        self,
        cmd: Dict[str, Any],
        screen_width: int,
        screen_height: int,
        auto_approve_low: bool = True,
    ) -> Dict[str, Any]:
        if self.is_stopped:
            raise PermissionError("🛑 مفتاح الإيقاف الطارئ المحلي (F12 / Stop) مفعّل.")

        action = str(cmd.get("action") or "").strip().lower()
        if action not in ALLOWED_CLIENT_ACTIONS:
            raise ValueError(f"عملية غير مسموحة محلياً: {action}")

        task_id = str(cmd.get("task_id") or "")
        seq = int(cmd.get("sequence") or 0)
        last_seq = self._last_sequence_by_task.get(task_id, 0)
        if seq <= last_seq:
            raise ValueError(f"رفض أمر مكرر أو قديم (sequence={seq} <= {last_seq}).")

        reason = str(cmd.get("reason") or "")
        text_val = str(cmd.get("text") or "")
        if BLOCKED_LOCAL_RE.search(f"{reason} {text_val}"):
            raise PermissionError("⛔ حظر محلي: الأمر يتضمن كلمات مرور أو معاملات مالية أو أوامر نظام محظورة.")

        if action in ("click", "double_click"):
            coords = cmd.get("coordinates") or {}
            cx, cy = int(coords.get("x", -1)), int(coords.get("y", -1))
            if not (0 <= cx <= screen_width and 0 <= cy <= screen_height):
                raise ValueError(f"الإحداثيات ({cx}, {cy}) خارج حدود الشاشة الفعلية ({screen_width}x{screen_height}).")

        risk = str(cmd.get("risk") or "low").lower()
        needs_confirm = bool(cmd.get("requires_confirmation")) or risk in ("medium", "high") or (not auto_approve_low)
        if needs_confirm and action not in ("observe", "done", "wait"):
            if callable(self.confirm_callback):
                approved = bool(self.confirm_callback(cmd))
                if not approved:
                    raise PermissionError("رفض المستخدم تنفيذ العملية محلياً.")
            elif risk == "high":
                raise PermissionError("العملية عالية الخطورة وتتطلب موافقة صريحة من المستخدم.")

        self._last_sequence_by_task[task_id] = seq
        return cmd
