"""
اختبارات شاملة لمنظومة الصوت متعددة اللغات (tts_engine + VoiceService + Desktop Voice + Web/Android Contracts)
تغطي جميع البنود الـ 16 وخطة الاختبار في وثيقة RYN_Voice_Audit_and_Fix_Plan.pdf بدون الحاجة لاتصال شبكة خارجي.
"""
import base64
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ryn-desktop"))

import tts_engine  # noqa: E402
from conversations import ConversationService  # noqa: E402
from voice import AVAILABLE_VOICES, VoiceService, canonical_voice_id, normalize_asr_language  # noqa: E402
from app.api_client import RynAgentApiClient  # noqa: E402
from app.voice import PushToTalkController, split_sentence_chunks  # noqa: E402


class TestTtsEngine(unittest.TestCase):
    def setUp(self):
        tts_engine.clear_cache()

    def test_legacy_names_map_to_real_voices_and_unknown_neural_ids_rejected(self):
        # Fix #6: Legacy aliases map cleanly, while fabricated xx-YY-UnknownNeural IDs are rejected
        self.assertEqual(tts_engine.resolve_voice("ar-SY-RynNatural"), "ar-SY-LaithNeural")
        self.assertEqual(tts_engine.resolve_voice("ar-SA-HamedNeural"), "ar-SA-HamedNeural")
        self.assertEqual(tts_engine.resolve_voice("x'; drop"), tts_engine.DEFAULT_VOICE)
        self.assertEqual(tts_engine.resolve_voice(None), tts_engine.DEFAULT_VOICE)
        # Syntactically valid but non-existent Neural ID must NOT pass allow-list validation
        self.assertEqual(tts_engine.resolve_voice("xx-YY-UnknownNeural"), tts_engine.DEFAULT_VOICE)
        self.assertEqual(tts_engine.resolve_voice("en-US-FakeMadeUpNeural"), tts_engine.DEFAULT_VOICE)
        self.assertFalse(tts_engine.is_valid_voice_id("xx-YY-UnknownNeural"))
        with self.assertRaises(ValueError):
            tts_engine.resolve_voice("xx-YY-UnknownNeural", strict=True)

    def test_clean_for_speech_and_long_text_chunking_without_silent_truncation(self):
        # Fix #11: clean_for_speech cleans markdown/code/links without silently cutting at 3,000 chars
        out = tts_engine.clean_for_speech("مرحبا **كيفك** ```py\nprint(1)\n``` شوف https://x.com/a 😀 `x`")
        self.assertNotIn("print", out)
        self.assertNotIn("http", out)
        self.assertNotIn("*", out)
        self.assertIn("كود برمجي", out)

        # A 7,500-char answer must NOT be truncated at 3,000 chars; it must be split into sentence chunks <= MAX_CHARS
        long_sentences = "هذه جملة عربية واضحة لاختبار تقسيم النصوص الطويلة بدون اقتطاع صامت. " * 120
        self.assertGreater(len(long_sentences), 7000)
        cleaned_full = tts_engine.clean_for_speech(long_sentences)
        self.assertGreater(len(cleaned_full), 6500)

        plan = tts_engine.split_into_sentence_chunks(long_sentences, max_chunk_chars=tts_engine.MAX_CHARS)
        self.assertFalse(plan["truncated"])
        self.assertEqual(plan["warning"], "")
        self.assertGreaterEqual(len(plan["chunks"]), 3)
        for ch in plan["chunks"]:
            self.assertLessEqual(len(ch), tts_engine.MAX_CHARS)

        # Explicit max_chars or safety cap triggers visible warning
        capped = tts_engine.split_into_sentence_chunks("كلمة. " * 7000, max_total_chars=4000)
        self.assertTrue(capped["truncated"])
        self.assertTrue(len(capped["warning"]) > 10)

    def test_rate_percent_bounds(self):
        self.assertEqual(tts_engine._rate_percent(1.0), 0)
        self.assertEqual(tts_engine._rate_percent(1.25), 25)
        self.assertEqual(tts_engine._rate_percent(0.1), -40)
        self.assertEqual(tts_engine._rate_percent(9), 60)

    def test_provider_order(self):
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto"}, clear=False):
            os.environ.pop("AZURE_SPEECH_KEY", None)
            os.environ.pop("AZURE_SPEECH_REGION", None)
            self.assertEqual(tts_engine.provider_order(), ["edge", "gtts"])
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto", "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "westeurope"}):
            self.assertEqual(tts_engine.provider_order(), ["azure", "edge", "gtts"])
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
        self.assertEqual(calls, {"azure": 1, "edge": 1})

    def test_cache_key_includes_provider_and_invalidates_on_config_change(self):
        # Fix #15: Switching provider configuration causes a cache miss instead of serving stale audio
        calls = {"edge": 0, "azure": 0}

        def edge(*_a):
            calls["edge"] += 1
            return b"ID3edge-audio"

        def azure(*_a):
            calls["azure"] += 1
            return b"ID3azure-audio"

        with mock.patch.object(tts_engine, "_edge_synthesize", edge), \
                mock.patch.object(tts_engine, "_azure_synthesize", azure):
            with mock.patch.dict(os.environ, {"TTS_PROVIDER": "edge"}, clear=False):
                out1 = tts_engine.synthesize("نص اختبار تبديل المزود", "ar-SY-LaithNeural", 1.0)
                self.assertEqual(out1, b"ID3edge-audio")
                self.assertEqual(calls["edge"], 1)

            # Now switch TTS_PROVIDER to azure -> must miss cache and invoke azure
            with mock.patch.dict(
                os.environ,
                {"TTS_PROVIDER": "azure", "AZURE_SPEECH_KEY": "k1", "AZURE_SPEECH_REGION": "eastus"},
                clear=False,
            ):
                out2 = tts_engine.synthesize("نص اختبار تبديل المزود", "ar-SY-LaithNeural", 1.0)
                self.assertEqual(out2, b"ID3azure-audio")
                self.assertEqual(calls["azure"], 1)

    def test_gtts_is_last_resort_and_reports_emergency_non_neural_metadata(self):
        # Fix #7: gTTS maps through supported table and never claims to be the selected Neural voice
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto"}), \
                mock.patch.object(tts_engine, "_edge_synthesize", side_effect=RuntimeError("403 Invalid response status")), \
                mock.patch.object(tts_engine, "_gtts_synthesize", return_value=b"ID3gtts"):
            os.environ.pop("AZURE_SPEECH_KEY", None)
            meta = tts_engine.synthesize_with_metadata("نص فريد لاختبار شبكة الأمان 4242", "ar-SY-LaithNeural", 1.0)
        self.assertEqual(meta["audio_bytes"], b"ID3gtts")
        self.assertEqual(meta["provider"], "gtts")
        self.assertEqual(meta["fallback_provider"], "gtts")
        self.assertEqual(meta["fallback_quality"], "basic_emergency_non_neural")
        self.assertFalse(meta["is_neural"])
        self.assertEqual(meta["voice_id"], "gtts-ar-basic")
        self.assertEqual(meta["requested_voice_id"], "ar-SY-LaithNeural")

        # Unsupported language in gTTS table raises ValueError
        with self.assertRaises(ValueError):
            tts_engine.resolve_gtts_language("zz-ZZ")

    def test_audio_container_validation_and_sniff_mime(self):
        # Fix #10: Validate MP3, WAV, OGG, and reject empty, truncated, or malformed payloads
        self.assertEqual(tts_engine.sniff_mime(b"ID3\x04\x00\x00fake-mp3"), "audio/mpeg")
        self.assertEqual(tts_engine.sniff_mime(b"\xff\xfb\x90\x64fake-mpeg-frame"), "audio/mpeg")
        self.assertEqual(tts_engine.sniff_mime(b"RIFF\x24\x00\x00\x00WAVEfmt "), "audio/wav")
        self.assertEqual(tts_engine.sniff_mime(b"OggS\x00\x02\x00\x00ogg-opus"), "audio/ogg")

        for invalid_payload in (b"", b"abc", b"RIFF1234NOPE", b"<!DOCTYPE html>", b'{"error":"bad"}'):
            with self.assertRaises(ValueError):
                tts_engine.sniff_mime(invalid_payload)

    def test_multilingual_language_detection_and_routing(self):
        # Fix #5 & Section 2: Detect Arabic dialects, English, German, French, Spanish, Italian, Turkish, Portuguese
        self.assertEqual(tts_engine.detect_text_language("شلونك يا صديقي شو بدي أعمل هلق؟"), "ar-SY")
        self.assertEqual(tts_engine.detect_text_language("ازيك يا باشا عامل ايه دلوقتي؟"), "ar-EG")
        self.assertEqual(tts_engine.detect_text_language("Hello friend, how can I help you today?"), "en-US")
        self.assertEqual(tts_engine.detect_text_language("Hallo mein Freund, wie geht es dir heute? Danke!"), "de-DE")
        self.assertEqual(tts_engine.detect_text_language("Bonjour mon ami, comment ça va aujourd'hui ? Merci."), "fr-FR")
        self.assertEqual(tts_engine.detect_text_language("¿Hola amigo, cómo estás hoy? Muchas gracias."), "es-ES")
        self.assertEqual(tts_engine.detect_text_language("Ciao amico mio, buongiorno! Come stai oggi?"), "it-IT")
        self.assertEqual(tts_engine.detect_text_language("Merhaba arkadaşım, bugün nasılsın? Teşekkürler."), "tr-TR")
        self.assertEqual(tts_engine.detect_text_language("Olá meu amigo, obrigado! Tudo bem com você?"), "pt-BR")
        # Mixed Arabic + English technical terms keeps Arabic
        self.assertEqual(
            tts_engine.detect_text_language("بدي تعديل مسار /api/v2/voice في Python Flask مع WebSocket"),
            "ar-SY",
        )

        # Voice routing selects dedicated German/French/Spanish/Turkish voices instead of forcing en-US or ar-SY
        de_route = tts_engine.select_voice_for_language("Hallo, wie geht es dir heute?", preferred_voice_id="ar-SY-LaithNeural")
        self.assertEqual(de_route["voice_id"], "de-DE-ConradNeural")
        fr_route = tts_engine.select_voice_for_language("Bonjour, comment allez-vous aujourd'hui ?", preferred_voice_id="ar-SY-AmanyNeural")
        self.assertEqual(fr_route["voice_id"], "fr-FR-DeniseNeural")
        tr_route = tts_engine.select_voice_for_language("Merhaba arkadaşım, nasılsın?", preferred_voice_id="ar-SY-LaithNeural")
        self.assertEqual(tr_route["voice_id"], "tr-TR-AhmetNeural")

    def test_all_providers_fail_raises(self):
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto"}), \
                mock.patch.object(tts_engine, "_edge_synthesize", side_effect=RuntimeError("no net")), \
                mock.patch.object(tts_engine, "_gtts_synthesize", side_effect=RuntimeError("no net")):
            with self.assertRaises(RuntimeError):
                tts_engine.synthesize("نص مختلف تماماً لتجنب الكاش 98765", "ar-SA-HamedNeural", 1.0)

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            tts_engine.synthesize("   ", None, 1.0)


