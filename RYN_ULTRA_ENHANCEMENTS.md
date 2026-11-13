# RYN UI and RYN Ultra Enhancements (Full Emerald & Debate Upgrade)

## Key Improvements
1. **Full Multi-Agent Debate Visibility (`ryn_ultra.html` & `ryn_ultra.py`)**:
   - Fixed CSS flexbox vertical clipping (`flex-shrink: 0` on `.msg` and `overflow: visible` on `.msg-card` / `.msg-content`) so long agent responses are never cut off.
   - Raised default output token limit (`RYN_ULTRA_MAX_OUTPUT_TOKENS=5000`) and transcript context window (`12,000` chars per turn).
   - Added an **Expand Debate View (`⤢ توسيع النقاش`)** mode, a **Full Message Reader Modal (`⤢ عرض كامل`)**, **One-Click Copy (`📋 نسخ`)** on every message and final report, `@Agent` mention highlighting (`@Gemini`, `@GPT`, `@Qwen`, `@DeepSeek`, `@Kimi`), round badges (`تحليل مستقل` vs `نقاش ومواجهة 💬`), and smart auto-scroll with a floating `⬇ عرض الردود الجديدة` button.
   - Parallelized Round 1 independent analysis via `ThreadPoolExecutor` and optimized SSE event streaming with `threading.Condition` + SQLite WAL connection reuse.
   - Upgraded ZIP project inspection to summarize large files up to `2.5 MB` (head + structural outline of routes/functions/classes + tail) and prioritize core code ahead of tests/docs.
2. **Strict Dynamic Language Matching Across ALL Models (`bot.py`, `webapp.py`, `core/__init__.py`, `ryn_ultra.py`)**:
   - Added `detect_message_language_rule` and `_is_arabic_query` (ignoring code blocks and URLs) so every model (`Gemini`, `GPT-OSS`, `Qwen`, `DeepSeek`, `Kimi`, `Llama`, `Codestral`, `Ryn mix`, and `RYN Ultra`) replies in English/foreign languages when the user writes in a foreign language, and in Arabic when the user writes in Arabic.
   - Localized injected memory and response-policy prompts to match the user's message language so Arabic system headers never cause foreign-language queries to receive Arabic replies.
3. **Unified Emerald Glass UI & Full Bilingual Translation (`webapp.py` & `ryn_ultra.html`)**:
   - Merged the three CSS layers in `ryn_ultra.html` into one unified emerald stylesheet and disabled offline agents in the target selector (`syncTargetOptions`).
   - Set the emerald theme (`green`) as default in `webapp.py`, updated robot SVG gradients, and expanded `applyRynUiLanguage` to translate all modals, placeholders, tooltips, and controls across the entire interface.
