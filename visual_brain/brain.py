# -*- coding: utf-8 -*-
"""
العقل البصري المستقل (VisualBrain Orchestrator):
ينسّق جميع مراحل تحليل الطلب، اكتشاف التعارضات، تخطيط التعديل، قفل الهوية،
بناء البرومبت المعياري، توجيه المحرك، التوليد/التعديل مع الفحص وإعادة المحاولة،
وحفظ الإصدارات في الذاكرة البصرية الدائمة.
"""
from __future__ import annotations
import base64
from typing import Optional, Dict, Any, List, Callable, Tuple
from .schemas import VisualPlan, QualityReport, ImageVersion
from .intent_analyzer import analyze_intent, detect_conflicts
from .scene_analyzer import analyze_scene
from .identity_engine import build_identity_lock, extract_reference_identity_signature
from .edit_planner import plan_edit_operation
from .prompt_engine import build_prompt_modules, synthesize_final_prompt
from .model_router import route_model_for_task
from .quality_inspector import inspect_generated_image
from .visual_memory import VisualMemoryStore
from image_processing.preprocessing import prepare_reference_image
from image_processing.masking import build_region_mask_hint
from image_processing.upscaling import enhance_image_clarity


class VisualBrain:
    """المحرك المركزي للعقل البصري في RYN."""

    def __init__(self, memory_store: Optional[VisualMemoryStore] = None):
        self.memory = memory_store or VisualMemoryStore()

    def build_plan(
        self,
        user_text: str,
        user_id: str = "main",
        attached_images_bytes: Optional[List[bytes]] = None,
        available_providers: Optional[Dict[str, bool]] = None,
        preferred_provider: str = "auto",
        aspect_hint_str: str = "",
        ask_llm_fn: Optional[Callable[[str, str], str]] = None,
        ask_vision_fn: Optional[Callable] = None,
        ref_parts: Optional[List[Any]] = None,
    ) -> VisualPlan:
        """
        تحليل طلب المستخدم وإنتاج خطة تنفيذ منظمة بصيغة JSON (VisualPlan)
        قبل إرسال أي شيء إلى محرك الصور.
        """
        raw = (user_text or "").strip()
        has_attached = bool(attached_images_bytes or ref_parts)
        latest_ver = self.memory.get_latest_version(user_id)
        has_mem_img = latest_ver is not None

        intent_info = analyze_intent(raw, has_attached_image=has_attached, has_memory_image=has_mem_img)
        lang = str(intent_info["language"])
        intent = str(intent_info["intent"])
        is_edit_like = bool(intent_info["is_edit_like"])
        uses_ref = bool(intent_info["uses_reference_image"])

        conflicts, resolutions = detect_conflicts(raw)
        scene = analyze_scene(raw, is_edit=is_edit_like, has_ref_image=uses_ref)
        id_lock = build_identity_lock(raw, is_edit=is_edit_like, has_ref_image=uses_ref)

        if id_lock.enabled and ref_parts and ask_vision_fn is not None:
            sig = extract_reference_identity_signature(ref_parts, ask_vision_fn=ask_vision_fn)
            if sig:
                id_lock.identity_descriptor = sig

        ref_cnt = len(attached_images_bytes or []) or (len(ref_parts) if ref_parts else (1 if uses_ref else 0))
        edit_plan = plan_edit_operation(raw, is_edit_like=is_edit_like, identity_lock=id_lock, ref_count=ref_cnt)

        if edit_plan.requires_mask:
            mask_hint = build_region_mask_hint(edit_plan.operation_type, raw)
            if mask_hint:
                edit_plan.edit_instructions_en += " " + mask_hint

        avail = available_providers or {"flux": True, "nano_banana": True, "pollinations": True, "together": False}
        primary_prov, fallbacks, _task_cat = route_model_for_task(
            scene, edit_plan, avail, has_ref_image=uses_ref, preferred_provider=preferred_provider
        )

        prompt_mods = build_prompt_modules(scene, id_lock, edit_plan, conflicts_resolutions=resolutions)
        final_en = synthesize_final_prompt(raw, prompt_mods, ask_llm_fn=ask_llm_fn)
        if aspect_hint_str:
            final_en = f"{final_en} {aspect_hint_str}".strip()

        return VisualPlan(
            raw_request=raw,
            language=lang,
            intent=intent,
            scene=scene,
            identity_lock=id_lock,
            edit_plan=edit_plan,
            conflicts_detected=conflicts,
            conflict_resolutions=resolutions,
            recommended_provider=primary_prov,
            fallback_providers=fallbacks,
            prompt_modules=prompt_mods,
            final_prompt_en=final_en,
            negative_constraints_en=prompt_mods.get("negative_constraints", ""),
            aspect_ratio_hint=aspect_hint_str,
        )

    def execute_plan(
        self,
        plan: VisualPlan,
        user_id: str,
        provider_runners: Dict[str, Callable[[str, Optional[List[Any]]], Tuple[bytes, str, str]]],
        ref_parts: Optional[List[Any]] = None,
        max_quality_retries: int = 2,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        تنفيذ الخطة عبر المزود الأنسب وسلسلة الاحتياط، مع فحص الجودة بعد التوليد
        وإعادة المحاولة بتعليمات محسّنة عند الفشل، ثم الحفظ في الذاكرة البصرية الدائمة.
        """
        P = progress_cb or (lambda _msg: None)

        # معالجة طلب الرجوع لإصدار سابق فوراً من الذاكرة البصرية
        if plan.intent == "revert":
            target = "first" if any(w in plan.raw_request for w in ("الأولى", "الأصلية", "first", "original")) else "previous"
            rev_ver = self.memory.revert_version(user_id, target=target)
            if rev_ver is not None:
                return {
                    "status": "done",
                    "image": rev_ver.image_data_uri,
                    "model": rev_ver.model_used or rev_ver.provider_used,
                    "note": rev_ver.model_used or rev_ver.provider_used or "Visual Memory",
                    "prompt": rev_ver.final_prompt_en,
                    "version_id": rev_ver.version_id,
                    "parent_version_id": rev_ver.parent_version_id,
                    "plan": plan.to_dict(),
                    "text": "تم الرجوع إلى النسخة السابقة من ذاكرة الصور بنجاح ⏪🎨",
                }

        chain_names = [plan.recommended_provider] + [p for p in plan.fallback_providers if p != plan.recommended_provider]
        chain = [(name, provider_runners[name]) for name in chain_names if name in provider_runners]
        if not chain:
            chain = list(provider_runners.items())

        cur_prompt = plan.final_prompt_en
        last_err: Optional[Exception] = None
        best_bytes: Optional[bytes] = None
        best_mime: str = "image/png"
        best_model: str = ""
        best_prov: str = ""
        quality_rep: Optional[QualityReport] = None

        for attempt in range(1, max_quality_retries + 1):
            for prov_name, runner_fn in chain:
                try:
                    P(f"🎨 جاري التوليد عبر {prov_name}..." if not plan.edit_plan.is_edit else f"🎨 جاري تطبيق التعديل ({plan.edit_plan.operation_type})...")
                    img_bytes, mime, model_name = runner_fn(cur_prompt, ref_parts)
                    if not img_bytes:
                        continue
                    # إذا كان المطلوب تحسين الجودة، نمرر الصورة عبر وحدة التحسين البصري
                    if plan.edit_plan.operation_type == "enhance_upscale":
                        img_bytes = enhance_image_clarity(img_bytes)
                    q_rep = inspect_generated_image(img_bytes, plan)
                    best_bytes, best_mime, best_model, best_prov, quality_rep = img_bytes, (mime or "image/png"), model_name, prov_name, q_rep
                    if q_rep.passed:
                        break
                except Exception as e:
                    last_err = e
                    if str(e).startswith("⛔"):
                        raise
                    continue

            if best_bytes and quality_rep and quality_rep.passed:
                break
            if quality_rep and quality_rep.retry_recommended and quality_rep.refined_prompt_addition:
                cur_prompt = f"{plan.final_prompt_en[:1800]} {quality_rep.refined_prompt_addition}".strip()

        if not best_bytes:
            if last_err:
                raise last_err
            raise RuntimeError("تعذر توليد الصورة من جميع المزودات المتاحة.")

        b64_uri = "data:%s;base64,%s" % (best_mime, base64.b64encode(best_bytes).decode("ascii"))
        parent_vid = None
        if plan.edit_plan.is_edit:
            prev_v = self.memory.get_latest_version(user_id)
            if prev_v:
                parent_vid = prev_v.version_id

        saved_ver: ImageVersion = self.memory.save_version(
            user_id=user_id,
            image_data_uri=b64_uri,
            user_prompt=plan.raw_request,
            final_prompt_en=cur_prompt,
            operation_type=plan.edit_plan.operation_type if plan.edit_plan.is_edit else "generate",
            provider_used=best_prov,
            model_used=best_model,
            parent_version_id=parent_vid,
            plan_summary={
                "intent": plan.intent,
                "operation": plan.edit_plan.operation_type,
                "style": plan.scene.style,
                "conflicts": plan.conflicts_detected,
                "quality_score": quality_rep.score if quality_rep else 1.0,
            },
        )

        caption_txt = "تم تعديل الصورة مع الحفاظ على الهوية والتفاصيل 🎨" if plan.edit_plan.is_edit else "تفضّل الصورة 🎨"
        short_note = best_model or best_prov or "Visual Brain"
        return {
            "status": "done",
            "image": b64_uri,
            "image_bytes": best_bytes,
            "mime_type": best_mime,
            "model": best_model,
            "note": short_note,
            "provider": best_prov,
            "prompt": cur_prompt,
            "version_id": saved_ver.version_id,
            "parent_version_id": saved_ver.parent_version_id,
            "quality": quality_rep.to_dict() if quality_rep else {},
            "plan": plan.to_dict(),
            "text": caption_txt,
        }


_GLOBAL_BRAIN: Optional[VisualBrain] = None


def get_visual_brain() -> VisualBrain:
    global _GLOBAL_BRAIN
    if _GLOBAL_BRAIN is None:
        _GLOBAL_BRAIN = VisualBrain()
    return _GLOBAL_BRAIN