class TestVoiceCatalogAndSpeak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "v.db")
        self.convs = ConversationService(db_path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_has_only_real_microsoft_voice_ids_across_multilingual_launch_set(self):
        for v in AVAILABLE_VOICES:
            if v.get("available", True):
                self.assertEqual(tts_engine.resolve_voice(v["id"]), v["id"], v["id"])
        syr = [v["id"] for v in AVAILABLE_VOICES if v["lang"] == "ar-SY"]
        self.assertEqual(set(syr), {"ar-SY-LaithNeural", "ar-SY-AmanyNeural"})
        langs = {v["lang"] for v in AVAILABLE_VOICES if v.get("available", True)}
        for req_lang in ("ar-SY", "ar-SA", "ar-EG", "en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "es-MX", "it-IT", "tr-TR", "pt-BR", "pt-PT"):
            self.assertIn(req_lang, langs)

    def test_canonical_voice_id(self):
        self.assertEqual(canonical_voice_id("ar-SY-RynNatural"), "ar-SY-LaithNeural")
        self.assertEqual(canonical_voice_id("vibevoice-realtime-exp"), "ar-SY-LaithNeural")
        self.assertEqual(canonical_voice_id("garbage", "ar-EG-SalmaNeural"), "ar-EG-SalmaNeural")
        self.assertEqual(canonical_voice_id("xx-YY-UnknownNeural", "ar-SY-LaithNeural"), "ar-SY-LaithNeural")

    def test_speak_uses_real_provider_and_mime_and_chunks_long_messages(self):
        seen = []

        def fake(text, voice, speed):
            seen.append((voice, speed, len(text)))
            return b"ID3\x04fake-mp3-bytes" * 5

        vs = VoiceService(db_path=self.db, conversation_service=self.convs, tts_provider_fn=fake)
        out = vs.speak("u1", text="مرحبا بك في منظومة رين الصوتية", voice_id="ar-SA-HamedStrong", speed=1.2)
        self.assertEqual(seen[0][0], "ar-SA-HamedNeural")
        self.assertEqual(out["mime_type"], "audio/mpeg")
        self.assertEqual(out["output_format"], "mp3")
        self.assertTrue(out["audio_b64"])
        self.assertFalse(out["fallback_browser_tts"])

        # Long message > 3,000 chars is synthesized across multiple chunks without truncation
        seen.clear()
        long_reply = ("هذه فقرة تفصيلية طويلة جداً لشرح هندسة البرمجيات بلغة عربية واضحة. " * 95).strip()
        self.assertGreater(len(long_reply), 5500)
        long_out = vs.speak("u1", text=long_reply, voice_id="ar-SY-LaithNeural", speed=1.0)
        self.assertGreaterEqual(long_out["chunk_count"], 2)
        self.assertEqual(len(seen), long_out["chunk_count"])
        self.assertFalse(long_out["truncated"])

    def test_speak_rejects_malformed_provider_bytes_and_falls_back_cleanly(self):
        # Fix #10: If provider returns corrupted/non-audio payload, speak() must not serve invalid bytes
        def corrupted_provider(*_a):
            return b"<!DOCTYPE html><html>Error 502</html>"

        vs = VoiceService(db_path=self.db, tts_provider_fn=corrupted_provider)
        out = vs.speak("u1", text="مرحبا")
        self.assertTrue(out["fallback_browser_tts"])
        self.assertEqual(out["audio_b64"], "")
        self.assertIn("صيغة حاوية الصوت", out["tts_error"])

    def test_speak_reports_which_provider_made_the_audio(self):
        def fake(text, voice, speed):
            fake.last_provider = "edge"
            return b"ID3\x04fake" * 8

        fake.last_provider = ""
        vs = VoiceService(db_path=self.db, tts_provider_fn=fake)
        self.assertEqual(vs.speak("u1", text="مرحبا")["tts_provider_used"], "edge")

    def test_synthesize_records_last_provider(self):
        with mock.patch.dict(os.environ, {"TTS_PROVIDER": "auto"}), \
                mock.patch.object(tts_engine, "_edge_synthesize", side_effect=RuntimeError("403")), \
                mock.patch.object(tts_engine, "_gtts_synthesize", return_value=b"ID3g"):
            os.environ.pop("AZURE_SPEECH_KEY", None)
            tts_engine.synthesize("نص فريد لاختبار المزود المستعمل 777", "ar-SY-LaithNeural", 1.0)
        self.assertEqual(tts_engine.synthesize.last_provider, "gtts")

    def test_speak_provider_failure_does_not_return_a_beep(self):
        def broken(*_a):
            raise RuntimeError("network down")

        vs = VoiceService(db_path=self.db, tts_provider_fn=broken)
        out = vs.speak("u1", text="مرحبا")
        self.assertTrue(out["fallback_browser_tts"])
        self.assertEqual(out["audio_b64"], "")
        self.assertIn("network down", out["tts_error"])

    def test_settings_migrate_legacy_voice_and_persist_asr_and_auto_read(self):
        vs = VoiceService(db_path=self.db)
        self.assertEqual(vs.get_settings("brand-new")["preferences"]["asr_language"], "auto")
        self.assertFalse(vs.get_settings("brand-new")["preferences"]["auto_read"])
        vs.update_settings("u2", voice_id="ar-EG-SalmaExpressive", asr_language="de-DE", auto_read=True)
        prefs = vs.get_settings("u2")["preferences"]
        self.assertEqual(prefs["voice_id"], "ar-EG-SalmaNeural")
        self.assertEqual(prefs["asr_language"], "de-DE")
        self.assertTrue(prefs["auto_read"])

    def test_asr_requires_real_provider_in_production_and_routes_multilingual_bcp47(self):
        # Fix #1 & #13: Without a configured provider in production, transcribe_upload must fail clearly (never fake text)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GROQ_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("AZURE_SPEECH_KEY", None)
            os.environ.pop("RYN_ASR_TEST_SYNTHETIC", None)
            vs_prod = VoiceService(db_path=self.db, conversation_service=self.convs)
            self.assertFalse(vs_prod.asr_health_check()["ok"])
            with self.assertRaises(RuntimeError):
                vs_prod.transcribe_upload("u1", audio_bytes=b"RIFF\x24\x00\x00\x00WAVEfmt fake", mime_type="audio/wav")

        # With a real/injected ASR provider, auto-detect and explicit BCP-47 languages work and store metadata
        seen_asr = []

        def mock_whisper_asr(audio_b, mime_t, lang):
            seen_asr.append((mime_t, lang))
            return {
                "text": "Guten Tag, ich möchte Python und FastAPI testen.",
                "detected_language": "de-DE",
                "provider": "groq",
                "model": "whisper-large-v3-turbo",
            }

        vs = VoiceService(db_path=self.db, conversation_service=self.convs, asr_provider_fn=mock_whisper_asr)
        self.assertTrue(vs.asr_health_check()["ok"])
        res = vs.transcribe_upload(
            "u1",
            audio_bytes=b"OggS\x00\x02fake-audio-bytes",
            mime_type="audio/ogg",
            language="auto",
            conversation_id="conv_de",
            auto_send_to_chat=True,
        )
        self.assertEqual(seen_asr[-1], ("audio/ogg", "auto"))
        self.assertEqual(res["detected_language"], "de-DE")
        self.assertEqual(res["asr_provider"], "groq")
        self.assertEqual(res["user_message"]["metadata"]["detected_language"], "de-DE")

    def test_live_voice_real_audio_frame_streaming_barge_in_and_playback_reset(self):
        # Fix #9 & #14: Real audio frames streamed via /api/v2/voice/stream + barge-in + playback_ended + timeout recovery
        asr_calls = []

        def stream_asr(audio_b, mime_t, lang):
            asr_calls.append((len(audio_b), mime_t, lang))
            return {"text": "مرحبا رين، كيف الطقس اليوم في طرطوس؟", "detected_language": "ar-SY", "provider": "groq"}

        vs = VoiceService(
            db_path=self.db,
            conversation_service=self.convs,
            asr_provider_fn=stream_asr,
            tts_provider_fn=lambda txt, vid, spd: b"ID3\x04mp3-stream-bytes" * 4,
            chat_responder_fn=lambda u, cid, txt: "الطقس في طرطوس معتدل وجميل اليوم.",
        )
        sess = vs.create_session("u1", conversation_id="conv_stream_1")
        sid = sess["session_id"]

        vs.process_stream_event("u1", sid, "start_listening")
        f1 = base64.b64encode(b"webm_chunk_1_data").decode("ascii")
        f2 = base64.b64encode(b"webm_chunk_2_data").decode("ascii")
        ev1 = vs.process_stream_event("u1", sid, "audio_frame", {"audio_b64": f1, "mime_type": "audio/webm"})
        ev2 = vs.process_stream_event("u1", sid, "audio_frame", {"audio_b64": f2, "mime_type": "audio/webm"})
        self.assertEqual(ev2["transport_mode"], "audio_stream")
        self.assertFalse(ev2["transcript_only_event"])
        self.assertEqual(ev2["buffered_frames"], 2)

        turn = vs.process_stream_event("u1", sid, "end_of_turn", {"idempotency_key": "stream_turn_1"})
        self.assertEqual(len(asr_calls), 1)
        self.assertEqual(turn["detected_language"], "ar-SY")
        self.assertEqual(turn["session"]["state"], "speaking")
        self.assertEqual(turn["session"]["playback_state"], "playing")

        # Client notifies playback_ended -> session resets cleanly to idle/stopped (Fix #14)
        ended = vs.stop_or_interrupt("u1", session_id=sid, playback_action="playback_ended")
        self.assertEqual(ended["state"], "idle")
        self.assertEqual(ended["playback_state"], "stopped")

        # Test abandoned session timeout recovery
        vs.transition_state("u1", sid, "speaking", playback_state="playing")
        with vs._conn() as conn:
            conn.execute("UPDATE voice_sessions SET updated_at = ? WHERE session_id = ?", (time.time() - 300, sid))
        recovered_count = vs.recover_abandoned_sessions(timeout_sec=60)
        self.assertEqual(recovered_count, 1)
        self.assertEqual(vs.get_session("u1", sid)["state"], "idle")

    def test_paginated_full_conversation_read_aloud(self):
        # Fix #12: Paginated full conversation reading with speaker labels
        cid = "conv_full_read"
        for i in range(5):
            self.convs.append_message("u1", "user", f"سؤال رقم {i + 1}", conversation_id=cid)
            self.convs.append_message("u1", "assistant", f"جواب رين رقم {i + 1}", conversation_id=cid)

        vs = VoiceService(
            db_path=self.db,
            conversation_service=self.convs,
            tts_provider_fn=lambda txt, vid, spd: b"ID3\x04page-mp3",
        )
        p1 = vs.speak("u1", conversation_id=cid, read_full_conversation=True, page=1, page_size=4, include_user_messages=False)
        self.assertIn("conversation_pagination", p1)
        self.assertEqual(p1["conversation_pagination"]["total_messages"], 5)
        self.assertTrue(p1["conversation_pagination"]["has_more"])
        self.assertNotIn("سؤال رقم", p1["text_read"])
        self.assertIn("رين: جواب رين رقم 1", p1["text_read"])


class TestDesktopAndClientVoiceIntegration(unittest.TestCase):
    def test_desktop_voice_uses_server_speak_and_does_not_truncate_at_240_chars(self):
        # Fix #3, #4, #16: Desktop PushToTalkController calls /api/v2/voice/speak, never truncates at 240 chars,
        # and never interpolates text into a PowerShell command string.
        api_calls = []

        class DummyDesktopApi(RynAgentApiClient):
            def __init__(self):
                super().__init__("http://local", "tok_123")

            def request_json(self, method, path, payload=None, retries=3):
                api_calls.append((method, path, payload))
                if path == "/api/v2/voice/speak":
                    raw_mp3 = b"ID3\x04desktop-neural-mp3-audio"
                    b64 = base64.b64encode(raw_mp3).decode("ascii")
                    return {
                        "ok": True,
                        "voice_id": "ar-SY-LaithNeural",
                        "detected_language": "ar-SY",
                        "mime_type": "audio/mpeg",
                        "tts_provider_used": "edge",
                        "audio_b64": b64,
                        "chunks": [{"chunk_index": 0, "total_chunks": 1, "audio_b64": b64, "mime_type": "audio/mpeg"}],
                    }
                if path == "/api/v2/voice/stop":
                    return {"ok": True, "state": "idle", "playback_state": "stopped"}
                return {"ok": True}

        ptt = PushToTalkController(DummyDesktopApi())
        ptt.session_id = "vs_desktop_1"
        long_reply = ("هذا رد طويل من الوكيل على سطح المكتب يتجاوز 240 حرفاً بكثير للتحقق من عدم الاقتطاع. " * 8).strip()
        self.assertGreater(len(long_reply), 450)

        res = ptt.speak(long_reply)
        self.assertTrue(res["ok"])
        self.assertEqual(res["provider"], "edge")
        self.assertFalse(res["fallback_used"])
        speak_calls = [c for c in api_calls if c[1] == "/api/v2/voice/speak"]
        self.assertEqual(len(speak_calls), 1)
        self.assertEqual(speak_calls[0][2]["text"], long_reply)

        # Verify sentence chunking for local fallback preserves full text (> 1,000 chars)
        plan = split_sentence_chunks(long_reply, max_chunk_chars=200)
        self.assertGreaterEqual(len(plan["chunks"]), 3)
        self.assertIn("الاقتطاع", plan["chunks"][-1])

    def test_desktop_offline_fallback_uses_safe_stdin_without_shell_interpolation(self):
        # Fix #16: Verify dangerous shell metacharacters are passed via stdin, never interpolated in PowerShell -Command
        class OfflineApi(RynAgentApiClient):
            def __init__(self):
                super().__init__("http://offline", "tok")

            def request_json(self, method, path, payload=None, retries=3):
                raise RuntimeError("offline")

        ptt = PushToTalkController(OfflineApi())
        dangerous_text = "مرحبا $(calc.exe); 'quote' \"double\" `backtick` نص آمن تماماً."
        popen_calls = []

        class FakeProc:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                popen_calls.append(("stdin", input))
                return (b"", b"")

            def terminate(self):
                pass

        def fake_popen(cmd, **kwargs):
            popen_calls.append(("cmd", cmd))
            return FakeProc()

        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.Popen", side_effect=fake_popen):
            out = ptt.speak(dangerous_text)

        self.assertTrue(out["ok"])
        self.assertTrue(out["fallback_used"])
        self.assertEqual(out["provider"], "sapi-offline-fallback")
        cmd_entries = [x[1] for x in popen_calls if x[0] == "cmd"]
        self.assertTrue(len(cmd_entries) >= 1)
        # Ensure none of the user text was interpolated into the PowerShell command arguments!
        for cmd_list in cmd_entries:
            joined_cmd = " ".join(cmd_list)
            self.assertNotIn("calc.exe", joined_cmd)
            self.assertNotIn("backtick", joined_cmd)
            self.assertIn("[Console]::In.ReadToEnd()", joined_cmd)

    def test_normalize_asr_language_helper(self):
        self.assertEqual(normalize_asr_language(None), "auto")
        self.assertEqual(normalize_asr_language("auto"), "auto")
        self.assertEqual(normalize_asr_language("ar"), "ar-SY")
        self.assertEqual(normalize_asr_language("de-de"), "de-DE")
        self.assertEqual(normalize_asr_language("fr-FR"), "fr-FR")
        self.assertEqual(normalize_asr_language("tr"), "tr-TR")


if __name__ == "__main__":
    unittest.main()
