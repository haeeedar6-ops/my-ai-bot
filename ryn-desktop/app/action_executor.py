"""
action_executor.py — منفذ الأوامر المحلي الصارم (Strict Local Action Dispatcher — Section 8).
يربط كل عملية مسموحة بدالة مخصصة فقط مع تفعيل PyAutoGUI Fail-Safe.
"""
import time
from typing import Any, Callable, Dict


class StrictActionDispatcher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._pyautogui = None
        if not dry_run:
            try:
                import pyautogui
                pyautogui.FAILSAFE = True
                pyautogui.PAUSE = 0.15
                self._pyautogui = pyautogui
            except Exception:
                self._pyautogui = None

        self._dispatch_table: Dict[str, Callable[[Dict[str, Any]], str]] = {
            "observe": self._do_observe,
            "click": self._do_click,
            "double_click": self._do_double_click,
            "type_text": self._do_type_text,
            "key_press": self._do_key_press,
            "scroll": self._do_scroll,
            "wait": self._do_wait,
            "done": self._do_done,
            "ask_user": self._do_ask_user,
        }

    def execute(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        action = str(cmd.get("action") or "").strip().lower()
        handler = self._dispatch_table.get(action)
        if handler is None:
            raise ValueError(f"لا توجد دالة تنفيذ محلية للعملية: {action}")
        note = handler(cmd)
        return {"status": "succeeded", "verification_note": note}

    def _do_observe(self, _cmd: Dict[str, Any]) -> str:
        return "Screen observed and sent for analysis"

    def _do_click(self, cmd: Dict[str, Any]) -> str:
        c = cmd["coordinates"]
        x, y = int(c["x"]), int(c["y"])
        if self._pyautogui and not self.dry_run:
            self._pyautogui.click(x=x, y=y)
        return f"Clicked at ({x}, {y})"

    def _do_double_click(self, cmd: Dict[str, Any]) -> str:
        c = cmd["coordinates"]
        x, y = int(c["x"]), int(c["y"])
        if self._pyautogui and not self.dry_run:
            self._pyautogui.doubleClick(x=x, y=y)
        return f"Double-clicked at ({x}, {y})"

    def _do_type_text(self, cmd: Dict[str, Any]) -> str:
        txt = str(cmd.get("text") or "")
        if self._pyautogui and not self.dry_run:
            self._pyautogui.write(txt, interval=0.02)
        return f"Typed text ({len(txt)} chars)"

    def _do_key_press(self, cmd: Dict[str, Any]) -> str:
        key = str(cmd.get("key") or "").lower()
        if self._pyautogui and not self.dry_run:
            if "+" in key:
                parts = [p.strip() for p in key.split("+") if p.strip()]
                self._pyautogui.hotkey(*parts)
            else:
                self._pyautogui.press(key)
        return f"Pressed key '{key}'"

    def _do_scroll(self, cmd: Dict[str, Any]) -> str:
        dy = int(cmd.get("scroll_dy") or -300)
        if self._pyautogui and not self.dry_run:
            self._pyautogui.scroll(dy)
        return f"Scrolled by {dy}"

    def _do_wait(self, cmd: Dict[str, Any]) -> str:
        sec = max(0.2, min(10.0, float(cmd.get("wait_seconds") or 1.0)))
        if not self.dry_run:
            time.sleep(sec)
        return f"Waited {sec:.1f}s"

    def _do_done(self, _cmd: Dict[str, Any]) -> str:
        return "Task marked done"

    def _do_ask_user(self, cmd: Dict[str, Any]) -> str:
        return f"Asked user: {cmd.get('question') or cmd.get('reason') or ''}"
