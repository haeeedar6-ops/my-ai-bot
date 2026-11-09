# -*- coding: utf-8 -*-
"""
duplex.py — مقاطعة المستخدم أثناء كلام رين على ويندوز (Phase B/D).
نفس منطق الويب (voice/duplex_vad.js): كشف طاقة مع عتبة تكيفية. الميكروفون عبر sounddevice (اختياري).
تنبيه صريح: sounddevice ما فيه إلغاء صدى؛ بدون سماعات ممكن رين يسمع حاله. لهيك الميزة مطفية افتراضياً
(RYN_DESKTOP_DUPLEX=1 لتفعيلها) والأفضل استخدام سماعات أو profile="speaker".
إذا المكتبة/الميكروفون مو متوفرين بنرجع لوضع الضغط-للكلام بدون ما نكسر شي.
"""
from __future__ import annotations

import math
import os
import threading
from typing import Callable, Dict, Optional

CFG = {"frame_ms": 30, "warmup_ms": 700, "min_speech_ms": 240, "ratio": 4.0, "min_rms": 0.05,
       "absorb_ms": 150, "floor_attack": 0.02, "floor_release": 0.05}
PROFILES: Dict[str, Dict[str, float]] = {
    "normal": {"min_rms": 0.05, "ratio": 4.0},
    "headphones": {"min_rms": 0.025, "ratio": 3.0},
    "speaker": {"min_rms": 0.08, "ratio": 5.0, "min_speech_ms": 320},
}


def duplex_enabled_by_env() -> bool:
    return os.environ.get("RYN_DESKTOP_DUPLEX", "0").strip().lower() in ("1", "true", "yes", "on")


class DuplexVad:
    """قلب الكشف (نقي، بدون أجهزة). step(rms, dt_ms) -> True مرة وحدة عند تأكد الكلام."""

    def __init__(self, profile: str = "normal", **overrides):
        c = dict(CFG)
        c.update(PROFILES.get(profile, PROFILES["normal"]))
        c.update(overrides)
        self.c = c
        self.t = 0.0
        self.floor = 0.01
        self.run = 0.0
        self.fired = False

    def step(self, rms: float, dt_ms: float = 30.0) -> bool:
        c = self.c
        self.t += dt_ms
        if self.fired:
            return False
        thr = max(c["min_rms"], self.floor * c["ratio"])
        hot = rms > thr
        self.run = self.run + dt_ms if hot else 0.0
        if self.run <= c["absorb_ms"] or not hot:
            a = c["floor_attack"] if rms > self.floor else c["floor_release"]
            self.floor += (rms - self.floor) * a
        if self.t < c["warmup_ms"]:
            return False
        if self.run >= c["min_speech_ms"]:
            self.fired = True
            return True
        return False


def rms_of_samples(samples) -> float:
    n = len(samples)
    if not n:
        return 0.0
    return math.sqrt(sum(float(x) * float(x) for x in samples) / n)


class SoundDeviceFrameSource:
    """مصدر إطارات من الميكروفون. بيرجّع RMS لكل إطار. يرمي RuntimeError إذا المكتبة/الجهاز مو متوفرين."""

    def __init__(self, frame_ms: int = 30, samplerate: int = 16000):
        try:
            import sounddevice as sd  # type: ignore
        except Exception as e:
            raise RuntimeError("sounddevice غير مثبّتة (pip install sounddevice)") from e
        self._sd = sd
        self.frame = int(samplerate * frame_ms / 1000)
        try:
            self._stream = sd.InputStream(samplerate=samplerate, channels=1, dtype="float32", blocksize=self.frame)
            self._stream.start()
        except Exception as e:
            raise RuntimeError(f"تعذر فتح الميكروفون: {e}") from e

    def __call__(self) -> Optional[float]:
        data, _overflow = self._stream.read(self.frame)
        return rms_of_samples(data[:, 0].tolist())

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class DuplexMonitor:
    """ينفّذ on_interrupt مرة وحدة إذا تأكد كلام المستخدم أثناء التشغيل. frame_source() -> RMS أو None للإيقاف."""

    def __init__(self, frame_source: Callable[[], Optional[float]], on_interrupt: Callable[[], None],
                 profile: str = "normal", frame_ms: float = 30.0):
        self.frame_source = frame_source
        self.on_interrupt = on_interrupt
        self.vad = DuplexVad(profile)
        self.frame_ms = frame_ms
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.triggered = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                rms = self.frame_source()
            except Exception:
                break
            if rms is None:
                break
            if self.vad.step(float(rms), self.frame_ms):
                self.triggered = True
                try:
                    self.on_interrupt()
                finally:
                    break

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="ryn-duplex", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        close = getattr(self.frame_source, "close", None)
        if callable(close):
            close()
