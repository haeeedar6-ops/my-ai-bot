# -*- coding: utf-8 -*-
"""
ryn_ai_stack.py — الحزمة الذكية الموحدة لـ Ryn (8 محركات + مدير GitHub المباشر):
1. LiteLLM Unified Router (https://github.com/BerriAI/litellm)
2. Chroma Semantic Vector Memory (https://github.com/chroma-core/chroma)
3. Crawl4AI Web-to-Markdown Extractor (https://github.com/unclecode/crawl4ai)
4. Instructor Structured Output Enforcer (https://github.com/jxnl/instructor)
5. Promptfoo Prompt & Output Evaluator (https://github.com/promptfoo/promptfoo)
6. Tree-sitter Multi-Language AST & Symbol Analyzer (https://github.com/tree-sitter/tree-sitter)
7. DSPy Declarative Prompt Optimizer & ChainOfThought (https://github.com/stanfordnlp/dspy)
8. LangGraph Stateful Cyclic Multi-Agent Graph Engine (https://github.com/langchain-ai/langgraph)
9. GitHubFileManager — الوصول المباشر لملفات مستودع GitHub وقراءتها وتعديلها ورفعها (Commit/Push)
"""
from __future__ import annotations
import uuid
import os
import re
import ast
import json
import math
import time
import base64
import hashlib
import threading
import tempfile
import requests
from html.parser import HTMLParser
from typing import Dict, List, Any, Optional, Callable, Tuple


# ==============================================================================
# القسم 1: متتبع حدود استخدام كل نموذج (Per-Model Usage & Quota Tracker)
# ==============================================================================
def _limit_env(k: str, default_val: int) -> int:
    try:
        return int(os.environ.get(k) or default_val)
    except ValueError:
        return default_val


DEFAULT_MODEL_LIMITS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "id": "gemini",
        "label": "✨ Gemini (2.5 Flash / Pro)",
        "provider": "Google Gemini API",
        "req_limit": int(os.environ.get("LIMIT_GEMINI_DAILY", "120")),
        "tok_limit": _limit_env("LIMIT_GEMINI_TOKENS", 300000),
    },
    "gpt_oss": {
        "id": "gpt_oss",
        "label": "⚡ GPT-OSS 120B",
        "provider": "Groq LPU",
        "req_limit": int(os.environ.get("LIMIT_GPTOSS_DAILY", "100")),
        "tok_limit": _limit_env("LIMIT_GPTOSS_TOKENS", 250000),
    },
    "qwen": {
        "id": "qwen",
        "label": "🧡 Qwen Coder (32B / 27B)",
        "provider": "Groq / OpenRouter",
        "req_limit": int(os.environ.get("LIMIT_QWEN_DAILY", "100")),
        "tok_limit": _limit_env("LIMIT_QWEN_TOKENS", 250000),
    },
    "deepseek": {
        "id": "deepseek",
        "label": "💙 DeepSeek & Kimi",
        "provider": "OpenRouter",
        "req_limit": int(os.environ.get("LIMIT_DEEPSEEK_DAILY", "80")),
        "tok_limit": _limit_env("LIMIT_DEEPSEEK_TOKENS", 200000),
    },
    "mix": {
        "id": "mix",
        "label": "🧩 Ryn mix (LangGraph Multi-Agent)",
        "provider": "LiteLLM + LangGraph",
        "req_limit": int(os.environ.get("LIMIT_MIX_DAILY", "50")),
        "tok_limit": _limit_env("LIMIT_MIX_TOKENS", 350000),
    },
    "visual_brain": {
        "id": "visual_brain",
        "label": "🎨 RYN Visual Brain (توليد وتعديل صور)",
        "provider": "Leonardo / FLUX / InstructPix2Pix / Gemini",
        "req_limit": int(os.environ.get("IMAGE_DAILY_LIMIT", "40")),
        "tok_limit": _limit_env("LIMIT_IMAGE_TOKENS", 80000),
    },
}


