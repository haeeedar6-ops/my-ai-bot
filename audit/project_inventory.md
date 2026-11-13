# RYN Project Inventory (`haeeedar6-ops/my-ai-bot`)

## 1. Architecture & Entry Points
- **Web & Core AI Runtime**:
  - `bot.py`: Core AI routing engine (`ask_gemini`, `ask_model`, `ask_mix`, document parsing) and Render WSGI entry point (`gunicorn bot:app`) calling `mount_web()` at module load time (Telegram bot completely removed).
  - `webapp.py`: Flask web application, REST APIs (`/api/v2/github/*`, `/api/v2/memory/*`, `/api/v2/preflight/status`), and Progressive Web App UI with local-first memory selector.
  - `ryn_ai_stack.py`: `GitHubFileManager` with 7-step Read-After-Write Verification transaction (`Prepare -> Validate -> Apply -> Commit -> Read-Back -> Verify Commit -> Report`) and `apply_chat_github_actions` write-verification guard.
  - `ryn_guard.py`: Security sanitizer, prompt-injection defense, and binary header validator (`validate_image_upload`).
- **Modular Subsystems**:
  - `memory/__init__.py`: Device-local persistent SQLite + [SQLite FTS5](https://www.sqlite.org/fts5.html) full-text search + on-device hybrid embeddings (`HybridEmbeddingEngine`) + `LocalMemoryCipher` (`PBKDF2-HMAC-SHA256` + `SHA256-CTR` + `HMAC-SHA256`) + encrypted backup/restore.
  - `code_intelligence/__init__.py`: `GitHubEngineeringService`, AST/syntax validation, unified diff generation, and atomic branch/PR operations via [GitHub REST API](https://docs.github.com/en/rest/repos/contents).
  - `ryn-desktop/`: Windows desktop companion agent (`run_desktop.py`, `app/action_executor.py`, `app/api_client.py`, `app/safety.py`, `app/task_loop.py`).
  - `ryn-android/`: Android native voice & assistant client (`VoiceController.kt`).
