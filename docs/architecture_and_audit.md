# RYN Complete Integrated Upgrade — Architecture, API Contracts, Operations & File Audit Map

## 1. Unified Architecture Overview ("One RYN — One Brain — One Memory")
All modalities and subsystems share the same authenticated server-derived `user_id`, `conversation_id`, canonical database (`RYN_DB_PATH` / `DATABASE_URL`), memory service, model router, and audit trail:
- **`core/`**: Unified RYN identity & response policy (`casual_chat`, `technical_support`, `coding`, `research`, `writing`), natural turn-taking filter, Arabic/Syrian Arabic & English evaluation suite, and the `CollaborationLedger` (`model_contributions` & `model_collaboration_issues`).
- **`conversations/`**: Canonical message store (`conversations`, `messages`, `conversation_conflicts`) with stable IDs, sequence numbers, SHA-256 checksums, idempotency keys, two-device conflict detection, and portable `RYN_Conversations/` export/import bundles.
- **`memory/`**: Exact 10-user-message window summarizer (`summary_windows`, `summaries` at 10/20/30 user messages) + 5-layer Long-Term Memory (`user_profile`, `project_memory`, `technical_memory`, `conversation_memory`, `knowledge_memory`) with hybrid exact keyword + semantic vector search (`HybridEmbeddingEngine`), provenance tracking, secret filtering, and superseding corrections.
- **`voice/`**: Full duplex audio experience (`voice_sessions`, `voice_assets`, `user_voice_settings`) with state machine (`idle -> listening -> transcribing -> thinking -> speaking -> idle`), immediate barge-in interruption, read-aloud (`speak`) for single messages or full conversations, pause/resume/stop controls, and VibeVoice / Arabic TTS configuration.
- **`code_intelligence/`**: Reliable 12-step GitHub engineering workflow (`code_change_requests`) with 7 independent review passes, isolated work branches (`ryn-work/`), stale SHA conflict detection, explicit approval gates, post-commit re-read verification, and technical memory recording.

## 2. Reconciled Environment Variables (No Secret Values Stored in Git)
- `DATABASE_URL` / `RYN_DB_PATH` / `RYN_DATA_DIR`: Canonical database connection or persistent disk path.
- `MEMORY_PROVIDER` / `EMBEDDING_MODEL` / `VECTOR_STORE_URL`: Embedding and vector index configuration.
- `ASR_PROVIDER` / `ASR_MODEL` / `ASR_LANGUAGE`: Speech-to-text configuration (compatible with `GROQ_STT_MODEL` and `STT_LANGUAGE`).
- `TTS_PROVIDER` / `TTS_MODEL` / `TTS_DEFAULT_VOICE`: Text-to-speech configuration.
- `AUDIO_STORAGE_BUCKET` / `AUDIO_RETENTION_DAYS`: Audio asset storage and retention policy.
- `VOICE_FEATURE_ENABLED` / `LIVE_VOICE_ENABLED`: Independent feature flags to disable voice without affecting text chat or memory.
- `GITHUB_TOKEN` / `GITHUB_REPO` / `GITHUB_BRANCH` / `GITHUB_WORK_BRANCH_PREFIX` / `GITHUB_REQUIRE_APPROVAL`: GitHub engineering and branch protection settings.
- `RYN_LOG_LEVEL` / `RYN_METRICS_ENABLED`: Observability and logging controls.

## 3. File-Level Audit Map (`KEEP / MODIFY / ADD / DEPRECATE`)

| File / Module | Action | Reason | Regression / Acceptance Test |
| :--- | :--- | :--- | :--- |
| `webapp.py` | **MODIFY** | Preserve all existing routes (`/api/chat`, `/api/transcribe`, `/api/v1/agent/*`, `/api/github/*`, `/api/image/*`); connect `/api/chat` to canonical `ConversationService`, `MemoryService`, and `RynResponsePolicy`; add `/api/v2/*` endpoints and UI/IndexedDB controls. | `tests/test_integrated_upgrade.py` & `ryn_selftest.py` |
| `ryn_ai_stack.py` | **MODIFY** | Preserve all 10 existing AI stack components; integrate `ConversationService`, `MemoryService`, `RynResponsePolicy`, `CollaborationLedger`, `GitHubEngineeringService`, and `VoiceService`; add stale SHA conflict check in `GitHubFileManager.save_file`. | `tests/test_ai_stack.py` & `tests/test_integrated_upgrade.py` |
| `conversations/__init__.py` | **ADD** | Canonical server-side message store, stable IDs, sequence numbers, checksums, idempotency, conflict detection, and `RYN_Conversations/` export/import layout. | `TestAcceptanceSuite.test_03_conversation_recovery` & `test_04_device_export_import` |
| `memory/__init__.py` | **ADD** | Exact 10-user-message window summarizer (10/20/30), safe retry queue, 5-layer long-term memory, hybrid keyword + vector retrieval, secret redaction, and superseding corrections. | `TestAcceptanceSuite.test_01_ten_message_summaries`, `test_02_summary_retry`, `test_05_memory_recall`, `test_06_memory_correction` |
| `core/__init__.py` | **ADD** | Unified RYN response policy (5 modes, natural turn-taking), `CollaborationLedger` (honest model contribution recording), and Arabic/Syrian Arabic & English benchmark. | `TestAcceptanceSuite.test_08_natural_ryn_mix_and_ledger` |
| `voice/__init__.py` | **ADD** | `/api/v2/voice/*` session state machine, live streaming turns, barge-in cancellation, TTS read-aloud, pause/resume/stop, and privacy/feature-flag controls. | `TestAcceptanceSuite.test_09_read_aloud` & `test_10_live_voice_and_barge_in` |
| `code_intelligence/__init__.py` | **ADD** | 12-step GitHub workflow, 7 review passes, `ryn-work/` branch isolation, stale SHA conflict detection, approval gates, and post-commit re-read verification. | `TestAcceptanceSuite.test_11_github_conflict` & `test_12_github_success` |
| `ryn-desktop/app/*` | **MODIFY** | Reconcile missing desktop client modules (`safety.py`, `capture.py`, `api_client.py`, `task_loop.py`, `action_executor.py`, `pairing.py`, `voice.py`, `local_config.py`, `main.py`) with `ui.py` and `tests/test_desktop_runner.py`. | `tests/test_desktop_runner.py` |
| `bot.py`, `filegen.py`, `ryn_agent.py`, `ryn_guard.py`, `ryn_selftest.py`, `visual_brain/*`, `image_providers/*`, `image_processing/*`, `ryn-android/*` | **KEEP** | Working foundations preserved intact without breaking changes. | `tests/test_generation.py`, `tests/test_editing.py`, `tests/test_identity.py`, `tests/test_quality.py` |
| `my-ai-bot-main (30).zip` | **DEPRECATE** | Nested historical archive audited separately and removed from final deployment package so repository stays clean. | `tests/test_integrated_upgrade.py` |
