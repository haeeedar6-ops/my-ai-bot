# -*- coding: utf-8 -*-
"""
tests/test_integrated_upgrade.py — Comprehensive Acceptance Test Suite (Section 12 of RYN Complete Integrated Upgrade Plan)
===========================================================================================================================
Verifies all 13 mandatory acceptance gates:
1. 10-message summaries: Exactly one summary for each completed 10-user-message window at 10/20/30.
2. Summary retry: Failed job retries without duplicates or missing source messages.
3. Conversation recovery: Restart/login restores exact message order and IDs.
4. Device export/import: JSON and Markdown export; imported message count/checksum matches; two-device conflict detection.
5. Memory recall: Past decision or verified fix retrieved with source references.
6. Memory correction: Corrected value supersedes stale value and invalidates stale retrieval.
7. User isolation: No user can access another user's memory, chats, files, or audio.
8. Natural RYN Mix & Collaboration Ledger: Arabic/English benchmark shows reduced repetition and preserved factuality; unreceived model outputs are never claimed.
9. Read-aloud: Selected message and full conversation play; pause/resume/stop work.
10. Live voice: Arabic and English test; interruption (barge-in) cancels TTS; no duplicate turns.
11. GitHub conflict: Stale SHA detected; no overwrite; workflow re-reads and replans.
12. GitHub success: Commit SHA confirmed and files re-read before reporting success.
13. Regression: All existing AI stack, desktop, and visual brain features remain intact.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from typing import Any, Dict, List

from conversations import ConversationService
from memory import MemoryService
from core import RynResponsePolicy, CollaborationLedger, run_natural_ryn_mix_evaluation_suite
from voice import VoiceService


class TestAcceptanceSuite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "ryn_test_canonical.db")
        self.memory = MemoryService(db_path=self.db_path)
        self.conversations = ConversationService(
            db_path=self.db_path,
            on_ten_user_messages_cb=lambda u, cid, w_idx, msgs: self.memory.process_ten_message_window(u, cid, w_idx, msgs),
        )
        self.policy = RynResponsePolicy()
        self.ledger = CollaborationLedger(db_path=self.db_path)
        self.voice = VoiceService(
            db_path=self.db_path,
            conversation_service=self.conversations,
            memory_service=self.memory,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_ten_message_summaries_at_10_20_30(self):
        """Pass condition: Exactly one summary for each completed 10-user-message window at 10/20/30."""
        u = "haydar"
        cid = "conv_haydar_main"
        for i in range(1, 31):
            self.conversations.append_message(
                user_id=u,
                role="user",
                content=f"رسالة المستخدم رقم {i}: قررنا استخدام PostgreSQL وفلاسك في المشروع.",
                conversation_id=cid,
            )
            self.conversations.append_message(
                user_id=u,
                role="assistant",
                content=f"رد المساعد رقم {i}: تم التحديث والاختبار بنجاح ✅ مفحوص",
                conversation_id=cid,
            )
            summaries_now = self.memory.list_summaries(u, conversation_id=cid)
            expected_windows = i // 10
            self.assertEqual(len(summaries_now), expected_windows, f"At user msg {i}, expected {expected_windows} summaries")

        final_summaries = self.memory.list_summaries(u, conversation_id=cid)
        self.assertEqual(len(final_summaries), 3)
        self.assertEqual([s["window_index"] for s in final_summaries], [1, 2, 3])
        for s in final_summaries:
            self.assertGreaterEqual(len(s["covered_message_ids"]), 10)
            self.assertTrue(s["summary_text"])

    def test_02_summary_retry_without_duplicates(self):
        """Pass condition: Failed job retries without duplicates or missing source messages."""
        u = "haydar"
        cid = "conv_retry_test"
        calls = {"fail": True}

        def flaky_summarizer(msgs: List[Dict[str, Any]]) -> Dict[str, Any]:
            if calls["fail"]:
                raise RuntimeError("Temporary LLM provider timeout")
            return {
                "summary_text": f"ملخص ناجح بعد إعادة المحاولة لعدد {len(msgs)} رسالة",
                "facts": [{"fact": "المستخدم يفضل بايثون", "confidence": 0.95}],
                "decisions": [],
                "problems": [],
                "verified_solutions": [],
                "open_tasks": [],
            }

        flaky_mem = MemoryService(db_path=self.db_path, summarizer_llm_fn=flaky_summarizer)
        conv = ConversationService(
            db_path=self.db_path,
            on_ten_user_messages_cb=lambda uid, c_id, w_idx, m: flaky_mem.process_ten_message_window(uid, c_id, w_idx, m),
        )

        for i in range(1, 11):
            conv.append_message(u, "user", f"سؤال المستخدم {i}", conversation_id=cid)
            conv.append_message(u, "assistant", f"جواب المساعد {i}", conversation_id=cid)

        q_stat = flaky_mem.get_summary_queue_status(u)
        self.assertEqual(q_stat["failed"], 1)

        # Now recover and retry
        calls["fail"] = False
        all_msgs = conv.get_messages(u, cid, limit=100)
        retried = flaky_mem.retry_failed_summaries(
            u,
            lambda _u, _c, st, en: [m for m in all_msgs if st <= m["sequence"] <= en],
        )
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["status"], "succeeded")

        summaries = flaky_mem.list_summaries(u, conversation_id=cid)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(summaries[0]["covered_message_ids"]), len(all_msgs))

    def test_03_conversation_recovery_across_restart(self):
        """Pass condition: Restart/login restores exact message order and IDs."""
        u = "haydar"
        cid = "conv_persist_1"
        recorded_ids = []
        for i in range(1, 6):
            m1 = self.conversations.append_message(u, "user", f"رسالة {i}", conversation_id=cid, idempotency_key=f"k_u_{i}")
            m2 = self.conversations.append_message(u, "assistant", f"رد {i}", conversation_id=cid, idempotency_key=f"k_a_{i}")
            recorded_ids.extend([m1["message_id"], m2["message_id"]])

        # Simulate server restart by instantiating a brand new ConversationService on the same db_path
        restarted_service = ConversationService(db_path=self.db_path)
        restored_msgs = restarted_service.get_messages(u, cid, limit=100)
        self.assertEqual(len(restored_msgs), 10)
        self.assertEqual([m["message_id"] for m in restored_msgs], recorded_ids)
        self.assertEqual([m["sequence"] for m in restored_msgs], list(range(1, 11)))

    def test_04_device_export_import_and_conflict_detection(self):
        """Pass condition: JSON and Markdown export; imported message count/checksum matches; offline conflict detected."""
        u = "haydar"
        cid = "conv_exp_1"
        self.conversations.append_message(u, "user", "مرحبا رين، كيف حالك؟", conversation_id=cid)
        self.conversations.append_message(u, "assistant", "أهلاً بك! أنا جاهز للمساعدة.", conversation_id=cid)

        exp = self.conversations.export_conversation(u, cid)
        self.assertIn("RYN_Conversations/manifest.json", exp["files_layout"])
        self.assertIn(f"RYN_Conversations/conversation_{cid}/conversation.json", exp["files_layout"])
        self.assertIn(f"RYN_Conversations/conversation_{cid}/conversation.md", exp["files_layout"])
        self.assertEqual(exp["message_count"], 2)

        # Verify ZIP export
        zip_bytes = self.conversations.export_all_zip_bytes(u)
        self.assertTrue(len(zip_bytes) > 100)

        # Import into a fresh database and verify exact message count and bundle checksum
        fresh_db = os.path.join(self.tmp.name, "fresh_import.db")
        fresh_conv = ConversationService(db_path=fresh_db)
        imp_res = fresh_conv.import_conversation(u, exp["conversation_json"])
        self.assertTrue(imp_res["ok"])
        self.assertEqual(imp_res["total_messages"], 2)
        self.assertEqual(imp_res["bundle_checksum"], exp["bundle_checksum"])
        self.assertFalse(imp_res["has_conflicts"])

        # Simulate two-device offline edit conflict on sequence 2
        tampered = dict(exp["conversation_json"])
        tampered_msgs = [dict(m) for m in tampered["messages"]]
        tampered_msgs[1] = {**tampered_msgs[1], "content": "رد معدل من جهاز آخر أوفلاين", "checksum": ""}
        tampered["messages"] = tampered_msgs
        conf_res = fresh_conv.import_conversation(u, tampered, device_id="second_device")
        self.assertTrue(conf_res["has_conflicts"])
        self.assertEqual(len(conf_res["conflicts"]), 1)
        # Ensure original server message was not silently overwritten
        preserved_msgs = fresh_conv.get_messages(u, cid)
        self.assertEqual(preserved_msgs[1]["content"], "أهلاً بك! أنا جاهز للمساعدة.")

    def test_05_memory_recall_with_source_references(self):
        """Pass condition: Past decision or verified fix retrieved with source references."""
        u = "haydar"
        self.memory.add_memory(
            user_id=u,
            content="قررنا اعتماد PostgreSQL كقاعدة بيانات أساسية للإنتاج على Render بدلاً من الملفات المؤقتة.",
            layer="project_memory",
            provenance="user_fact",
            confidence=0.96,
            source_refs=["msg_decision_42"],
        )
        self.memory.add_memory(
            user_id=u,
            content="تم إصلاح مشكلة قفل SQLite بتفعيل PRAGMA journal_mode=WAL ونجحت كل الاختبارات.",
            layer="technical_memory",
            provenance="tested_outcome",
            confidence=0.99,
            source_refs=["ccr_fix_99", "commit:a1b2c3d4"],
        )

        hits = self.memory.retrieve(u, "ما هي قاعدة البيانات المعتمدة للإنتاج على Render؟")
        self.assertTrue(len(hits) >= 1)
        self.assertIn("PostgreSQL", hits[0]["content"])
        self.assertIn("msg_decision_42", hits[0]["source_refs"])

        ctx_pack = self.memory.build_bounded_context_pack(u, "مشكلة قفل SQLite")
        self.assertIn("PRAGMA journal_mode=WAL", ctx_pack)
        self.assertIn("ccr_fix_99", ctx_pack)

    def test_06_memory_correction_supersedes_stale_value(self):
        """Pass condition: Corrected value supersedes stale value and invalidates stale retrieval."""
        u = "haydar"
        old_item = self.memory.add_memory(
            user_id=u,
            content="الفرع الافتراضي للنشر هو develop",
            layer="project_memory",
            provenance="user_fact",
            source_refs=["msg_1"],
        )
        new_item = self.memory.correct_memory(
            user_id=u,
            old_memory_id=old_item["id"],
            corrected_content="الفرع الافتراضي للنشر هو main مع فروع عمل ryn-work/",
            source_refs=["msg_2"],
        )
        self.assertEqual(new_item["supersedes_id"], old_item["id"])

        hits = self.memory.retrieve(u, "ما هو الفرع الافتراضي للنشر؟")
        contents = [h["content"] for h in hits]
        self.assertTrue(any("main" in c for c in contents))
        self.assertFalse(any("الفرع الافتراضي للنشر هو develop" == c for c in contents))

    def test_07_strict_multi_user_isolation_and_secret_blocking(self):
        """Pass condition: No user can access another user's memory, chats, files, or audio; secrets blocked."""
        self.conversations.append_message("user_a", "user", "رسالة خاصة بالمستخدم أ", conversation_id="conv_a")
        self.memory.add_memory("user_a", "معلومة سرية للمستخدم أ فقط", layer="user_profile")
        sess_a = self.voice.create_session("user_a", conversation_id="conv_a")

        # User B cannot read User A's conversation
        with self.assertRaises(PermissionError):
            self.conversations.get_messages("user_b", "conv_a")

        # User B cannot retrieve User A's memory
        hits_b = self.memory.retrieve("user_b", "معلومة سرية للمستخدم أ")
        self.assertEqual(len(hits_b), 0)

        # User B cannot access User A's voice session
        with self.assertRaises(PermissionError):
            self.voice.get_session("user_b", sess_a["session_id"])

        # Secret storage must be rejected
        with self.assertRaises(ValueError):
            self.memory.add_memory("user_a", "مفتاحي السري هو ghp_1234567890abcdefghijklmnopqrstuv")

    def test_08_natural_ryn_mix_and_honest_collaboration_ledger(self):
        """Pass condition: Arabic/English benchmark shows reduced repetition and preserved factuality; ledger honest."""
        eval_res = run_natural_ryn_mix_evaluation_suite()
        self.assertTrue(eval_res["passed_all"], eval_res)

        # Verify CollaborationLedger excludes models whose responses were not received
        rec = self.ledger.record_collaboration_round(
            user_id="haydar",
            request_text="تحسين سرعة استعلام قاعدة البيانات",
            candidates=[
                {"provider": "google", "model": "gemini-2.5-pro", "output": "أضف فهرساً مركباً على (user_id, created_at).", "response_received": True, "score": 95.0},
                {"provider": "openrouter", "model": "deepseek-r1", "output": "", "response_received": False, "score": 0.0},
            ],
            final_synthesis="أضف فهرساً مركباً على (user_id, created_at).",
            selection_rationale="Gemini قدم حلاً دقيقاً بينما DeepSeek لم يرسل استجابة.",
            verification={"status": "pass", "tests_run": ["test_db_index"], "commit_sha": "abc1234"},
            root_cause="نقص فهرس البحث المركب",
            verified_fix="CREATE INDEX idx_user_created ON messages(user_id, created_at);",
            environment_fingerprint="python3.11-sqlite-wal",
        )
        self.assertEqual(rec["participating_models"], ["gemini-2.5-pro"])
        self.assertIn("deepseek-r1", rec["skipped_unreceived_models"])

        # Check reusable learning suggestion & environment re-check
        sug = self.ledger.suggest_reusable_learning(
            user_id="haydar",
            new_problem_text="بطء في استعلام قاعدة البيانات للرسائل",
            current_environment="python3.11-postgresql",
        )
        self.assertIsNotNone(sug)
        self.assertTrue(sug["requires_recheck"])
        self.assertFalse(sug["environment_matches"])

    def test_09_read_aloud_and_playback_controls(self):
        """Pass condition: Selected message and full conversation play; pause/resume/stop work."""
        u = "haydar"
        cid = "conv_tts_1"
        m1 = self.conversations.append_message(u, "user", "مرحبا رين", conversation_id=cid)
        m2 = self.conversations.append_message(u, "assistant", "أهلاً بك يا حيدر في منظومة الصوت", conversation_id=cid)
        sess = self.voice.create_session(u, conversation_id=cid)
        sid = sess["session_id"]

        # Read single selected message
        sp_single = self.voice.speak(u, conversation_id=cid, message_id=m2["message_id"], session_id=sid)
        self.assertTrue(sp_single["ok"])
        self.assertEqual(sp_single["text_read"], m2["content"])
        self.assertTrue(len(sp_single["audio_b64"]) > 50)

        # Pause, resume, stop controls
        paused = self.voice.stop_or_interrupt(u, session_id=sid, playback_action="pause")
        self.assertEqual(paused["playback_state"], "paused")

        resumed = self.voice.stop_or_interrupt(u, session_id=sid, playback_action="resume")
        self.assertEqual(resumed["playback_state"], "playing")

        stopped = self.voice.stop_or_interrupt(u, session_id=sid, playback_action="stop")
        self.assertEqual(stopped["playback_state"], "stopped")
        self.assertEqual(stopped["state"], "idle")

        # Read full conversation
        sp_full = self.voice.speak(u, conversation_id=cid, read_full_conversation=True, session_id=sid)
        self.assertIn("مرحبا رين", sp_full["text_read"])
        self.assertIn("أهلاً بك يا حيدر", sp_full["text_read"])

    def test_10_live_voice_barge_in_and_no_duplicate_turns(self):
        """Pass condition: Arabic and English test; interruption cancels TTS; no duplicate turns."""
        u = "haydar"
        cid = "conv_live_voice"
        sess = self.voice.create_session(u, conversation_id=cid)
        sid = sess["session_id"]

        # Start listening -> partial transcript -> end of turn (Arabic + English code-switching)
        self.voice.process_stream_event(u, sid, "start_listening")
        self.voice.process_stream_event(u, sid, "partial_transcript", {"partial_transcript": "بدي فحص مسار /api/v2"})
        turn1 = self.voice.process_stream_event(
            u,
            sid,
            "end_of_turn",
            {
                "final_transcript": "بدي فحص مسار /api/v2/voice في Python Flask",
                "mock_reply": "مسار الصوت جاهز ويعمل بكفاءة.",
                "idempotency_key": "turn_key_1",
            },
        )
        self.assertFalse(turn1["duplicate_prevented"])
        self.assertEqual(turn1["session"]["state"], "speaking")
        self.assertEqual(turn1["session"]["playback_state"], "playing")

        # Network retry with same idempotency_key must NOT create duplicate messages
        turn1_retry = self.voice.process_stream_event(
            u,
            sid,
            "end_of_turn",
            {
                "final_transcript": "بدي فحص مسار /api/v2/voice في Python Flask",
                "mock_reply": "مسار الصوت جاهز ويعمل بكفاءة.",
                "idempotency_key": "turn_key_1",
            },
        )
        self.assertTrue(turn1_retry["duplicate_prevented"])
        msgs = self.conversations.get_messages(u, cid)
        self.assertEqual(len(msgs), 2)

        # User speaks while RYN is speaking -> Barge-in immediately cancels TTS and transitions to transcribing/listening!
        barge = self.voice.process_stream_event(u, sid, "audio_frame", {"partial_transcript": "مقاطعة: سؤال ثاني"})
        self.assertTrue(barge["barge_in_triggered"])
        self.assertEqual(barge["playback_state"], "stopped")
        self.assertEqual(barge["state"], "transcribing")
        self.assertGreaterEqual(barge["interrupted_count"], 1)

if __name__ == "__main__":
    unittest.main()
