# -*- coding: utf-8 -*-
"""
core/ryn_ultra.py — RYN ULTRA: Five-Agent Collaborative Intelligence System
===========================================================================
Implements the complete RYN Ultra add-on specification (Sections 1..15):
  - Five specialized AI team members:
      1. Gemini   — Chief Strategist (Understanding, research, planning)
      2. GPT      — Chief Architect (Reasoning, architecture, technical decisions)
      3. Qwen     — Lead Developer (Programming and implementation)
      4. DeepSeek — Critical Reviewer (Code review, debugging, verification)
      5. Kimi     — Context Analyst (Large files, specifications, long-context analysis)
  - Multi-Round Discussion Engine (Rounds 1..5 + user-requested extra rounds)
  - Shared Intelligence & Memory context pack (immutable original request, transcript,
    project excerpts, disagreements, rejected proposals, verification results)
  - Full User Controls (broadcast message, private instruction to 1 agent, interrupt,
    pause, resume, stop, extra round, explain reasoning, update task, approve changes)
  - Read-only Project Analysis until explicit user approval (Section 7)
  - Structured 10-point Final Report distinguishing model opinions from verified facts
  - Per-user Admin Configuration (roles, enabled flags, max rounds, timeouts, motion)
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple


_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z\-_]{20,}|sk-[A-Za-z0-9\-_]{16,})"
)


def redact_secrets(text: Any) -> str:
    return _SECRET_RE.sub("[REDACTED]", str(text or ""))


# ---------------------------------------------------------------------------
# Section 2 & 3: Five AI Team Members & Robot Visual Identity
# ---------------------------------------------------------------------------

DEFAULT_ULTRA_AGENTS: List[Dict[str, Any]] = [
    {
        "id": "gemini",
        "name": "Gemini",
        "role": "Chief Strategist",
        "role_ar": "الاستراتيجي الرئيسي",
        "responsibility": "Understanding, research, planning",
        "color": "#a78bfa",
        "avatar_variant": "strategist_crown",
        "default_tag": "Proposal",
        "enabled": True,
        "system_prompt": (
            "You are Gemini — Chief Strategist in a live RYN Ultra 5-agent engineering meeting. "
            "Your focus: understand the complete user request, identify the actual objective and missing requirements, "
            "research solutions, prepare the initial project plan, and explain risks and limitations."
        ),
    },
    {
        "id": "gpt",
        "name": "GPT",
        "role": "Chief Architect",
        "role_ar": "المعماري الرئيسي",
        "responsibility": "Reasoning, architecture, technical decisions",
        "color": "#34d399",
        "avatar_variant": "architect_visor",
        "default_tag": "Objection",
        "enabled": True,
        "system_prompt": (
            "You are GPT — Chief Architect in a live RYN Ultra 5-agent engineering meeting. "
            "Your focus: review Gemini's proposed plan, challenge weak architectural decisions with evidence, "
            "design the technical architecture, define system components/dependencies, and ensure consistency."
        ),
    },
    {
        "id": "qwen",
        "name": "Qwen",
        "role": "Lead Developer",
        "role_ar": "المطور الرئيسي",
        "responsibility": "Programming and implementation",
        "color": "#fb923c",
        "avatar_variant": "developer_cyber",
        "default_tag": "Technical suggestion",
        "enabled": True,
        "system_prompt": (
            "You are Qwen — Lead Developer in a live RYN Ultra 5-agent engineering meeting. "
            "Your focus: analyze source code, implement approved changes, write clean code, fix errors, "
            "and explain every file modification clearly."
        ),
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "role": "Critical Reviewer",
        "role_ar": "المراجع الناقد",
        "responsibility": "Code review, debugging, verification",
        "color": "#38bdf8",
        "avatar_variant": "reviewer_scanner",
        "default_tag": "Review",
        "enabled": True,
        "system_prompt": (
            "You are DeepSeek — Critical Reviewer in a live RYN Ultra 5-agent engineering meeting. "
            "Your focus: review proposed code and ideas, detect logical/security/performance flaws, "
            "challenge incorrect technical conclusions with concrete evidence, and verify against requirements."
        ),
    },
    {
        "id": "kimi",
        "name": "Kimi",
        "role": "Context Analyst",
        "role_ar": "محلل السياق والمشاريع",
        "responsibility": "Large files, specifications, long-context analysis",
        "color": "#f472b6",
        "avatar_variant": "context_radar",
        "default_tag": "Context finding",
        "enabled": True,
        "system_prompt": (
            "You are Kimi — Context Analyst in a live RYN Ultra 5-agent engineering meeting. "
            "Your focus: analyze project structure and specifications, compare existing and proposed files, "
            "track requirements throughout the discussion, and flag any missing components or inconsistencies."
        ),
    },
]

ROUND_DEFINITIONS: Dict[int, Dict[str, str]] = {
    1: {
        "name": "Round 1 — Independent Analysis",
        "name_ar": "الجولة 1 — التحليل المستقل",
        "instruction": (
            "Round 1 (Independent Analysis): Independently analyze the user's request and project context from your specialty. "
            "State your initial analysis, key requirements, and concrete proposal concisely."
        ),
    },
    2: {
        "name": "Round 2 — Team Discussion",
        "name_ar": "الجولة 2 — نقاش الفريق",
        "instruction": (
            "Round 2 (Team Discussion): Examine the Round 1 conclusions from the other agents. "
            "Respond directly to specific points, support strong ideas, or raise reasoned objections with evidence."
        ),
    },
    3: {
        "name": "Round 3 — Critical Debate",
        "name_ar": "الجولة 3 — النقاش النقدي وتحدي الافتراضات",
        "instruction": (
            "Round 3 (Critical Debate): Challenge any weak assumptions, identify edge cases or missing requirements, "
            "and compare trade-offs without repeating earlier arguments."
        ),
    },
    4: {
        "name": "Round 4 — Revision & Refinement",
        "name_ar": "الجولة 4 — المراجعة وتنقيح الحل",
        "instruction": (
            "Round 4 (Revision): Revise or refine your recommendation based on the debate evidence, "
            "or clearly state why you maintain a dissenting position."
        ),
    },
    5: {
        "name": "Round 5 — Final Decision & Synthesis",
        "name_ar": "الجولة 5 — القرار النهائي والتقرير الموحد",
        "instruction": (
            "Round 5 (Final Decision): Provide your final verified position and actionable takeaway for the final report."
        ),
    },
}

ROBOT_STATES = ("idle", "thinking", "speaking", "listening", "reviewing", "error", "offline")


# ---------------------------------------------------------------------------
# RYN Ultra Engine
# ---------------------------------------------------------------------------

class RynUltraEngine:
    """Orchestrates 5-agent live meetings, turn-by-turn streaming state, user controls, and final reports."""

    def __init__(self, db_path: Optional[str] = None):
        base = os.environ.get("RYN_DATA_DIR", tempfile.gettempdir())
        os.makedirs(base, exist_ok=True)
        self.db_path = db_path or os.path.join(base, "ryn_ultra.db")
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS ultra_settings (
                        user_id TEXT PRIMARY KEY,
                        config_json TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS ultra_meetings (
                        meeting_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        original_request TEXT NOT NULL,
                        current_task TEXT NOT NULL,
                        status TEXT NOT NULL,
                        current_round INTEGER NOT NULL DEFAULT 1,
                        max_rounds INTEGER NOT NULL DEFAULT 3,
                        active_speaker TEXT NOT NULL DEFAULT '',
                        plan_approval_required INTEGER NOT NULL DEFAULT 0,
                        plan_approved INTEGER NOT NULL DEFAULT 0,
                        robot_states_json TEXT NOT NULL DEFAULT '{}',
                        messages_json TEXT NOT NULL DEFAULT '[]',
                        shared_memory_json TEXT NOT NULL DEFAULT '{}',
                        final_report_json TEXT NOT NULL DEFAULT '{}',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )
        finally:
            conn.close()

    # ---------- Section 12: Admin / User Configuration ----------
    def get_settings(self, user_id: str = "main") -> Dict[str, Any]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT config_json FROM ultra_settings WHERE user_id = ?", (str(user_id),)).fetchone()
        finally:
            conn.close()
        default_cfg = {
            "max_rounds": 3,
            "default_depth": "standard",  # quick (2 rounds) | standard (3 rounds) | deep (5 rounds)
            "timeout_sec": 65.0,
            "token_budget": 32000,
            "reduced_motion": False,
            "require_code_approval": True,
            "agents": [dict(a) for a in DEFAULT_ULTRA_AGENTS],
        }
        if not row:
            return default_cfg
        try:
            saved = json.loads(row["config_json"] or "{}")
            if isinstance(saved, dict):
                default_cfg.update({k: v for k, v in saved.items() if k != "agents"})
                if isinstance(saved.get("agents"), list) and len(saved["agents"]) == 5:
                    default_cfg["agents"] = saved["agents"]
        except Exception:
            pass
        return default_cfg

    def update_settings(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        cur = self.get_settings(user_id)
        if "max_rounds" in updates:
            cur["max_rounds"] = max(1, min(5, int(updates["max_rounds"])))
        if "default_depth" in updates and updates["default_depth"] in ("quick", "standard", "deep"):
            cur["default_depth"] = updates["default_depth"]
        if "timeout_sec" in updates:
            cur["timeout_sec"] = max(10.0, min(180.0, float(updates["timeout_sec"])))
        if "token_budget" in updates:
            cur["token_budget"] = max(2000, min(200000, int(updates["token_budget"])))
        if "reduced_motion" in updates:
            cur["reduced_motion"] = bool(updates["reduced_motion"])
        if "require_code_approval" in updates:
            cur["require_code_approval"] = bool(updates["require_code_approval"])
        if isinstance(updates.get("agents"), list):
            by_id = {a["id"]: dict(a) for a in cur["agents"]}
            for ua in updates["agents"]:
                if isinstance(ua, dict) and ua.get("id") in by_id:
                    target = by_id[ua["id"]]
                    for k in ("role", "role_ar", "responsibility", "enabled", "system_prompt", "model_id"):
                        if k in ua:
                            target[k] = ua[k]
            cur["agents"] = [by_id[a["id"]] for a in DEFAULT_ULTRA_AGENTS]

        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ultra_settings (user_id, config_json, updated_at) VALUES (?,?,?)",
                    (str(user_id), redact_secrets(json.dumps(cur, ensure_ascii=False)), time.time()),
                )
        finally:
            conn.close()
        return cur

    # ---------- Meeting Storage Helpers ----------
    def _save_meeting(self, m: Dict[str, Any]) -> None:
        now = time.time()
        m["updated_at"] = now
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO ultra_meetings (
                        meeting_id, user_id, original_request, current_task, status,
                        current_round, max_rounds, active_speaker, plan_approval_required,
                        plan_approved, robot_states_json, messages_json, shared_memory_json,
                        final_report_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        m["meeting_id"],
                        m["user_id"],
                        redact_secrets(m["original_request"]),
                        redact_secrets(m["current_task"]),
                        m["status"],
                        int(m.get("current_round", 1)),
                        int(m.get("max_rounds", 3)),
                        str(m.get("active_speaker") or ""),
                        int(bool(m.get("plan_approval_required", False))),
                        int(bool(m.get("plan_approved", False))),
                        json.dumps(m.get("robot_states", {}), ensure_ascii=False),
                        redact_secrets(json.dumps(m.get("messages", []), ensure_ascii=False)),
                        redact_secrets(json.dumps(m.get("shared_memory", {}), ensure_ascii=False)),
                        redact_secrets(json.dumps(m.get("final_report", {}), ensure_ascii=False)),
                        float(m.get("created_at", now)),
                        now,
                    ),
                )
        finally:
            conn.close()

    def get_meeting(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM ultra_meetings WHERE meeting_id = ?", (str(meeting_id),)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        d = dict(row)
        d["plan_approval_required"] = bool(d["plan_approval_required"])
        d["plan_approved"] = bool(d["plan_approved"])
        d["robot_states"] = json.loads(d.pop("robot_states_json") or "{}")
        d["messages"] = json.loads(d.pop("messages_json") or "[]")
        d["shared_memory"] = json.loads(d.pop("shared_memory_json") or "{}")
        d["final_report"] = json.loads(d.pop("final_report_json") or "{}")
        return d

    def list_meetings(self, user_id: str = "main", limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT meeting_id FROM ultra_meetings WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (str(user_id), int(limit)),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            m = self.get_meeting(r["meeting_id"])
            if m:
                out.append({
                    "meeting_id": m["meeting_id"],
                    "original_request": m["original_request"][:160],
                    "status": m["status"],
                    "current_round": m["current_round"],
                    "max_rounds": m["max_rounds"],
                    "message_count": len(m.get("messages", [])),
                    "updated_at": m["updated_at"],
                })
        return out

    # ---------- Create & Run Meeting (Sections 3, 4, 5, 7, 9, 10) ----------
    def create_meeting(
        self,
        *,
        user_id: str = "main",
        request_text: str,
        project_files: Optional[Dict[str, str]] = None,
        max_rounds: Optional[int] = None,
        available_providers: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        req = str(request_text or "").strip()
        if not req:
            raise ValueError("اكتب موضوع الاجتماع أو المهمة المطلوبة أولاً.")
        cfg = self.get_settings(user_id)
        eff_rounds = max(1, min(5, int(max_rounds if max_rounds is not None else cfg.get("max_rounds", 3))))
        files_map = dict(project_files or {})
        is_code_task = bool(files_map) or bool(re.search(r"(?i)(كود|برمج|ملف|مشروع|دالة|خطأ|code|function|class|bug|python|javascript|repo)", req))

        robot_states: Dict[str, Dict[str, Any]] = {}
        avail = available_providers or {}
        for ag in cfg["agents"]:
            aid = ag["id"]
            enabled = bool(ag.get("enabled", True))
            online = enabled and bool(avail.get(aid, True))
            robot_states[aid] = {
                "id": aid,
                "name": ag["name"],
                "role": ag["role"],
                "role_ar": ag.get("role_ar", ag["role"]),
                "color": ag["color"],
                "avatar_variant": ag.get("avatar_variant", aid),
                "online": online,
                "enabled": enabled,
                "state": "idle" if online else "offline",
            }

        meeting_id = f"ultra_{uuid.uuid4().hex[:10]}"
        now = time.time()
        meeting = {
            "meeting_id": meeting_id,
            "user_id": str(user_id),
            "original_request": req,  # Immutable per Section 9
            "current_task": req,
            "status": "running",  # running | paused | waiting_approval | completed | stopped
            "current_round": 1,
            "max_rounds": eff_rounds,
            "active_speaker": "",
            "plan_approval_required": bool(is_code_task and cfg.get("require_code_approval", True) and files_map),
            "plan_approved": False,
            "robot_states": robot_states,
            "messages": [
                {
                    "id": f"msg_{uuid.uuid4().hex[:8]}",
                    "round": 1,
                    "sender_id": "user",
                    "sender_name": "أنت (قائد الاجتماع)",
                    "role": "User Request",
                    "tag": "Task",
                    "content": req,
                    "ts": now,
                }
            ],
            "shared_memory": {
                "original_user_request": req,
                "task_requirements": [req],
                "project_files_summary": {k: f"{len(v.splitlines())} lines ({len(v)} chars)" for k, v in list(files_map.items())[:25]},
                "project_files": {k: v[:12000] for k, v in list(files_map.items())[:10]},
                "private_instructions": {},
                "previous_decisions": [],
                "rejected_proposals": [],
                "disagreements": [],
                "outstanding_questions": [],
                "proposed_file_changes": {},
                "approved_file_changes": {},
                "test_results": {},
                "interrupted_speaker": "",
            },
            "final_report": {},
            "created_at": now,
            "updated_at": now,
        }
        self._save_meeting(meeting)
        return meeting

    @staticmethod
    def _classify_message_tag(agent_id: str, round_num: int, content: str) -> Tuple[str, bool]:
        low = (content or "").lower()
        has_disagreement = any(
            w in low
            for w in (
                "أختلف", "لا أتفق", "اعتراض", "غير دقيق", "مشكلة في", "خطر",
                "i disagree", "objection", "however,", "flaw", "vulnerability", "counter-proposal",
            )
        )
        if has_disagreement:
            return "Objection", True
        if agent_id == "gemini":
            return ("Proposal" if round_num <= 2 else "Synthesis"), False
        if agent_id == "gpt":
            return ("Architecture" if round_num == 1 else "Objection / Design"), False
        if agent_id == "qwen":
            return "Technical suggestion", False
        if agent_id == "deepseek":
            return "Review", False
        if agent_id == "kimi":
            return "Context finding", False
        return "Analysis", False

    def _build_agent_prompt(self, meeting: Dict[str, Any], agent_cfg: Dict[str, Any], round_num: int) -> str:
        sm = meeting["shared_memory"]
        r_info = ROUND_DEFINITIONS.get(round_num, ROUND_DEFINITIONS[5])
        transcript_lines: List[str] = []
        for m in meeting["messages"][-16:]:
            if m.get("target_agent") and m["target_agent"] != agent_cfg["id"]:
                continue
            transcript_lines.append(f"[Round {m.get('round', 1)} | {m['sender_name']} ({m.get('role', '')}) - {m.get('tag', '')}]:\n{m['content']}")

        proj_summary = ""
        if sm.get("project_files"):
            excerpts = []
            for fn, fc in list(sm["project_files"].items())[:4]:
                excerpts.append(f"--- File: {fn} ---\n{fc[:3000]}")
            proj_summary = "\n[Project Files Excerpts (Read-Only Analysis until user approval)]:\n" + "\n".join(excerpts) + "\n"

        priv_list = sm.get("private_instructions", {}).get(agent_cfg["id"]) or []
        priv_txt = ("\n[Private Instruction from User to You]:\n" + "\n".join(priv_list) + "\n") if priv_list else ""

        lang_is_ar = bool(re.search(r"[\u0600-\u06FF]", meeting["current_task"]))
        lang_rule = (
            "Respond in clear, natural Arabic (concise, technical, 3-6 sentences, directly addressing the team and user request)."
            if lang_is_ar
            else "Respond in clear, concise technical English (3-6 sentences, directly addressing the team and user request)."
        )

        return (
            f"{agent_cfg.get('system_prompt', '')}\n"
            f"Role: {agent_cfg['name']} — {agent_cfg['role']} ({agent_cfg.get('responsibility', '')})\n"
            f"Current Stage: {r_info['name']} ({r_info['instruction']})\n"
            f"Original Immutable User Request: {meeting['original_request']}\n"
            f"Current Active Task: {meeting['current_task']}\n"
            f"{priv_txt}"
            f"{proj_summary}"
            f"\n--- Live Meeting Transcript So Far ---\n"
            + ("\n\n".join(transcript_lines) if transcript_lines else "(Start of meeting)")
            + "\n\nRules:\n"
            "1. Do not repeat identical arguments already stated.\n"
            "2. If you disagree with another agent's assumption or architecture, state WHY with technical evidence.\n"
            "3. Never claim code or tests were executed unless verified.\n"
            f"4. {lang_rule}"
        )

    def run_single_round(
        self,
        meeting_id: str,
        provider_callers: Dict[str, Callable[[str, str], str]],
        *,
        round_num: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute one full discussion round across all enabled & online agents in sequence."""
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            raise KeyError(f"Meeting not found: {meeting_id}")
        if meeting["status"] in ("paused", "stopped"):
            return meeting

        cfg = self.get_settings(meeting["user_id"])
        r_num = int(round_num if round_num is not None else meeting["current_round"])
        meeting["current_round"] = r_num

        # If this is a code task with project files, enforce read-only analysis order in Round 1:
        # Kimi (Repository Mapping) -> Gemini (Requirements) -> GPT (Architecture) -> Qwen (Implementation Plan) -> DeepSeek (Verification)
        agents_list = list(cfg["agents"])
        if meeting["shared_memory"].get("project_files") and r_num == 1:
            order_map = {"kimi": 0, "gemini": 1, "gpt": 2, "qwen": 3, "deepseek": 4}
            agents_list.sort(key=lambda a: order_map.get(a["id"], 99))

        for ag in agents_list:
            # Reload meeting to respect real-time pause/stop/interrupt from user
            latest = self.get_meeting(meeting_id)
            if not latest or latest["status"] in ("paused", "stopped"):
                return latest or meeting
            meeting = latest

            aid = ag["id"]
            r_state = meeting["robot_states"].get(aid) or {}
            if not ag.get("enabled", True) or not r_state.get("online", True):
                r_state["state"] = "offline"
                meeting["robot_states"][aid] = r_state
                self._save_meeting(meeting)
                continue

            # Check if user interrupted this speaker
            if meeting["shared_memory"].get("interrupted_speaker") == aid:
                meeting["shared_memory"]["interrupted_speaker"] = ""
                r_state["state"] = "listening"
                meeting["robot_states"][aid] = r_state
                self._save_meeting(meeting)
                continue

            # Set active speaker to 'thinking' -> 'speaking' and others to 'listening'
            meeting["active_speaker"] = aid
            for other_id, ost in meeting["robot_states"].items():
                if other_id == aid:
                    ost["state"] = "reviewing" if aid in ("deepseek", "kimi") else "thinking"
                elif ost.get("online", True):
                    ost["state"] = "listening"
            self._save_meeting(meeting)

            prompt = self._build_agent_prompt(meeting, ag, r_num)
            caller = provider_callers.get(aid)
            if caller is None:
                r_state["state"] = "offline"
                r_state["online"] = False
                meeting["robot_states"][aid] = r_state
                self._save_meeting(meeting)
                continue

            try:
                r_state["state"] = "speaking"
                meeting["robot_states"][aid] = r_state
                self._save_meeting(meeting)

                reply_txt = redact_secrets((caller(prompt, ag.get("system_prompt", "")) or "").strip())
                if not reply_txt:
                    raise RuntimeError(f"Empty response from {ag['name']}")

                tag, is_disagreement = self._classify_message_tag(aid, r_num, reply_txt)
                msg_obj = {
                    "id": f"msg_{uuid.uuid4().hex[:8]}",
                    "round": r_num,
                    "sender_id": aid,
                    "sender_name": f"{ag['name']} — {ag['role']}",
                    "role": ag["role"],
                    "role_ar": ag.get("role_ar", ag["role"]),
                    "color": ag["color"],
                    "tag": tag,
                    "content": reply_txt,
                    "ts": time.time(),
                }
                meeting = self.get_meeting(meeting_id) or meeting
                meeting["messages"].append(msg_obj)
                if is_disagreement:
                    meeting["shared_memory"]["disagreements"].append({
                        "round": r_num,
                        "agent": ag["name"],
                        "role": ag["role"],
                        "excerpt": reply_txt[:320],
                    })
                # Extract any proposed code blocks from Qwen into proposed_file_changes (kept read-only until approval)
                if aid == "qwen" and "```" in reply_txt:
                    blocks = re.findall(r"```([\w+#.\-]*)\n(.*?)```", reply_txt, flags=re.DOTALL)
                    for idx_b, (lang_b, code_b) in enumerate(blocks, 1):
                        ext = "py" if "py" in lang_b.lower() else (lang_b.lower() or "txt")
                        fname = f"proposed_change_{idx_b}.{ext}"
                        meeting["shared_memory"]["proposed_file_changes"][fname] = code_b.strip()

                r_state["state"] = "listening"
                meeting["robot_states"][aid] = r_state
                self._save_meeting(meeting)
            except Exception as err:
                r_state["state"] = "error"
                meeting["robot_states"][aid] = r_state
                meeting["messages"].append({
                    "id": f"msg_{uuid.uuid4().hex[:8]}",
                    "round": r_num,
                    "sender_id": aid,
                    "sender_name": f"{ag['name']} — {ag['role']}",
                    "role": ag["role"],
                    "color": ag["color"],
                    "tag": "Error",
                    "content": f"⚠️ تعذر الحصول على رد من {ag['name']}: {redact_secrets(str(err)[:160])}",
                    "is_error": True,
                    "ts": time.time(),
                })
                self._save_meeting(meeting)

        # Reset active speaker to idle at end of round
        meeting = self.get_meeting(meeting_id) or meeting
        meeting["active_speaker"] = ""
        for ost in meeting["robot_states"].values():
            if ost.get("online", True) and ost.get("state") != "error":
                ost["state"] = "idle"
        self._save_meeting(meeting)
        return meeting

    def run_meeting(
        self,
        meeting_id: str,
        provider_callers: Dict[str, Callable[[str, str], str]],
    ) -> Dict[str, Any]:
        """Run multi-round discussion up to max_rounds, then build the structured Final Report."""
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            raise KeyError(f"Meeting not found: {meeting_id}")

        start_r = max(1, int(meeting.get("current_round", 1)))
        max_r = max(start_r, int(meeting.get("max_rounds", 3)))

        for r_num in range(start_r, max_r + 1):
            meeting = self.get_meeting(meeting_id) or meeting
            if meeting["status"] in ("paused", "stopped"):
                return meeting
            meeting = self.run_single_round(meeting_id, provider_callers, round_num=r_num)
            if meeting["status"] in ("paused", "stopped"):
                return meeting
            if r_num < max_r:
                meeting["current_round"] = r_num + 1
                self._save_meeting(meeting)

        meeting = self.get_meeting(meeting_id) or meeting
        report = self.build_final_report(meeting, provider_callers)
        meeting["final_report"] = report
        if meeting["plan_approval_required"] and not meeting["plan_approved"]:
            meeting["status"] = "waiting_approval"
        else:
            meeting["status"] = "completed"
        self._save_meeting(meeting)
        return meeting

    # ---------- Section 10: Final Report System ----------
    def build_final_report(
        self,
        meeting: Dict[str, Any],
        provider_callers: Optional[Dict[str, Callable[[str, str], str]]] = None,
    ) -> Dict[str, Any]:
        """Generate the 10-part structured report distinguishing model opinions from verified facts."""
        msgs = [m for m in meeting.get("messages", []) if m.get("sender_id") != "user" and not m.get("is_error")]
        contributions: Dict[str, List[str]] = {}
        for m in msgs:
            sid = m["sender_id"]
            contributions.setdefault(sid, []).append(f"[Round {m['round']} - {m.get('tag', '')}]: {m['content'][:350]}")

        sm = meeting.get("shared_memory", {})
        proposed_files = sm.get("proposed_file_changes") or {}
        approved_files = sm.get("approved_file_changes") or {}

        # Run real AST verification on any proposed Python code
        verified_facts: List[str] = []
        test_results: Dict[str, Any] = {"executed": False, "details": []}
        for fname, code_txt in proposed_files.items():
            if fname.endswith(".py"):
                test_results["executed"] = True
                try:
                    ast.parse(code_txt, filename=fname)
                    test_results["details"].append(f"{fname}: Python AST syntax check PASSED")
                    verified_facts.append(f"Verified syntax of {fname} via Python AST parser.")
                except SyntaxError as se:
                    test_results["details"].append(f"{fname}: Python AST syntax FAILED at line {se.lineno}: {se.msg}")

        if not verified_facts:
            verified_facts.append(
                f"Meeting completed across {meeting.get('current_round', 1)} round(s) with {len(msgs)} real agent messages."
            )

        # Synthesize final recommendation via Gemini or fallback to last round summary
        recommendation = ""
        if provider_callers and "gemini" in provider_callers:
            try:
                lang_is_ar = bool(re.search(r"[\u0600-\u06FF]", meeting["original_request"]))
                synth_prompt = (
                    f"Original User Request: {meeting['original_request']}\n"
                    f"Active Task: {meeting['current_task']}\n\n"
                    "Agent Contributions:\n"
                    + "\n".join(f"- {k.upper()}: {v[-1]}" for k, v in contributions.items() if v)
                    + "\n\nDisagreements Recorded:\n"
                    + json.dumps(sm.get("disagreements", []), ensure_ascii=False)
                    + "\n\nWrite a concise, well-structured Final Team Recommendation and Technical Implementation Plan "
                    + ("in Arabic." if lang_is_ar else "in English.")
                )
                recommendation = redact_secrets((provider_callers["gemini"](synth_prompt, "You are the RYN Ultra Report Synthesizer.") or "").strip())
            except Exception:
                recommendation = ""

        if not recommendation:
            last_msgs = [m["content"] for m in msgs[-3:]]
            recommendation = "\n\n".join(last_msgs) if last_msgs else "لم تكتمل ردود النماذج."

        sm["test_results"] = test_results
        return {
            "original_user_request": meeting["original_request"],
            "problem_summary": meeting["current_task"],
            "agent_contributions": {
                k: v for k, v in contributions.items()
            },
            "main_disagreements": sm.get("disagreements", []),
            "arguments_and_evidence": [
                {"agent": m["sender_name"], "round": m["round"], "tag": m["tag"], "argument": m["content"][:400]}
                for m in msgs[-10:]
            ],
            "final_team_recommendation": recommendation,
            "technical_implementation_plan": [
                "1. مراجعة وتحليل المتطلبات والسياق (Gemini & Kimi)",
                "2. اعتماد التصميم المعماري وحدود الملفات (GPT)",
                "3. تطبيق التعديلات المقترحة بعد موافقة المستخدم الصريحة (Qwen)",
                "4. التحقق الأمني والمنطقي واختبار الانحدار (DeepSeek)",
            ],
            "risks_and_limitations": [
                "التحليل يبقى للقراءة فقط (Read-Only) حتى يوافق المستخدم صراحةً على التعديلات.",
                "آراء النماذج تُعرض منفصلة عن الحقائق التي تم التحقق منها برمجياً.",
            ],
            "verification_and_test_results": test_results,
            "distinction": {
                "model_generated_opinions_count": len(msgs),
                "independently_verified_facts": verified_facts,
            },
            "code_changes": {
                "proposed_files": list(proposed_files.keys()),
                "approved_files": list(approved_files.keys()),
                "approval_status": "APPROVED" if meeting.get("plan_approved") else ("WAITING_USER_APPROVAL" if meeting.get("plan_approval_required") else "READ_ONLY"),
                "rollback_info": "Original files preserved untouched in shared_memory.project_files until explicit approval.",
            },
            "remaining_tasks": (
                ["موافقة المستخدم الصريحة لتطبيق التعديلات المقترحة"] if (meeting.get("plan_approval_required") and not meeting.get("plan_approved")) else []
            ),
        }

    # ---------- Section 6: User Interaction & Controls ----------
    def handle_control_action(
        self,
        meeting_id: str,
        action: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        provider_callers: Optional[Dict[str, Callable[[str, str], str]]] = None,
    ) -> Dict[str, Any]:
        """Execute user control actions during or after a meeting (Section 6)."""
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            raise KeyError(f"Meeting not found: {meeting_id}")
        body = dict(payload or {})
        act = str(action or "").strip().lower()
        now = time.time()

        if act == "pause":
            meeting["status"] = "paused"
            self._save_meeting(meeting)
            return meeting

        if act == "resume":
            meeting["status"] = "running"
            self._save_meeting(meeting)
            if provider_callers:
                return self.run_meeting(meeting_id, provider_callers)
            return meeting

        if act == "stop":
            meeting["status"] = "stopped"
            meeting["active_speaker"] = ""
            for ost in meeting["robot_states"].values():
                if ost.get("online", True):
                    ost["state"] = "idle"
            meeting["final_report"] = self.build_final_report(meeting, provider_callers)
            self._save_meeting(meeting)
            return meeting

        if act == "interrupt":
            cur_sp = meeting.get("active_speaker") or str(body.get("agent_id") or "")
            meeting["shared_memory"]["interrupted_speaker"] = cur_sp
            meeting["active_speaker"] = ""
            if cur_sp and cur_sp in meeting["robot_states"]:
                meeting["robot_states"][cur_sp]["state"] = "listening"
            meeting["messages"].append({
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "round": meeting["current_round"],
                "sender_id": "user",
                "sender_name": "أنت (مقاطعة المتحدث)",
                "role": "User Interrupt",
                "tag": "Interrupt",
                "content": str(body.get("message") or f"تمت مقاطعة المتحدث ({cur_sp or 'الحالي'}) من قبل المستخدم."),
                "ts": now,
            })
            self._save_meeting(meeting)
            return meeting

        if act == "broadcast":
            msg_txt = str(body.get("message") or "").strip()
            if not msg_txt:
                raise ValueError("اكتب الرسالة الموجهة للفريق.")
            meeting["messages"].append({
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "round": meeting["current_round"],
                "sender_id": "user",
                "sender_name": "أنت (رسالة لكل الوكلاء الـ 5)",
                "role": "Broadcast Instruction",
                "tag": "Broadcast",
                "content": msg_txt,
                "ts": now,
            })
            meeting["shared_memory"]["task_requirements"].append(msg_txt)
            self._save_meeting(meeting)
            if provider_callers and body.get("run_round", True):
                return self.run_single_round(meeting_id, provider_callers, round_num=meeting["current_round"])
            return meeting

        if act == "private_instruction":
            target_ag = str(body.get("agent_id") or "").strip().lower()
            msg_txt = str(body.get("message") or "").strip()
            if target_ag not in meeting["robot_states"] or not msg_txt:
                raise ValueError("حدد الوكيل المستهدف واكتب التعليمات الخاصة.")
            priv_map = meeting["shared_memory"].setdefault("private_instructions", {})
            priv_map.setdefault(target_ag, []).append(msg_txt)
            meeting["messages"].append({
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "round": meeting["current_round"],
                "sender_id": "user",
                "sender_name": f"أنت 🔒 (تعليمة خاصة إلى {target_ag.upper()})",
                "role": "Private Instruction",
                "tag": "Private",
                "target_agent": target_ag,
                "content": msg_txt,
                "ts": now,
            })
            self._save_meeting(meeting)
            if provider_callers and target_ag in provider_callers:
                cfg = self.get_settings(meeting["user_id"])
                ag_cfg = next((a for a in cfg["agents"] if a["id"] == target_ag), DEFAULT_ULTRA_AGENTS[0])
                prompt = self._build_agent_prompt(meeting, ag_cfg, meeting["current_round"])
                reply = redact_secrets((provider_callers[target_ag](prompt, ag_cfg.get("system_prompt", "")) or "").strip())
                if reply:
                    meeting["messages"].append({
                        "id": f"msg_{uuid.uuid4().hex[:8]}",
                        "round": meeting["current_round"],
                        "sender_id": target_ag,
                        "sender_name": f"{ag_cfg['name']} — {ag_cfg['role']}",
                        "role": ag_cfg["role"],
                        "color": ag_cfg["color"],
                        "tag": "Direct Reply",
                        "content": reply,
                        "ts": time.time(),
                    })
                    self._save_meeting(meeting)
            return meeting

        if act == "explain_reasoning":
            target_ag = str(body.get("agent_id") or "deepseek").strip().lower()
            question_txt = str(body.get("message") or "اشرح بالتفصيل الدليل والمنطق وراء استنتاجك الأخير.").strip()
            if provider_callers and target_ag in provider_callers:
                cfg = self.get_settings(meeting["user_id"])
                ag_cfg = next((a for a in cfg["agents"] if a["id"] == target_ag), DEFAULT_ULTRA_AGENTS[0])
                prompt = (
                    self._build_agent_prompt(meeting, ag_cfg, meeting["current_round"])
                    + f"\n\n[User Request for Reasoning Explanation]: {question_txt}\n"
                    "Explain the concrete evidence, assumptions, and reasoning steps behind your conclusion."
                )
                reply = redact_secrets((provider_callers[target_ag](prompt, ag_cfg.get("system_prompt", "")) or "").strip())
                meeting["messages"].append({
                    "id": f"msg_{uuid.uuid4().hex[:8]}",
                    "round": meeting["current_round"],
                    "sender_id": target_ag,
                    "sender_name": f"{ag_cfg['name']} — {ag_cfg['role']}",
                    "role": ag_cfg["role"],
                    "color": ag_cfg["color"],
                    "tag": "Reasoning Explanation",
                    "content": reply or "تم توضيح الاستنتاج أعلاه.",
                    "ts": time.time(),
                })
                self._save_meeting(meeting)
            return meeting

        if act == "extra_round":
            next_r = int(meeting["current_round"]) + 1
            meeting["current_round"] = next_r
            meeting["max_rounds"] = max(int(meeting["max_rounds"]), next_r)
            meeting["status"] = "running"
            self._save_meeting(meeting)
            if provider_callers:
                meeting = self.run_single_round(meeting_id, provider_callers, round_num=next_r)
                meeting["final_report"] = self.build_final_report(meeting, provider_callers)
                meeting["status"] = "completed"
                self._save_meeting(meeting)
            return meeting

        if act == "update_task":
            new_task = str(body.get("task") or body.get("message") or "").strip()
            if not new_task:
                raise ValueError("اكتب المهمة المحدثة.")
            meeting["current_task"] = new_task
            meeting["shared_memory"]["task_requirements"].append(f"[Task Updated]: {new_task}")
            meeting["messages"].append({
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "round": meeting["current_round"],
                "sender_id": "orchestrator",
                "sender_name": "RYN Ultra Orchestrator",
                "role": "System Notice",
                "tag": "Task Updated",
                "content": f"📢 **تنبيه لكل الوكلاء الـ 5**: قام المستخدم بتحديث المهمة الجارية إلى:\n«{new_task}»\n(مع الحفاظ على الطلب الأصلي في السجل: «{meeting['original_request']}»).",
                "ts": now,
            })
            self._save_meeting(meeting)
            if provider_callers and body.get("run_round", True):
                return self.run_single_round(meeting_id, provider_callers, round_num=meeting["current_round"])
            return meeting

        if act == "approve_changes":
            meeting["plan_approved"] = True
            meeting["shared_memory"]["approved_file_changes"] = dict(meeting["shared_memory"].get("proposed_file_changes") or {})
            meeting["status"] = "completed"
            meeting["final_report"] = self.build_final_report(meeting, provider_callers)
            self._save_meeting(meeting)
            return meeting

        raise ValueError(f"Unsupported RYN Ultra control action: {action}")
