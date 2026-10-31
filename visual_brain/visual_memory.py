# -*- coding: utf-8 -*-
"""
الذاكرة البصرية الدائمة والمعزولة لكل مستخدم (Persistent Visual Memory):
- حفظ الصورة الأصلية وكل نسخة معدّلة.
- ربط النسخ ببعضها كشجرة إصدارات (parent_version_id -> version_id).
- الرجوع لأي إصدار سابق (Revert / Undo).
- معرفة آخر صورة جرى تعديلها لكل مستخدم بشكل مستقل تماماً (عدم خلط صور المستخدمين).
- الاحتفاظ بتعليمات وخطة كل عملية.
- تخزين دائم وآمن على القرص (مقاوم لإعادة التشغيل) مع إدارة الحصص اليومية (img_counts).
"""
from __future__ import annotations
import os
import json
import time
import uuid
import base64
import threading
import tempfile
from typing import Dict, List, Optional, Tuple
from .schemas import ImageVersion


class VisualMemoryStore:
    """مخزن دائم وآمن لإصدارات صور المستخدمين وحصص الاستخدام اليومية."""

    def __init__(self, storage_dir: Optional[str] = None, max_versions_per_user: int = 15):
        base_dir = storage_dir or os.environ.get(
            "RYN_VISUAL_MEMORY_DIR",
            os.path.join(os.environ.get("RYN_DATA_DIR", tempfile.gettempdir()), "ryn_visual_memory")
        )
        self.storage_dir = os.path.abspath(base_dir)
        self.max_versions = max_versions_per_user
        self._lock = threading.Lock()
        self._users: Dict[str, List[ImageVersion]] = {}
        self._active_cursor: Dict[str, str] = {}  # user_id -> current active version_id
        self._daily_counts: Dict[str, int] = {}
        os.makedirs(self.storage_dir, exist_ok=True)
        self._load_index()

    def _safe_user_key(self, user_id: str) -> str:
        raw = str(user_id or "main").strip()
        cleaned = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in raw)[:64]
        return cleaned or "main"

    def _index_path(self) -> str:
        return os.path.join(self.storage_dir, "visual_memory_index.json")

    def _user_file_path(self, safe_u: str) -> str:
        path = os.path.abspath(os.path.join(self.storage_dir, f"user_{safe_u}.json"))
        if not path.startswith(self.storage_dir):
            raise ValueError("Invalid storage path")
        return path

    def _load_index(self) -> None:
        idx_p = self._index_path()
        if not os.path.isfile(idx_p):
            return
        try:
            with open(idx_p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._daily_counts = {str(k): int(v) for k, v in (data.get("daily_counts") or {}).items()}
                self._active_cursor = {str(k): str(v) for k, v in (data.get("active_cursor") or {}).items()}
        except Exception:
            pass

    def _save_index_unlocked(self) -> None:
        try:
            payload = {
                "updated_at": time.time(),
                "daily_counts": self._daily_counts,
                "active_cursor": self._active_cursor,
            }
            with open(self._index_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_user_unlocked(self, safe_u: str) -> List[ImageVersion]:
        if safe_u in self._users:
            return self._users[safe_u]
        uf = self._user_file_path(safe_u)
        versions: List[ImageVersion] = []
        if os.path.isfile(uf):
            try:
                with open(uf, "r", encoding="utf-8") as f:
                    arr = json.load(f)
                if isinstance(arr, list):
                    for item in arr[-self.max_versions:]:
                        if isinstance(item, dict) and item.get("version_id") and item.get("image_data_uri"):
                            versions.append(ImageVersion(
                                version_id=str(item["version_id"]),
                                user_id=safe_u,
                                parent_version_id=item.get("parent_version_id"),
                                created_at=float(item.get("created_at", 0.0)),
                                operation_type=str(item.get("operation_type", "generate")),
                                user_prompt=str(item.get("user_prompt", "")),
                                final_prompt_en=str(item.get("final_prompt_en", "")),
                                provider_used=str(item.get("provider_used", "")),
                                model_used=str(item.get("model_used", "")),
                                image_data_uri=str(item["image_data_uri"]),
                                plan_summary=item.get("plan_summary") or {},
                            ))
            except Exception:
                pass
        self._users[safe_u] = versions
        return versions

    def _save_user_unlocked(self, safe_u: str) -> None:
        versions = self._users.get(safe_u) or []
        try:
            uf = self._user_file_path(safe_u)
            data = [v.to_dict(include_image=True) for v in versions[-self.max_versions:]]
            with open(uf, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def save_version(
        self,
        user_id: str,
        image_data_uri: str,
        user_prompt: str,
        final_prompt_en: str,
        operation_type: str = "generate",
        provider_used: str = "",
        model_used: str = "",
        parent_version_id: Optional[str] = None,
        plan_summary: Optional[ dict ] = None,
    ) -> ImageVersion:
        """حفظ صورة جديدة أو نسخة معدلة وربطها بالإصدار الأب لنفس المستخدم."""
        safe_u = self._safe_user_key(user_id)
        with self._lock:
            versions = self._load_user_unlocked(safe_u)
            if parent_version_id is None and operation_type != "generate" and versions:
                parent_version_id = self._active_cursor.get(safe_u) or versions[-1].version_id
            vid = "v_" + uuid.uuid4().hex[:10]
            ver = ImageVersion(
                version_id=vid,
                user_id=safe_u,
                parent_version_id=parent_version_id,
                created_at=time.time(),
                operation_type=operation_type,
                user_prompt=user_prompt[:600],
                final_prompt_en=final_prompt_en[:1800],
                provider_used=provider_used,
                model_used=model_used,
                image_data_uri=image_data_uri,
                plan_summary=plan_summary or {},
            )
            versions.append(ver)
            if len(versions) > self.max_versions:
                del versions[:-self.max_versions]
            self._active_cursor[safe_u] = vid
            self._save_user_unlocked(safe_u)
            self._save_index_unlocked()
            return ver

    def get_latest_version(self, user_id: str) -> Optional[ImageVersion]:
        """استرجاع النسخة النشطة أو الأخيرة للمستخدم."""
        safe_u = self._safe_user_key(user_id)
        with self._lock:
            versions = self._load_user_unlocked(safe_u)
            if not versions:
                return None
            cur_id = self._active_cursor.get(safe_u)
            if cur_id:
                for v in reversed(versions):
                    if v.version_id == cur_id:
                        return v
            return versions[-1]

    def get_latest_image_uri(self, user_id: str) -> Optional[str]:
        v = self.get_latest_version(user_id)
        return v.image_data_uri if v else None

    def get_version(self, user_id: str, version_id: str) -> Optional[ImageVersion]:
        safe_u = self._safe_user_key(user_id)
        with self._lock:
            versions = self._load_user_unlocked(safe_u)
            for v in versions:
                if v.version_id == version_id:
                    return v
            return None

    def revert_version(self, user_id: str, target: str = "previous") -> Optional[ImageVersion]:
        """الرجوع إلى النسخة السابقة أو النسخة الأصلية الأولى أو معرف نسخة محدد."""
        safe_u = self._safe_user_key(user_id)
        with self._lock:
            versions = self._load_user_unlocked(safe_u)
            if not versions:
                return None
            if target in ("first", "original", "الأولى", "الأصلية"):
                chosen = versions[0]
            elif target in ("previous", "prev", "السابقة", "القبلها"):
                cur_id = self._active_cursor.get(safe_u)
                idx = len(versions) - 1
                for i, v in enumerate(versions):
                    if v.version_id == cur_id:
                        idx = i
                        break
                chosen = versions[max(0, idx - 1)]
            else:
                chosen = next((v for v in versions if v.version_id == target), None)
                if chosen is None:
                    chosen = versions[max(0, len(versions) - 2)]
            self._active_cursor[safe_u] = chosen.version_id
            self._save_index_unlocked()
            return chosen

    def list_history(self, user_id: str) -> List[Dict]:
        """استرجاع سجل إصدارات الصور للمستخدم (بدون حمل بيانات Base64 الثقيلة)."""
        safe_u = self._safe_user_key(user_id)
        with self._lock:
            versions = self._load_user_unlocked(safe_u)
            active_id = self._active_cursor.get(safe_u)
            out = []
            for idx, v in enumerate(versions, 1):
                d = v.to_dict(include_image=False)
                d["index"] = idx
                d["is_active"] = (v.version_id == active_id)
                out.append(d)
            return out

    def get_daily_count(self, day_key: str) -> int:
        with self._lock:
            return self._daily_counts.get(day_key, 0)

    def increment_daily_count(self, day_key: str) -> int:
        with self._lock:
            val = self._daily_counts.get(day_key, 0) + 1
            self._daily_counts[day_key] = val
            self._save_index_unlocked()
            return val

    def decrement_daily_count(self, day_key: str) -> int:
        with self._lock:
            val = max(0, self._daily_counts.get(day_key, 1) - 1)
            self._daily_counts[day_key] = val
            self._save_index_unlocked()
            return val

    @staticmethod
    def decode_data_uri(data_uri: str) -> Tuple[bytes, str]:
        if not data_uri or not isinstance(data_uri, str):
            raise ValueError("Empty data URI")
        if data_uri.startswith("data:image/"):
            header, b64 = data_uri.split(",", 1)
            mime = header.split(";")[0].replace("data:", "") or "image/png"
            return base64.b64decode(b64), mime
        return base64.b64decode(data_uri), "image/png"