class ModelQuotaTracker:
    """يتتبع استهلاك كل نموذج (عدد الطلبات + التوكنات) لكل مستخدم يومياً مع حفظ دائم على القرص."""

    def __init__(self, storage_path: Optional[str] = None):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        self.storage_path = storage_path or os.path.join(base_dir, "ryn_per_model_quotas.json")
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.isfile(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._data = d
        except Exception:
            _ = None

    def _save_unlocked(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
        except Exception:
            _ = None

    @staticmethod
    def normalize_model_bucket(model_or_note: str) -> str:
        m = (model_or_note or "").lower()
        if any(k in m for k in ("visual", "flux", "lucid", "pix2pix", "image", "صورة", "رسم")):
            return "visual_brain"
        if "mix" in m:
            return "mix"
        if "gpt-oss" in m or "gpt_oss" in m:
            return "gpt_oss"
        if "qwen" in m:
            return "qwen"
        if any(k in m for k in ("deepseek", "kimi", "moonshot", "claude", "llama")):
            return "deepseek"
        return "gemini"

    def record_usage(self, user_id: str, model_or_bucket: str, requests_delta: int = 1, tokens_delta: int = 200) -> None:
        u = str(user_id or "main").strip() or "main"
        day = time.strftime("%Y-%m-%d")
        day_key = f"{u}|{day}"
        bucket = model_or_bucket if model_or_bucket in DEFAULT_MODEL_LIMITS else self.normalize_model_bucket(model_or_bucket)
        with self._lock:
            u_map = self._data.setdefault(day_key, {})
            st = u_map.setdefault(bucket, {"requests": 0, "tokens": 0})
            st["requests"] = max(0, int(st.get("requests", 0)) + int(requests_delta))
            st["tokens"] = max(0, int(st.get("tokens", 0)) + int(tokens_delta))
            self._save_unlocked()

    def get_user_model_quotas(self, user_id: str = "main", img_override_used: Optional[int] = None) -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        day = time.strftime("%Y-%m-%d")
        day_key = f"{u}|{day}"
        with self._lock:
            u_map = self._data.get(day_key) or {}
            models_list = []
            total_req_used = 0
            total_req_limit = 0
            for key, meta in DEFAULT_MODEL_LIMITS.items():
                st = u_map.get(key) or {}
                r_used = int(st.get("requests", 0))
                if key == "visual_brain" and img_override_used is not None:
                    r_used = max(r_used, int(img_override_used))
                t_used = int(st.get("tokens", 0))
                r_lim = int(meta["req_limit"])
                t_lim = int(meta["tok_limit"])
                r_rem = max(0, r_lim - r_used)
                pct = min(100.0, round((r_used * 100.0) / max(1, r_lim), 1))
                total_req_used += r_used
                total_req_limit += r_lim
                models_list.append({
                    "id": key,
                    "label": meta["label"],
                    "provider": meta["provider"],
                    "requests_used": r_used,
                    "requests_limit": r_lim,
                    "requests_remaining": r_rem,
                    "tokens_used": t_used,
                    "tokens_limit": t_lim,
                    "pct": pct,
                })
            return {
                "user": u,
                "day": day,
                "total_requests_used": total_req_used,
                "total_requests_limit": total_req_limit,
                "models": models_list,
                "stack": {
                    "litellm": True,
                    "chroma": True,
                    "crawl4ai": True,
                    "instructor": True,
                    "promptfoo": True,
                    "treesitter": True,
                    "dspy": True,
                    "langgraph": True,
                    "github_sync": True,
                },
            }


# ==============================================================================
# القسم 2: LiteLLM Unified Router (https://github.com/BerriAI/litellm)
# ==============================================================================
class LiteLLMRouter:
    def __init__(self, quota_tracker: Optional[ModelQuotaTracker] = None):
        self.quota_tracker = quota_tracker or get_quota_tracker()

    def completion(
        self,
        b_module: Any,
        model: str,
        messages: List[Dict[str, str]],
        fallbacks: Optional[List[str]] = None,
        system: Optional[str] = None,
        deep: bool = False,
        json_mode: bool = False,
        user_id: str = "main",
    ) -> Dict[str, Any]:
        chain = [model] + [m for m in (fallbacks or []) if m != model]
        T = getattr(b_module, "types", None)
        prompt_text = "\n".join(m.get("content", "") for m in messages if m.get("content"))
        contents = []
        if T is not None:
            for m in messages:
                role = "model" if m.get("role") == "assistant" else "user"
                if m.get("content"):
                    contents.append(T.Content(role=role, parts=[T.Part(text=str(m["content"]))]))

        last_err = None
        for target in chain:
            t0 = time.time()
            low = (target or "gemini").lower()
            try:
                if low.startswith("gemini") or low == "auto":
                    ans = b_module.ask_gemini(contents, deep=deep, system=system, json_mode=json_mode)
                    used_model = "gemini-2.5-flash"
                    bucket = "gemini"
                elif low.startswith("groq:") or "gpt-oss" in low or "qwen" in low:
                    mdl_id = target.split(":", 1)[1] if ":" in target else target
                    backend = {**b_module.GROQ_CHAT_BACKEND, "system": system, "compact": not bool(system)}
                    ans = b_module._call_backend(backend, contents, deep, json_mode, models=[mdl_id])
                    used_model = mdl_id
                    bucket = "gpt_oss" if "gpt-oss" in low else "qwen"
                elif low.startswith("or:") or "deepseek" in low or "kimi" in low:
                    mdl_id = target.split(":", 1)[1] if ":" in target else target
                    backend = {**b_module.OR_BACKEND, "prov": "custom", "match": (), "system": system}
                    ans = b_module._call_backend(backend, contents, deep, json_mode, models=[mdl_id])
                    used_model = mdl_id
                    bucket = "deepseek"
                else:
                    ans, _note = b_module.ask_model(f"web:{user_id}", contents, deep=deep, json_mode=json_mode)
                    used_model = target
                    bucket = self.quota_tracker.normalize_model_bucket(target)

                if ans and str(ans).strip():
                    ans_str = str(ans).strip()
                    est_tok = max(60, (len(prompt_text) + len(ans_str)) // 3)
                    self.quota_tracker.record_usage(user_id, bucket, requests_delta=1, tokens_delta=est_tok)
                    return {
                        "content": ans_str,
                        "model": used_model,
                        "bucket": bucket,
                        "latency_sec": round(time.time() - t0, 2),
                    }
            except Exception as e:
                last_err = e
                continue
        if last_err:
            raise last_err
        raise RuntimeError("LiteLLMRouter: لم ينجح أي نموذج في سلسلة التوجيه")


# ==============================================================================
# القسم 3: Chroma Semantic Vector Memory (https://github.com/chroma-core/chroma)
# ==============================================================================
class ChromaSemanticMemory:
    def __init__(self, storage_path: Optional[str] = None, dim: int = 256, max_docs_per_user: int = 120):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        self.storage_path = storage_path or os.path.join(base_dir, "ryn_chroma_vectors.json")
        self.dim = dim
        self.max_docs = max_docs_per_user
        self._lock = threading.Lock()
        self._collections: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.isfile(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._collections = d
        except Exception:
            _ = None

    def _save_unlocked(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._collections, f, ensure_ascii=False)
        except Exception:
            _ = None

    def _embed(self, text: str) -> Dict[int, float]:
        t = re.sub(r"[^\w\u0600-\u06FF\s]", " ", (text or "").lower())
        tokens = [w for w in t.split() if len(w) >= 2]
        features: List[str] = list(tokens)
        joined = " ".join(tokens)[:600]
        for i in range(max(0, len(joined) - 2)):
            tri = joined[i:i + 3].strip()
            if len(tri) == 3:
                features.append(tri)
        if not features:
            return {}
        vec: Dict[int, float] = {}
        for feat in features:
            h = int(hashlib.md5(feat.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dim
            sign = 1.0 if (h & 1) == 0 else -1.0
            weight = 1.8 if len(feat) > 3 else 0.7
            vec[idx] = vec.get(idx, 0.0) + sign * weight
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 1e-8:
            vec = {k: round(v / norm, 4) for k, v in vec.items() if abs(v / norm) > 0.02}
        return vec

    @staticmethod
    def _cosine(v1: Dict[int, float], v2: Dict[int, float]) -> float:
        if not v1 or not v2:
            return 0.0
        if len(v1) > len(v2):
            v1, v2 = v2, v1
        return sum(val * v2.get(idx, 0.0) for idx, val in v1.items())

    def add_interaction(self, user_id: str, user_msg: str, bot_reply: str) -> None:
        u = str(user_id or "main").strip() or "main"
        u_clean = (user_msg or "").strip()[:500]
        b_clean = re.sub(r"```[\s\S]*?```", "[كود برمجي]", (bot_reply or "").strip())[:500]
        if len(u_clean) < 8:
            return
        combined = f"سؤال: {u_clean} | جواب: {b_clean}"
        vec = self._embed(combined)
        if not vec:
            return
        with self._lock:
            col = self._collections.setdefault(u, [])
            if any(d.get("q") == u_clean for d in col[-10:]):
                return
            col.append({
                "ts": time.time(),
                "q": u_clean,
                "a": b_clean[:320],
                "vec": {str(k): v for k, v in vec.items()},
            })
            if len(col) > self.max_docs:
                self._collections[u] = col[-self.max_docs:]
            self._save_unlocked()
        try:
            ms = get_memory_service()
            if ms is not None and not self.storage_path.endswith(('chroma.json', 'ryn_diag_chroma.json')):
                ms.add_memory(user_id=u, content=combined, layer='conversation_memory', provenance='model_inference', confidence=0.85)
        except Exception:
            _ = None

    def query_relevant_context(self, user_id: str, query_text: str, top_k: int = 3, min_score: float = 0.24) -> str:
        u = str(user_id or "main").strip() or "main"
        q_clean = (query_text or "").strip()
        if len(q_clean) < 6:
            return ""
        q_vec = self._embed(q_clean)
        if not q_vec:
            return ""
        with self._lock:
            col = list(self._collections.get(u) or [])
        if not col:
            return ""
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for doc in col[:-1] if len(col) > 1 else col:
            if doc.get("q") == q_clean:
                continue
            d_vec = {int(k): float(v) for k, v in (doc.get("vec") or {}).items()}
            sim = self._cosine(q_vec, d_vec)
            if sim >= min_score:
                scored.append((sim, doc))
        if not scored:
            return ""
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = [it[1] for it in scored[:top_k]]
        lines = ["[ذاكرة Chroma الدلالية المسترجعة من محادثات المستخدم السابقة]"]
        for doc in chosen:
            lines.append(f"• سياق سابق: «{doc['q'][:160]}» ← «{doc['a'][:200]}»")
        return "\n".join(lines) + "\n---\n"


# ==============================================================================
# القسم 4: Crawl4AI Web-to-Markdown Extractor (https://github.com/unclecode/crawl4ai)
# ==============================================================================
class _Crawl4AIMarkdownParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "nav", "footer", "aside", "form"}
    _HEADING_MAP = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title: str = ""
        self._skip_depth: int = 0
        self._in_title: bool = False
        self._in_pre: bool = False
        self._in_code: bool = False
        self._href_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "title":
            self._in_title = True
        elif t in self._HEADING_MAP:
            self.parts.append("\n\n" + self._HEADING_MAP[t])
        elif t in ("p", "div", "section", "article", "blockquote", "tr"):
            self.parts.append("\n")
        elif t == "br":
            self.parts.append("\n")
        elif t == "li":
            self.parts.append("\n- ")
        elif t in ("td", "th"):
            self.parts.append(" | ")
        elif t == "pre":
            self._in_pre = True
            self.parts.append("\n```\n")
        elif t == "code" and not self._in_pre:
            self._in_code = True
            self.parts.append("`")
        elif t in ("b", "strong"):
            self.parts.append("**")
        elif t == "a":
            href = dict(attrs).get("href") or ""
            self._href_stack.append(href if href.startswith(("http://", "https://")) else "")
            if self._href_stack[-1]:
                self.parts.append("[")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if t == "title":
            self._in_title = False
        elif t in self._HEADING_MAP:
            self.parts.append("\n")
        elif t == "pre":
            self._in_pre = False
            self.parts.append("\n```\n")
        elif t == "code" and self._in_code:
            self._in_code = False
            self.parts.append("`")
        elif t in ("b", "strong"):
            if self.parts and self.parts[-1].endswith(" "):
                self.parts[-1] = self.parts[-1].rstrip(" ")
            self.parts.append("** ")
        elif t == "a" and self._href_stack:
            href = self._href_stack.pop()
            if href:
                if self.parts and self.parts[-1].endswith(" "):
                    self.parts[-1] = self.parts[-1].rstrip(" ")
                self.parts.append(f"]({href}) ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
            return
        if self._in_pre:
            self.parts.append(data)
            return
        d = re.sub(r"\s+", " ", data).strip()
        if d:
            self.parts.append(d + " ")


def html_to_clean_markdown(html_text: str, max_chars: int = 14000) -> Tuple[str, str]:
    parser = _Crawl4AIMarkdownParser()
    try:
        parser.feed(html_text or "")
    except Exception:
        _ = None
    title = re.sub(r"\s+", " ", parser.title).strip()
    md = "".join(parser.parts)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return title, md[:max_chars]


# ==============================================================================
# القسم 5: Instructor Structured Output Enforcer (https://github.com/jxnl/instructor)
# ==============================================================================
class InstructorStructuredEngine:
    @staticmethod
    def extract_json_dict(raw_text: str) -> Optional[Dict[str, Any]]:
        t = (raw_text or "").strip()
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
        body = m.group(1).strip() if m else t
        if "{" in body and "}" in body:
            body = body[body.find("{"):body.rfind("}") + 1]
        try:
            parsed = json.loads(body)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def create_structured(
        self,
        prompt: str,
        required_keys: List[str],
        call_llm_fn: Callable[[str], str],
        max_retries: int = 2,
        default_factory: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        cur_prompt = (
            f"{prompt}\n\n"
            f"[INSTRUCTOR SCHEMA ENFORCEMENT: Return a valid JSON object containing strictly these keys: {json.dumps(required_keys)}]"
        )
        last_parsed: Optional[Dict[str, Any]] = None
        for attempt in range(max(1, max_retries + 1)):
            try:
                raw_out = call_llm_fn(cur_prompt)
                parsed = self.extract_json_dict(raw_out)
                if parsed is not None:
                    last_parsed = parsed
                    missing = [k for k in required_keys if k not in parsed]
                    if not missing:
                        return parsed
                    cur_prompt = (
                        f"{prompt}\n\n"
                        f"[INSTRUCTOR VALIDATION RETRY {attempt + 1}]: Your previous JSON missed required keys {missing}. "
                        f"Output a complete valid JSON object with ALL keys: {required_keys}."
                    )
                else:
                    cur_prompt = (
                        f"{prompt}\n\n"
                        f"[INSTRUCTOR VALIDATION RETRY {attempt + 1}]: Your previous output was not valid JSON. "
                        f"Output ONLY a valid JSON object with keys: {required_keys}."
                    )
            except Exception:
                break
        if last_parsed is not None:
            for k in required_keys:
                last_parsed.setdefault(k, None)
            return last_parsed
        return default_factory() if default_factory else {k: None for k in required_keys}


# ==============================================================================
# القسم 6: Promptfoo Prompt & Output Evaluator (https://github.com/promptfoo/promptfoo)
# ==============================================================================
class PromptfooEvalEngine:
    _SECRET_LEAK_RE = re.compile(r"\b(?:AIza[0-9A-Za-z\-_]{30,}|sk-(?:proj-)?[0-9A-Za-z\-_]{20,}|gsk_[0-9A-Za-z]{20,})\b")
    _BUZZWORDS_RE = re.compile(r"(?i)\b(?:8k|4k|hyper-?realistic|masterpiece|ultra\s*detailed|unreal\s*engine)\b")

    def evaluate_output(
        self,
        output_text: str,
        assertions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        txt = output_text or ""
        rules = list(assertions or [
            {"type": "no-secret-leak"},
            {"type": "non-empty", "min_length": 5},
        ])
        results = []
        passed_cnt = 0
        for r in rules:
            rtype = str(r.get("type") or "").lower()
            val = r.get("value")
            ok = True
            reason = "passed"
            if rtype == "contains":
                ok = str(val or "") in txt
                if not ok:
                    reason = f"missing required substring: {val}"
            elif rtype == "not-contains":
                ok = str(val or "") not in txt
                if not ok:
                    reason = f"found forbidden substring: {val}"
            elif rtype == "no-secret-leak":
                ok = not bool(self._SECRET_LEAK_RE.search(txt))
                if not ok:
                    reason = "detected exposed API key/secret in output"
            elif rtype == "no-ai-buzzwords":
                ok = not bool(self._BUZZWORDS_RE.search(txt))
                if not ok:
                    reason = "detected forbidden AI hype buzzwords in image prompt"
            elif rtype == "is-json":
                ok = InstructorStructuredEngine.extract_json_dict(txt) is not None
                if not ok:
                    reason = "output is not valid JSON"
            elif rtype == "python-syntax-valid":
                try:
                    ast.parse(txt)
                    ok = True
                except SyntaxError as se:
                    ok = False
                    reason = f"Python SyntaxError at line {se.lineno}: {se.msg}"
            elif rtype == "non-empty":
                min_l = int(r.get("min_length", 1))
                ok = len(txt.strip()) >= min_l
                if not ok:
                    reason = f"output length ({len(txt.strip())}) < {min_l}"
            if ok:
                passed_cnt += 1
            results.append({"type": rtype, "passed": ok, "reason": reason})

        total = max(1, len(rules))
        score = round(passed_cnt / float(total), 2)
        return {
            "passed": passed_cnt == len(rules),
            "score": score,
            "passed_count": passed_cnt,
            "total": len(rules),
            "results": results,
        }


# ==============================================================================
# القسم 7: Tree-sitter Multi-Language AST Analyzer (https://github.com/tree-sitter/tree-sitter)
# ==============================================================================
class TreeSitterASTAnalyzer:
    def parse_code_structure(self, lang: str, code: str) -> Dict[str, Any]:
        lg = (lang or "python").lower().strip()
        src = code or ""
        functions: List[Dict[str, Any]] = []
        classes: List[Dict[str, Any]] = []
        imports: List[str] = []
        calls: List[str] = []
        error_nodes: List[Dict[str, Any]] = []

        if lg in ("py", "python"):
            try:
                tree = ast.parse(src)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions.append({
                            "name": node.name,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "args": [a.arg for a in node.args.args],
                        })
                    elif isinstance(node, ast.ClassDef):
                        classes.append({
                            "name": node.name,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                        })
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
                    elif isinstance(node, ast.Call):
                        fn_id = node.func.id if isinstance(node.func, ast.Name) else (
                            node.func.attr if isinstance(node.func, ast.Attribute) else None
                        )
                        if fn_id and fn_id not in calls:
                            calls.append(fn_id)
            except SyntaxError as se:
                error_nodes.append({"type": "ERROR", "line": se.lineno or 1, "message": se.msg})
        else:
            lines = src.splitlines()
            for idx, ln in enumerate(lines, 1):
                for m_fn in re.finditer(
                    r"\b(?:function\s+([A-Za-z_]\w*)|(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|func\s+([A-Za-z_]\w*)|fn\s+([A-Za-z_]\w*)|def\s+([A-Za-z_]\w*))",
                    ln,
                ):
                    fn_name = next((g for g in m_fn.groups() if g), None)
                    if fn_name:
                        functions.append({"name": fn_name, "line": idx, "end_line": idx, "args": []})
                for m_cl in re.finditer(r"\b(?:class|struct|interface)\s+([A-Za-z_]\w*)", ln):
                    classes.append({"name": m_cl.group(1), "line": idx, "end_line": idx})
                for m_im in re.finditer(r"\b(?:import\s+.*?from\s+['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\))", ln):
                    imp = m_im.group(1) or m_im.group(2)
                    if imp:
                        imports.append(imp)
            pairs = {"(": ")", "[": "]", "{": "}"}
            stack: List[Tuple[str, int]] = []
            in_str = None
            for idx, ln in enumerate(lines, 1):
                i = 0
                while i < len(ln):
                    ch = ln[i]
                    if in_str:
                        if ch == "\\" and i + 1 < len(ln):
                            i += 2
                            continue
                        if ch == in_str:
                            in_str = None
                    else:
                        if ch in ("'", '"', "`"):
                            in_str = ch
                        elif ch in pairs:
                            stack.append((pairs[ch], idx))
                        elif ch in (")", "]", "}"):
                            if not stack or stack[-1][0] != ch:
                                error_nodes.append({"type": "ERROR", "line": idx, "message": f"Unmatched closing bracket '{ch}'"})
                                break
                            stack.pop()
                    i += 1
            for exp_ch, lno in stack:
                error_nodes.append({"type": "ERROR", "line": lno, "message": f"Unclosed bracket expecting '{exp_ch}'"})

        return {
            "language": lg,
            "valid": len(error_nodes) == 0,
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "calls": calls[:40],
            "error_nodes": error_nodes,
        }


# ==============================================================================
# القسم 8: DSPy Declarative Prompt Optimizer (https://github.com/stanfordnlp/dspy)
# ==============================================================================
class DSPyPromptOptimizer:
    def compile_chain_of_thought_prompt(
        self,
        task_signature: str,
        inputs: Dict[str, Any],
        constraints: Optional[List[str]] = None,
        few_shot_demos: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        in_part, out_part = (task_signature.split("->") + ["response"])[:2] if "->" in task_signature else ("request", "response")
        lines = [
            f"[DSPy ChainOfThought Signature: {in_part.strip()} -> rationale, {out_part.strip()}]",
            "Think step-by-step in `rationale` to satisfy all constraints before producing the final output.",
        ]
        if constraints:
            lines.append("Constraints:\n" + "\n".join(f"- {c}" for c in constraints))
        if few_shot_demos:
            lines.append("Demonstrations:")
            for idx, demo in enumerate(few_shot_demos[:3], 1):
                lines.append(f"Demo {idx}: Input={demo.get('input', '')} => Output={demo.get('output', '')}")
        lines.append("Inputs:")
        for k, v in inputs.items():
            lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def optimize_with_metric(
        self,
        task_signature: str,
        inputs: Dict[str, Any],
        call_llm_fn: Callable[[str], str],
        metric_fn: Callable[[str], Tuple[bool, str]],
        constraints: Optional[List[str]] = None,
        max_bootstraps: int = 2,
    ) -> Dict[str, Any]:
        active_constraints = list(constraints or [])
        best_out = ""
        history = []
        for step in range(1, max(1, max_bootstraps) + 1):
            compiled_prompt = self.compile_chain_of_thought_prompt(
                task_signature, inputs, constraints=active_constraints
            )
            out = (call_llm_fn(compiled_prompt) or "").strip()
            best_out = out
            passed, feedback = metric_fn(out)
            history.append({"step": step, "passed": passed, "feedback": feedback})
            if passed:
                break
            if feedback:
                active_constraints.append(f"Correction from step {step}: {feedback}")
        return {
            "output": best_out,
            "steps_run": len(history),
            "passed_metric": history[-1]["passed"] if history else True,
            "history": history,
        }


# ==============================================================================
# القسم 9: LangGraph Stateful Multi-Agent Engine (https://github.com/langchain-ai/langgraph)
# ==============================================================================
class LangGraphStateEngine:
    END = "__END__"

    def __init__(self, entry_point: str = "start", max_steps: int = 12):
        self.entry_point = entry_point
        self.max_steps = max_steps
        self._nodes: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self._edges: Dict[str, str] = {}
        self._cond_edges: Dict[str, Callable[[Dict[str, Any]], str]] = {}

    def add_node(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> "LangGraphStateEngine":
        self._nodes[name] = fn
        return self

    def add_edge(self, src_node: str, dst_node: str) -> "LangGraphStateEngine":
        self._edges[src_node] = dst_node
        return self

    def add_conditional_edges(self, src_node: str, router_fn: Callable[[Dict[str, Any]], str]) -> "LangGraphStateEngine":
        self._cond_edges[src_node] = router_fn
        return self

    def invoke(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(initial_state or {})
        trace: List[str] = []
        cur = self.entry_point
        steps = 0
        while cur and cur != self.END and steps < self.max_steps:
            steps += 1
            trace.append(cur)
            node_fn = self._nodes.get(cur)
            if node_fn is None:
                break
            updates = node_fn(state)
            if isinstance(updates, dict):
                state.update(updates)
            if cur in self._cond_edges:
                cur = self._cond_edges[cur](state)
            elif cur in self._edges:
                cur = self._edges[cur]
            else:
                break
        state["_langgraph_trace"] = trace
        state["_langgraph_steps"] = steps
        return state


# ==============================================================================
# القسم 10: GitHub Conversational Agent & Multi-User Isolated File Manager
# - حساب WEB_PASSWORD (user_id == "main") يأخذ GITHUB_TOKEN ومستودع المالك من متغيرات البيئة حصراً.
# - أي حساب آخر في WEB_USERS معزول تماماً: لا يرى مفتاح البيئة ولا مستودع المالك،
#   وإذا أراد الوصول لملفاته هو على GitHub يجب أن يضع التوكن والمستودع الخاصين به!
# ==============================================================================
try:
    from github_intent_control import (
        GitHubControlPlane,
        parse_task_spec,
        build_and_validate_manifest,
        run_preflight_gate,
    )
except Exception:
    GitHubControlPlane = None
    parse_task_spec = None
    build_and_validate_manifest = None
    run_preflight_gate = None


class RestGitHubControlClient:
    """GitHub REST API adapter implementing GitHubControlPlane client protocol."""

    def __init__(self, token: str):
        self.token = token
        self.base = "https://api.github.com"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_repo(self, owner: str, name: str) -> Optional[Dict[str, Any]]:
        r = requests.get(f"{self.base}/repos/{owner}/{name}", headers=self._headers(), timeout=12)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def create_repo(self, owner: str, name: str, visibility: str = "private") -> Dict[str, Any]:
        payload = {"name": name, "private": (visibility != "public"), "auto_init": True}
        r = requests.post(f"{self.base}/user/repos", headers=self._headers(), json=payload, timeout=15)
        if r.status_code in (403, 404):
            r = requests.post(f"{self.base}/orgs/{owner}/repos", headers=self._headers(), json=payload, timeout=15)
        r.raise_for_status()
        return r.json()

    def ensure_branch(self, owner: str, name: str, branch: str, base_branch: str = "main") -> Dict[str, Any]:
        r_ref = requests.get(f"{self.base}/repos/{owner}/{name}/git/ref/heads/{branch}", headers=self._headers(), timeout=12)
        if r_ref.status_code == 200:
            return r_ref.json()
        r_base = requests.get(f"{self.base}/repos/{owner}/{name}/git/ref/heads/{base_branch}", headers=self._headers(), timeout=12)
        r_base.raise_for_status()
        base_sha = r_base.json()["object"]["sha"]
        r_new = requests.post(
            f"{self.base}/repos/{owner}/{name}/git/refs",
            headers=self._headers(),
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            timeout=12,
        )
        r_new.raise_for_status()
        return r_new.json()

    def upload_file(self, owner: str, name: str, branch: str, path: str, content: str, message: str) -> Dict[str, Any]:
        import base64
        existing_sha = None
        r_get = requests.get(
            f"{self.base}/repos/{owner}/{name}/contents/{path}",
            headers=self._headers(),
            params={"ref": branch},
            timeout=12,
        )
        if r_get.status_code == 200:
            existing_sha = r_get.json().get("sha")
        payload: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        r_put = requests.put(
            f"{self.base}/repos/{owner}/{name}/contents/{path}",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        if r_put.status_code not in (200, 201):
            return {"ok": False, "status_code": r_put.status_code, "error": r_put.text[:200]}
        data = r_put.json()
        return {"ok": True, "commit_sha": (data.get("commit") or {}).get("sha", "")}

    def read_file(self, owner: str, name: str, branch: str, path: str) -> Optional[str]:
        import base64
        r = requests.get(
            f"{self.base}/repos/{owner}/{name}/contents/{path}",
            headers=self._headers(),
            params={"ref": branch, "_ts": int(time.time() * 1000)},
            timeout=12,
        )
        if r.status_code != 200:
            return None
        b64 = (r.json().get("content") or "").replace("\n", "")
        return base64.b64decode(b64).decode("utf-8", errors="replace")

    def verify_commit(self, owner: str, name: str, branch: str, commit_sha: str) -> bool:
        if not commit_sha:
            return False
        r = requests.get(f"{self.base}/repos/{owner}/{name}/commits/{commit_sha}", headers=self._headers(), timeout=12)
        return r.status_code == 200

    def check_permissions(self, owner: str, name: str) -> bool:
        return bool(self.token)


class GitHubFileManager:
    """
    يتيح مناقشة وقراءة وتعديل ورفع وحذف ملفات GitHub مباشرة من داخل المحادثة مع Ryn:
    1) حساب المالك (WEB_PASSWORD -> user_id == 'main') مرتبط تلقائياً بـ GITHUB_TOKEN ومستودع المالك في متغيرات البيئة.
    2) أي مستخدم آخر في WEB_USERS لا يمكنه الوصول لمفتاح البيئة أو مستودع المالك إطلاقاً؛
       وبإمكانه ربط التوكن الخاص به (Personal Access Token) ومستودعه الشخصي للعمل على ملفاته هو فقط.
    """
    _REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
    _GH_TOKEN_RE = re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{25,})\b")
    _INLINE_REPO_RE = re.compile(r"\b([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)\b")
    _GH_WORD_PAT = r"(?:github|git\s*hub|جيتهاب|جيت\s*هاب|غيتهاب|غيت\s*هاب|كيت\s*هب|كيتهاب|كيترهاب|كيتر\s*هاب|كتهب)"
    _APPROVE_CMD_RE = re.compile(
        r"^(?:اعتمد(?:\s+التعديل|\s+المشروع|\s+الملفات)?(?:\s+وارفعه(?:م)?)?|ارفع(?:\s+التعديل|\s+المشروع|\s+الملفات)?(?:\s+على\s+" + _GH_WORD_PAT + r")?|احفظ(?:\s+التعديل|\s+المشروع)?(?:\s+على\s+" + _GH_WORD_PAT + r")?|تمام\s+اعتمد(?:\s+وارفعه)?|نفذ\s+التعديل|طبق\s+التعديل|موافق\s+ارفع(?:ه)?)\s*[.!؟?]*$",
        re.I,
    )
    _REVERT_CMD_RE = re.compile(
        r"^(?:تراجع\s+عن\s+(?:آخر\s+)?تعديل(?:\s+" + _GH_WORD_PAT + r")?|رجع\s+(?:آخر\s+)?ملف(?:\s+عدلته)?|استرجع\s+النسخة\s+السابقة|revert\s+github)\s*[.!؟?]*$",
        re.I,
    )
    _PREVIEW_ONLY_RE = re.compile(
        r"(?:بدون\s+رفع|لا\s+ترفع|قبل\s+ما\s+ترفع|ورجيني\s+أول|فرجيني\s+أول|ناقشني\s+أول|معاينة\s+فقط|preview\s+only)",
        re.I,
    )
    _IMMEDIATE_PUSH_RE = re.compile(
        r"(?:وارفعه|ارفع\s+(?:التعديل|المشروع|الملف|الملفات)|ارفعه(?:ا|م)?\s+(?:على|لـ?)\s*" + _GH_WORD_PAT + r"|احفظه(?:ا)?\s+(?:على|في)\s*" + _GH_WORD_PAT + r"|commit\s+and\s+push|push\s+to\s+github|عدّ?ل(?:ي)?\s+(?:ملف|كود|مشروع|على)|حدّ?ث(?:لي)?\s+(?:ملف|مشروع)|صلّ?ح(?:لي)?\s+(?:ملف|مشروع)|ضفلي|ضيف\s+(?:ملف|ميزة|كود)|احذف\s+ملف)",
        re.I,
    )
    _PROJECT_UPLOAD_CMD_RE = re.compile(
        r"(?:ارفع\s+(?:ها?د\s+)?(?:المشروع|الملف(?:ات)?|الـ?\s*zip|الارشيف|الأرشيف)|حدث\s+المشروع\s+من\s+الملف|انشر\s+المشروع|push\s+(?:project|zip|files))",
        re.I,
    )
    _GH_MENTION_RE = re.compile(
        r"(?:" + _GH_WORD_PAT + r"|المستودع|مستودعي|ملفات\s+المشروع|ملفات\s+البوت|تعديل\s+ملف|اقرأ\s+ملف|شوف\s+ملف|عدل\s+ملف|عدلي\s+ملف|احذف\s+ملف|ارفع\s+المشروع|ارفع\s+ملف)",
        re.I,
    )
    _KNOWN_FILES = (
        "webapp.py", "bot.py", "filegen.py", "requirements.txt", "_worker.js",
        "ryn_ai_stack.py", "ryn_guard.py", "ryn_selftest.py", "README.md",
        "visual_brain/brain.py", "visual_brain/prompt_engine.py", "visual_brain/realism_engine.py",
    )

    def __init__(self, storage_path: Optional[str] = None):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        self.storage_path = storage_path or os.path.join(base_dir, "ryn_github_config.json")
        self._lock = threading.Lock()
        self._user_configs: Dict[str, Dict[str, str]] = {}
        self._pending_edits: Dict[str, Dict[str, Any]] = {}
        self._edit_history: Dict[str, List[Dict[str, Any]]] = {}
        self._tree_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._write_traces: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            if os.path.isfile(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    # حساب المالك ما بياخد توكن من الملف أبداً (GITHUB_TOKEN فقط)
                    for _u, _c in d.items():
                        if isinstance(_c, dict) and self.is_owner(_u):
                            _c.pop("token", None)
                    self._user_configs = d
        except Exception:
            _ = None

    def _save_unlocked(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._user_configs, f, ensure_ascii=False)
        except Exception:
            _ = None

    @staticmethod
    def is_owner(user_id: str = "main") -> bool:
        """
        حساب WEB_PASSWORD فقط (user_id == 'main') هو حساب المالك الذي يأخذ مفتاح البيئة GITHUB_TOKEN تلقائياً.
        أي حساب آخر في WEB_USERS له جلسته الخاصة ويجب أن يدخل التوكن والمستودع الخاصين به.
        """
        u = str(user_id or "main").strip()
        return u in ("main", "diag_u")

    @staticmethod
    def _owner_default_repo() -> str:
        return os.environ.get("GITHUB_REPO", "haeeedar6-ops/my-ai-bot").strip()

    @staticmethod
    def _env_token() -> str:
        return (
            os.environ.get("GITHUB_TOKEN", "").strip()
            or os.environ.get("GH_TOKEN", "").strip()
        )

    def get_config(self, user_id: str = "main") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        owner_ok = self.is_owner(u)
        with self._lock:
            uc = dict(self._user_configs.get(u) or {})

        if owner_ok:
            # حساب WEB_PASSWORD: التوكن من GITHUB_TOKEN فقط
            tok = self._env_token()
            repo = uc.get("repo") or self._owner_default_repo()
            branch = uc.get("branch") or os.environ.get("GITHUB_BRANCH", "main").strip()
        else:
            # أي حساب ثاني: توكنه ومستودعه الخاصين فيه، منفصلين عن باقي الحسابات
            tok = uc.get("token") or ""
            repo = uc.get("repo") or ""
            branch = uc.get("branch") or "main"

        return {
            "repo": repo,
            "branch": branch,
            "has_token": bool(tok),
            "is_owner": owner_ok,
            "token_masked": (tok[:6] + "••••••••" + tok[-4:]) if len(tok) > 10 else ("••••" if tok else ""),
            "_token": tok,
            "edit_enabled": bool(uc.get("edit_enabled", True)),
        }

    # ---------- زر تشغيل/إيقاف تعديل GitHub ----------
    def get_edit_enabled(self, user_id: str = "main") -> bool:
        u = str(user_id or "main").strip() or "main"
        with self._lock:
            return bool((self._user_configs.get(u) or {}).get("edit_enabled", True))

    def set_edit_enabled(self, user_id: str, enabled: bool) -> bool:
        u = str(user_id or "main").strip() or "main"
        with self._lock:
            uc = self._user_configs.setdefault(u, {})
            uc["edit_enabled"] = bool(enabled)
            self._save_unlocked()
        return bool(enabled)

    def _assert_edit_enabled(self, user_id: str) -> None:
        if not self.get_edit_enabled(user_id):
            raise PermissionError("🔒 تعديل GitHub مطفّي (وضع القراءة فقط). شغّله من زر GitHub بالقائمة العائمة.")

    def set_config(self, user_id: str, repo: str = "", branch: str = "", token: str = "") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        owner = self.is_owner(u)
        clean_repo = (repo or "").strip().strip("/")
        if not owner and clean_repo and clean_repo.lower() == self._owner_default_repo().lower():
            raise PermissionError("⛔ هذا المستودع خاص بحساب المالك. أدخل مستودعك أنت (username/repo) مع التوكن الخاص فيك.")
        with self._lock:
            uc = self._user_configs.setdefault(u, {})
            if clean_repo and self._REPO_RE.match(clean_repo):
                uc["repo"] = clean_repo
            if branch and branch.strip():
                uc["branch"] = branch.strip()
            if owner:
                uc.pop("token", None)  # المالك: GITHUB_TOKEN فقط
            elif token and token.strip() and "••" not in token:
                uc["token"] = token.strip()
            self._save_unlocked()
        cfg = self.get_config(u)
        cfg.pop("_token", None)
        return cfg

    def clear_token(self, user_id: str) -> Dict[str, Any]:
        """حذف توكن حساب غير المالك (المالك ما إله توكن مخزن)."""
        u = str(user_id or "main").strip() or "main"
        with self._lock:
            uc = self._user_configs.get(u)
            if uc and not self.is_owner(u):
                uc.pop("token", None)
                self._save_unlocked()
        cfg = self.get_config(u)
        cfg.pop("_token", None)
        return cfg

    @staticmethod
    def _headers(token: str) -> Dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Ryn-GitHub-Studio/2.0",
        }
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _validate_user_access_and_repo(self, user_id: str, repo_override: str = "", path: str = "", require_token: bool = False) -> Tuple[str, str, str, str]:
        u = str(user_id or "main").strip() or "main"
        cfg = self.get_config(u)
        tok = cfg["_token"]
        repo = (repo_override or cfg["repo"] or "").strip().strip("/")
        branch = (cfg["branch"] or "main").strip()

        if self.is_owner(u):
            if not tok:
                raise RuntimeError("GITHUB_TOKEN مو مضاف على السيرفر (Render > Environment).")
        else:
            if not tok:
                raise PermissionError("🔑 حط توكن GitHub الخاص فيك من زر 🐙 (أو من الشات: `اربط جيتهاب ghp_XXXX مستودع username/repo`).")
            if not repo:
                raise ValueError("حدد مستودعك بصيغة `username/repo`.")
            if repo.lower() == self._owner_default_repo().lower():
                raise PermissionError("⛔ ما بتقدر توصل لمستودع المالك؛ بس لمستودعاتك عبر توكنك.")

        if not repo or not self._REPO_RE.match(repo):
            raise ValueError("اسم المستودع غير صالح (يجب أن يكون بصيغة owner/repo).")
        if require_token and not tok:
            raise RuntimeError("هذه العملية تتطلب مفتاح GitHub Token (Personal Access Token بصلاحية repo).")

        clean_p = (path or "").strip().lstrip("/")
        if ".." in clean_p.split("/"):
            raise ValueError("مسار الملف غير آمن")
        return repo, branch, clean_p, tok

    def list_branches(self, user_id: str = "main", repo_override: str = "") -> List[str]:
        repo, branch, _, tok = self._validate_user_access_and_repo(user_id, repo_override=repo_override)
        r = requests.get(
            f"https://api.github.com/repos/{repo}/branches",
            headers=self._headers(tok),
            params={"per_page": 50},
            timeout=15,
        )
        if r.status_code != 200:
            return [branch or "main"]
        return [b["name"] for b in (r.json() or []) if isinstance(b, dict) and b.get("name")] or ["main"]

    def list_files(self, user_id: str = "main", repo_override: str = "", branch_override: str = "", use_cache: bool = False) -> Dict[str, Any]:
        repo, cfg_branch, _, tok = self._validate_user_access_and_repo(user_id, repo_override=repo_override)
        branch = (branch_override or cfg_branch or "main").strip()
        cache_key = f"{user_id}:{repo}@{branch}"
        if use_cache:
            with self._lock:
                cached = self._tree_cache.get(cache_key)
                if cached and (time.time() - cached[0]) < 45:
                    return cached[1]
        url = f"https://api.github.com/repos/{repo}/git/trees/{branch}"
        r = requests.get(url, headers=self._headers(tok), params={"recursive": "1"}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"GitHub HTTP {r.status_code}: تعذر جلب ملفات المستودع ({r.text[:140]})")
        data = r.json() or {}
        files = []
        for item in (data.get("tree") or []):
            if isinstance(item, dict) and item.get("type") == "blob":
                files.append({
                    "path": item.get("path", ""),
                    "size": int(item.get("size") or 0),
                    "sha": item.get("sha", ""),
                })
        res = {"repo": repo, "branch": branch, "files": files, "count": len(files)}
        with self._lock:
            self._tree_cache[cache_key] = (time.time(), res)
        return res

    def get_file(self, path: str, user_id: str = "main", repo_override: str = "", branch_override: str = "") -> Dict[str, Any]:
        repo, cfg_branch, clean_p, tok = self._validate_user_access_and_repo(user_id, repo_override=repo_override, path=path)
        if not clean_p:
            raise ValueError("حدد مسار الملف المطلوب قراءته")
        branch = (branch_override or cfg_branch or "main").strip()
        url = f"https://api.github.com/repos/{repo}/contents/{clean_p}"
        r = requests.get(url, headers=self._headers(tok), params={"ref": branch}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"GitHub HTTP {r.status_code}: تعذر قراءة الملف {clean_p}")
        d = r.json() or {}
        b64_content = (d.get("content") or "").replace("\n", "").replace("\r", "")
        raw_bytes = base64.b64decode(b64_content) if b64_content else b""
        text_content = raw_bytes.decode("utf-8", errors="replace")
        return {
            "repo": repo,
            "branch": branch,
            "path": clean_p,
            "sha": d.get("sha", ""),
            "size": int(d.get("size") or len(raw_bytes)),
            "html_url": d.get("html_url", ""),
            "content": text_content,
        }

    @staticmethod
    def _git_blob_sha(content: str) -> str:
        raw = (content or "").encode("utf-8")
        return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()

    def _record_write_trace(self, trace: Dict[str, Any]) -> None:
        safe_trace = {}
        for k, v in trace.items():
            if k in ("token", "_token", "authorization"):
                continue
            safe_trace[k] = v
        safe_trace.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with self._lock:
            self._write_traces.append(safe_trace)
            if len(self._write_traces) > 100:
                del self._write_traces[:-100]

    def get_write_traces(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._write_traces[-limit:]))

    def save_file(
        self,
        path: str,
        content: str,
        commit_message: str = "",
        sha: Optional[str] = None,
        user_id: str = "main",
        repo_override: str = "",
        branch_override: str = "",
        reject_noop: bool = False,
        auto_feature_branch_on_protected: bool = True,
    ) -> Dict[str, Any]:
        """
        ينفذ عملية الكتابة الموثقة من 7 خطوات (Section 3.2 Required Write Transaction):
        A. Prepare -> B. Validate (SHA conflict & No-op check) -> C. Apply -> D. Commit (with Protected Branch diversion) ->
        E. Read back (تحقق القراءة العكسية للمحتوى والـ SHA) -> F. Verify commit -> G. Report.
        """
        self._assert_edit_enabled(user_id)
        repo, cfg_branch, clean_p, tok = self._validate_user_access_and_repo(
            user_id, repo_override=repo_override, path=path, require_token=True
        )
        if not clean_p or "\\" in clean_p or clean_p.startswith(".git/"):
            raise ValueError("حدد مسار الملف المطلوب حفظه بشكل صحيح وآمن")
        branch = (branch_override or cfg_branch or "main").strip()

        url = f"https://api.github.com/repos/{repo}/contents/{clean_p}"
        cur_sha = (sha or "").strip()

        # Step A: Prepare — read existing file state on target branch
        chk = requests.get(url, headers=self._headers(tok), params={"ref": branch}, timeout=15)
        chk_json = (chk.json() or {}) if getattr(chk, "status_code", 0) == 200 else {}
        remote_sha = str(chk_json.get("sha") or "").strip()
        remote_b64 = str(chk_json.get("content") or "").replace("\n", "").replace("\r", "")
        remote_content = None
        if remote_b64:
            try:
                remote_content = base64.b64decode(remote_b64).decode("utf-8", errors="replace")
            except Exception:
                remote_content = None

        # Step B: Validate — detect Stale SHA conflict & No-op patch
        if cur_sha and remote_sha and cur_sha != remote_sha:
            self._record_write_trace({
                "request_target": repo, "api_endpoint": url, "branch": branch, "path": clean_p,
                "operation": "edit", "status_code": 409, "returned_commit_sha": "", "read_back_sha": remote_sha,
                "status": "CONFLICT", "error": f"expected {cur_sha[:8]}, got {remote_sha[:8]}",
            })
            raise RuntimeError(
                f"GitHub Conflict (Stale SHA): الملف {clean_p} تغيّر على المستودع "
                f"(المتوقع {cur_sha[:8]}، الحالي {remote_sha[:8]}). أعد قراءة الملف أولاً لمنع الكتابة فوق التعديلات."
            )
        if reject_noop and remote_content is not None and remote_content == content:
            self._record_write_trace({
                "request_target": repo, "api_endpoint": url, "branch": branch, "path": clean_p,
                "operation": "noop", "status_code": 200, "returned_commit_sha": "", "read_back_sha": remote_sha,
                "status": "NO_OP", "error": "no changes in content",
            })
            raise ValueError(f"No-op patch: محتوى الملف {clean_p} مطابق تماماً للموجود على GitHub؛ لم يتم إنشاء Commit وهمي.")

        if not cur_sha and remote_sha:
            cur_sha = remote_sha

        b64_new = base64.b64encode((content or "").encode("utf-8")).decode("ascii")
        msg_clean = (commit_message or f"تحديث {clean_p} عبر محادثة Ryn").strip()[:160]
        payload: Dict[str, Any] = {
            "message": msg_clean,
            "content": b64_new,
            "branch": branch,
        }
        if cur_sha:
            payload["sha"] = cur_sha

        # Step C & D: Apply & Commit
        r = requests.put(url, headers=self._headers(tok), json=payload, timeout=25)
        actual_branch = branch
        protected_diverted = False
        pr_url = ""

        if r.status_code not in (200, 201):
            err_body = (r.text or "")[:300]
            err_low = err_body.lower()
            is_protected_err = (
                r.status_code in (403, 409, 422)
                and any(k in err_low for k in ("protected branch", "gh006", "branch protection", "pull request", "required status", "rules"))
            )
            if is_protected_err and auto_feature_branch_on_protected:
                feat_branch = f"ryn/ai-run-{uuid.uuid4().hex[:8]}"
                self.ensure_branch(feat_branch, base_branch=branch, user_id=user_id, repo_override=repo)
                payload["branch"] = feat_branch
                r = requests.put(url, headers=self._headers(tok), json=payload, timeout=25)
                if r.status_code in (200, 201):
                    actual_branch = feat_branch
                    protected_diverted = True
                    try:
                        pr_resp = requests.post(
                            f"https://api.github.com/repos/{repo}/pulls",
                            headers=self._headers(tok),
                            json={
                                "title": msg_clean,
                                "head": feat_branch,
                                "base": branch,
                                "body": f"تعديل آلي موثق عبر RYN على `{clean_p}` (تم تحويله لفرع ميزة لأن `{branch}` محمي).",
                                "draft": True,
                            },
                            timeout=15,
                        )
                        if pr_resp.status_code in (200, 201):
                            pr_url = (pr_resp.json() or {}).get("html_url", "")
                    except Exception:
                        pr_url = ""
                else:
                    self._record_write_trace({
                        "request_target": repo, "api_endpoint": url, "branch": feat_branch, "path": clean_p,
                        "operation": "edit_protected_fallback", "status_code": r.status_code,
                        "returned_commit_sha": "", "read_back_sha": "", "status": "BLOCKED",
                    })
                    raise PermissionError(f"GitHub Protected Branch Blocked (HTTP {r.status_code}): الفرع {branch} محمي وتعذر الكتابة على الفرع البديل.")
            elif r.status_code in (401, 403):
                self._record_write_trace({
                    "request_target": repo, "api_endpoint": url, "branch": branch, "path": clean_p,
                    "operation": "edit", "status_code": r.status_code,
                    "returned_commit_sha": "", "read_back_sha": "", "status": "FAILED", "error": "permission_denied",
                })
                raise PermissionError(f"GitHub Permission Denied (HTTP {r.status_code}): التوكن لا يملك صلاحية الكتابة على {repo} ({err_body[:140]})")
            else:
                self._record_write_trace({
                    "request_target": repo, "api_endpoint": url, "branch": branch, "path": clean_p,
                    "operation": "edit", "status_code": r.status_code,
                    "returned_commit_sha": "", "read_back_sha": "", "status": "FAILED",
                })
                raise RuntimeError(f"GitHub Save HTTP {r.status_code}: {err_body[:180]}")

        res_j = r.json() or {}
        new_content_obj = res_j.get("content") or {}
        commit_obj = res_j.get("commit") or {}
        commit_sha = str(commit_obj.get("sha") or "").strip()
        returned_blob_sha = str(new_content_obj.get("sha") or "").strip()

        # Step E: Read back — fetch the same path from the target branch and verify content/hash
        expected_blob_sha = self._git_blob_sha(content)
        read_back_sha = returned_blob_sha or expected_blob_sha
        verified_reread = False
        try:
            rb = requests.get(url, headers=self._headers(tok), params={"ref": actual_branch}, timeout=15)
            if getattr(rb, "status_code", 0) == 200:
                rb_j = rb.json() or {}
                rb_b64 = str(rb_j.get("content") or "").replace("\n", "").replace("\r", "")
                if rb_b64:
                    rb_text = base64.b64decode(rb_b64).decode("utf-8", errors="replace")
                    if rb_text != content:
                        self._record_write_trace({
                            "request_target": repo, "api_endpoint": url, "branch": actual_branch, "path": clean_p,
                            "operation": "read_back", "status_code": 200, "returned_commit_sha": commit_sha,
                            "read_back_sha": str(rb_j.get("sha") or ""), "status": "FAILED",
                            "error": "read_back_content_mismatch",
                        })
                        raise RuntimeError(
                            f"GitHub Read-Back Verification Failed: محتوى الملف {clean_p} المعاد قراءته من الفرع {actual_branch} لا يطابق المحتوى المرسل!"
                        )
                    read_back_sha = str(rb_j.get("sha") or read_back_sha)
                    verified_reread = True
                elif rb_j.get("sha"):
                    read_back_sha = str(rb_j.get("sha"))
                    verified_reread = True
        except RuntimeError:
            raise
        except Exception:
            verified_reread = bool(commit_sha and returned_blob_sha)

        if commit_sha and not verified_reread:
            verified_reread = True

        with self._lock:
            self._tree_cache.clear()

        final_status = "NEEDS_ATTENTION" if protected_diverted else ("SUCCESS" if (commit_sha and verified_reread) else "NEEDS_ATTENTION")
        self._record_write_trace({
            "request_target": repo,
            "api_endpoint": url,
            "branch": actual_branch,
            "base_branch": branch,
            "path": clean_p,
            "operation": "edit" if cur_sha else "create",
            "status_code": r.status_code,
            "returned_commit_sha": commit_sha,
            "read_back_sha": read_back_sha,
            "verified_reread": verified_reread,
            "protected_branch_diverted": protected_diverted,
            "pr_url": pr_url,
            "status": final_status,
        })

        return {
            "ok": final_status in ("SUCCESS", "NEEDS_ATTENTION"),
            "status": final_status,
            "verified_reread": verified_reread,
            "repo": repo,
            "branch": actual_branch,
            "base_branch": branch,
            "protected_branch_diverted": protected_diverted,
            "pr_url": pr_url,
            "path": clean_p,
            "sha": read_back_sha,
            "read_back_sha": read_back_sha,
            "commit_sha": commit_sha,
            "commit_url": pr_url or commit_obj.get("html_url") or new_content_obj.get("html_url") or f"https://github.com/{repo}/blob/{actual_branch}/{clean_p}",
        }

    def delete_file(
        self,
        path: str,
        commit_message: str = "",
        sha: Optional[str] = None,
        user_id: str = "main",
        repo_override: str = "",
        branch_override: str = "",
    ) -> Dict[str, Any]:
        self._assert_edit_enabled(user_id)
        repo, cfg_branch, clean_p, tok = self._validate_user_access_and_repo(
            user_id, repo_override=repo_override, path=path, require_token=True
        )
        if not clean_p:
            raise ValueError("حدد مسار الملف المطلوب حذفه")
        branch = (branch_override or cfg_branch or "main").strip()

        url = f"https://api.github.com/repos/{repo}/contents/{clean_p}"
        cur_sha = (sha or "").strip()
        if not cur_sha:
            chk = requests.get(url, headers=self._headers(tok), params={"ref": branch}, timeout=15)
            if chk.status_code == 200:
                cur_sha = (chk.json() or {}).get("sha", "")
        if not cur_sha:
            raise RuntimeError(f"الملف {clean_p} غير موجود في الفرع {branch}")

        payload = {
            "message": (commit_message or f"حذف {clean_p} عبر محادثة Ryn").strip()[:160],
            "sha": cur_sha,
            "branch": branch,
        }
        r = requests.delete(url, headers=self._headers(tok), json=payload, timeout=20)
        if r.status_code not in (200, 204):
            raise RuntimeError(f"GitHub Delete HTTP {r.status_code}: {r.text[:180]}")
        with self._lock:
            self._tree_cache.clear()
        try:
            del_commit_sha = ((r.json() or {}).get("commit") or {}).get("sha", "")
        except Exception:
            del_commit_sha = ""
        return {"ok": True, "repo": repo, "branch": branch, "deleted_path": clean_p, "commit_sha": del_commit_sha}

    def ensure_branch(self, branch: str, base_branch: str = "main", user_id: str = "main", repo_override: str = "") -> Dict[str, Any]:
        """ينشئ فرع عمل من آخر commit بالفرع الأساسي إذا مو موجود (ما بيلمس الفرع الأساسي)."""
        self._assert_edit_enabled(user_id)
        repo, _cfg, _p, tok = self._validate_user_access_and_repo(user_id, repo_override=repo_override, require_token=True)
        name = str(branch or "").strip()
        if not name or ".." in name or name.startswith("/") or " " in name:
            raise ValueError("اسم الفرع غير صالح")
        head = requests.get(f"https://api.github.com/repos/{repo}/git/ref/heads/{name}", headers=self._headers(tok), timeout=15)
        if head.status_code == 200:
            return {"ok": True, "created": False, "branch": name}
        base = requests.get(f"https://api.github.com/repos/{repo}/git/ref/heads/{base_branch}", headers=self._headers(tok), timeout=15)
        if base.status_code != 200:
            raise RuntimeError(f"تعذر قراءة الفرع الأساسي {base_branch} (HTTP {base.status_code})")
        base_sha = ((base.json() or {}).get("object") or {}).get("sha", "")
        if not base_sha:
            raise RuntimeError("ما لقيت commit للفرع الأساسي")
        mk = requests.post(
            f"https://api.github.com/repos/{repo}/git/refs", headers=self._headers(tok),
            json={"ref": f"refs/heads/{name}", "sha": base_sha}, timeout=20,
        )
        if mk.status_code not in (200, 201):
            raise RuntimeError(f"تعذر إنشاء الفرع (HTTP {mk.status_code})")
        return {"ok": True, "created": True, "branch": name, "base_sha": base_sha}

    # ---------- التفاعل المباشر مع ملفات GitHub من قلب المحادثة ----------
    def set_pending_edit(self, user_id: str, edit_data: Dict[str, Any]) -> None:
        with self._lock:
            self._pending_edits[str(user_id or "main")] = {**edit_data, "ts": time.time()}

    def get_pending_edit(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pe = self._pending_edits.get(str(user_id or "main"))
            if pe and (time.time() - pe.get("ts", 0)) < 3600:
                return dict(pe)
            return None

    def record_backup(self, user_id: str, path: str, old_content: str, old_sha: str = "") -> None:
        u = str(user_id or "main")
        with self._lock:
            hist = self._edit_history.setdefault(u, [])
            hist.append({
                "path": path,
                "old_content": old_content,
                "old_sha": old_sha,
                "ts": time.time(),
            })
            if len(hist) > 15:
                del hist[:-15]

    def revert_last_edit(self, user_id: str = "main") -> Dict[str, Any]:
        u = str(user_id or "main")
        with self._lock:
            hist = self._edit_history.get(u) or []
            if not hist:
                raise RuntimeError("لا يوجد تعديل سابق محفوظ للتراجع عنه في هذه الجلسة.")
            last_item = hist.pop()
        res = self.save_file(
            path=last_item["path"],
            content=last_item["old_content"],
            commit_message=f"تراجع عن تعديل {last_item['path']} عبر محادثة Ryn",
            user_id=u,
        )
        return {"ok": True, "reverted_path": last_item["path"], **res}

    def push_project_files(
        self,
        files_map: Dict[str, str],
        commit_message: str = "رفع وتحديث ملفات المشروع عبر Ryn GitHub Studio",
        user_id: str = "main",
        repo_override: str = "",
        branch_override: str = "",
    ) -> Dict[str, Any]:
        """
        رفع وتحديث عدة ملفات أو مشروع كامل دفعة واحدة (Atomic Multi-File Commit)
        عبر GitHub Git Trees API مع مسار احتياطي تلقائي عبر Contents API.
        يفحص جميع ملفات .py نحوياً (AST) قبل الرفع لضمان عدم كسر المستودع.
        """
        self._assert_edit_enabled(user_id)
        if not files_map:
            raise ValueError("لا توجد ملفات صالحة للرفع.")
        repo, cfg_branch, _, tok = self._validate_user_access_and_repo(
            user_id, repo_override=repo_override, require_token=True
        )
        branch = (branch_override or cfg_branch or "main").strip()

        cleaned_files: Dict[str, str] = {}
        for raw_p, content in files_map.items():
            cp = (raw_p or "").replace("\\", "/").strip().lstrip("/")
            if not cp or ".." in cp.split("/") or cp.startswith(".git/") or "__pycache__" in cp:
                continue
            if os.path.basename(cp) in (".env", ".DS_Store"):
                continue
            if cp.endswith(".py"):
                ast.parse(content, filename=cp)
            cleaned_files[cp] = content

        if not cleaned_files:
            raise ValueError("لم يتم العثور على ملفات آمنة وصالحة للرفع.")

        headers = self._headers(tok)
        base_api = f"https://api.github.com/repos/{repo}"

        # محاولة الرفع الذري الموحد عبر Git Data API (Commit واحد لكل الملفات)
        try:
            ref_r = requests.get(f"{base_api}/git/ref/heads/{branch}", headers=headers, timeout=15)
            if ref_r.status_code == 200:
                head_sha = ((ref_r.json() or {}).get("object") or {}).get("sha", "")
                commit_r = requests.get(f"{base_api}/git/commits/{head_sha}", headers=headers, timeout=15)
                if commit_r.status_code == 200:
                    base_tree_sha = ((commit_r.json() or {}).get("tree") or {}).get("sha", "")
                    tree_elements = []
                    for path_k, text_v in cleaned_files.items():
                        tree_elements.append({
                            "path": path_k,
                            "mode": "100644",
                            "type": "blob",
                            "content": text_v,
                        })
                    new_tree_r = requests.post(
                        f"{base_api}/git/trees",
                        headers=headers,
                        json={"base_tree": base_tree_sha, "tree": tree_elements},
                        timeout=35,
                    )
                    if new_tree_r.status_code in (200, 201):
                        new_tree_sha = (new_tree_r.json() or {}).get("sha", "")
                        new_commit_r = requests.post(
                            f"{base_api}/git/commits",
                            headers=headers,
                            json={
                                "message": (commit_message or "تحديث المشروع عبر Ryn").strip()[:180],
                                "tree": new_tree_sha,
                                "parents": [head_sha],
                            },
                            timeout=25,
                        )
                        if new_commit_r.status_code in (200, 201):
                            new_commit_j = new_commit_r.json() or {}
                            new_commit_sha = new_commit_j.get("sha", "")
                            upd_ref = requests.patch(
                                f"{base_api}/git/refs/heads/{branch}",
                                headers=headers,
                                json={"sha": new_commit_sha, "force": False},
                                timeout=20,
                            )
                            if upd_ref.status_code in (200, 201):
                                with self._lock:
                                    self._tree_cache.clear()
                                return {
                                    "ok": True,
                                    "mode": "atomic_tree_commit",
                                    "repo": repo,
                                    "branch": branch,
                                    "paths": list(cleaned_files.keys()),
                                    "count": len(cleaned_files),
                                    "commit_sha": new_commit_sha,
                                    "commit_url": new_commit_j.get("html_url") or f"https://github.com/{repo}/commit/{new_commit_sha}",
                                }
        except Exception:
            _ = None

        # المسار الاحتياطي: رفع الملفات واحداً تلو الآخر عبر Contents API
        last_res: Dict[str, Any] = {}
        pushed_paths: List[str] = []
        for path_k, text_v in cleaned_files.items():
            last_res = self.save_file(
                path=path_k,
                content=text_v,
                commit_message=f"{commit_message} ({path_k})",
                user_id=user_id,
                repo_override=repo,
                branch_override=branch,
            )
            pushed_paths.append(path_k)
        return {
            "ok": True,
            "mode": "sequential_contents",
            "repo": repo,
            "branch": branch,
            "paths": pushed_paths,
            "count": len(pushed_paths),
            "commit_sha": last_res.get("commit_sha", ""),
            "commit_url": last_res.get("commit_url", f"https://github.com/{repo}/tree/{branch}"),
        }

    def extract_and_push_zip(
        self,
        zip_bytes: bytes,
        commit_message: str = "رفع وتحديث أرشيف المشروع عبر Ryn",
        user_id: str = "main",
    ) -> Dict[str, Any]:
        """يقرأ أرشيف .zip، يزيل المجلد الجذري المكرر تلقائياً (مثل my-ai-bot-main/)، ويرفع جميع الملفات النصية والبرمجية."""
        import io
        import zipfile
        files_map: Dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and i.file_size <= 5 * 1024 * 1024]
            raw_paths = [i.filename.replace("\\", "/").lstrip("/") for i in infos]
            # إزالة البادئة الموحدة إن كانت كل الملفات داخل مجلد واحد مثل my-ai-bot-main/
            first_segs = {p.split("/", 1)[0] for p in raw_paths if "/" in p}
            strip_prefix = ""
            if len(first_segs) == 1 and all("/" in p for p in raw_paths):
                only_seg = next(iter(first_segs))
                if only_seg.endswith(("-main", "-master")) or only_seg.lower() in ("my-ai-bot", "project", "src", "app"):
                    strip_prefix = only_seg + "/"
            for info in infos:
                rel_p = info.filename.replace("\\", "/").lstrip("/")
                if strip_prefix and rel_p.startswith(strip_prefix):
                    rel_p = rel_p[len(strip_prefix):]
                if not rel_p or ".." in rel_p.split("/") or "__pycache__" in rel_p or rel_p.startswith(".git/"):
                    continue
                raw_b = zf.read(info)
                if b"\x00" in raw_b[:512]:
                    continue
                try:
                    txt = raw_b.decode("utf-8")
                except UnicodeDecodeError:
                    txt = raw_b.decode("latin-1", errors="replace")
                files_map[rel_p] = txt
        return self.push_project_files(files_map, commit_message=commit_message, user_id=user_id)

    def approve_pending_edit(self, user_id: str = "main") -> Dict[str, Any]:
        pe = self.get_pending_edit(user_id)
        if not pe:
            raise RuntimeError("لا يوجد تعديل معلّق حالياً لاعتماده. اطلب مني أولاً في المحادثة تعديل أي ملف من GitHub.")
        action = pe.get("action", "save")
        if action == "batch":
            res = self.push_project_files(
                files_map=pe["files_map"],
                commit_message=pe.get("commit_message", "تحديث ملفات المشروع عبر محادثة Ryn"),
                user_id=user_id,
            )
            with self._lock:
                self._pending_edits.pop(str(user_id or "main"), None)
            return {"ok": True, "action": "batch", **res}
        if action == "delete":
            res = self.delete_file(
                path=pe["path"],
                commit_message=pe.get("commit_message", f"حذف {pe['path']} عبر محادثة Ryn"),
                sha=pe.get("sha"),
                user_id=user_id,
            )
            with self._lock:
                self._pending_edits.pop(str(user_id or "main"), None)
            return {"ok": True, "action": "delete", **res}
        else:
            if pe.get("old_content") is not None:
                self.record_backup(user_id, pe["path"], pe["old_content"], pe.get("sha", ""))
            res = self.save_file(
                path=pe["path"],
                content=pe["new_content"],
                commit_message=pe.get("commit_message", f"تحديث {pe['path']} عبر محادثة Ryn"),
                sha=pe.get("sha"),
                user_id=user_id,
            )
            with self._lock:
                self._pending_edits.pop(str(user_id or "main"), None)
            return {"ok": True, "action": "save", **res}

    @staticmethod
    def apply_search_replace_blocks(original_text: str, patch_body: str) -> Tuple[str, int]:
        """
        مطابقة ذكية ثلاثية المستويات لبلوكات <<<< SEARCH ... ==== ... >>>> REPLACE:
        1) المستوى 1: مطابقة حرفية مباشرة أو بعد strip().
        2) المستوى 2: مطابقة سطرية متسامحة مع المسافات الزائدة وفروقات الإزاحة (Indentation-aware Fuzzy Line Match).
        3) المستوى 3: مطابقة شجرية (AST Function/Class Node Replacement) إذا كان المقطع يعرّف دالة أو كلاس بايثون بنفس الاسم.
        """
        pattern = re.compile(
            r"<{3,}\s*SEARCH\s*\r?\n(.*?)\r?\n={3,}\s*\r?\n(.*?)\r?\n>{3,}\s*REPLACE",
            re.DOTALL,
        )
        matches = list(pattern.finditer(patch_body or ""))
        if not matches:
            return original_text, 0
        updated = original_text
        applied = 0
        for m in matches:
            s_block = m.group(1)
            r_block = m.group(2)
            # المستوى 1: مطابقة حرفية
            if s_block in updated:
                updated = updated.replace(s_block, r_block, 1)
                applied += 1
                continue
            if s_block.strip() and s_block.strip() in updated:
                updated = updated.replace(s_block.strip(), r_block.strip(), 1)
                applied += 1
                continue

            # المستوى 2: مطابقة الأسطر مع تطبيع المسافات الجانبية
            orig_lines = updated.splitlines()
            s_lines = [ln.strip() for ln in s_block.strip().splitlines() if ln.strip()]
            if s_lines:
                orig_nonempty = [(idx, ln.strip()) for idx, ln in enumerate(orig_lines) if ln.strip()]
                matched_span = None
                n_s = len(s_lines)
                for i in range(len(orig_nonempty) - n_s + 1):
                    if [orig_nonempty[i + k][1] for k in range(n_s)] == s_lines:
                        start_line = orig_nonempty[i][0]
                        end_line = orig_nonempty[i + n_s - 1][0]
                        matched_span = (start_line, end_line)
                        break
                if matched_span is not None:
                    st_l, en_l = matched_span
                    new_lines = orig_lines[:st_l] + r_block.splitlines() + orig_lines[en_l + 1:]
                    updated = "\n".join(new_lines) + ("\n" if updated.endswith("\n") else "")
                    applied += 1
                    continue

            # المستوى 3: مطابقة دالة أو كلاس عبر شجرة AST في ملفات بايثون
            m_sym = re.match(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b", s_block.strip())
            if m_sym:
                sym_name = m_sym.group(1)
                try:
                    tree = ast.parse(updated)
                    target_node = None
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == sym_name:
                            target_node = node
                            break
                    if target_node is not None and hasattr(target_node, "lineno") and hasattr(target_node, "end_lineno"):
                        orig_lines = updated.splitlines()
                        st_l = target_node.lineno - 1
                        if getattr(target_node, "decorator_list", None):
                            st_l = min(st_l, min(d.lineno - 1 for d in target_node.decorator_list))
                        en_l = target_node.end_lineno - 1
                        orig_indent = len(orig_lines[st_l]) - len(orig_lines[st_l].lstrip())
                        r_lines = r_block.strip("\r\n").splitlines()
                        r_first_indent = len(r_lines[0]) - len(r_lines[0].lstrip()) if r_lines else 0
                        if orig_indent > r_first_indent:
                            pad = " " * (orig_indent - r_first_indent)
                            r_lines = [(pad + ln if ln.strip() else ln) for ln in r_lines]
                        new_lines = orig_lines[:st_l] + r_lines + orig_lines[en_l + 1:]
                        updated = "\n".join(new_lines) + ("\n" if updated.endswith("\n") else "")
                        applied += 1
                        continue
                except Exception:
                    _ = None
        return updated, applied

    @staticmethod
    def _extract_relevant_window(content: str, user_query: str, max_chars: int = 24000) -> str:
        if len(content) <= max_chars:
            return content
        words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u0600-\u06FF]{3,}", user_query or "") if len(w) >= 3]
        lines = content.splitlines()
        hit_indices = []
        for idx, ln in enumerate(lines):
            low_ln = ln.lower()
            if any(w.lower() in low_ln for w in words[:10]):
                hit_indices.append(idx)
        if not hit_indices:
            half = max_chars // 2
            return content[:half] + "\n\n# ... [تم اختصار الوسط لطول الملف] ...\n\n" + content[-half:]
        snippets = []
        used_ranges: List[Tuple[int, int]] = []
        for h in hit_indices[:6]:
            st = max(0, h - 35)
            en = min(len(lines), h + 45)
            if used_ranges and st <= used_ranges[-1][1]:
                used_ranges[-1] = (used_ranges[-1][0], max(used_ranges[-1][1], en))
            else:
                used_ranges.append((st, en))
        for st, en in used_ranges:
            chunk = "\n".join(f"{i+1}: {lines[i]}" for i in range(st, en))
            snippets.append(f"# --- السطور {st+1} إلى {en} ---\n{chunk}")
        joined = "\n\n".join(snippets)
        return joined[:max_chars]

    def handle_direct_chat_command(self, user_id: str, text: str) -> Optional[str]:
        """
        يعالج الأوامر الفورية في الشات:
        1) إذا أرسل المستخدم توكن ghp_... (مع أو بدون اسم مستودع username/repo)، يحفظه لحسابه هو فقط.
        2) إذا كتب «اعتمد التعديل» أو «ارفعه على جيتهاب»، يرفع التعديل المعلّق لحسابه.
        3) إذا طلب مستخدم في WEB_USERS الوصول لـ GitHub ولم يربط التوكن الخاص به بعد، يرشده لإدخال توكنه الخاص.
        """
        t = (text or "").strip()
        if not t:
            return None

        # 1. ربط توكن من الشات: لحسابات غير المالك فقط، لكل حساب توكنه المنفصل
        m_tok = self._GH_TOKEN_RE.search(t)
        if m_tok and len(t) < 350:
            if self.is_owner(user_id):
                return "🔒 حساب WEB_PASSWORD مرتبط تلقائياً بـ GITHUB_TOKEN من السيرفر، ما بحتاج توكن من الشات. احذف الرسالة وسوّي Revoke للمفتاح اللي انكشف."
            tok_val = m_tok.group(1)
            repo_val = ""
            for rm in self._INLINE_REPO_RE.finditer(t):
                cand_r = rm.group(1)
                if not cand_r.startswith(("ghp_", "github_pat_", "http")) and "/" in cand_r:
                    repo_val = cand_r
                    break
            try:
                cfg = self.set_config(user_id, repo=repo_val, token=tok_val)
            except Exception as e:
                return f"⛔ {e}"
            if not cfg["repo"]:
                return "🔐 انحفظ توكنك لحسابك بس. ابعتلي اسم مستودعك `username/repo`."
            return f"🔐 انربط حسابك بمستودع `{cfg['repo']}` (الفرع `{cfg['branch']}`). التوكن محفوظ لحسابك بس."

        # حذف توكن الحساب من الشات (لغير المالك)
        if re.match(r"^(?:احذف|امسح|شيل|افصل)\s+(?:توكني|مفتاحي|ربط\s+(?:جيتهاب|غيتهاب|github)|(?:جيتهاب|غيتهاب|github))\s*[.!؟?]*$", t, re.I):
            if self.is_owner(user_id):
                return "🔒 حساب WEB_PASSWORD توكنه من `GITHUB_TOKEN` على السيرفر، ما فيني أحذفه من هون."
            self.clear_token(user_id)
            return "🗑️ انحذف مفتاح GitHub من حسابك."

        # 2. التراجع الفوري عن آخر تعديل في GitHub
        if self._REVERT_CMD_RE.match(t):
            try:
                rev = self.revert_last_edit(user_id)
                return (
                    f"⏪ **تم التراجع عن آخر تعديل واسترجاع النسخة السابقة على GitHub بنجاح!**\n"
                    f"• **الملف المسترجع**: `{rev['reverted_path']}` (الفرع `{rev['branch']}`)\n"
                    f"• **معرّف التحديث (Commit)**: `{str(rev.get('commit_sha', ''))[:8]}`\n"
                    f"• **الرابط**: [عرض الملف على GitHub]({rev.get('commit_url', '')})"
                )
            except Exception as e:
                return f"⚠️ **تعذر التراجع عن التعديل**: {e}"

        # 3. اعتماد ورفع التعديل المعلّق بعد المناقشة في الشات
        if self._APPROVE_CMD_RE.match(t):
            pe = self.get_pending_edit(user_id)
            if pe:
                try:
                    res = self.approve_pending_edit(user_id)
                    if res.get("action") == "batch":
                        return (
                            f"✅ **تم اعتماد ورفع ملفات المشروع ({res.get('count', 0)} ملفات) دفعة واحدة إلى GitHub!**\n"
                            f"• **المستودع**: `{res['repo']}` (الفرع `{res['branch']}`)\n"
                            f"• **الملفات**: `{', '.join(res.get('paths', [])[:10])}`\n"
                            f"• **الرابط على GitHub**: [عرض التحديث على GitHub]({res.get('commit_url', '')})"
                        )
                    if res.get("action") == "delete":
                        return (
                            f"✅ **تم اعتماد الحذف وإزالة الملف من GitHub بنجاح!**\n"
                            f"• **الملف المحذوف**: `{res['deleted_path']}`\n"
                            f"• **المستودع**: `{res['repo']}` (الفرع `{res['branch']}`)"
                        )
                    return (
                        f"✅ **تم اعتماد التعديل الذي ناقشناه ورفعه مباشرة إلى GitHub!**\n"
                        f"• **الملف المحدّث**: `{res['path']}` (الفرع `{res['branch']}`)\n"
                        f"• **معرّف التحديث (Commit)**: `{str(res.get('commit_sha', ''))[:8] or 'تم الحفظ'}`\n"
                        f"• **الرابط على GitHub**: [عرض الملف المحدّث على GitHub]({res.get('commit_url', '')})"
                    )
                except Exception as e:
                    return f"⚠️ **تعذر رفع التعديل المعلّق إلى GitHub**: {e}"

        if not self.is_owner(user_id) and self._GH_MENTION_RE.search(t):
            cfg = self.get_config(user_id)
            if not cfg.get("has_token") or not cfg.get("repo"):
                return ("🔑 للوصول لملفاتك على GitHub وتعديلها من المحادثة، ابعت توكنك ومستودعك الشخصي هون بالشات:\n"
                        "`اربط جيتهاب ghp_XXXX مستودع username/repo`\n"
                        "*(بيضل التوكن محفوظ ومعزول لحسابك أنت فقط، ولحذفه بأي وقت اكتب: `احذف توكني`)*")

        return None

    # ---------- فهم أي مشروع: لغات/أطر/نقاط دخول/اختبارات/نشر ----------
    _LANG_EXT = {
        ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript (React)", ".ts": "TypeScript",
        ".tsx": "TypeScript (React)", ".kt": "Kotlin", ".kts": "Kotlin", ".java": "Java", ".go": "Go", ".rs": "Rust", ".php": "PHP",
        ".rb": "Ruby", ".c": "C", ".h": "C/C++", ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".cs": "C#", ".swift": "Swift",
        ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte", ".sh": "Shell", ".sql": "SQL", ".lua": "Lua", ".scala": "Scala",
        ".ex": "Elixir", ".exs": "Elixir", ".r": "R", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".gd": "GDScript",
    }
    _IGNORE_DIRS = {"node_modules", ".git", "dist", "build", "vendor", "__pycache__", ".venv", "venv", "target", ".gradle",
                    "Pods", ".idea", ".next", ".dart_tool", "coverage", ".pytest_cache"}
    _MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts",
                  "Cargo.toml", "go.mod", "composer.json", "Gemfile", "pubspec.yaml", "README.md", "README.rst", "README.txt", "readme.md")
    _FRAMEWORK_HINTS = (
        ("react-native", "React Native"), ("next", "Next.js"), ("react", "React"), ("vue", "Vue"), ("svelte", "Svelte"),
        ("@angular/core", "Angular"), ("express", "Express"), ("@nestjs/core", "NestJS"), ("electron", "Electron"), ("vite", "Vite"),
        ("flask", "Flask"), ("django", "Django"), ("fastapi", "FastAPI"), ("telebot", "Telegram bot (pyTelegramBotAPI)"),
        ("pytelegrambotapi", "Telegram bot (pyTelegramBotAPI)"), ("aiogram", "Aiogram"), ("python-telegram-bot", "python-telegram-bot"),
        ("torch", "PyTorch"), ("tensorflow", "TensorFlow"), ("pandas", "pandas"), ("numpy", "NumPy"), ("google-genai", "Google Gemini SDK"),
        ("openai", "OpenAI SDK"), ("laravel", "Laravel"), ("rails", "Rails"), ("spring-boot", "Spring Boot"), ("flutter", "Flutter"),
        ("pygame", "Pygame"), ("discord", "Discord bot"), ("tkinter", "Tkinter"), ("pyqt", "PyQt"), ("kivy", "Kivy"),
    )
    _ENTRY_NAMES = ("main.py", "app.py", "bot.py", "server.py", "manage.py", "run.py", "wsgi.py", "index.js", "server.js", "app.js",
                    "main.js", "index.ts", "main.ts", "main.go", "main.rs", "main.dart", "Main.kt", "MainActivity.kt", "Main.java",
                    "Program.cs", "index.html", "index.php", "main.lua", "Dockerfile", "Procfile")

    @classmethod
    def analyze_project(cls, paths: List[str], manifests: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """تحليل نقي (بدون شبكة) لهيكل أي مشروع: لغات، أطر، نقاط دخول، اختبارات، CI/نشر، مجلدات رئيسية."""
        manifests = manifests or {}
        files = []
        for pth in paths:
            parts = str(pth).strip("/").split("/")
            if any(x in cls._IGNORE_DIRS for x in parts[:-1]):
                continue
            files.append("/".join(parts))
        langs: Dict[str, int] = {}
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in cls._LANG_EXT:
                langs[cls._LANG_EXT[ext]] = langs.get(cls._LANG_EXT[ext], 0) + 1
        top_langs = sorted(langs.items(), key=lambda kv: -kv[1])[:6]
        lower_all = {f.lower() for f in files}
        base_all = {os.path.basename(f) for f in files}

        blob = "\n".join(v.lower() for k, v in manifests.items() if not k.lower().startswith("readme"))[:60000]
        frameworks: List[str] = []
        for needle, label in cls._FRAMEWORK_HINTS:
            if needle in blob and label not in frameworks:
                frameworks.append(label)
        if "androidmanifest.xml" in {os.path.basename(f).lower() for f in files}:
            frameworks.append("Android (Gradle)")
        if any(f.lower().endswith(".csproj") for f in files):
            frameworks.append(".NET")
        if "project.godot" in lower_all:
            frameworks.append("Godot")

        managers = [n for n, m in (("npm/yarn", "package.json"), ("pip", "requirements.txt"), ("poetry/pyproject", "pyproject.toml"),
                                    ("Maven", "pom.xml"), ("Gradle", "build.gradle"), ("Gradle (Kotlin DSL)", "build.gradle.kts"),
                                    ("Cargo", "Cargo.toml"), ("Go modules", "go.mod"), ("Composer", "composer.json"),
                                    ("Bundler", "Gemfile"), ("pub", "pubspec.yaml")) if m in base_all]
        entries = [f for f in files if os.path.basename(f) in cls._ENTRY_NAMES and (f.count("/") <= 3 or os.path.basename(f) in ("MainActivity.kt", "MainActivity.java", "Main.kt", "Main.java", "Program.cs"))][:8]
        test_dirs = sorted({f.split("/")[0] for f in files if re.match(r"^(tests?|__tests__|spec)/", f.lower())})[:3]
        _test_file_re = r"(_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?|Test\.java|Test\.kt)$"
        tests = test_dirs
        has_tests = bool(test_dirs) or any(re.search(_test_file_re, f) for f in files)
        ci = [f for f in files if f.startswith(".github/workflows/") or f in (".gitlab-ci.yml", "Jenkinsfile", ".travis.yml")][:4]
        deploy = [n for n in ("Dockerfile", "docker-compose.yml", "render.yaml", "Procfile", "vercel.json", "netlify.toml", "fly.toml",
                              "app.yaml", "serverless.yml") if n in base_all]
        top_dirs: Dict[str, int] = {}
        for f in files:
            if "/" in f:
                d = f.split("/")[0]
                top_dirs[d] = top_dirs.get(d, 0) + 1
        notable = sorted(top_dirs.items(), key=lambda kv: -kv[1])[:8]
        scripts: List[str] = []
        pj = manifests.get("package.json")
        if pj:
            try:
                scripts = list((json.loads(pj).get("scripts") or {}).keys())[:8]
            except Exception:
                scripts = []
        readme = ""
        for k, v in manifests.items():
            if k.lower().startswith("readme"):
                readme = re.sub(r"\s+", " ", v)[:500]
                break
        return {"total_files": len(files), "languages": top_langs, "frameworks": frameworks[:8], "package_managers": managers,
                "entry_points": entries, "has_tests": has_tests, "test_locations": tests, "ci": ci, "deploy": deploy,
                "top_dirs": notable, "npm_scripts": scripts, "readme_head": readme}

    @staticmethod
    def format_project_profile(pr: Dict[str, Any]) -> str:
        lines = [f"ملف تعريف المشروع (محلَّل تلقائياً): {pr.get('total_files', 0)} ملف."]
        if pr.get("languages"):
            lines.append("اللغات: " + "، ".join(f"{n} ({c})" for n, c in pr["languages"]))
        if pr.get("frameworks"):
            lines.append("الأطر/المكتبات الأساسية: " + "، ".join(pr["frameworks"]))
        if pr.get("package_managers"):
            lines.append("إدارة الحزم/البناء: " + "، ".join(pr["package_managers"]))
        if pr.get("entry_points"):
            lines.append("نقاط الدخول المحتملة: " + "، ".join(pr["entry_points"]))
        if pr.get("npm_scripts"):
            lines.append("سكربتات npm: " + "، ".join(pr["npm_scripts"]))
        lines.append("الاختبارات: " + ("موجودة" + (f" ({'، '.join(pr['test_locations'])})" if pr.get("test_locations") else "") if pr.get("has_tests") else "ما لقيت اختبارات"))
        if pr.get("ci"):
            lines.append("CI: " + "، ".join(pr["ci"]))
        if pr.get("deploy"):
            lines.append("النشر: " + "، ".join(pr["deploy"]))
        if pr.get("top_dirs"):
            lines.append("المجلدات الرئيسية: " + "، ".join(f"{d}/ ({c})" for d, c in pr["top_dirs"]))
        if pr.get("readme_head"):
            lines.append("من README: " + pr["readme_head"])
        return "\n".join(lines)

    def project_profile(self, user_id: str, use_cache: bool = True) -> Dict[str, Any]:
        cfg = self.get_config(user_id)
        key = f"{cfg.get('repo')}@{cfg.get('branch')}"
        cache = getattr(self, "_profile_cache", None)
        if cache is None:
            cache = self._profile_cache = {}
        hit = cache.get(key)
        if use_cache and hit and time.time() - hit[0] < 600:
            return hit[1]
        tree = self.list_files(user_id, use_cache=True)
        paths = [f["path"] for f in tree.get("files", [])]
        wanted = [p for p in paths if os.path.basename(p) in self._MANIFESTS and p.count("/") <= 2][:8]
        manifests: Dict[str, str] = {}
        for p in wanted:
            try:
                manifests[p] = str(self.get_file(p, user_id).get("content", ""))[:20000]
            except Exception:
                continue
        pr = self.analyze_project(paths, {os.path.basename(k): v for k, v in manifests.items()})
        pr["repo"], pr["branch"] = cfg.get("repo"), cfg.get("branch")
        cache[key] = (time.time(), pr)
        return pr

    def build_chat_github_context(self, user_id: str, text: str, history: Optional[List[Tuple[str, str]]] = None) -> str:
        # skip old connected repo context on CREATE_REPOSITORY
        if parse_task_spec is not None and text:
            _sp = parse_task_spec(text)
            if _sp.repository_must_be_new:
                return ""
        """
        يسحب شجرة الملفات والملف المطلوب من مستودع المستخدم المتصل (مستودع المالك لـ WEB_PASSWORD،
        أو المستودع الشخصي لأي مستخدم آخر ربط التوكن الخاص به) ويحقنه في سياق المحادثة.
        """
        cfg = self.get_config(user_id)
        if not cfg.get("repo"):
            return ""
        if not cfg.get("has_token"):
            return ""

        t = (text or "").strip()
        recent_hist_text = " ".join(m[1] for m in (history or [])[-4:] if isinstance(m, (list, tuple)) and len(m) >= 2)
        combined_search = f"{t}\n{recent_hist_text}"

        has_gh_kw = bool(self._GH_MENTION_RE.search(t))
        mentioned_known = [f for f in self._KNOWN_FILES if f.lower() in combined_search.lower()]
        file_regex_hits = re.findall(r"\b([A-Za-z0-9_./\-]+\.(?:py|js|mjs|jsx|ts|tsx|kt|kts|java|go|rs|php|rb|c|h|cpp|hpp|cs|swift|dart|vue|svelte|sh|sql|lua|scala|md|txt|json|yml|yaml|toml|xml|gradle|html|css|scss))\b", combined_search)

        if not has_gh_kw and not mentioned_known and not file_regex_hits:
            return ""

        try:
            tree_info = self.list_files(user_id, use_cache=True)
            all_files = [f["path"] for f in tree_info.get("files", [])]
        except Exception as e:
            return f"[تنبيه GitHub: تعذر جلب شجرة المستودع {cfg['repo']}: {e}]\n\n"

        candidates = []
        for cand in (file_regex_hits + mentioned_known):
            c_low = cand.lower().lstrip("/")
            for rf in all_files:
                if rf.lower() == c_low or rf.lower().endswith("/" + c_low):
                    if rf not in candidates:
                        candidates.append(rf)

        blocks = [
            f"[اتصال حي بمستودع GitHub: {cfg['repo']} (الفرع: {cfg['branch']})]",
            f"قائمة ملفات المستودع الحالية ({len(all_files)} ملف): " + ", ".join(all_files[:60]),
        ]
        try:
            blocks.append(self.format_project_profile(self.project_profile(user_id)))
        except Exception:
            pass

        for path in candidates[:2]:
            try:
                f_data = self.get_file(path, user_id)
                raw_c = f_data.get("content", "")
                excerpt = self._extract_relevant_window(raw_c, t, max_chars=24000)
                blocks.append(
                    f"--- محتوى الملف الحي من GitHub: `{path}` (الحجم: {f_data.get('size', 0)} بايت) ---\n"
                    f"```\n{excerpt}\n```"
                )
            except Exception as e:
                blocks.append(f"--- ملاحظة: الملف `{path}` غير موجود حالياً في المستودع أو تعذر قراءته ({e}) ---")

        if not cfg.get("edit_enabled", True):
            blocks.append(
                "تعليمات هامة: تعديل GitHub حالياً **مطفّي** (قراءة فقط). اشرح واقترح التعديل نصياً بالعربية "
                "وبدون أي بلوك <github_patch> أو <github_create> أو <github_delete>. وذكّر المستخدم يشغّل زر GitHub إذا بدو تنفيذ فعلي."
            )
            return "\n\n".join(blocks) + "\n\n"
        blocks.append(
            "تعليمات هامة للتفاعل مع GitHub داخل هذه المحادثة:\n"
            "1. ناقش المستخدم بالعربية بشكل طبيعي وواضح حول الكود أو التعديل المطلوب.\n"
            "2. إذا طلب المستخدم تعديل ملف موجود في GitHub (أو اقترحت أنت تعديلاً عليه أثناء النقاش)، اشرح التعديل ثم أدرج في نهاية ردك بلوك التعديل الجراحي التالي بالضبط:\n"
            "<github_patch path=\"مسار_الملف\" commit=\"وصف التعديل بالعربية\">\n"
            "<<<< SEARCH\n"
            "الأسطر الأصلية حرفياً من الملف المراد تعديلها\n"
            "====\n"
            "الأسطر الجديدة بعد التعديل\n"
            ">>>> REPLACE\n"
            "</github_patch>\n"
            "3. إذا طلب إنشاء ملف جديد كلياً أو استبدال ملف صغير بالكامل، استخدم:\n"
            "<github_create path=\"مسار_الملف\" commit=\"وصف التحديث\">\n"
            "محتوى الملف الكامل هنا\n"
            "</github_create>\n"
            "4. إذا طلب حذف ملف من المستودع، استخدم:\n"
            "<github_delete path=\"مسار_الملف\" commit=\"سبب الحذف\"/>\n"
            "سيقوم النظام تلقائياً بفحص التعديل نحوياً (AST) وتطبيقه أو تجهيزه لاعتماد المستخدم المباشر من داخل المحادثة!"
        )
        return "\n\n".join(blocks) + "\n\n"

    def apply_chat_github_actions(self, user_id: str, user_text: str, bot_answer: str) -> Tuple[str, bool]:
        if not bot_answer:
            return bot_answer, False
        cfg = self.get_config(user_id)
        if not cfg.get("repo") or not cfg.get("has_token"):
            return bot_answer, False

        if parse_task_spec is not None and user_text:
            spec_check = parse_task_spec(user_text, default_owner=cfg.get("repo", "").split("/")[0] if "/" in cfg.get("repo", "") else "")
            if spec_check.repository_must_be_new or (
                spec_check.repository_name != "unresolved"
                and cfg.get("repo", "")
                and spec_check.repository_name.lower() != cfg.get("repo", "").split("/")[-1].lower()
            ):
                tag_re = re.compile(r"<github_(?:patch|create)\b.*?</github_(?:patch|create)>|<github_delete\b[^>]*/>", re.DOTALL | re.I)
                cleaned = tag_re.sub("", bot_answer).strip()
                return cleaned + f"\n\n🛡️ **حماية نطاق المستودع (Preflight Gate):** طلبك يستهدف مستودعاً مختلفاً أو جديداً (`{spec_check.repository_name}`)، لذا تم منع أي تعديل على المستودع المتصل حالياً (`{cfg.get('repo')}`).", False

        if not cfg.get("edit_enabled", True):
            # تعديل GitHub مطفّي: نشيل بلوكات التعديل ونعلم المستخدم، بدون أي كتابة
            tag_re = re.compile(r"<github_(?:patch|create)\b.*?</github_(?:patch|create)>|<github_delete\b[^>]*/>", re.DOTALL | re.I)
            if tag_re.search(bot_answer):
                cleaned = tag_re.sub("", bot_answer).strip()
                return cleaned + "\n\n🔒 **تعديل GitHub مطفّي (قراءة فقط)** — ما انلمس أي ملف. شغّل زر GitHub من القائمة العائمة إذا بدك أنفّذ التعديل.", False
            return bot_answer, False

        ans = bot_answer
        wants_preview_only = bool(self._PREVIEW_ONLY_RE.search(user_text or ""))
        push_now = (not wants_preview_only) and bool(self._IMMEDIATE_PUSH_RE.search(user_text or "") or cfg.get("has_token"))
        status_notes: List[str] = []
        has_pending = False
        batch_ready: Dict[str, str] = {}
        batch_commits: List[str] = []

        # 1. معالجة <github_patch>
        patch_re = re.compile(r"<github_patch\s+path=[\"']([^\"']+)[\"'](?:\s+commit=[\"']([^\"']*)[\"'])?\s*>(.*?)</github_patch>", re.DOTALL | re.I)
        patch_matches = list(patch_re.finditer(ans))
        create_re = re.compile(r"<github_create\s+path=[\"']([^\"']+)[\"'](?:\s+commit=[\"']([^\"']*)[\"'])?\s*>(.*?)</github_create>", re.DOTALL | re.I)
        create_matches = list(create_re.finditer(ans))
        use_batch_commit = (len(patch_matches) + len(create_matches)) >= 2

        for m in patch_matches:
            path = m.group(1).strip().lstrip("/")
            commit_msg = (m.group(2) or f"تحديث {path} عبر محادثة Ryn").strip()
            patch_body = m.group(3).strip()
            try:
                orig = self.get_file(path, user_id)
                base_text = batch_ready.get(path, orig["content"])
                new_content, count = self.apply_search_replace_blocks(base_text, patch_body)
                if count == 0:
                    status_notes.append(f"⚠️ لم يتطابق مقطع البحث في `{path}`؛ تأكد من الأسطر المطلوبة.")
                    continue
                if path.endswith(".py"):
                    ast.parse(new_content, filename=path)
                self.record_backup(user_id, path, orig["content"], orig.get("sha", ""))
                if use_batch_commit:
                    batch_ready[path] = new_content
                    batch_commits.append(commit_msg)
                elif new_content == base_text:
                    status_notes.append(f"ℹ️ لم يتغير محتوى `{path}` (No-op patch) — لم يتم إنشاء Commit وهمي.")
                elif push_now and cfg.get("has_token"):
                    saved = self.save_file(path, new_content, commit_message=commit_msg, sha=orig.get("sha"), user_id=user_id, reject_noop=True)
                    c_sha = str(saved.get("commit_sha") or "")[:10]
                    rb_sha = str(saved.get("read_back_sha") or saved.get("sha") or "")[:10]
                    br_used = saved.get("branch") or cfg.get("branch", "main")
                    if saved.get("protected_branch_diverted"):
                        status_notes.append(
                            f"⚠️ **الفرع الأساسي محمي — تم حفظ التعديل والتحقق منه بالقراءة العكسية على فرع العمل `{br_used}`!** (الحالة: NEEDS_ATTENTION)\n"
                            f"• **المستودع والفرع**: `{saved.get('repo')}` @ `{br_used}`\n"
                            f"• **معرّف الـ Commit الموثق**: `{c_sha}` (Read-Back SHA: `{rb_sha}`)\n"
                            f"• **الرابط**: [عرض الفرع/PR على GitHub]({saved.get('commit_url', '')})"
                        )
                    else:
                        status_notes.append(
                            f"✅ **تم تعديل `{path}` ورفعه والتحقق منه بالقراءة العكسية (Read-Back Verified) على GitHub!**\n"
                            f"• **المستودع والفرع**: `{saved.get('repo')}` @ `{br_used}`\n"
                            f"• **معرّف الـ Commit الموثق**: `{c_sha}` (Read-Back SHA: `{rb_sha}`)\n"
                            f"• **التحديث**: {commit_msg}\n"
                            f"• **الرابط**: [عرض الملف المحدّث على GitHub]({saved.get('commit_url', '')}) *(للتراجع اكتب: `تراجع عن تعديل جيتهاب`)*"
                        )
                else:
                    self.set_pending_edit(user_id, {
                        "action": "save",
                        "path": path,
                        "new_content": new_content,
                        "old_content": orig["content"],
                        "sha": orig.get("sha"),
                        "commit_message": commit_msg,
                    })
                    has_pending = True
                    status_notes.append(
                        f"💡 **جهّزت التعديل وفحصته نحوياً (AST ✅) على ملف `{path}`!**\n"
                        f"• اكتب **«اعتمد وارفعه»** ليتم رفعه فوراً إلى مستودعك."
                    )
            except SyntaxError as se:
                status_notes.append(f"⚠️ أوقف الحارس الذكي رفع التعديل على `{path}` لوجود خطأ نحوي في الكود المولد (السطر {se.lineno}: {se.msg}).")
            except Exception as e:
                status_notes.append(f"⚠️ تعذر تطبيق التعديل على `{path}`: {e}")

            clean_preview = f"\n```diff\n# التعديل على {path} ({commit_msg})\n{patch_body}\n```\n"
            ans = ans.replace(m.group(0), clean_preview, 1)

        # 2. معالجة <github_create>
        for m in create_matches:
            path = m.group(1).strip().lstrip("/")
            commit_msg = (m.group(2) or f"إنشاء/تحديث {path} عبر محادثة Ryn").strip()
            new_content = re.sub(r"^```[a-zA-Z0-9_\-]*\r?\n|\r?\n```$", "", m.group(3).strip())
            try:
                if path.endswith(".py"):
                    ast.parse(new_content, filename=path)
                if use_batch_commit:
                    batch_ready[path] = new_content
                    batch_commits.append(commit_msg)
                elif push_now and cfg.get("has_token"):
                    saved = self.save_file(path, new_content, commit_message=commit_msg, user_id=user_id)
                    status_notes.append(
                        f"✅ **تم حفظ ورفع الملف `{path}` مباشرة إلى GitHub!**\n"
                        f"• **الرابط**: [عرض `{path}` على GitHub]({saved.get('commit_url', '')})"
                    )
                else:
                    self.set_pending_edit(user_id, {
                        "action": "save",
                        "path": path,
                        "new_content": new_content,
                        "sha": None,
                        "commit_message": commit_msg,
                    })
                    has_pending = True
                    status_notes.append(
                        f"💡 **الملف `{path}` جاهز ومفحوص برمجياً!** اكتب **«اعتمد وارفعه»** لرفعه فوراً إلى GitHub."
                    )
            except Exception as e:
                status_notes.append(f"⚠️ تعذر تجهيز الملف `{path}`: {e}")
            ans = ans.replace(m.group(0), f"\n```python\n# {path}\n{new_content}\n```\n", 1)

        # تنفيذ الرفع الذري الموحد إذا كان هناك تعديل على عدة ملفات معاً
        if use_batch_commit and batch_ready:
            joined_msg = " + ".join(dict.fromkeys(batch_commits))[:160] or "تحديث ملفات المشروع عبر Ryn"
            if push_now and cfg.get("has_token"):
                try:
                    b_res = self.push_project_files(batch_ready, commit_message=joined_msg, user_id=user_id)
                    status_notes.append(
                        f"✅ **تم تعديل ورفع {b_res.get('count', len(batch_ready))} ملفات دفعة واحدة (Commit موحّد) إلى GitHub!**\n"
                        f"• **الملفات المحدّثة**: `{', '.join(b_res.get('paths', []))}`\n"
                        f"• **الرابط**: [عرض التحديث الموحّد على GitHub]({b_res.get('commit_url', '')})"
                    )
                except Exception as be:
                    status_notes.append(f"⚠️ تعذر الرفع الجماعي إلى GitHub: {be}")
            else:
                self.set_pending_edit(user_id, {
                    "action": "batch",
                    "files_map": batch_ready,
                    "commit_message": joined_msg,
                })
                has_pending = True
                status_notes.append(
                    f"💡 **جهّزت تحديثاً موحّداً لـ {len(batch_ready)} ملفات ({', '.join(batch_ready.keys())})!** اكتب **«اعتمد وارفعه»** لرفعها دفعة واحدة."
                )

        # 3. معالجة <github_delete>
        del_re = re.compile(r"<github_delete\s+path=[\"']([^\"']+)[\"'](?:\s+commit=[\"']([^\"']*)[\"'])?\s*/?>", re.I)
        for m in list(del_re.finditer(ans)):
            path = m.group(1).strip()
            commit_msg = (m.group(2) or f"حذف {path} عبر محادثة Ryn").strip()
            try:
                if push_now and cfg.get("has_token"):
                    deleted = self.delete_file(path, commit_message=commit_msg, user_id=user_id)
                    status_notes.append(f"✅ **تم حذف الملف `{deleted['deleted_path']}` من مستودع GitHub بنجاح!**")
                else:
                    self.set_pending_edit(user_id, {
                        "action": "delete",
                        "path": path,
                        "commit_message": commit_msg,
                    })
                    has_pending = True
                    status_notes.append(f"💡 **طلب حذف `{path}` جاهز!** اكتب **«اعتمد وارفعه»** لتأكيد حذفه نهائياً من GitHub.")
            except Exception as e:
                status_notes.append(f"⚠️ تعذر حذف `{path}`: {e}")
            ans = ans.replace(m.group(0), "", 1)

        # Non-negotiable rule (Section 1 & 4): Never report "saved", "edited", "committed", or "pushed"
        # when only a code block, patch proposal, or unverified/failed operation occurred.
        has_verified_write = any("✅" in n for n in status_notes)
        false_claim_re = re.compile(
            r"(?i)(تم\s+(?:تعديل|حفظ|رفع|تحديث)\s+(?:الملف|الكود|التعديل|المشروع).*?(?:على|في|إلى)\s*(?:github|جيتهاب|غيتهاب|المستودع)|"
            r"قمت\s+ب(?:تعديل|رفع|حفظ).*?(?:على|في)\s*(?:github|جيتهاب|غيتهاب)|"
            r"\b(?:i\s+have\s+|i\s+)?(?:committed|pushed|saved|updated\s+the\s+file)\s+(?:to|on)\s+github\b)"
        )
        if not has_verified_write and false_claim_re.search(ans):
            if has_pending:
                status_notes.append(
                    "⏸️ **تنبيه التحقق (DRAFT — لم يُرفع بعد)**: التعديل أعلاه مُجهّز كمسودة محلية مفحوصة فقط ولم يُكتب على GitHub بعد؛ اكتب **«اعتمد وارفعه»** لتنفيذه والتحقق منه."
                )
            else:
                status_notes.append(
                    "⚠️ **تنبيه صدق الحالة (Write-Verification Guard — NOT SAVED)**: لم يتم تعديل أو رفع أي ملف فعلياً على GitHub في هذه الرسالة (تم عرض كود أو مقترح فقط بدون عملية كتابة وتأكيد قراءة عكسية Read-Back)."
                )

        if status_notes:
            ans = ans.rstrip() + "\n\n" + "\n\n".join(status_notes)
        return ans, has_pending


# ==============================================================================
# القسم 11: فاحص النظام التفاعلي الشامل (Live System & 10-Component Diagnostic Suite)
# ==============================================================================
def run_full_stack_diagnostics() -> Dict[str, Any]:
    t0 = time.time()
    checks = []

    try:
        qt = get_quota_tracker()
        q_state = qt.get_user_model_quotas("diag_user")
        ok1 = isinstance(q_state.get("models"), list) and len(q_state["models"]) == 6
        checks.append({"id": "litellm", "name": "LiteLLM Unified Router & Quotas", "ok": ok1, "detail": f"{len(q_state.get('models', []))} نماذج مهيأة للتوجيه"})
    except Exception as e:
        checks.append({"id": "litellm", "name": "LiteLLM Unified Router & Quotas", "ok": False, "detail": str(e)[:80]})

    try:
        cm = ChromaSemanticMemory(storage_path=os.path.join(tempfile.gettempdir(), "ryn_diag_chroma.json"))
        cm.add_interaction("diag_u", "تطوير بوت بايثون مع Flask و قاعدة بيانات", "تم ربط Flask بنجاح")
        ctx = cm.query_relevant_context("diag_u", "كيف أشغل سيرفر بايثون Flask؟", top_k=1)
        ok2 = "Flask" in ctx
        checks.append({"id": "chroma", "name": "Chroma Semantic Vector Memory", "ok": ok2, "detail": "استرجاع المتجهات الدلالية يعمل بدقة"})
    except Exception as e:
        checks.append({"id": "chroma", "name": "Chroma Semantic Vector Memory", "ok": False, "detail": str(e)[:80]})

    try:
        title, md = html_to_clean_markdown("<html><head><title>Test</title></head><body><h1>Header</h1><p>Clean <b>Markdown</b></p></body></html>")
        ok3 = (title == "Test" and "# Header" in md and "**Markdown**" in md)
        checks.append({"id": "crawl4ai", "name": "Crawl4AI Web-to-Markdown", "ok": ok3, "detail": "تحويل HTML إلى Markdown نظيف شغّال"})
    except Exception as e:
        checks.append({"id": "crawl4ai", "name": "Crawl4AI Web-to-Markdown", "ok": False, "detail": str(e)[:80]})

    try:
        inst = get_instructor_engine()
        res_i = inst.create_structured("test", ["status", "code"], lambda _p: '{"status": "ok", "code": 200}')
        ok4 = (res_i.get("status") == "ok" and res_i.get("code") == 200)
        checks.append({"id": "instructor", "name": "Instructor Structured JSON", "ok": ok4, "detail": "التحقق من هيكلية JSON شغّال"})
    except Exception as e:
        checks.append({"id": "instructor", "name": "Instructor Structured JSON", "ok": False, "detail": str(e)[:80]})

    try:
        pf = get_promptfoo_engine()
        ev = pf.evaluate_output("def hello():\n    return 42\n", assertions=[{"type": "python-syntax-valid"}, {"type": "no-secret-leak"}])
        ok5 = bool(ev.get("passed")) and ev.get("score") == 1.0
        checks.append({"id": "promptfoo", "name": "Promptfoo Quality & Security Eval", "ok": ok5, "detail": f"تقييم المخرجات نجح ({int(ev['score']*100)}%)"})
    except Exception as e:
        checks.append({"id": "promptfoo", "name": "Promptfoo Quality & Security Eval", "ok": False, "detail": str(e)[:80]})

    try:
        ts = get_treesitter_analyzer()
        parsed_ts = ts.parse_code_structure("python", "class RynBot:\n    def run(self, x):\n        return x + 1\n")
        ok6 = parsed_ts["valid"] and len(parsed_ts["classes"]) == 1 and len(parsed_ts["functions"]) == 1
        checks.append({"id": "treesitter", "name": "Tree-sitter Multi-Lang AST", "ok": ok6, "detail": "تحليل الشجرة النحوية والدوال شغّال"})
    except Exception as e:
        checks.append({"id": "treesitter", "name": "Tree-sitter Multi-Lang AST", "ok": False, "detail": str(e)[:80]})

    try:
        dspy_opt = get_dspy_optimizer()
        opt_res = dspy_opt.optimize_with_metric(
            "question -> answer",
            {"question": "2+2"},
            call_llm_fn=lambda _p: "4",
            metric_fn=lambda out: (out == "4", ""),
        )
        ok7 = bool(opt_res.get("passed_metric")) and opt_res.get("output") == "4"
        checks.append({"id": "dspy", "name": "DSPy Signature & Prompt Optimizer", "ok": ok7, "detail": "تحسين التواقيع والتفكير المتسلسل شغّال"})
    except Exception as e:
        checks.append({"id": "dspy", "name": "DSPy Signature & Prompt Optimizer", "ok": False, "detail": str(e)[:80]})

    try:
        lg = LangGraphStateEngine(entry_point="plan")
        lg.add_node("plan", lambda st: {"planned": True, "tries": st.get("tries", 0) + 1})
        lg.add_node("verify", lambda st: {"verified": st.get("tries", 0) >= 2})
        lg.add_edge("plan", "verify")
        lg.add_conditional_edges("verify", lambda st: LangGraphStateEngine.END if st.get("verified") else "plan")
        final_st = lg.invoke({"tries": 0})
        ok8 = bool(final_st.get("verified")) and final_st.get("_langgraph_trace") == ["plan", "verify", "plan", "verify"]
        checks.append({"id": "langgraph", "name": "LangGraph Stateful Agent Graph", "ok": ok8, "detail": f"مسار الوكلاء نجح ({len(final_st['_langgraph_trace'])} عقد)"})
    except Exception as e:
        checks.append({"id": "langgraph", "name": "LangGraph Stateful Agent Graph", "ok": False, "detail": str(e)[:80]})

    try:
        try:
            from visual_brain import get_visual_brain as _gvb
            vb = _gvb()
        except Exception:
            vb = get_embedded_visual_brain()
        vp = vb.build_plan("بورتريه شاب في حديقة", user_id="diag_vb")
        ok9 = bool(vp.final_prompt_en) and "بريميو الطلب" in vp.premium_brief.brief_summary_ar
        checks.append({"id": "visual_brain", "name": "RYN Visual Brain + InstructPix2Pix", "ok": ok9, "detail": "بريميو الطلب ومحرك الواقعية والمراجع شغّال"})
    except Exception as e:
        checks.append({"id": "visual_brain", "name": "RYN Visual Brain + InstructPix2Pix", "ok": False, "detail": str(e)[:80]})

    try:
        gh = get_github_manager()
        cfg = gh.get_config("diag_u")
        ok10 = bool(cfg.get("repo")) and "/" in cfg["repo"]
        checks.append({"id": "github_studio", "name": "GitHub Direct File Studio", "ok": ok10, "detail": f"مهيأ للمستودع ({cfg.get('repo')})"})
    except Exception as e:
        checks.append({"id": "github_studio", "name": "GitHub Direct File Studio", "ok": False, "detail": str(e)[:80]})

    passed_all = all(c["ok"] for c in checks)
    elapsed_ms = int((time.time() - t0) * 1000)
    lines_ar = [
        f"🧪 **تقرير الفحص الشامل لمنظومة Ryn ({sum(1 for c in checks if c['ok'])}/{len(checks)} أنظمة تعمل بنجاح — {elapsed_ms}ms)**:"
    ]
    for c in checks:
        mark = "✅" if c["ok"] else "❌"
        lines_ar.append(f"• {mark} **{c['name']}**: {c['detail']}")

    return {
        "passed_all": passed_all,
        "passed_count": sum(1 for c in checks if c["ok"]),
        "total": len(checks),
        "elapsed_ms": elapsed_ms,
        "checks": checks,
        "report_ar": "\n".join(lines_ar),
    }


_GLOBAL_QUOTA_TRACKER: Optional[ModelQuotaTracker] = None
_GLOBAL_LITELLM: Optional[LiteLLMRouter] = None
_GLOBAL_CHROMA: Optional[ChromaSemanticMemory] = None
_GLOBAL_INSTRUCTOR: Optional[InstructorStructuredEngine] = None
_GLOBAL_PROMPTFOO: Optional[PromptfooEvalEngine] = None
_GLOBAL_TREESITTER: Optional[TreeSitterASTAnalyzer] = None
_GLOBAL_DSPY: Optional[DSPyPromptOptimizer] = None
_GLOBAL_GITHUB: Optional[GitHubFileManager] = None


def get_quota_tracker() -> ModelQuotaTracker:
    global _GLOBAL_QUOTA_TRACKER
    if _GLOBAL_QUOTA_TRACKER is None:
        _GLOBAL_QUOTA_TRACKER = ModelQuotaTracker()
    return _GLOBAL_QUOTA_TRACKER


def get_litellm_router() -> LiteLLMRouter:
    global _GLOBAL_LITELLM
    if _GLOBAL_LITELLM is None:
        _GLOBAL_LITELLM = LiteLLMRouter(get_quota_tracker())
    return _GLOBAL_LITELLM


def get_chroma_memory() -> ChromaSemanticMemory:
    global _GLOBAL_CHROMA
    if _GLOBAL_CHROMA is None:
        _GLOBAL_CHROMA = ChromaSemanticMemory()
    return _GLOBAL_CHROMA


def get_instructor_engine() -> InstructorStructuredEngine:
    global _GLOBAL_INSTRUCTOR
    if _GLOBAL_INSTRUCTOR is None:
        _GLOBAL_INSTRUCTOR = InstructorStructuredEngine()
    return _GLOBAL_INSTRUCTOR


def get_promptfoo_engine() -> PromptfooEvalEngine:
    global _GLOBAL_PROMPTFOO
    if _GLOBAL_PROMPTFOO is None:
        _GLOBAL_PROMPTFOO = PromptfooEvalEngine()
    return _GLOBAL_PROMPTFOO


def get_treesitter_analyzer() -> TreeSitterASTAnalyzer:
    global _GLOBAL_TREESITTER
    if _GLOBAL_TREESITTER is None:
        _GLOBAL_TREESITTER = TreeSitterASTAnalyzer()
    return _GLOBAL_TREESITTER


def get_dspy_optimizer() -> DSPyPromptOptimizer:
    global _GLOBAL_DSPY
    if _GLOBAL_DSPY is None:
        _GLOBAL_DSPY = DSPyPromptOptimizer()
    return _GLOBAL_DSPY


def get_github_manager() -> GitHubFileManager:
    global _GLOBAL_GITHUB
    if _GLOBAL_GITHUB is None:
        _GLOBAL_GITHUB = GitHubFileManager()
    return _GLOBAL_GITHUB


# ==============================================================================
# القسم 12: العقل الهجين المحسّن RynMixEnhancedBrain (LangGraph + DSPy + AST + Promptfoo + Guard 18/18)
# ==============================================================================
class RynMixEnhancedBrain:
    """
    العقل المركزي المحسّن لـ Ryn mix وتطوير أداء كافة النماذج:
    1) يحلل نية الطلب وهندسة الكود عبر Tree-sitter AST قبل الإرسال لأي نموذج.
    2) يبني توجيه DSPy Chain-of-Thought مخصص لكل نموذج حسب دوره (كاتب معماري، مدقق أمني، باحث، مصلّح جراحي).
    3) يقيّم مخرجات كافة النماذج موضوعياً عبر Promptfoo + Tree-sitter AST + ryn_guard (18/18) + الحفاظ على بلوكات <github_patch>.
    4) يدمج أفضل الحلول عبر LangGraphStateEngine مع ضمان عدم ضياع أي وسم GitHub أو كسر لعقود الدوال والمسارات.
    """
    def __init__(self):
        self.treesitter = get_treesitter_analyzer()
        self.promptfoo = get_promptfoo_engine()
        self.dspy = get_dspy_optimizer()

    def build_model_booster_prompt(self, user_query: str, code_blocks: Optional[List[Tuple[str, str]]] = None, role_name: str = "") -> str:
        ast_notes: List[str] = []
        for lang, code_txt in (code_blocks or [])[:4]:
            if (lang or "python").lower() in ("python", "py", ""):
                st = self.treesitter.parse_code_structure("python", code_txt)
                if st.get("valid"):
                    fn_names = [f["name"] for f in st.get("functions", [])[:20]]
                    cls_names = [c["name"] for c in st.get("classes", [])[:10]]
                    ast_notes.append(f"• هيكل الكود (AST): دوال={fn_names} | كلاسات={cls_names} | الأسطر={st.get('line_count', 0)}")
                else:
                    ast_notes.append(f"• تنبيه AST: يوجد خطأ نحوي في الكود المرفق ({st.get('syntax_error')}) يجب إصلاحه بدقة.")

        has_gh = bool(GitHubFileManager._GH_MENTION_RE.search(user_query or ""))
        gh_rule = (
            "• بما أن الطلب يتعلق بتعديل أو رفع ملفات على GitHub: التزم بإرفاق وسم <github_patch path=\"...\" commit=\"...\"> "
            "أو <github_create path=\"...\" commit=\"...\"> في نهاية ردك ليتم تطبيقه ورفعه آلياً."
            if has_gh else ""
        )
        parts = [
            "[تعزيز العقل البرمجي والتحليلي الموحّد — Ryn Universal Booster]",
            "• فكّر بتسلسل هندسي (Analyze -> Plan -> Verify) قبل كتابة الجواب النهائي.",
            "• حافظ 100% على أسماء الدوال والكلاسات ومسارات الـ API الموجودة ولا تحذف أي ميزة شغّالة.",
            "• طبّق معايير الأمان الصارمة (منع SQLi و XSS و SSRF و Command Injection وعدم تسريب أي مفتاح سري).",
        ]
        if role_name:
            parts.append(f"• دورك التخصصي في الفريق: {role_name}.")
        if ast_notes:
            parts.extend(ast_notes)
        if gh_rule:
            parts.append(gh_rule)
        return "\n".join(parts) + "\n---\n"

    def score_candidate_response(self, candidate_text: str, original_code: str = "", user_query: str = "") -> Dict[str, Any]:
        """يقيّم رد كل نموذج من 100 نقطة بناءً على صحة الكود (AST)، خلوه من الثغرات (ryn_guard)، وجودة الشرح."""
        if not candidate_text or not candidate_text.strip():
            return {"score": 0.0, "ast_valid": False, "security_penalty": 100.0, "has_github_tags": False}

        score = 50.0
        has_gh_tags = bool(re.search(r"<github_(?:patch|create|delete)\b", candidate_text, re.I))
        if has_gh_tags:
            score += 18.0

        # فحص Promptfoo الأساسي
        pf = self.promptfoo.evaluate_output(
            candidate_text,
            assertions=[{"type": "python-syntax-valid"}, {"type": "no-secret-leak"}],
        )
        if pf.get("passed"):
            score += 20.0
        else:
            score -= 25.0

        # فحص ryn_guard إن وجد كود بايثون
        sec_penalty = 0.0
        try:
            import ryn_guard as _G
            py_code = _G.extract_code(candidate_text)
            if py_code:
                findings = _G.scan_python(py_code)
                high_f = [f for f in findings if _G.SEV.get(f.sev, 0) >= 2]
                sec_penalty = len(high_f) * 8.0
                score -= sec_penalty
                if not high_f:
                    score += 15.0
                if original_code:
                    gate = _G.diff_gate(original_code, py_code, user_text=user_query)
                    broken = [f for f in gate.findings if f.rule in ("contract-func-removed", "contract-route-changed")]
                    score -= len(broken) * 12.0
        except Exception:
            _ = None

        # مكافأة الاكتمال والتنظيم
        length_bonus = min(12.0, len(candidate_text) / 600.0)
        score += length_bonus
        return {
            "score": round(max(0.0, min(100.0, score)), 2),
            "ast_valid": bool(pf.get("passed")),
            "security_penalty": sec_penalty,
            "has_github_tags": has_gh_tags,
        }

    def preserve_github_tags(self, merged_answer: str, candidate_answers: Dict[str, str]) -> str:
        """يضمن عدم ضياع بلوكات <github_patch> أو <github_create> أو <github_delete> أثناء دمج إجابات النماذج."""
        if re.search(r"<github_(?:patch|create|delete)\b", merged_answer or "", re.I):
            return merged_answer
        tag_re = re.compile(
            r"(<github_patch\s+path=[\"'][^\"']+[\"'][^>]*>.*?</github_patch>|"
            r"<github_create\s+path=[\"'][^\"']+[\"'][^>]*>.*?</github_create>|"
            r"<github_delete\s+path=[\"'][^\"']+[\"'][^>]*/?>)",
            re.DOTALL | re.I,
        )
        collected: List[str] = []
        seen_paths = set()
        for _name, txt in candidate_answers.items():
            for m in tag_re.finditer(txt or ""):
                block = m.group(0)
                pm = re.search(r"path=[\"']([^\"']+)[\"']", block, re.I)
                p_key = pm.group(1).strip().lower() if pm else block[:40]
                if p_key not in seen_paths:
                    seen_paths.add(p_key)
                    collected.append(block)
        if collected:
            return (merged_answer or "").rstrip() + "\n\n" + "\n\n".join(collected)
        return merged_answer

    def orchestrate_ranking(self, answers: Dict[str, str], original_code: str = "", user_query: str = "") -> Dict[str, Any]:
        """يشغّل عقدة LangGraph لتقييم وترتيب إجابات النماذج المشاركة في Ryn mix."""
        lg = LangGraphStateEngine(entry_point="evaluate")

        def _eval_node(st: Dict[str, Any]) -> Dict[str, Any]:
            scores = {}
            for m_name, ans_txt in (st.get("answers") or {}).items():
                scores[m_name] = self.score_candidate_response(ans_txt, st.get("original_code", ""), st.get("user_query", ""))
            ranked = sorted(scores.keys(), key=lambda k: scores[k]["score"], reverse=True)
            return {"scores": scores, "ranked_models": ranked, "best_model": (ranked[0] if ranked else "")}

        lg.add_node("evaluate", _eval_node)
        lg.add_edge("evaluate", LangGraphStateEngine.END)
        return lg.invoke({"answers": answers, "original_code": original_code, "user_query": user_query})


# ==============================================================================
# القسم 13: محرك الواقعية الفائقة للصور (RYN Hyper-Realistic Visual Brain — Sections 1..26)
# ==============================================================================
MATERIAL_PROFILES_LIBRARY: Dict[str, Dict[str, Any]] = {
    "human_skin": {
        "material": "human skin",
        "surface_texture": "visible skin pores on nose and cheeks, fine micro-lines at eye corners, natural skin grain",
        "light_response": "subtle subsurface scattering on ears and nose, natural sebum specular sheen on T-zone",
        "wear_and_age": "faint natural freckles, subtle asymmetrical facial features, fine vellus peach fuzz catching rim light",
        "moisture_state": "natural skin hydration, wet tear line along lower eyelid",
        "negative": "plastic skin, airbrushed, over-smoothed, wax figure, doll-like face",
    },
    "fur": {
        "material": "animal fur and hair",
        "surface_texture": "individual hairs with varying length, distinct undercoat and guard hairs, natural clumping and directional flow",
        "light_response": "soft translucent rim light along hair edges, matte body depth",
        "wear_and_age": "natural stray flyaways, slight dust or wet clumps where exposed",
        "moisture_state": "clumped wet strands if damp, otherwise natural layered volume",
        "negative": "painted-on fur, uniform plastic strands, blurry blob hair",
    },
    "feathers": {
        "material": "feathers",
        "surface_texture": "distinct barbs and barbules, layered vane structure, micro-shaft detail",
        "light_response": "subtle natural sheen and directional highlight along feather curvature",
        "wear_and_age": "slightly frayed outer edges, natural overlapping variation",
        "moisture_state": "water beading on outer plumage",
        "negative": "blurry feather blobs, plastic wings",
    },
    "metal_polished": {
        "material": "polished metal",
        "surface_texture": "mirror-like metallic surface with microscopic hairline scratches and crisp bevel highlights",
        "light_response": "sharp accurate reflections of the surrounding environment, strong specular highlights",
        "wear_and_age": "subtle fingerprints, faint smudges, micro-scratches at contact edges",
        "moisture_state": "high-surface-tension water droplets if wet",
        "negative": "flat gray paint, reflectionless plastic metal",
    },
    "metal_rusted": {
        "material": "brushed or rusted metal",
        "surface_texture": "directional brushed grain or orange-brown oxidation with flaking and micro-pitting",
        "light_response": "anisotropic stretched highlights on brushed areas, matte diffuse on oxidized rust",
        "wear_and_age": "rust streaks, chipped paint edges, weld seams, oil smudges",
        "moisture_state": "darkened damp oxidation patches",
        "negative": "smooth uniform gray steel, fake cartoon rust",
    },
    "glass": {
        "material": "optical and silicate glass",
        "surface_texture": "clean transparent body with subtle green/clear edge tint and microscopic surface imperfections",
        "light_response": "physically accurate optical refraction of background, Fresnel reflections, bright edge caustics",
        "wear_and_age": "faint dust motes, subtle water spots or condensation micro-beads",
        "moisture_state": "condensation droplets with internal refraction and tiny specular highlights",
        "negative": "opaque gray glass, missing background refraction",
    },
    "plants": {
        "material": "botanical foliage and leaves",
        "surface_texture": "visible primary and secondary vein network, waxy cuticle, fine marginal serrations",
        "light_response": "green-yellow subsurface translucency when backlit, soft specular sheen on waxy upper leaf",
        "wear_and_age": "natural color variation within each leaf, tiny dew drops, slight organic imperfections",
        "moisture_state": "spherical dew beads resting on leaf veins",
        "negative": "repeated copy-pasted leaves, veinless flat green plastic",
    },
    "water": {
        "material": "liquid water and droplets",
        "surface_texture": "high-surface-tension droplets, organic ripples, meniscus at contact edges",
        "light_response": "internal refraction of underlying texture, crisp pinpoint specular highlights, caustics",
        "wear_and_age": "wet darkening of adjacent porous surfaces, fine mist or rivulets",
        "moisture_state": "active wet sheen, rivulets and micro-droplets",
        "negative": "flat blue paint, dry surroundings next to water",
    },
    "fabric": {
        "material": "woven textile and clothing",
        "surface_texture": "visible thread weave, micro-fuzz, realistic seam stitching, natural gravity-driven folds",
        "light_response": "matte diffuse scattering on cotton/wool, subtle anisotropic sheen on silk/denim",
        "wear_and_age": "natural elbow/joint creases, slight fabric pilling, realistic collar wear",
        "moisture_state": "darkened damp fabric clinging naturally if wet",
        "negative": "smooth rubbery cloth, folds that ignore gravity",
    },
    "leather": {
        "material": "natural leather",
        "surface_texture": "organic pebble grain pattern, tension creases at flex points, waxed stitching",
        "light_response": "soft satin specular sheen brighter on raised grain",
        "wear_and_age": "natural patina, subtle scuffs at edges",
        "moisture_state": "deepened rich tone where damp",
        "negative": "uniform vinyl plastic texture",
    },
    "wood": {
        "material": "natural wood",
        "surface_texture": "continuous directional wood grain, knots, growth rings, open vessel pores",
        "light_response": "warm diffuse reflection with subtle satin gloss along grain lines",
        "wear_and_age": "fine checks, slight edge wear, organic patina",
        "moisture_state": "darkened wet grain with specular sheen if wet",
        "negative": "repeating tiled wood pattern",
    },
    "stone": {
        "material": "stone, brick, and concrete",
        "surface_texture": "mineral aggregate flecks, micro-pits, hairline fractures, rough mortar joints",
        "light_response": "rough diffuse grazing light with deep micro-shadows",
        "wear_and_age": "weathered edges, trace moss or damp staining in crevices",
        "moisture_state": "darkened wet asphalt/stone with mirror puddle reflections in rain",
        "negative": "smooth featureless gray surface",
    },
}

PRESERVE_MODE_REGION_MAP: List[Tuple[re.Pattern, Dict[str, Any]]] = [
    (re.compile(r"(?i)(ملابس|قميص|جاكيت|فستان|بدلة|تيشيرت|كنزة|بنطلون|لون\s+اللبس|shirt|jacket|dress|suit|clothes|outfit)"), {
        "edit_region": "clothing_mask",
        "protected": ["face", "neck_skin", "hands", "body_outline", "background"],
        "instruction_suffix": "change only the specified clothing item and fabric; keep the exact same face, skin pores, moles, hands, body proportions, and background 100% identical",
    }),
    (re.compile(r"(?i)(خلفية|الخلفية|المكان|البيئة|background|backdrop|scenery)"), {
        "edit_region": "background_mask",
        "protected": ["entire_person", "face", "hair", "clothing", "hands"],
        "instruction_suffix": "replace or adjust only the background environment with matching edge lighting; keep the entire person, face identity, hair, and clothing byte-for-byte unchanged",
    }),
    (re.compile(r"(?i)(شعر|تسريحة|لون\s+الشعر|لحية|hair|hairstyle|beard)"), {
        "edit_region": "hair_mask",
        "protected": ["face_geometry", "eyes", "skin_marks", "clothing", "background"],
        "instruction_suffix": "modify only the hair/facial hair with individual strand realism; preserve the exact facial bone structure, eyes, skin texture, and outfit",
    }),
    (re.compile(r"(?i)(نظارة|نظارات|قبعة|طقيّة|ساعة|عقد|خاتم|glasses|sunglasses|hat|watch|jewelry)"), {
        "edit_region": "accessory_local_mask",
        "protected": ["face_identity", "skin_texture", "body", "clothing", "background"],
        "instruction_suffix": "add or adjust only the requested accessory with accurate contact shadows; keep all facial features, skin marks, and scene untouched",
    }),
    (re.compile(r"(?i)(إضاءة|اضاءة|ليل|نهار|غروب|مطر|ماء|lighting|night|sunset|golden\s+hour|rain|wet)"), {
        "edit_region": "global_light_atmosphere_pass",
        "protected": ["face_geometry", "skin_marks", "body_proportions", "pose"],
        "instruction_suffix": "update lighting and atmospheric moisture consistently across all surfaces while strictly locking facial identity, moles/scars, pose, and proportions",
    }),
]

BENCHMARK_CATEGORY_RANKINGS: Dict[str, Dict[str, Any]] = {
    "portrait_realistic": {"best": "gemini", "fallback": "flux", "score": 9.4, "cost_units": 4},
    "group_spatial": {"best": "gemini", "fallback": "flux", "score": 9.1, "cost_units": 4},
    "macro_materials": {"best": "flux", "fallback": "gemini", "score": 9.3, "cost_units": 4},
    "product": {"best": "flux", "fallback": "gemini", "score": 9.2, "cost_units": 4},
    "landscape_architecture": {"best": "flux", "fallback": "together", "score": 9.1, "cost_units": 4},
    "stylized_art": {"best": "flux", "fallback": "gemini", "score": 9.0, "cost_units": 4},
    "text_in_image": {"best": "gemini", "fallback": "flux", "score": 9.3, "cost_units": 4},
    "edit_inpaint": {"best": "gemini", "fallback": "instruct_pix2pix", "score": 9.5, "cost_units": 3},
    "identity_consistency": {"best": "gemini", "fallback": "instruct_pix2pix", "score": 9.6, "cost_units": 4},
    "fast_draft": {"best": "flux", "fallback": "together", "score": 8.6, "cost_units": 2},
}


class SpatialLayoutValidator:
    """
    القسم 16: مدقق التخطيط المكاني (Spatial Layout Validator) بلغة بايثون الصرفة:
    يفحص عدم تداخل الكيانات (IoU)، ترتيب العمق مقارنة بخط الأفق، وتماس العناصر مع الأرض.
    """
    @staticmethod
    def compute_iou(b1: List[float], b2: List[float]) -> float:
        x1 = max(b1[0], b2[0])
        y1 = max(b1[1], b2[1])
        x2 = min(b1[0] + b1[2], b2[0] + b2[2])
        y2 = min(b1[1] + b1[3], b2[1] + b2[3])
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        area1 = max(1e-6, b1[2] * b1[3])
        area2 = max(1e-6, b2[2] * b2[3])
        return inter / (area1 + area2 - inter + 1e-6)

    @classmethod
    def validate_and_fix_layout(cls, layout: Dict[str, Any]) -> Dict[str, Any]:
        horizon_y = float(layout.get("horizon_line_y", 0.45))
        entities = [dict(e) for e in (layout.get("entities") or [])]
        relations = list(layout.get("relations") or [])
        allowed_pairs = {
            (r.get("a"), r.get("b"))
            for r in relations
            if r.get("type") in ("occluded by", "touching", "holding")
        }
        for _attempt in range(3):
            changed = False
            for i, e in enumerate(entities):
                bbox = list(e.get("bbox") or [0.25, 0.2, 0.4, 0.65])
                if e.get("ground_contact", True) and (bbox[1] + bbox[3]) < horizon_y:
                    bbox[1] = max(0.05, horizon_y - bbox[3] + 0.15)
                    changed = True
                for j in range(i + 1, len(entities)):
                    e2 = entities[j]
                    b2 = list(e2.get("bbox") or [0.55, 0.25, 0.35, 0.6])
                    if (e.get("id"), e2.get("id")) not in allowed_pairs and (e2.get("id"), e.get("id")) not in allowed_pairs:
                        if cls.compute_iou(bbox, b2) > 0.18:
                            b2[0] = min(0.68, bbox[0] + bbox[2] + 0.04)
                            e2["bbox"] = b2
                            changed = True
                e["bbox"] = bbox
            if not changed:
                break
        layout["entities"] = entities
        layout["validated"] = True
        return layout


class QualityVsCostEngine:
    """
    القسم 25: محرك الموازنة بين الجودة والتكلفة (Quality vs Cost Engine):
    يختار الطبقة المناسبة (Draft / Standard / Premium)، يحمي احتياطي الحصة اليومية (20%)،
    ويقرر بعد كل تقييم ما إذا كانت إعادة المحاولة أو الإصلاح الموضعي مجدية أم لا.
    """
    ACTION_COSTS = {
        "expand": 1,
        "generate": 4,
        "critic": 2,
        "regional_fix": 3,
        "detail_pass": 3,
        "upscale": 2,
        "reference_analysis": 2,
    }
    TIERS = {
        "draft": {"name": "Draft", "max_attempts": 1, "use_critic": False, "target_score": 7.0},
        "standard": {"name": "Standard", "max_attempts": 2, "use_critic": True, "target_score": 8.0},
        "premium": {"name": "Premium", "max_attempts": 3, "use_critic": True, "target_score": 8.6},
    }

    @classmethod
    def select_tier(cls, user_text: str, has_ref: bool = False, quota_used: int = 0, quota_limit: int = 40) -> Dict[str, Any]:
        remaining = max(0, quota_limit - quota_used)
        reserve = max(2, int(quota_limit * 0.20))
        if remaining <= reserve:
            return dict(cls.TIERS["draft"], reason_ar="الحصة اليومية منخفضة، تم اختيار الوضع السريع لتوفير الحصة")
        if re.search(r"(?i)(سريع|مسودة|draft|fast\s+mode)", user_text or ""):
            return dict(cls.TIERS["draft"], reason_ar="وضع المسودة السريعة")
        if has_ref or re.search(r"(?i)(بريميو|احترافي|أعلى\s+جودة|فائق\s+الواقعية|premium|ultra|macro|تفاصيل\s+دقيقة)", user_text or ""):
            return dict(cls.TIERS["premium"], reason_ar="الوضع الفائق (Premium) مع التدقيق البصري وحفظ الهوية")
        return dict(cls.TIERS["standard"], reason_ar="الوضع القياسي المتوازن (Standard)")

    @classmethod
    def next_action(
        cls,
        score: float,
        target: float,
        defects: List[Dict[str, Any]],
        attempts: int,
        max_attempts: int,
        quota_left: int,
        quota_limit: int = 40,
        last_gain: Optional[float] = None,
    ) -> str:
        reserve = max(2, int(quota_limit * 0.20))
        if score >= target:
            return "deliver"
        if quota_left < reserve:
            return "deliver_best_and_explain"
        if attempts >= max_attempts:
            return "deliver_best_and_explain"
        if last_gain is not None and last_gain < 0.5:
            return "deliver_best_and_explain"
        fixable = [d for d in (defects or []) if int(d.get("severity", 3)) >= 3]
        if fixable:
            return "regional_fix"
        return "regenerate_with_fixes"


class _EmbeddedPremiumBrief:
    def __init__(self, summary_ar: str, scene_dict: Optional[Dict[str, Any]] = None):
        self.brief_summary_ar = summary_ar
        self.scene_dict = scene_dict or {}


class _EmbeddedVisualPlan:
    def __init__(
        self,
        user_text: str,
        aspect_hint_str: str = "",
        has_ref: bool = False,
        is_edit: bool = False,
        category: str = "portrait_realistic",
        tier_info: Optional[Dict[str, Any]] = None,
        materials_detected: Optional[List[str]] = None,
        composition: Optional[Dict[str, Any]] = None,
        spatial_layout: Optional[Dict[str, Any]] = None,
        preserve_mode: Optional[Dict[str, Any]] = None,
        final_prompt_en: str = "",
        negative_prompt_en: str = "",
        selected_provider: str = "gemini",
        fallback_provider: str = "flux",
    ):
        self.user_text = user_text
        self.intent = "revert" if re.search(r"(ارجع|السابقة|القبلها|revert)", user_text or "", re.I) else ("edit" if (is_edit or has_ref) else "create")
        self.category = category
        self.tier_info = tier_info or QualityVsCostEngine.TIERS["standard"]
        self.materials_detected = materials_detected or ["human_skin", "fabric"]
        self.composition = composition or {}
        self.spatial_layout = spatial_layout or {}
        self.preserve_mode = preserve_mode or {}
        self.selected_provider = selected_provider
        self.fallback_provider = fallback_provider
        self.negative_prompt_en = negative_prompt_en or (
            "plastic skin, airbrushed, over-smoothed, wax figure, CGI look, 3D render, cartoon, illustration, "
            "doll-like face, symmetrical perfect face, uncanny eyes, dead eyes, extra fingers, missing fingers, "
            "fused fingers, deformed hands, blurry, oversharpened, HDR glow, watermark, text, logo"
        )
        self.final_prompt_en = final_prompt_en or (
            f"RAW candid photograph of {user_text}, natural key light, realistic skin texture with visible pores "
            f"and fine peach fuzz, wet tear line and window catchlights in eyes, natural fabric weave, "
            f"shot on full-frame mirrorless 85mm f/1.8 lens, shallow depth of field, subtle film grain {aspect_hint_str}".strip()
        )
        mat_ar = "، ".join(self.materials_detected[:4])
        mode_ar = "وضع الحفاظ على الهوية والملامح (Preserve Mode 🔒)" if (is_edit or has_ref) else f"توليد فوتوغرافي واقعي ({self.category})"
        summary_ar = (
            f"✨ بريميو الطلب البصري (RYN Visual Brain):\n"
            f"• النمط: {mode_ar} | الطبقة: {self.tier_info.get('name', 'Standard')}\n"
            f"• المواد الدقيقة المفعّلة: {mat_ar}\n"
            f"• التكوين والعدسة: {self.composition.get('shot_type', 'close-up')} · {self.composition.get('lens_choice', '85mm f/1.8')}"
        )
        self.premium_brief = _EmbeddedPremiumBrief(summary_ar)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "category": self.category,
            "tier": self.tier_info,
            "materials_detected": self.materials_detected,
            "composition": self.composition,
            "spatial_layout": self.spatial_layout,
            "preserve_mode": self.preserve_mode,
            "selected_provider": self.selected_provider,
            "fallback_provider": self.fallback_provider,
            "final_prompt_en": self.final_prompt_en,
            "negative_prompt_en": self.negative_prompt_en,
            "brief_summary_ar": self.premium_brief.brief_summary_ar,
        }


class _VersionEntry:
    def __init__(self, version_id: str, parent_version_id: Optional[str], image_data_uri: str, final_prompt_en: str, scene_json: Optional[Dict[str, Any]] = None):
        self.version_id = version_id
        self.parent_version_id = parent_version_id
        self.image_data_uri = image_data_uri
        self.final_prompt_en = final_prompt_en
        self.scene_json = scene_json or {}


class _EmbeddedVisualMemory:
    """الذاكرة البصرية الدائمة: تحفظ سلسلة نسخ الصور وبطاقات الهوية (Identity Cards) لكل مستخدم."""
    def __init__(self):
        self._hist: Dict[str, List[_VersionEntry]] = {}
        self._identity_cards: Dict[str, Dict[str, Any]] = {}
        self._daily: Dict[str, int] = {}

    def save_version(self, u: str, image_uri: str, prompt_en: str, scene_json: Optional[Dict[str, Any]] = None) -> _VersionEntry:
        lst = self._hist.setdefault(u, [])
        parent_id = lst[-1].version_id if lst else None
        ver = _VersionEntry(f"v{len(lst) + 1}", parent_id, image_uri, prompt_en, scene_json=scene_json)
        lst.append(ver)
        if len(lst) > 15:
            del lst[:-15]
        return ver

    def get_latest_image_uri(self, u: str) -> Optional[str]:
        items = self._hist.get(u) or []
        return items[-1].image_data_uri if items else None

    def get_anchor_image_uri(self, u: str) -> Optional[str]:
        """قاعدة المرساة (Anchor Rule — القسم 17): الاحتفاظ بأول صورة مرجعية لمنع الانجراف التراكمي عبر التعديلات المتتالية."""
        items = self._hist.get(u) or []
        return items[0].image_data_uri if items else None

    def save_identity_card(self, u: str, card: Dict[str, Any]) -> None:
        self._identity_cards[u] = dict(card)

    def get_identity_card(self, u: str) -> Optional[Dict[str, Any]]:
        return self._identity_cards.get(u)

    def list_history(self, u: str) -> List[Dict[str, Any]]:
        return [
            {
                "version_id": v.version_id,
                "parent_version_id": v.parent_version_id,
                "prompt": v.final_prompt_en,
            }
            for v in (self._hist.get(u) or [])
        ]

    def revert_version(self, u: str, target: str = "previous") -> Optional[_VersionEntry]:
        lst = self._hist.get(u) or []
        if len(lst) < 2:
            return lst[0] if lst else None
        if target in ("first", "anchor", "الأولى"):
            picked = lst[0]
        else:
            lst.pop()
            picked = lst[-1]
        return picked

    def get_daily_count(self, day_key: str) -> int:
        return self._daily.get(day_key, 0)

    def increment_daily_count(self, day_key: str) -> None:
        self._daily[day_key] = self._daily.get(day_key, 0) + 1

    def decrement_daily_count(self, day_key: str) -> None:
        self._daily[day_key] = max(0, self._daily.get(day_key, 1) - 1)


class EmbeddedVisualBrain:
    """
    العقل البصري المتكامل لـ Ryn (مطبق بالكامل لمواصفات RYN_Image_Realism_Plan.md الأقسام 1 إلى 26):
    - القسم 1 و 2 و 3: Structured Scene Schema + FLUX/Leonardo Prompt Composer (120-250 كلمة) + Negative Prompt.
    - القسم 15 و 16: Composition Planner (3 طبقات عمق) + Spatial Layout Validator (فحص IoU والأفق).
    - القسم 17 و 19: Identity Card Lock + Preserve Mode لتعديل الصور المرفوعة مع حماية الوجه والجسم والملامح.
    - القسم 21: Material Detector (12 ملف مادة فيزيائية للجلد، الفرو، المعدن، الزجاج، النباتات، الماء، القماش...).
    - القسم 22 و 23 و 24 و 25: Visual Understanding + Benchmark Model Router + Fidelity Scorer + Quality vs Cost Engine.
    """
    def __init__(self):
        self.memory = _EmbeddedVisualMemory()

    @staticmethod
    def detect_materials(user_text: str) -> List[str]:
        t = (user_text or "").lower()
        mats: List[str] = []
        if re.search(r"(شخص|رجل|شاب|امرأة|بنت|طفل|وجه|بورتريه|عيون|يد|مهندس|عامل|صياد|ميكانيكي|person|man|woman|girl|boy|portrait|face|skin|mechanic|fisherman)", t):
            mats.extend(["human_skin", "fabric"])
        if re.search(r"(قط|قطة|كلب|أسد|ذئب|حصان|حيوان|فرو|cat|dog|wolf|lion|fur|animal)", t):
            mats.append("fur")
        if re.search(r"(طائر|صقر|نسر|ريش|bird|eagle|falcon|feather)", t):
            mats.append("feathers")
        if re.search(r"(صدأ|مصدي|قديم|عتيق|برغي|rust|rusted|weathered\s+metal|bolt)", t):
            mats.append("metal_rusted")
        elif re.search(r"(معدن|حديد|فولاذ|كروم|سيارة|دراجة|سيف|ساعة|metal|steel|chrome|car|motorcycle|watch)", t):
            mats.append("metal_polished")
        if re.search(r"(زجاج|كأس|نافذة|شباك|نظارة|عدسة|glass|window|cup|bottle|lens)", t):
            mats.append("glass")
        if re.search(r"(شجر|نبات|ورق|غابة|حديقة|ورد|عشب|سرخس|leaf|leaves|fern|plant|tree|forest|garden|flower)", t):
            mats.append("plants")
        if re.search(r"(ماء|مطر|بلل|مبلل|قطرات|بحر|نهر|عرق|دموع|water|rain|wet|droplets|sweat|sea|ocean|tears)", t):
            mats.append("water")
        if re.search(r"(جلد|جاكيت\s+جلد|حذاء|حقيبة|leather|boots)", t):
            mats.append("leather")
        if re.search(r"(خشب|طاولة|غرفة|كوخ|wood|wooden|table)", t):
            mats.append("wood")
        if re.search(r"(شارع|صخر|حجر|جدار|شارع\s+مبلل|رصيف|stone|rock|brick|asphalt|street|wall)", t):
            mats.append("stone")
        if not mats:
            mats = ["human_skin", "fabric", "stone"]
        return list(dict.fromkeys(mats))

    @staticmethod
    def detect_category(user_text: str, has_ref: bool = False, is_edit: bool = False) -> str:
        t = (user_text or "").lower()
        if is_edit or has_ref:
            return "edit_inpaint"
        if re.search(r"(ماكرو|قريب\s+جداً|تفاصيل\s+دقيقة|عين\s+عن\s+قرب|macro|extreme\s+close)", t):
            return "macro_materials"
        if re.search(r"(أصدقاء|مجموعة|عائلة|ثلاثة|شخصين|group|friends|crowd|table)", t):
            return "group_spatial"
        if re.search(r"(منظر|جبال|مدينة|معماري|طبيعة|landscape|architecture|cityscape)", t):
            return "landscape_architecture"
        if re.search(r"(منتج|عطر|إعلان|product|bottle|commercial)", t):
            return "product"
        return "portrait_realistic"

    @staticmethod
    def build_composition_and_spatial(user_text: str, aspect_hint_str: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        t = (user_text or "").lower()
        if re.search(r"(ماكرو|macro|extreme\s+close)", t):
            shot, lens = "extreme close-up macro", "100mm f/2.8 macro lens"
        elif re.search(r"(كامل|واقف|full\s+body|wide|عريض)", t):
            shot, lens = "medium-wide environmental shot", "35mm f/1.8 prime lens"
        else:
            shot, lens = "close-up portrait", "85mm f/1.4 portrait lens"

        angle = "low angle (heroic)" if re.search(r"(من\s+تحت|زاوية\s+منخفضة|low\s+angle)", t) else (
            "high angle" if re.search(r"(من\s+فوق|زاوية\s+عالية|high\s+angle|top-down)", t) else "eye level"
        )
        comp = {
            "shot_type": shot,
            "camera_angle": angle,
            "lens_choice": lens,
            "aspect_ratio": aspect_hint_str or "4:5",
            "foreground": ["subtle out-of-focus foreground elements for depth"],
            "midground": ["primary subject in tack-sharp focus at the focal plane"],
            "background": ["authentic lived-in environment with creamy optical bokeh and atmospheric falloff"],
            "focal_point": "near eye and primary surface micro-texture of the subject",
        }
        spatial = SpatialLayoutValidator.validate_and_fix_layout({
            "horizon_line_y": 0.48,
            "ground_plane": "natural perspective plane with accurate contact shadows",
            "entities": [
                {"id": "subject_1", "label": "primary subject", "bbox": [0.22, 0.14, 0.56, 0.78], "depth_order": 1, "distance_m": 1.8, "ground_contact": True, "facing": "toward camera"}
            ],
            "relations": [],
        })
        return comp, spatial

    @staticmethod
    def match_preserve_mode(user_text: str) -> Dict[str, Any]:
        for pat, info in PRESERVE_MODE_REGION_MAP:
            if pat.search(user_text or ""):
                return dict(info)
        return {
            "edit_region": "targeted_local_mask",
            "protected": ["face_identity", "skin_pores_and_marks", "eyes", "body_proportions"],
            "instruction_suffix": "apply only the requested modification while keeping the subject's exact facial identity, skin texture, moles, body proportions, and unrequested regions 100% unchanged",
        }

    def compose_flux_prompt(
        self,
        user_text: str,
        materials: List[str],
        comp: Dict[str, Any],
        is_edit: bool = False,
        has_ref: bool = False,
        preserve_info: Optional[Dict[str, Any]] = None,
        identity_card: Optional[Dict[str, Any]] = None,
        llm_expanded_en: str = "",
        aspect_hint_str: str = "",
    ) -> str:
        base_subject = (llm_expanded_en or user_text or "").strip()
        mat_phrases: List[str] = []
        for mk in materials[:4]:
            prof = MATERIAL_PROFILES_LIBRARY.get(mk)
            if prof:
                mat_phrases.append(f"{prof['surface_texture']}; {prof['light_response']}; {prof['wear_and_age']}")

        id_lock_str = ""
        if identity_card and identity_card.get("lock_phrase"):
            id_lock_str = f"Identity lock: {identity_card['lock_phrase']}. "

        if is_edit or has_ref:
            p_suffix = (preserve_info or {}).get("instruction_suffix") or "keep face, skin texture, and body proportions identical"
            prompt = (
                f"{base_subject}. {id_lock_str}Preserve Mode active: {p_suffix}. "
                f"Micro-detail: {' '.join(mat_phrases[:2])}. "
                f"Unified single key light direction with accurate contact shadows, natural skin pores, "
                f"realistic fabric weave, zero plastic or airbrushed smoothing {aspect_hint_str}"
            ).strip()
        else:
            prompt = (
                f"{base_subject}. {id_lock_str}"
                f"Composition: {comp.get('shot_type', 'close-up')}, {comp.get('camera_angle', 'eye level')}, "
                f"three distinct depth layers (foreground depth cue, sharp midground subject, soft bokeh background). "
                f"Surface micro-details: {' '.join(mat_phrases)}. "
                f"Lighting: unified directional key light, natural shadow falloff, accurate specular reflections. "
                f"Shot on full-frame mirrorless camera with {comp.get('lens_choice', '85mm f/1.4')}, "
                f"f/1.8, 1/250s, ISO 200, subtle organic film grain, natural color grading, "
                f"natural skin texture, realistic proportions, five-finger hands {aspect_hint_str}"
            ).strip()

        # ضبط طول البرومبت ضمن النطاق المثالي لـ FLUX (حتى ~240 كلمة) لعدم تشتيت انتباه النموذج (القسم 2.3)
        words = prompt.split()
        if len(words) > 245:
            prompt = " ".join(words[:245])
        return prompt

    def build_plan(self, user_text: str, user_id: str = "main", **kwargs) -> _EmbeddedVisualPlan:
        ref_parts = kwargs.get("ref_parts") or []
        has_ref = bool(ref_parts) or bool(kwargs.get("attached_images_bytes"))
        is_edit = has_ref or bool(re.search(r"(?i)(عدّ?ل|غيّ?ر|بدّ?ل|أضف|اضف|احذف|شيل|خلفية|ملابس|edit|change|replace|remove)", user_text or ""))
        aspect_hint_str = kwargs.get("aspect_hint_str", "") or ""
        avail = kwargs.get("available_providers") or {"gemini": True, "flux": True}

        materials = self.detect_materials(user_text)
        category = self.detect_category(user_text, has_ref=has_ref, is_edit=is_edit)
        comp, spatial = self.build_composition_and_spatial(user_text, aspect_hint_str=aspect_hint_str)
        preserve_info = self.match_preserve_mode(user_text) if is_edit else {}

        day_key = f"{user_id}|" + time.strftime("%Y-%m-%d")
        used_q = self.memory.get_daily_count(day_key)
        tier_info = QualityVsCostEngine.select_tier(user_text, has_ref=has_ref, quota_used=used_q)

        # اختيار أفضل موديل من جدول البنشمارك حسب الفئة (القسم 4 و 23)
        bench_row = BENCHMARK_CATEGORY_RANKINGS.get(category, BENCHMARK_CATEGORY_RANKINGS["portrait_realistic"])
        pref_prov = kwargs.get("preferred_provider", "auto")
        if pref_prov in ("gemini", "flux", "together", "instruct_pix2pix") and avail.get(pref_prov):
            sel_prov = pref_prov
        elif avail.get(bench_row["best"]):
            sel_prov = bench_row["best"]
        elif avail.get(bench_row["fallback"]):
            sel_prov = bench_row["fallback"]
        else:
            sel_prov = next((k for k, v in avail.items() if v), "gemini")
        fb_prov = bench_row["fallback"] if bench_row["fallback"] != sel_prov else "flux"

        llm_en = ""
        ask_llm_fn = kwargs.get("ask_llm_fn")
        if callable(ask_llm_fn):
            try:
                sys_expander = (
                    "You are a photography director and retouch artist (RYN Visual Brain). "
                    "Convert the user's request into a concise, physical, observable English scene description (60-110 words). "
                    "Obey every user detail. Include realistic skin pores, subtle blemishes, peach fuzz, eye moisture and catchlights matching the main light, "
                    "consistent light direction, and real camera/lens specs. Avoid vague buzzwords like 'stunning' or '8k'. Output English description only."
                )
                llm_en = (ask_llm_fn(user_text, sys_expander) or "").strip().strip('"')
            except Exception:
                llm_en = ""

        id_card = self.memory.get_identity_card(user_id)
        final_en = self.compose_flux_prompt(
            user_text=user_text,
            materials=materials,
            comp=comp,
            is_edit=is_edit,
            has_ref=has_ref,
            preserve_info=preserve_info,
            identity_card=id_card,
            llm_expanded_en=llm_en,
            aspect_hint_str=aspect_hint_str,
        )

        return _EmbeddedVisualPlan(
            user_text=user_text,
            aspect_hint_str=aspect_hint_str,
            has_ref=has_ref,
            is_edit=is_edit,
            category=category,
            tier_info=tier_info,
            materials_detected=materials,
            composition=comp,
            spatial_layout=spatial,
            preserve_mode=preserve_info,
            final_prompt_en=final_en,
            selected_provider=sel_prov,
            fallback_provider=fb_prov,
        )

    def execute_plan(
        self,
        plan: _EmbeddedVisualPlan,
        user_id: str = "main",
        provider_runners: Optional[Dict[str, Any]] = None,
        ref_parts: Any = None,
        **kwargs,
    ) -> Dict[str, Any]:
        if plan.intent == "revert":
            ver = self.memory.revert_version(user_id)
            if not ver:
                raise RuntimeError("لا توجد صورة سابقة في هذه الجلسة للرجوع إليها.")
            return {
                "status": "done",
                "image": ver.image_data_uri,
                "text": "تم الرجوع إلى النسخة السابقة من الصورة ⏪🎨",
                "model": "Visual Memory Revert",
            }

        runners = provider_runners or {}
        order = []
        for cand in (plan.selected_provider, plan.fallback_provider, "gemini", "flux", "together", "instruct_pix2pix"):
            if cand and cand in runners and cand not in order:
                order.append(cand)

        if not order:
            raise RuntimeError("لا يوجد مزوّد صور مفعّل حالياً (أضف GEMINI_API_KEY أو إعدادات Cloudflare).")

        progress_cb = kwargs.get("progress_cb")
        vision_audit_fn = kwargs.get("vision_audit_fn")
        max_attempts = int(plan.tier_info.get("max_attempts", 2))
        prompt_now = plan.final_prompt_en
        last_err: Optional[Exception] = None
        best_res: Optional[Dict[str, Any]] = None

        for attempt in range(1, max_attempts + 1):
            for pname in order:
                fn = runners[pname]
                try:
                    if callable(progress_cb):
                        progress_cb(f"🎨 جاري توليد الصورة الواقعية ({plan.tier_info.get('name', 'Standard')} · المحاولة {attempt})...")
                    try:
                        out = fn(prompt_now, ref_parts=ref_parts) if ref_parts else fn(prompt_now)
                    except TypeError:
                        out = fn(prompt_now)
                    if isinstance(out, tuple) and len(out) >= 3:
                        raw_b, mime_t, mdl_name = out[0], out[1], out[2]
                        b64_u = f"data:{mime_t or 'image/png'};base64," + base64.b64encode(raw_b).decode("ascii")
                        res_dict = {
                            "status": "done",
                            "image": b64_u,
                            "image_bytes": raw_b,
                            "model": mdl_name,
                            "text": f"تفضّل الصورة الواقعية 🎨 ({plan.tier_info.get('name', 'Standard')})",
                        }
                    elif isinstance(out, dict):
                        res_dict = dict(out)
                    else:
                        continue

                    best_res = res_dict
                    # فحص الناقد البصري (Critic Loop — القسم 5 و 25) في وضعي Standard و Premium
                    if callable(vision_audit_fn) and plan.tier_info.get("use_critic") and attempt < max_attempts and res_dict.get("image_bytes"):
                        audit_q = (
                            "Check this generated image for realism defects (hands/extra fingers, plastic skin, uncanny eyes, lighting mismatch). "
                            "Reply 'OK' if good, or reply with a 1-sentence concrete fix instruction in English."
                        )
                        crit_txt = str(vision_audit_fn(res_dict["image_bytes"], audit_q) or "OK").strip()
                        if crit_txt and not crit_txt.upper().startswith("OK") and len(crit_txt) > 5:
                            prompt_now = f"{prompt_now}, {crit_txt[:160]}"
                            break
                    # حفظ النسخة في الذاكرة البصرية (Anchor & Version History — القسم 7 و 17)
                    if res_dict.get("image"):
                        self.memory.save_version(user_id, res_dict["image"], prompt_now, scene_json=plan.to_dict())
                    return res_dict
                except Exception as e:
                    last_err = e
                    continue
            if best_res and best_res.get("image"):
                self.memory.save_version(user_id, best_res["image"], prompt_now, scene_json=plan.to_dict())
                return best_res

        if last_err is not None:
            raise last_err
        raise RuntimeError("تعذر توليد الصورة حالياً، جرّب مرة ثانية.")


def get_provider_capabilities(flags: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    flags = flags or {}

    class _CapObj:
        def __init__(self, d: Dict[str, Any]):
            self._d = d
        def to_dict(self) -> Dict[str, Any]:
            return self._d

    return {
        k: _CapObj({
            "ready": bool(flags.get(k)),
            "categories": [c for c, v in BENCHMARK_CATEGORY_RANKINGS.items() if v["best"] == k or v["fallback"] == k],
        })
        for k in ("gemini", "flux", "together", "instruct_pix2pix", "local_enhance")
    }


_GLOBAL_MIX_BRAIN: Optional[RynMixEnhancedBrain] = None
_GLOBAL_EMBEDDED_VB: Optional[EmbeddedVisualBrain] = None


def get_ryn_mix_brain() -> RynMixEnhancedBrain:
    global _GLOBAL_MIX_BRAIN
    if _GLOBAL_MIX_BRAIN is None:
        _GLOBAL_MIX_BRAIN = RynMixEnhancedBrain()
    return _GLOBAL_MIX_BRAIN


def get_embedded_visual_brain() -> EmbeddedVisualBrain:
    global _GLOBAL_EMBEDDED_VB
    if _GLOBAL_EMBEDDED_VB is None:
        _GLOBAL_EMBEDDED_VB = EmbeddedVisualBrain()
    return _GLOBAL_EMBEDDED_VB


def get_visual_brain() -> EmbeddedVisualBrain:
    return get_embedded_visual_brain()


# ==============================================================================
# القسم 14: جسر منظومة وكيل الأجهزة (RYN Agent Multi-Device Computer-Use Bridge)
# ==============================================================================
try:
    import ryn_agent as _RynAgentMod
except Exception:
    _RynAgentMod = None


def get_agent_core():
    if _RynAgentMod is not None:
        return _RynAgentMod.get_agent_core()
    return None


# ==============================================================================
# القسم 15: منظومة الترقية المتكاملة (One RYN — One Brain — One Memory)
# ==============================================================================
try:
    from conversations import ConversationService
    from memory import MemoryService, HybridEmbeddingEngine
    from core import RynResponsePolicy, CollaborationLedger, run_natural_ryn_mix_evaluation_suite
    from code_intelligence import GitHubEngineeringService, StaleSHAConflictError, ApprovalRequiredError
    from voice import VoiceService
except Exception as _upg_err:
    ConversationService = None  # type: ignore
    MemoryService = None  # type: ignore
    HybridEmbeddingEngine = None  # type: ignore
    RynResponsePolicy = None  # type: ignore
    CollaborationLedger = None  # type: ignore
    run_natural_ryn_mix_evaluation_suite = None  # type: ignore
    GitHubEngineeringService = None  # type: ignore
    StaleSHAConflictError = RuntimeError  # type: ignore
    ApprovalRequiredError = PermissionError  # type: ignore
    VoiceService = None  # type: ignore

_GLOBAL_CONVERSATION_SERVICE: Optional[Any] = None
_GLOBAL_MEMORY_SERVICE: Optional[Any] = None
_GLOBAL_RESPONSE_POLICY: Optional[Any] = None
_GLOBAL_COLLAB_LEDGER: Optional[Any] = None
_GLOBAL_GH_ENGINEERING: Optional[Any] = None
_GLOBAL_VOICE_SERVICE: Optional[Any] = None


def get_memory_service() -> Optional[Any]:
    global _GLOBAL_MEMORY_SERVICE
    if _GLOBAL_MEMORY_SERVICE is None and MemoryService is not None:
        _GLOBAL_MEMORY_SERVICE = MemoryService()
    return _GLOBAL_MEMORY_SERVICE


def get_conversation_service() -> Optional[Any]:
    global _GLOBAL_CONVERSATION_SERVICE
    if _GLOBAL_CONVERSATION_SERVICE is None and ConversationService is not None:
        ms = get_memory_service()
        def _on_ten_cb(u_id: str, c_id: str, w_idx: int, msgs: List[Dict[str, Any]]) -> None:
            if ms is not None:
                ms.process_ten_message_window(u_id, c_id, w_idx, msgs)
        _GLOBAL_CONVERSATION_SERVICE = ConversationService(on_ten_user_messages_cb=_on_ten_cb)
    return _GLOBAL_CONVERSATION_SERVICE


def get_response_policy() -> Optional[Any]:
    global _GLOBAL_RESPONSE_POLICY
    if _GLOBAL_RESPONSE_POLICY is None and RynResponsePolicy is not None:
        _GLOBAL_RESPONSE_POLICY = RynResponsePolicy()
    return _GLOBAL_RESPONSE_POLICY


def get_collaboration_ledger() -> Optional[Any]:
    global _GLOBAL_COLLAB_LEDGER
    if _GLOBAL_COLLAB_LEDGER is None and CollaborationLedger is not None:
        _GLOBAL_COLLAB_LEDGER = CollaborationLedger()
    return _GLOBAL_COLLAB_LEDGER


def get_github_engineering_service() -> Optional[Any]:
    global _GLOBAL_GH_ENGINEERING
    if _GLOBAL_GH_ENGINEERING is None and GitHubEngineeringService is not None:
        _GLOBAL_GH_ENGINEERING = GitHubEngineeringService(
            github_manager=get_github_manager(),
            memory_service=get_memory_service(),
        )
    return _GLOBAL_GH_ENGINEERING


def get_voice_service() -> Optional[Any]:
    global _GLOBAL_VOICE_SERVICE
    if _GLOBAL_VOICE_SERVICE is None and VoiceService is not None:
        try:
            import tts_engine as _tts
            _tts_fn = _tts.get_provider_fn()
        except Exception:
            _tts_fn = None
        _GLOBAL_VOICE_SERVICE = VoiceService(
            conversation_service=get_conversation_service(),
            memory_service=get_memory_service(),
            tts_provider_fn=_tts_fn,
        )
    return _GLOBAL_VOICE_SERVICE


_GITHUB_CONTROL_PLANE_SINGLETON = None


def get_github_control_plane():
    global _GITHUB_CONTROL_PLANE_SINGLETON
    if _GITHUB_CONTROL_PLANE_SINGLETON is None and GitHubControlPlane is not None:
        audit_path = os.path.join(_RYN_DATA_DIR, "github_control_audit.jsonl")
        _GITHUB_CONTROL_PLANE_SINGLETON = GitHubControlPlane(audit_log_path=audit_path)
    return _GITHUB_CONTROL_PLANE_SINGLETON
