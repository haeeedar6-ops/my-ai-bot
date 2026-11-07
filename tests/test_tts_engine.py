"""اختبارات محرك الأصوات الحقيقي (Microsoft Neural) وخدمة الصوت — بدون شبكة."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tts_engine  # noqa: E402
from voice import AVAILABLE_VOICES, VoiceService, canonical_voice_id  # noqa: E402


class TestTtsEngine(unittest.TestCase):
    def test_legacy_names_map_to_real_voices(self):
        self.assertEqual(tts_engine.resolve_voice("ar-SY-RynNatural"), "ar-SY-LaithNeural")
        self.assertEqual(tts_engine.resolve_voice("ar-SA-HamedNeural"), "ar-SA-HamedNeural")
        self.assertEqual(tts_engine.resolve_voice("x'; drop"), tts_engine.DEFAULT_VOICE)
        self.assertEqual(tts_engine.resolve_voice(None), tts_engine.DEFAULT_VOICE)

    def test_clean_for_speech(self):
        out = tts_engine.clean_for_speech("مرحبا **كيفك** ```py\nprint(1)\n``` شوف https://x.com/a 😀 `x`")
        self.assertNotIn("print", out)
        self.assertNotIn("http", out)
        self.assertNotIn("*", out)
        self.assertIn("كود برمجي", out)
        self.assertLessEqual(len(tts_engine.clean_for_speech("أ" * 9000)), tts_engine.MAX_CHARS)

    def test_rate_percent_bounds(self):
        self.assertEqual(tts_engine._rate_percent(1.0), 0)
        self.assertEqual(tts_engine._rate_percent(1.25), 25)
        self.assertEqual(tts_engine._rate_percent(0.1), -40)
        self.assertEqual(tts_engine._rate_percent(9), 60)

    def test_provider_order(self):
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto"}, clear=False):
            os.environ.pop("AZURE_SPEECH_KEY", None)
            os.environ.pop("AZURE_SPEECH_REGION", None)
            self.assertEqual(tts_engine.provider_order(), ["edge"])
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto", "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "westeurope"}):
            self.assertEqual(tts_engine.provider_order(), ["azure", "edge"])
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "edge", "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}):
            self.assertEqual(tts_engine.provider_order(), ["edge"])

    def test_synthesize_caches_and_falls_back_between_providers(self):
        calls = {"azure": 0, "edge": 0}

        def azure(*_a):
            calls["azure"] += 1
            raise RuntimeError("azure down")

        def edge(*_a):
            calls["edge"] += 1
            return b"ID3fake-mp3"

        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto", "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}), \
                mock.patch.object(tts_engine, "_azure_synthesize", azure), \
                mock.patch.object(tts_engine, "_edge_synthesize", edge):
            a = tts_engine.synthesize("مرحبا يا صديقي تجربة الكاش 123", "ar-SY-AmanyNeural", 1.0)
            b = tts_engine.synthesize("مرحبا يا صديقي تجربة الكاش 123", "ar-SY-AmanyNeural", 1.0)
        self.assertEqual(a, b"ID3fake-mp3")
        self.assertEqual(calls, {"azure": 1, "edge": 1})  # الثانية من الكاش

    def test_all_providers_fail_raises(self):
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "edge"}), \
                mock.patch.object(tts_engine, "_edge_synthesize", side_effect=RuntimeError("no net")):
            with self.assertRaises(RuntimeError):
                tts_engine.synthesize("نص مختلف تماماً لتجنب الكاش 98765", "ar-SA-HamedNeural", 1.0)

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            tts_engine.synthesize("   ", None, 1.0)


class TestVoiceCatalogAndSpeak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "v.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_has_only_real_microsoft_voice_ids(self):
        for v in AVAILABLE_VOICES:
            if v.get("available", True):
                self.assertEqual(tts_engine.resolve_voice(v["id"]), v["id"], v["id"])
        syr = [v["id"] for v in AVAILABLE_VOICES if v["lang"] == "ar-SY"]
        self.assertEqual(set(syr), {"ar-SY-LaithNeural", "ar-SY-AmanyNeural"})

    def test_canonical_voice_id(self):
        self.assertEqual(canonical_voice_id("ar-SY-RynNatural"), "ar-SY-LaithNeural")
        self.assertEqual(canonical_voice_id("vibevoice-realtime-exp"), "ar-SY-LaithNeural")  # غير متاح
        self.assertEqual(canonical_voice_id("garbage", "ar-EG-SalmaNeural"), "ar-EG-SalmaNeural")

    def test_speak_uses_real_provider_and_mime(self):
        seen = {}

        def fake(text, voice, speed):
            seen.update(voice=voice, speed=speed)
            return b"ID3\x04fake-mp3-bytes" * 5

        vs = VoiceService(db_path=self.db, tts_provider_fn=fake)
        out = vs.speak("u1", text="مرحبا", voice_id="ar-SA-HamedStrong", speed=1.2)  # اسم قديم
        self.assertEqual(seen["voice"], "ar-SA-HamedNeural")
        self.assertEqual(out["mime_type"], "audio/mpeg")
        self.assertTrue(out["audio_b64"])
        self.assertFalse(out["fallback_browser_tts"])

    def test_speak_provider_failure_does_not_return_a_beep(self):
        def broken(*_a):
            raise RuntimeError("network down")

        vs = VoiceService(db_path=self.db, tts_provider_fn=broken)
        out = vs.speak("u1", text="مرحبا")
        self.assertTrue(out["fallback_browser_tts"])
        self.assertEqual(out["audio_b64"], "")
        self.assertIn("network down", out["tts_error"])

    def test_settings_migrate_legacy_voice(self):
        vs = VoiceService(db_path=self.db)
        vs.update_settings("u2", voice_id="ar-EG-SalmaExpressive")
        self.assertEqual(vs.get_settings("u2")["preferences"]["voice_id"], "ar-EG-SalmaNeural")
        self.assertEqual(vs.get_settings("brand-new")["preferences"]["voice_id"], "ar-SY-LaithNeural")


if __name__ == "__main__":
    unittest.main()
