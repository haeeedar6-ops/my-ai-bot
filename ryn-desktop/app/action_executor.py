"""
action_executor.py — منفذ الأوامر المحلي الصارم (Strict Local Action Dispatcher — Section 8).
يربط كل عملية مسموحة بدالة مخصصة فقط مع تفعيل PyAutoGUI Fail-Safe.
"""
import ctypes
import struct
import sys
import time
from typing import Any, Callable, Dict

# ---- SendInput (ويندوز): كتابة أي نص Unicode (عربي/إيموجي/...) ----
# pyautogui.write بيتجاهل أي حرف مو ASCII بصمت، فالنص العربي كان ما بينكتب أبداً.
# الأنواع بأحجام ثابتة (مو wintypes) لتطلع sizeof(INPUT) == 40 على ويندوز 64-بت.
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16))


class _INPUT_UNION(ctypes.Union):
    _fields_ = (("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT))


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", ctypes.c_uint32), ("u", _INPUT_UNION))


def type_unicode_text(text: str, interval: float = 0.01, press_enter: Callable[[], None] = None) -> bool:
    """يكتب النص حرف حرف عبر SendInput (KEYEVENTF_UNICODE). بيرجّع False إذا مو ويندوز أو فشل الإرسال."""
    if not sys.platform.startswith("win"):
        return False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int)
        user32.SendInput.restype = ctypes.c_uint
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for li, line in enumerate(lines):
            if li > 0 and press_enter is not None:
                press_enter()
            raw = line.encode("utf-16-le")  # الإيموجي (surrogate pairs) بينبعتوا كوحدتين متتاليتين
            for unit in struct.unpack("<%dH" % (len(raw) // 2), raw):
                down = _INPUT(type=_INPUT_KEYBOARD)
                down.ki = _KEYBDINPUT(0, unit, _KEYEVENTF_UNICODE, 0, 0)
                up = _INPUT(type=_INPUT_KEYBOARD)
                up.ki = _KEYBDINPUT(0, unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0, 0)
                arr = (_INPUT * 2)(down, up)
                if user32.SendInput(2, arr, ctypes.sizeof(_INPUT)) != 2:
                    return False
                if interval:
                    time.sleep(interval)
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
        if self._pyautogui and not self.dry_run:
            if txt.isascii() and "\n" not in txt:
                self._pyautogui.write(txt, interval=0.02)
            elif not type_unicode_text(txt, press_enter=lambda: self._pyautogui.press("enter")):
                # أحسن من إنو يكتب نص ناقص بصمت: نفشّل الخطوة برسالة واضحة
                raise RuntimeError("تعذّر كتابة هالنص (غير لاتيني/سطور متعددة) — تأكد أن النافذة المستهدفة مفتوحة وتقبل الإدخال")
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
