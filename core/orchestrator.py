# -*- coding: utf-8 -*-
"""
core/orchestrator.py — RYN Mix كطبقة قيادة (Phase E من الخطة)
=============================================================
INPUT → INTENT → PLAN → ROUTE → EXECUTE → VALIDATE → SYNTHESIS → AUDIT

- RYN Mix هو نقطة الدخول وصاحب الرد النهائي دايماً؛ الموديلات المتخصصة أدوات بس.
- التوجيه (routing) قابل للإعداد ومُسجّل بسبب مختصر (بدون سلسلة تفكير داخلية وبدون أسرار).
- أدوات مسجّلة فقط، بمخطط مدخلات، مهلة، وصلاحية؛ وإجراءات خطرة بتحتاج تأكيد صريح.
- ميزانيات: وقت، عدد أدوات، إعادة محاولات. فشل أي متخصص => رجوع آمن لرد RYN Mix المباشر.
- أنظمة محمية (صور/كتالوج الموديلات): ما بنوجّه لها من هون؛ بنرجّع "protected" وبتكمل المسار الحالي.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z\-_]{20,}|sk-[A-Za-z0-9\-_]{16,}|Bearer\s+[A-Za-z0-9\-._~+/]+=*)"
)


def redact_secrets(text: Any) -> str:
    return _SECRET_RE.sub("[REDACTED]", str(text or ""))


STAGES = ("input", "intent", "plan", "route", "execute", "validate", "synthesis", "audit")

_CONFIRM_PATTERNS = {
    "destructive": re.compile(r"(احذف|امسح|حذف|delete|remove|drop\s+table|wipe)", re.I),
    "financial": re.compile(r"(اشتري|بيع|حوّل|حول\s+مبلغ|ادفع|buy|sell|transfer|pay\b|trade)", re.I),
    "publish": re.compile(r"(انشر|ارفع\s+على|push|publish|deploy|ادفع\s+للريبو)", re.I),
    "privacy": re.compile(r"(ابعت\s+بياناتي|شارك\s+(ملفاتي|بياناتي)|share\s+my\s+(data|files)|export\s+all)", re.I),
}
_INTENT_PATTERNS = (
    ("image", re.compile(r"(ارسم|صورة|صوّر|ولّد\s+صورة|عدّل\s+الصورة|generate\s+image|draw|edit\s+(this\s+)?image)", re.I)),
    ("code", re.compile(r"(كود|برمج|دالة|خطأ\s+برمجي|bug|function|class\s|python|javascript|github|repo|اختبار(ات)?\s+الوحدة)", re.I)),
    ("research", re.compile(r"(ابحث|بحث|مصادر|اخر\s+اخبار|research|latest|compare|قارن)", re.I)),
    ("writing", re.compile(r"(اكتب|صيغ|لخّص|لخص|ترجم|write|summar|translate|draft)", re.I)),
)


@dataclass
class Budget:
    max_seconds: float = 45.0
    max_tool_calls: int = 4
    max_retries: int = 1

    @classmethod
    def from_env(cls) -> "Budget":
        def f(k, d, cast):
            try:
                return cast(os.environ.get(k, d))
            except Exception:
                return cast(d)
        return cls(f("RYN_MIX_MAX_SECONDS", 45, float), f("RYN_MIX_MAX_TOOL_CALLS", 4, int), f("RYN_MIX_MAX_RETRIES", 1, int))


@dataclass
class Specialist:
    name: str
    fn: Callable[[Dict[str, Any]], Any]
    handles: List[str]                      # أنواع النوايا اللي بيفيد فيها
    description: str = ""
    input_schema: Dict[str, type] = field(default_factory=lambda: {"text": str})
    timeout_sec: float = 20.0
    needs_confirmation: bool = False        # لو الأداة بحد ذاتها بتأثر برّا (نشر/حذف/مال)
    validator: Optional[Callable[[Any], bool]] = None
    enabled: bool = True


class RynMixOrchestrator:
    def __init__(
        self,
        direct_answer_fn: Callable[[str, Dict[str, Any]], str],
        synthesize_fn: Optional[Callable[[str, List[Dict[str, Any]]], str]] = None,
        budget: Optional[Budget] = None,
        db_path: Optional[str] = None,
    ):
        self.direct_answer_fn = direct_answer_fn
        self.synthesize_fn = synthesize_fn
        self.budget = budget or Budget.from_env()
        base = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        os.makedirs(base, exist_ok=True)
        self.db_path = db_path or os.environ.get("RYN_DB_PATH") or os.path.join(base, "ryn_canonical.db")
        self._specialists: Dict[str, Specialist] = {}
        self._lock = threading.RLock()
        self._init_db()

    # ---------- تسجيل الأدوات ----------
    def register_specialist(self, spec: Specialist) -> None:
        if not re.match(r"^[a-z][a-z0-9_]{1,40}$", spec.name):
            raise ValueError("اسم الأداة غير صالح")
        self._specialists[spec.name] = spec

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name in self._specialists:
            self._specialists[name].enabled = bool(enabled)

    # ---------- قاعدة التدقيق ----------
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS mix_audit (
                        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, intent TEXT NOT NULL, route TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '', stages_json TEXT NOT NULL DEFAULT '[]',
                        outcome TEXT NOT NULL DEFAULT '', elapsed REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL)"""
                )
        finally:
            conn.close()

    def list_audit(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM mix_audit WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (str(user_id), int(limit))).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["stages"] = json.loads(d.pop("stages_json") or "[]")
            out.append(d)
        return out

    # ---------- المراحل ----------
    @staticmethod
    def understand(text: str) -> Dict[str, Any]:
        t = str(text or "")
        intent = "chat"
        for name, pat in _INTENT_PATTERNS:
            if pat.search(t):
                intent = name
                break
        risks = [k for k, pat in _CONFIRM_PATTERNS.items() if pat.search(t)]
        lang = "ar" if re.search(r"[\u0600-\u06FF]", t) else "en"
        return {"intent": intent, "risks": risks, "language": lang, "length": len(t)}

    def plan(self, intent: Dict[str, Any]) -> List[str]:
        steps = ["فهم الطلب"]
        if intent["intent"] in ("code", "research"):
            steps.append("اختيار أداة متخصصة إذا بتفيد")
            steps.append("مراجعة مخرجات الأداة")
        steps.append("صياغة الرد النهائي بواسطة RYN Mix")
        return steps

    def route(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        if intent["intent"] == "image":
            return {"route": "protected", "specialist": None,
                    "reason": "طلب صور: نظام محمي؛ بيكمل بمساره الحالي بدون تدخل من الموجّه."}
        for spec in self._specialists.values():
            if spec.enabled and intent["intent"] in spec.handles:
                return {"route": "specialist", "specialist": spec.name, "reason": f"النية '{intent['intent']}' بتطابق قدرات الأداة {spec.name}."}
        return {"route": "direct", "specialist": None, "reason": "ما في أداة بتضيف قيمة؛ رد مباشر من RYN Mix."}

    @staticmethod
    def _validate_input(spec: Specialist, payload: Dict[str, Any]) -> Optional[str]:
        for k, typ in spec.input_schema.items():
            if k not in payload or not isinstance(payload[k], typ):
                return f"حقل '{k}' مفقود أو نوعه غلط"
        extra = set(payload) - set(spec.input_schema) - {"user_id", "context"}
        return f"حقول غير مسموحة: {sorted(extra)}" if extra else None

    def _call_with_timeout(self, spec: Specialist, payload: Dict[str, Any], timeout: float):
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(spec.fn, payload)
        try:
            return fut.result(timeout=max(0.05, timeout))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    # ---------- التنفيذ الكامل ----------
    def run(self, user_id: str, text: str, context: Optional[Dict[str, Any]] = None,
            confirmed_actions: Optional[List[str]] = None) -> Dict[str, Any]:
        t0 = time.time()
        stages: List[Dict[str, Any]] = [{"stage": "input", "ok": True}]
        ctx = dict(context or {})
        confirmed = set(confirmed_actions or [])
        intent = self.understand(text)
        stages.append({"stage": "intent", "ok": True, "intent": intent["intent"], "risks": intent["risks"]})
        steps = self.plan(intent)
        stages.append({"stage": "plan", "ok": True, "steps": steps})
        decision = self.route(intent)
        stages.append({"stage": "route", "ok": True, **decision})

        outputs: List[Dict[str, Any]] = []
        outcome = "direct"
        route_used = decision["route"]
        confirm_needed: List[str] = []

        # إجراءات خطرة: لازم تأكيد صريح قبل أي أداة
        pending = [r for r in intent["risks"] if r not in confirmed]
        spec = self._specialists.get(decision["specialist"]) if decision["specialist"] else None
        if spec and spec.needs_confirmation and spec.name not in confirmed:
            pending.append(f"tool:{spec.name}")
        if pending:
            confirm_needed = pending
            stages.append({"stage": "execute", "ok": False, "blocked": "needs_confirmation", "actions": pending})
            outcome = "needs_confirmation"
            res = {"answer": "", "needs_confirmation": confirm_needed, "route": route_used, "intent": intent["intent"]}
            return self._finish(user_id, intent, decision, stages, outcome, t0, res)

        if spec:
            calls = 0
            attempt = 0
            payload = {"text": str(text), "user_id": str(user_id), "context": ctx}
            err = self._validate_input(spec, {k: v for k, v in payload.items()})
            if err:
                stages.append({"stage": "execute", "ok": False, "error": err})
                route_used = "direct_fallback"
            else:
                while calls < self.budget.max_tool_calls and attempt <= self.budget.max_retries:
                    left = self.budget.max_seconds - (time.time() - t0)
                    if left <= 0:
                        stages.append({"stage": "execute", "ok": False, "error": "budget_time"})
                        break
                    calls += 1
                    attempt += 1
                    try:
                        out = self._call_with_timeout(spec, payload, min(spec.timeout_sec, left))
                        ok = spec.validator(out) if spec.validator else bool(out)
                        stages.append({"stage": "validate", "ok": bool(ok), "tool": spec.name})
                        if ok:
                            outputs.append({"tool": spec.name, "output": out})
                            stages.append({"stage": "execute", "ok": True, "tool": spec.name, "calls": calls})
                            break
                    except Exception as e:  # timeout أو خطأ داخلي
                        stages.append({"stage": "execute", "ok": False, "tool": spec.name, "error": redact_secrets(type(e).__name__)})
                if not outputs:
                    route_used = "direct_fallback"
                else:
                    route_used = "specialist"

        # التوليف: RYN Mix هو صاحب الرد النهائي دايماً
        try:
            if outputs and self.synthesize_fn:
                answer = self.synthesize_fn(str(text), outputs)
            else:
                answer = self.direct_answer_fn(str(text), {**ctx, "specialist_outputs": outputs})
            stages.append({"stage": "synthesis", "ok": True, "owner": "ryn_mix"})
            outcome = "answered" if route_used != "direct_fallback" else "answered_fallback"
        except Exception as e:
            answer = ""
            stages.append({"stage": "synthesis", "ok": False, "error": redact_secrets(type(e).__name__)})
            outcome = "error"
        res = {"answer": answer, "route": route_used, "intent": intent["intent"], "needs_confirmation": [],
               "tools_used": [o["tool"] for o in outputs]}
        return self._finish(user_id, intent, decision, stages, outcome, t0, res)

    def _finish(self, user_id, intent, decision, stages, outcome, t0, res):
        elapsed = round(time.time() - t0, 3)
        audit_id = f"mix_{uuid.uuid4().hex[:12]}"
        stages.append({"stage": "audit", "ok": True})
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO mix_audit (id, user_id, intent, route, reason, stages_json, outcome, elapsed, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (audit_id, str(user_id), intent["intent"], res.get("route", decision["route"]), redact_secrets(decision["reason"])[:300],
                     redact_secrets(json.dumps(stages, ensure_ascii=False))[:4000], outcome, elapsed, time.time()),
                )
        finally:
            conn.close()
        res.update({"audit_id": audit_id, "outcome": outcome, "elapsed": elapsed})
        return res
