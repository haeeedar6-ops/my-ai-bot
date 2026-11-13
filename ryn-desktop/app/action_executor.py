"""
action_executor.py — منفذ الأوامر المحلي الصارم (Strict Local Action Dispatcher — Section 8).
يربط كل عملية مسموحة بدالة مخصصة فقط مع تفعيل PyAutoGUI Fail-Safe ودعم الكتابة العربية عبر Win32 SendInput.
"""
import ctypes
import sys
import time
from typing import Any, Callable, Dict

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("u", _INPUT_UNION),
    ]


def type_unicode_text(text: str, interval: float = 0.01) -> bool:
    """يكتب نصوص Unicode (مثل العربية) مباشرة على ويندوز عبر SendInput بدون الاعتماد على الحافظة أو تخطيط لوحة المفاتيح."""
    if not text:
        return True
    if sys.platform == "win32" and hasattr(ctypes, "windll"):
        try:
            send_input = ctypes.windll.user32.SendInput
            utf16_units = text.encode("utf-16-le")
            for i in range(0, len(utf16_units), 2):
                code_unit = int.from_bytes(utf16_units[i:i + 2], "little")
                down = _INPUT(type=INPUT_KEYBOARD)
                down.ki = _KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
                up = _INPUT(type=INPUT_KEYBOARD)
                up.ki = _KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                arr = (_INPUT * 2)(down, up)
                sent = send_input(2, ctypes.byref(arr), ctypes.sizeof(_INPUT))
                if sent != 2:
                    return False
                if interval > 0:
                    time.sleep(interval)
            return True
        except Exception:
            pass
    try:
        from pynput.keyboard import Controller
        kb = Controller()
        kb.type(text)
        return True
    except Exception:
        return False


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
        if not self.dry_run and txt:
            is_ascii = all(ord(ch) < 128 for ch in txt)
            if not is_ascii:
                if not type_unicode_text(txt, interval=0.01):
                    raise RuntimeError("تعذر كتابة النص غير اللاتيني (Unicode) على هذا الجهاز")
            elif self._pyautogui:
                self._pyautogui.write(txt, interval=0.02)
            else:
                if not type_unicode_text(txt, interval=0.01):
                    raise RuntimeError("لا توجد أداة متاحة لكتابة النص")
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
