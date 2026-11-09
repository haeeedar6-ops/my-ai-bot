# -*- coding: utf-8 -*-
import os, sys, threading, time, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ryn-desktop"))

from app.duplex import DuplexMonitor, DuplexVad, SoundDeviceFrameSource, rms_of_samples  # noqa: E402
from app.voice import PushToTalkController  # noqa: E402


def feed(frames):
    it = iter(frames)
    def src():
        time.sleep(0.001)
        return next(it, None)
    return src


class TestDesktopVad(unittest.TestCase):
    def run_frames(self, frames, profile="normal"):
        v = DuplexVad(profile)
        for i, f in enumerate(frames):
            if v.step(f, 30):
                return i
        return -1

    def test_silence_and_noise(self):
        self.assertEqual(self.run_frames([0.004] * 200), -1)
        self.assertEqual(self.run_frames([0.03] * 200), -1)

    def test_click_ignored_speech_fires(self):
        self.assertEqual(self.run_frames([0.004] * 40 + [0.3] * 3 + [0.004] * 40), -1)
        idx = self.run_frames([0.004] * 40 + [0.2] * 20)
        self.assertTrue(40 <= idx < 60)

    def test_warmup(self):
        self.assertEqual(self.run_frames([0.4] * 20), -1)

    def test_profiles(self):
        frames = [0.004] * 40 + [0.06] * 30
        self.assertGreaterEqual(self.run_frames(frames, "headphones"), 0)
        self.assertEqual(self.run_frames(frames, "speaker"), -1)

    def test_rms(self):
        self.assertAlmostEqual(rms_of_samples([0.5, -0.5]), 0.5)
        self.assertEqual(rms_of_samples([]), 0.0)


class TestMonitorAndController(unittest.TestCase):
    def test_monitor_fires_once(self):
        hits = []
        m = DuplexMonitor(feed([0.004] * 40 + [0.3] * 60), lambda: hits.append(1), profile="normal")
        m.start()
        m._thread.join(timeout=3)
        m.stop()
        self.assertEqual(hits, [1])

    def test_monitor_never_fires_on_silence(self):
        hits = []
        m = DuplexMonitor(feed([0.004] * 100), lambda: hits.append(1))
        m.start()
        m._thread.join(timeout=3)
        m.stop()
        self.assertEqual(hits, [])

    def test_controller_interrupts_playback_when_user_speaks(self):
        ctl = PushToTalkController(api_client=mock.Mock())
        ctl.duplex_enabled = True
        ctl.duplex_frame_source_factory = lambda: feed([0.004] * 40 + [0.3] * 200)
        notified = []
        ctl._notify_server_playback = lambda a, barge_in=False: notified.append((a, barge_in))

        def fake_impl(*_a, **_k):
            ctl._stop_event.clear()
            ctl._stop_event.wait(timeout=5)  # "تشغيل" لحد ما حدا يقاطع
            return {"ok": True, "interrupted": ctl._stop_event.is_set()}

        ctl._speak_impl = fake_impl
        res = ctl.speak("نص طويل")
        self.assertTrue(res["interrupted"])
        self.assertIn(("barge_in", True), notified)

    def test_disabled_by_default_and_no_mic_opened(self):
        with mock.patch.dict(os.environ, {"RYN_DESKTOP_DUPLEX": "0"}):
            ctl = PushToTalkController(api_client=mock.Mock())
        self.assertFalse(ctl.duplex_enabled)
        ctl.duplex_frame_source_factory = mock.Mock(side_effect=AssertionError("ما لازم يفتح الميكروفون"))
        ctl._speak_impl = lambda *a, **k: {"ok": True}
        self.assertTrue(ctl.speak("x")["ok"])

    def test_graceful_fallback_when_mic_unavailable(self):
        ctl = PushToTalkController(api_client=mock.Mock())
        ctl.duplex_enabled = True
        ctl.duplex_frame_source_factory = mock.Mock(side_effect=RuntimeError("no mic"))
        logs = []
        ctl.log_cb = logs.append
        ctl._speak_impl = lambda *a, **k: {"ok": True}
        self.assertTrue(ctl.speak("x")["ok"])  # ما كسر شي
        self.assertTrue(any("الضغط للكلام" in l for l in logs))

    def test_sounddevice_missing_raises_clear_error(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            with self.assertRaises(RuntimeError):
                SoundDeviceFrameSource()


if __name__ == "__main__":
    unittest.main()
