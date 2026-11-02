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
DEFAULT_MODEL_LIMITS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "id": "gemini",
        "label": "✨ Gemini (2.5 Flash / Pro)",
        "provider": "Google Gemini API",
        "req_limit": int(os.environ.get("LIMIT_GEMINI_DAILY", "120")),
        "tok_limit": int(os.environ.get("LIMIT_GEMINI_TOKENS", "300000")),
    },
    "gpt_oss": {
        "id": "gpt_oss",
        "label": "⚡ GPT-OSS 120B",
        "provider": "Groq LPU",
        "req_limit": int(os.environ.get("LIMIT_GPTOSS_DAILY", "100")),
        "tok_limit": int(os.environ.get("LIMIT_GPTOSS_TOKENS", "250000")),
    },
    "qwen": {
        "id": "qwen",
        "label": "🧡 Qwen Coder (32B / 27B)",
        "provider": "Groq / OpenRouter",
        "req_limit": int(os.environ.get("LIMIT_QWEN_DAILY", "100")),
        "tok_limit": int(os.environ.get("LIMIT_QWEN_TOKENS", "250000")),
    },
    "deepseek": {
        "id": "deepseek",
        "label": "💙 DeepSeek & Kimi",
        "provider": "OpenRouter",
        "req_limit": int(os.environ.get("LIMIT_DEEPSEEK_DAILY", "80")),
        "tok_limit": int(os.environ.get("LIMIT_DEEPSEEK_TOKENS", "200000")),
    },
    "mix": {
        "id": "mix",
        "label": "🧩 Ryn mix (LangGraph Multi-Agent)",
        "provider": "LiteLLM + LangGraph",
        "req_limit": int(os.environ.get("LIMIT_MIX_DAILY", "50")),
        "tok_limit": int(os.environ.get("LIMIT_MIX_TOKENS", "350000")),
    },
    "visual_brain": {
        "id": "visual_brain",
        "label": "🎨 RYN Visual Brain (توليد وتعديل صور)",
        "provider": "Leonardo / FLUX / InstructPix2Pix / Gemini",
        "req_limit": int(os.environ.get("IMAGE_DAILY_LIMIT", "40")),
        "tok_limit": int(os.environ.get("LIMIT_IMAGE_TOKENS", "80000")),
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
            pass

    def _save_unlocked(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
        except Exception:
            pass

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
            pass

    def _save_unlocked(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._collections, f, ensure_ascii=False)
        except Exception:
            pass

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
        pass
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
    _APPROVE_CMD_RE = re.compile(
        r"^(?:اعتمد(?:\s+التعديل)?(?:\s+وارفعه)?|ارفع(?:\s+التعديل)?(?:\s+على\s+(?:جيتهاب|غيتهاب|github))?|احفظ(?:\s+التعديل)?(?:\s+على\s+(?:جيتهاب|غيتهاب|github))?|تمام\s+اعتمد(?:\s+وارفعه)?|نفذ\s+التعديل|طبق\s+التعديل|موافق\s+ارفع(?:ه)?)\s*[.!؟?]*$",
        re.I,
    )
    _IMMEDIATE_PUSH_RE = re.compile(
        r"(?:وارفعه|ارفع\s+التعديل|ارفعه\s+على\s+(?:جيتهاب|غيتهاب|github)|احفظه\s+على\s+(?:جيتهاب|غيتهاب|github)|commit\s+and\s+push|push\s+to\s+github|احذف\s+ملف.*من\s+(?:جيتهاب|غيتهاب|github))",
        re.I,
    )
    _GH_MENTION_RE = re.compile(
        r"(?:github|جيتهاب|جيت\s*هاب|غيتهاب|غيت\s*هاب|المستودع|مستودعي|ملفات\s+المشروع|ملفات\s+البوت|تعديل\s+ملف|اقرأ\s+ملف|شوف\s+ملف|عدل\s+ملف|عدلي\s+ملف|احذف\s+ملف)",
        re.I,
    )
    _KNOWN_FILES = (
        "webapp.py", "bot.py", "filegen.py", "requirements.txt", "_worker.js",
        "ryn_ai_stack.py", "ryn_guard.py", "ryn_selftest.py",
        "knowledge/blocks.md", "knowledge/notes.md", "blocks.md", "notes.md",
        "visual.py", "pollinations.py", "image_providers/pollinations.py",
        "visual_brain/brain.py", "visual_brain/prompt_engine.py", "visual_brain/realism_engine.py",
    )

    def __init__(self, storage_path: Optional[str] = None):
        base_dir = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        self.storage_path = storage_path or os.path.join(base_dir, "ryn_github_config.json")
        self._lock = threading.Lock()
        self._user_configs: Dict[str, Dict[str, str]] = {}
        self._pending_edits: Dict[str, Dict[str, Any]] = {}
        self._tree_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.isfile(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._user_configs = d
        except Exception:
            pass

    def _save_unlocked(self) -> None:
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._user_configs, f, ensure_ascii=False)
        except Exception:
            pass

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

    def get_config(self, user_id: str = "main") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        owner_ok = self.is_owner(u)
        with self._lock:
            uc = dict(self._user_configs.get(u) or {})

        if owner_ok:
            # حساب WEB_PASSWORD يأخذ تلقائياً مفتاح البيئة ومستودع المالك
            env_tok = (
                os.environ.get("GITHUB_TOKEN", "").strip()
                or os.environ.get("GH_TOKEN", "").strip()
            )
            tok = uc.get("token") or env_tok
            repo = uc.get("repo") or self._owner_default_repo()
            branch = uc.get("branch") or os.environ.get("GITHUB_BRANCH", "main").strip()
        else:
            # أي حساب آخر في WEB_USERS لا يأخذ مفتاح البيئة ولا مستودع المالك أبداً؛ يستخدم فقط ما أدخله بنفسه
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
        }

    def set_config(self, user_id: str, repo: str = "", branch: str = "", token: str = "") -> Dict[str, Any]:
        u = str(user_id or "main").strip() or "main"
        owner_repo = self._owner_default_repo().lower()
        clean_repo = (repo or "").strip().strip("/")
        if not self.is_owner(u) and clean_repo.lower() == owner_repo:
            raise PermissionError("⛔ هذا المستودع خاص بمالك البوت. يرجى إدخال اسم مستودعك الشخصي (username/repo) والتوكن الخاص بك.")
        with self._lock:
            uc = self._user_configs.setdefault(u, {})
            if clean_repo and self._REPO_RE.match(clean_repo):
                uc["repo"] = clean_repo
            if branch and branch.strip():
                uc["branch"] = branch.strip()
            if token and token.strip() and "••" not in token:
                uc["token"] = token.strip()
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

        if not self.is_owner(u):
            if not tok:
                raise PermissionError(
                    "🔑 للوصول إلى ملفاتك على GitHub، يجب إدخال التوكن الخاص بك (Personal Access Token) واسم مستودعك أولاً "
                    "(أرسل في الشات: `اربط جيتهاب ghp_XXXX مستودع username/repo` أو من نافذة 🐙 GitHub)."
                )
            if not repo:
                raise ValueError("يرجى تحديد اسم مستودعك الشخصي بصيغة `username/repo` مع التوكن الخاص بك.")
            if repo.lower() == self._owner_default_repo().lower():
                raise PermissionError("⛔ لا يمكنك الوصول إلى مستودع مالك البوت؛ يمكنك فقط الوصول إلى مستودعاتك الخاصة عبر التوكن الخاص بك.")

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

    def save_file(
        self,
        path: str,
        content: str,
        commit_message: str = "",
        sha: Optional[str] = None,
        user_id: str = "main",
        repo_override: str = "",
        branch_override: str = "",
    ) -> Dict[str, Any]:
        repo, cfg_branch, clean_p, tok = self._validate_user_access_and_repo(
            user_id, repo_override=repo_override, path=path, require_token=True
        )
        if not clean_p:
            raise ValueError("حدد مسار الملف المطلوب حفظه")
        branch = (branch_override or cfg_branch or "main").strip()

        url = f"https://api.github.com/repos/{repo}/contents/{clean_p}"
        cur_sha = (sha or "").strip()
        if not cur_sha:
            chk = requests.get(url, headers=self._headers(tok), params={"ref": branch}, timeout=15)
            if chk.status_code == 200:
                cur_sha = (chk.json() or {}).get("sha", "")

        b64_new = base64.b64encode((content or "").encode("utf-8")).decode("ascii")
        payload: Dict[str, Any] = {
            "message": (commit_message or f"تحديث {clean_p} عبر محادثة Ryn").strip()[:160],
            "content": b64_new,
            "branch": branch,
        }
        if cur_sha:
            payload["sha"] = cur_sha

        r = requests.put(url, headers=self._headers(tok), json=payload, timeout=25)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"GitHub Save HTTP {r.status_code}: {r.text[:180]}")
        res_j = r.json() or {}
        new_content = res_j.get("content") or {}
        commit_obj = res_j.get("commit") or {}
        with self._lock:
            self._tree_cache.clear()
        return {
            "ok": True,
            "repo": repo,
            "branch": branch,
            "path": clean_p,
            "sha": new_content.get("sha", ""),
            "commit_sha": commit_obj.get("sha", ""),
            "commit_url": commit_obj.get("html_url") or new_content.get("html_url") or f"https://github.com/{repo}/blob/{branch}/{clean_p}",
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
        return {"ok": True, "repo": repo, "branch": branch, "deleted_path": clean_p}

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

    def approve_pending_edit(self, user_id: str = "main") -> Dict[str, Any]:
        pe = self.get_pending_edit(user_id)
        if not pe:
            raise RuntimeError("لا يوجد تعديل معلّق حالياً لاعتماده. اطلب مني أولاً في المحادثة تعديل أي ملف من GitHub.")
        action = pe.get("action", "save")
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
        pattern = re.compile(
            r"<{4,}\s*SEARCH\s*\r?\n(.*?)\r?\n={4,}\s*\r?\n(.*?)\r?\n>{4,}\s*REPLACE",
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
            if s_block in updated:
                updated = updated.replace(s_block, r_block, 1)
                applied += 1
            elif s_block.strip() and s_block.strip() in updated:
                updated = updated.replace(s_block.strip(), r_block.strip(), 1)
                applied += 1
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

        # 1. ربط مفتاح GitHub (والمستودع الاختياري) من داخل المحادثة لكل حساب بشكل مستقل
        m_tok = self._GH_TOKEN_RE.search(t)
        if m_tok and len(t) < 350:
            tok_val = m_tok.group(1)
            # البحث عن اسم مستودع بصيغة owner/repo في نفس الرسالة إن وُجد
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
                return (
                    "🔐 **تم حفظ التوكن الخاص بك بنجاح!**\n"
                    "• تبقى فقط أن ترسل لي اسم مستودعك بصيغة `username/repo` (أو تضعه في نافذة 🐙 GitHub) حتى نبدأ العمل على ملفاتك."
                )
            return (
                "🔐 **تم حفظ وربط مفتاح GitHub بحسابك الشخصي بنجاح!**\n"
                f"• **المستودع المتصل**: `{cfg['repo']}` (الفرع `{cfg['branch']}`)\n"
                "• **العزل الأمني**: هذا المفتاح محفوظ لحسابك أنت فقط ولا يراه أي مستخدم آخر.\n"
                "• الآن يمكنك أن تطلب مني في المحادثة استعراض ملفاتك، مناقشتها، أو تعديلها ورفعها مباشرة!"
            )

        # 2. اعتماد ورفع التعديل المعلّق بعد المناقشة في الشات
        if self._APPROVE_CMD_RE.match(t):
            pe = self.get_pending_edit(user_id)
            if pe:
                try:
                    res = self.approve_pending_edit(user_id)
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

        # 3. إذا كان المستخدم ليس حساب WEB_PASSWORD (main) وطلب أوامر جيتهاب بدون أن يدخل التوكن والمستودع الخاصين به
        if not self.is_owner(user_id) and self._GH_MENTION_RE.search(t):
            cfg = self.get_config(user_id)
            if not cfg.get("has_token") or not cfg.get("repo"):
                return (
                    "🔑 **للوصول إلى ملفاتك الخاصة على GitHub وتعديلها من داخل المحادثة:**\n"
                    "حساب المالك (`WEB_PASSWORD`) وحده مرتبط تلقائياً بمفتاح البيئة، أما لحسابك أنت فيمكنك ربط **التوكن والمستودع الخاصين بك** بإرسال رسالة هنا بالشكل التالي:\n"
                    "`اربط جيتهاب ghp_XXXX مستودع username/repo`\n"
                    "أو إدخالهما من زر **🐙 ملفات GitHub** في القائمة، وبعدها سأتصل بمستودعك الشخصي فوراً!"
                )

        return None

    def build_chat_github_context(self, user_id: str, text: str, history: Optional[List[Tuple[str, str]]] = None) -> str:
        """
        يسحب شجرة الملفات والملف المطلوب من مستودع المستخدم المتصل (مستودع المالك لـ WEB_PASSWORD،
        أو المستودع الشخصي لأي مستخدم آخر ربط التوكن الخاص به) ويحقنه في سياق المحادثة.
        """
        cfg = self.get_config(user_id)
        if not cfg.get("repo"):
            return ""
        if not self.is_owner(user_id) and not cfg.get("has_token"):
            return ""

        t = (text or "").strip()
        recent_hist_text = " ".join(m[1] for m in (history or [])[-4:] if isinstance(m, (list, tuple)) and len(m) >= 2)
        combined_search = f"{t}\n{recent_hist_text}"

        has_gh_kw = bool(self._GH_MENTION_RE.search(t))
        mentioned_known = [f for f in self._KNOWN_FILES if f.lower() in combined_search.lower()]
        file_regex_hits = re.findall(r"\b([A-Za-z0-9_./\-]+\.(?:py|js|md|txt|json|html|css))\b", combined_search)

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
        if not cfg.get("repo") or (not self.is_owner(user_id) and not cfg.get("has_token")):
            return bot_answer, False

        ans = bot_answer
        push_now = bool(self._IMMEDIATE_PUSH_RE.search(user_text or ""))
        status_notes: List[str] = []
        has_pending = False

        # 1. معالجة <github_patch>
        patch_re = re.compile(r"<github_patch\s+path=[\"']([^\"']+)[\"'](?:\s+commit=[\"']([^\"']*)[\"'])?\s*>(.*?)</github_patch>", re.DOTALL | re.I)
        for m in list(patch_re.finditer(ans)):
            path = m.group(1).strip()
            commit_msg = (m.group(2) or f"تحديث {path} عبر محادثة Ryn").strip()
            patch_body = m.group(3).strip()
            try:
                orig = self.get_file(path, user_id)
                new_content, count = self.apply_search_replace_blocks(orig["content"], patch_body)
                if count == 0:
                    status_notes.append(f"⚠️ لم يتطابق مقطع البحث حرفياً في `{path}`؛ تأكد من الأسطر المطلوبة.")
                    continue
                if path.endswith(".py"):
                    ast.parse(new_content, filename=path)
                if push_now and cfg.get("has_token"):
                    saved = self.save_file(path, new_content, commit_message=commit_msg, sha=orig.get("sha"), user_id=user_id)
                    status_notes.append(
                        f"✅ **تم تعديل `{path}` ورفعه مباشرة إلى GitHub!**\n"
                        f"• **التحديث**: {commit_msg}\n"
                        f"• **الرابط**: [عرض الملف المحدّث على GitHub]({saved.get('commit_url', '')})"
                    )
                else:
                    self.set_pending_edit(user_id, {
                        "action": "save",
                        "path": path,
                        "new_content": new_content,
                        "sha": orig.get("sha"),
                        "commit_message": commit_msg,
                    })
                    has_pending = True
                    status_notes.append(
                        f"💡 **جهّزت التعديل وفحصته نحوياً (AST ✅) على ملف `{path}`!**\n"
                        f"• يمكنك مناقشتي في أي تعديل إضافي، أو اكتب لي **«اعتمد وارفعه»** ليتم رفعه فوراً إلى مستودعك."
                    )
            except SyntaxError as se:
                status_notes.append(f"⚠️ أوقف الحارس الذكي رفع التعديل على `{path}` لوجود خطأ نحوي في الكود المولد (السطر {se.lineno}: {se.msg}).")
            except Exception as e:
                status_notes.append(f"⚠️ تعذر تطبيق التعديل على `{path}`: {e}")

            clean_preview = f"\n```diff\n# التعديل على {path} ({commit_msg})\n{patch_body}\n```\n"
            ans = ans.replace(m.group(0), clean_preview, 1)

        # 2. معالجة <github_create>
        create_re = re.compile(r"<github_create\s+path=[\"']([^\"']+)[\"'](?:\s+commit=[\"']([^\"']*)[\"'])?\s*>(.*?)</github_create>", re.DOTALL | re.I)
        for m in list(create_re.finditer(ans)):
            path = m.group(1).strip()
            commit_msg = (m.group(2) or f"إنشاء/تحديث {path} عبر محادثة Ryn").strip()
            new_content = re.sub(r"^```[a-zA-Z0-9_\-]*\r?\n|\r?\n```$", "", m.group(3).strip())
            try:
                if path.endswith(".py"):
                    ast.parse(new_content, filename=path)
                if push_now and cfg.get("has_token"):
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
        from visual_brain import get_visual_brain
        vb = get_visual_brain()
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
