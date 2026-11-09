# -*- coding: utf-8 -*-
import os, sqlite3, tempfile, unittest
from unittest import mock

import tts_engine
import voice


class TestPaidProviderLock(unittest.TestCase):
    def test_azure_never_in_default_order_even_with_keys(self):
        env = {"TTS_PROVIDER": "auto", "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("TTS_ALLOW_PAID", None)
            self.assertNotIn("azure", tts_engine.provider_order())

    def test_azure_only_last_after_opt_in(self):
        env = {"TTS_PROVIDER": "auto", "TTS_ALLOW_PAID": "1", "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(tts_engine.provider_order()[-1], "azure")


class TestDialectRegistry(unittest.TestCase):
    def test_all_nine_dialects_present(self):
        self.assertEqual(len(tts_engine.DIALECT_REGISTRY), 9)

    def test_voice_ids_exist_in_catalog(self):
        for key in tts_engine.DIALECT_REGISTRY:
            cap = tts_engine.get_dialect_capability(key)
            self.assertIn(cap["voice_id"], tts_engine.ALLOWED_VOICE_IDS)

    def test_fallback_dialects_disclosed(self):
        for key in ("palestinian", "iraqi", "moroccan"):
            cap = tts_engine.get_dialect_capability(key)
            self.assertTrue(cap["approximate"])
            self.assertTrue(cap["note"])

    def test_default_is_syrian_male(self):
        cap = tts_engine.get_dialect_capability(None)
        self.assertEqual(cap["voice_id"], "ar-SY-LaithNeural")
        self.assertEqual(tts_engine.ALLOWED_VOICE_IDS[cap["voice_id"]]["gender"], "male")

    def test_nothing_claimed_verified_without_listening_test(self):
        for v in tts_engine.DIALECT_REGISTRY.values():
            self.assertFalse(v["listening_verified"])


class TestVoiceStatesAndConnections(unittest.TestCase):
    def test_roadmap_states_available(self):
        for st in ("idle", "listening", "speaking", "interrupted", "reconnecting", "error"):
            self.assertIn(st, voice.VOICE_STATES)

    def test_conn_context_closes_connection(self):
        with tempfile.TemporaryDirectory() as d:
            svc = voice.VoiceService(db_path=os.path.join(d, "v.db"))
            with svc._conn() as conn:
                conn.execute("SELECT 1")
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
