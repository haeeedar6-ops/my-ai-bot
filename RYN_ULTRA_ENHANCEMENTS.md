# RYN UI and RYN Ultra Enhancements

## Changes
- Refreshed the main RYN and RYN Ultra visual styling with a darker emerald/green palette, glass-like panels, improved spacing, and smoother control transitions.
- Added an Arabic/English interface toggle to the main RYN page and a separate Arabic/English toggle to RYN Ultra.
- Strengthened the shared RYN system instruction so every model follows the language of the latest user request and switches language when the user does.
- Added explicit per-task language instructions to RYN Ultra agents and the final synthesis.
- Fixed the RYN Ultra multi-round debate prompt: the previous implementation emitted the literal `{transcript}` placeholder instead of inserting the actual conversation. Later-round agents now receive the actual recorded discussion and are instructed to address other agents' specific points.
- Expanded agent response guidance to avoid one-line contributions and require substantive, direct engagement.
- Made Ultra transcript message bodies explicitly preserve line breaks and automatic text direction for Arabic/English mixed content.
- Added optional subtle UI sound cues to RYN Ultra, with a user-controlled mute toggle. Existing RYN voice/TTS functionality remains unchanged.

## Validation
- Python compilation: passed for `bot.py`, `webapp.py`, and `ryn_ultra.py`.
- JavaScript syntax checks: passed for the main RYN page and RYN Ultra page.
- Full automated test suite: 187 passed.

## Notes
- API language behavior depends on the configured model/provider following the system instruction; no translation API is added.
- Ultra agent discussion is model-generated and sequentially orchestrated. It is not simulated, but responses are displayed after each provider call completes rather than token-by-token streaming.
- The sound toggle controls subtle interface sound effects, not synthesized spoken narration. Existing RYN voice features are preserved.


## Review follow-up (Nov 2026)
Fixed after an independent review of the RYN_Ultra_RYN_UI_Updated package (each item has a regression test in `tests/test_ryn_fixes.py`, `tests/memory_harness.js`):
- Security: login throttle no longer trusts a client-supplied `X-Forwarded-For` (`TRUSTED_PROXY_HOPS`, plus a global failure cap); session secret is random/persisted instead of derived from passwords; `/ultra` now sends the same security headers as the main page.
- RYN Ultra: oversized project context no longer pushes the debate out of the prompt; lost "another round" during the final report; status never `running`; stop overwritten by completion; offline-agent commands piling up; bad env values breaking agents; fallback-model attempts capped; duplicated events on reconnect and stuck UI after an error ending.
- `webapp.py`: undefined `has_gh_action_tags` (silently disabled response cleaning and the Ryn mix participants line); `/api/v2/preflight/status` now reads the recorded baseline and warns about missing `WEB_SECRET` / `RYN_DATA_DIR`.
- Models: dead Gemini/Groq/OpenRouter IDs removed or de-prioritised, live filtering and dead-name memory for Gemini, current Live model first.
- New: device memory shared by the whole site (`ryn_memory.js`) — all models and the five Ultra agents.
- Test suite: 221 Python tests + 45 JS assertions.
