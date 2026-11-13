# Known Issues & Pre-Flight Repair Log

All 20 baseline issues and all Phase 0–3 requirements from `RYN_Preflight_Repair_Local_Memory_GitHub_Write_Plan.pdf` have been resolved:

1. **Telegram Bot Removal & Render Web Mounting (`bot.py`, `requirements.txt`)**:
   - Removed `telebot`, `BOT_TOKEN`, `ENABLE_TELEGRAM`, `@bot.message_handler`, `run_polling()`, and `pytelegrambotapi`.
   - Added `mount_web()` called at module import time so `gunicorn bot:app` mounts `webapp.py` automatically.
   - Removed accidental nested archive `my-ai-bot-main (30).zip`.
2. **GitHub Read-After-Write Verification (`ryn_ai_stack.py`, `code_intelligence/__init__.py`)**:
   - Implemented the 7-step verified write transaction with stale SHA detection (`CONFLICT`), no-op patch rejection (`NO_OP`), protected-branch fallback (`ryn/ai-run-<id>` + Draft PR -> `NEEDS_ATTENTION`), and post-write read-back verification (`SAVED_AND_VERIFIED`).
   - Added `Write-Verification Guard` in `apply_chat_github_actions` to prevent unverified LLM edit claims.
3. **Device-Local Long-Term Memory (`memory/__init__.py`, `webapp.py`)**:
   - Added SQLite FTS5 (`memory_fts`) + local hybrid embeddings (`HybridEmbeddingEngine`), `LocalMemoryCipher` encryption for sensitive records, encrypted backup/restore, secret blocking, and zero server memory persistence by default (`RYN_SERVER_MEMORY_PERSISTENCE=0`).
4. **Desktop, Upload Guard & Android Voice Fixes**:
   - Added `validate_image_upload` in `ryn_guard.py`.
   - Fixed HTTP 5xx retry & non-JSON error handling in `ryn-desktop/app/api_client.py`, Win64 `SendInput` struct alignment & Arabic Unicode typing in `ryn-desktop/app/action_executor.py`, Arabic sensitive keyword blocking in `ryn-desktop/app/safety.py`, `IDLE` / `NEEDS_USER_INPUT` handling in `ryn-desktop/app/task_loop.py`, and `ryn-desktop/build.spec` + `requirements.txt`.
   - Fixed fallback TTS race condition in `ryn-android/app/src/main/java/com/ryn/agent/VoiceController.kt`.


## RYN Ultra implementation notes
- RYN Ultra is an additive feature; it does not replace RYN Mix, Visual Brain, voice, or the existing chat routes.
- The first implementation is intentionally read-only for project files. ZIP inspection creates a bounded text context only; there is no automatic write, delete, shell, deployment, or GitHub patch execution route in this feature.
- Live meeting updates are streamed as completed agent/event messages over SSE. Individual token-by-token streaming is not implemented in this release.
- The five roles require their corresponding model families to be configured and accessible. A meeting can start with at least two available agents; unavailable agents are visibly marked offline rather than simulated.
