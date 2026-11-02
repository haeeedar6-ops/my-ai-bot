# -*- coding: utf-8 -*-
"""
المنسّق المركزي لـ RYN Visual Brain (Orchestrator — Section 1 to Section 12):
1. تحليل الطلب بعمق (Intent + Scene + Identity Lock + Edit Plan).
2. إنشاء «بريميو الطلب» (`PremiumRequestBrief`) الذي يصيغ الرؤية الفوتوغرافية والفيزيائية وأوامر التعديل قبل إنشاء الصورة.
3. توجيه الطلب إلى المزود المتوافق (`timbrooks/instruct-pix2pix` أو `Gemini Image` للتعديل، و `FLUX` / `Gemini` للتوليد) مع منع التحويل الصامت (`No silent downgrade`).
4. فحص ومراجعة الصورة بعد التوليد/التعديل عبر نموذج المراجعة (`QualityInspector`)، وإذا وُجد فيها خطأ يكتب المراجع ملاحظات تصحيحية مباشرة (`generator_feedback_en` & `critic_notes_ar`) لنموذج التوليد ليُعيد رسمها بدقة.
"""
from __future__ import annotations
import base64
from typing import Optional, Dict, Any, List, Callable, Tuple
from .schemas import VisualPlan, QualityReport, ImageVersion
from .intent_analyzer import analyze_intent, detect_conflicts, detect_ambiguity_and_assumptions
from .scene_analyzer import analyze_scene
from .identity_engine import build_identity_lock, extract_reference_identity_signature
from .edit_planner import plan_edit_operation
from .prompt_engine import build_prompt_modules, synthesize_final_prompt
from .request_brief import build_premium_request_brief
from .model_router import route_model_for_task
from .quality_inspector import inspect_generated_image
from .visual_memory import VisualMemoryStore
from image_processing.masking import build_region_mask_hint
from image_processing.upscaling import enhance_image_clarity


class UnsupportedEditOperationError(RuntimeError):
    """يرفع عندما يطلب المستخدم تعديل صورة مرجعية ولا يتوفر أي مزود مفعّل يدعم التعديل (No silent downgrade)."""


class VisualBrain:
    """المحرك المركزي لـ RYN Visual Brain."""

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
        تحليل الطلب وبناء «بريميو الطلب» (`PremiumRequestBrief`) والخطة البصرية الكاملة (`VisualPlan`) قبل توليد أو تعديل الصورة.
        """
        raw = (user_text or "").strip()
        has_attached = bool(attached_images_bytes or ref_parts)
        latest_ver = self.memory.get_latest_version(user_id)
        has_mem_img = latest_ver is not None

        intent_info = analyze_intent(raw, has_attached_image=has_attached, has_memory_image=has_mem_img)
        lang = str(intent_info["language"])
        intent = str(intent_info["intent"])
        task_type = str(intent_info["task_type"])
        is_edit_like = bool(intent_info["is_edit_like"])
        uses_ref = bool(intent_info["uses_reference_image"])

        uncertainties, safe_assumptions, clarification_q = detect_ambiguity_and_assumptions(
            raw, is_edit=is_edit_like, has_ref_image=uses_ref
        )
        conflicts, resolutions = detect_conflicts(raw)
        scene = analyze_scene(raw, is_edit=is_edit_like, has_ref_image=uses_ref, aspect_hint=aspect_hint_str)
        id_lock = build_identity_lock(raw, is_edit=is_edit_like, has_ref_image=uses_ref)

        if id_lock.enabled and ref_parts and ask_vision_fn is not None:
            sig = extract_reference_identity_signature(ref_parts, ask_vision_fn=ask_vision_fn)
            if sig:
                id_lock.identity_descriptor = sig

        ref_cnt = len(attached_images_bytes or []) or (len(ref_parts) if ref_parts else (1 if uses_ref else 0))
        edit_plan = plan_edit_operation(
            raw,
            is_edit_like=is_edit_like,
            identity_lock=id_lock,
            ref_count=ref_cnt,
            task_type_override=task_type,
        )
        if edit_plan.is_edit:
            task_type = edit_plan.workflow_type or task_type

        if edit_plan.requires_mask:
            mask_hint = build_region_mask_hint(edit_plan.operation_type, raw)
            if mask_hint:
                edit_plan.edit_instructions_en += " " + mask_hint

        avail = dict(available_providers or {"flux": True, "gemini": True, "instruct_pix2pix": False, "together": False})
        avail.setdefault("local_enhance", True)
        primary_prov, fallbacks, _task_cat = route_model_for_task(
            scene, edit_plan, avail, has_ref_image=uses_ref, preferred_provider=preferred_provider
        )

        prompt_mods = build_prompt_modules(scene, id_lock, edit_plan, conflicts_resolutions=resolutions)
        if safe_assumptions:
            prompt_mods["safe_assumptions"] = " ".join(safe_assumptions)
        final_en = synthesize_final_prompt(raw, prompt_mods, ask_llm_fn=ask_llm_fn)
        if aspect_hint_str:
            final_en = f"{final_en} {aspect_hint_str}".strip()

        # بناء «بريميو الطلب» الاحترافي (Request Analysis & Premium Blueprint) قبل توليد الصورة
        premium_brief = build_premium_request_brief(
            raw_request=raw,
            scene=scene,
            identity_lock=id_lock,
            edit_plan=edit_plan,
            final_prompt_en=final_en,
            ask_llm_fn=ask_llm_fn,
        )

        ref_id = latest_ver.version_id if (uses_ref and latest_ver) else ("uploaded_ref" if has_attached else None)
        prov_reqs = {
            "operation": task_type,
            "requires_reference_image": bool(edit_plan.is_edit or uses_ref),
            "requires_mask": bool(edit_plan.requires_mask),
            "supports_instruct_pix2pix": bool(edit_plan.is_edit),
        }

        return VisualPlan(
            raw_request=raw,
            language=lang,
            intent=intent,
            task_type=task_type,
            user_goal=scene.user_goal or raw,
            reference_image_id=ref_id,
            scene=scene,
            identity_lock=id_lock,
            edit_plan=edit_plan,
            premium_brief=premium_brief,
            must_keep=list(edit_plan.protected_elements),
            must_change=list(edit_plan.target_changes),
            uncertainties=uncertainties,
            safe_assumptions=safe_assumptions,
            clarification_question=clarification_q,
            conflicts_detected=conflicts,
            conflict_resolutions=resolutions,
            provider_requirements=prov_reqs,
            recommended_provider=primary_prov,
            fallback_providers=fallbacks,
            prompt_modules=prompt_mods,
            final_prompt_en=final_en,
            negative_constraints_en=prompt_mods.get("negative_constraints", ""),
            aspect_ratio_hint=aspect_hint_str,
        )

    def _extract_raw_ref_bytes(self, user_id: str, ref_parts: Optional[List[Any]]) -> Optional[bytes]:
        if ref_parts:
            for p in ref_parts:
                inl = getattr(p, "inline_data", None)
                data = getattr(inl, "data", None) if inl else None
                if isinstance(data, (bytes, bytearray)) and len(data) > 16:
                    return bytes(data)
                if isinstance(data, str) and data:
                    try:
                        return base64.b64decode(data)
                    except Exception:
                        pass
        latest_uri = self.memory.get_latest_image_uri(user_id)
        if latest_uri:
            try:
                raw_b, _m = self.memory.decode_data_uri(latest_uri)
                return raw_b
            except Exception:
                pass
        return None

    def execute_plan(
        self,
        plan: VisualPlan,
        user_id: str,
        provider_runners: Dict[str, Callable[[str, Optional[List[Any]]], Tuple[bytes, str, str]]],
        ref_parts: Optional[List[Any]] = None,
        max_quality_retries: int = 2,
        progress_cb: Optional[Callable[[str], None]] = None,
        vision_audit_fn: Optional[Callable[[bytes, str], str]] = None,
    ) -> Dict[str, Any]:
        """
        تنفيذ الخطة عبر المراحل المتسلسلة:
        1. تحليل الطلب وإنشاء بريميو الطلب (`PremiumRequestBrief`).
        2. اختيار المحرك المتوافق (`timbrooks/instruct-pix2pix` أو `gemini` للتعديل، أو `flux`/`gemini` للتوليد).
        3. توليد أو تعديل الصورة.
        4. مراجعة الصورة عبر نموذج المراجعة (`QualityInspector`)، وإذا وُجد خطأ يكتب المراجع ملاحظات تصحيحية لنموذج توليد الصور ويعيد التوليد.
        """
        P = progress_cb or (lambda _msg: None)

        if plan.intent == "revert":
            target = "first" if any(w in plan.raw_request for w in ("الأولى", "الأصلية", "first", "original")) else "previous"
            rev_ver = self.memory.revert_version(user_id, target=target)
            if rev_ver is not None:
                return {
                    "status": "done",
                    "image": rev_ver.image_data_uri,
                    "model": rev_ver.model_used or rev_ver.provider_used,
                    "note": rev_ver.model_used or rev_ver.provider_used or "RYN Visual Brain",
                    "prompt": rev_ver.final_prompt_en,
                    "version_id": rev_ver.version_id,
                    "parent_version_id": rev_ver.parent_version_id,
                    "plan": plan.to_dict(),
                    "premium_brief": plan.premium_brief.to_dict(),
                    "text": "تم الرجوع إلى النسخة المطلوبة من ذاكرة الصور بنجاح ⏪🎨",
                }

        P("📐 Planning scene & Premium Brief · تحليل الطلب وإنشاء بريميو احترافي للمشهد...")
        orig_ref_bytes = self._extract_raw_ref_bytes(user_id, ref_parts)
        orig_ver: Optional[ImageVersion] = None
        if plan.edit_plan.is_edit and orig_ref_bytes:
            orig_ver = self.memory.retain_original_upload(user_id, orig_ref_bytes)
            plan.reference_image_id = orig_ver.version_id

        if plan.edit_plan.is_edit:
            if not orig_ref_bytes and not ref_parts:
                raise UnsupportedEditOperationError(
                    "⚠️ طلبت تعديل صورة ولكن لا توجد صورة مرفقة حالياً؛ ارفع الصورة المطلوب تعديلها أولاً."
                )

        P("🧭 Selecting model · اختيار المحرك المتوافق مع العملية...")
        runners = dict(provider_runners or {})
        if orig_ref_bytes and "local_enhance" not in runners:
            def _local_enhancer_runner(_prompt_en: str, _r_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
                enhanced = enhance_image_clarity(orig_ref_bytes, mode=plan.task_type)
                return enhanced, "image/png", "Pillow-Enhancer"
            if plan.task_type in ("upscale", "color_correct") or (
                plan.task_type == "restore" and not any(k in runners for k in ("gemini", "instruct_pix2pix"))
            ):
                runners["local_enhance"] = _local_enhancer_runner

        chain_names = [plan.recommended_provider] + [p for p in plan.fallback_providers if p != plan.recommended_provider]
        if plan.edit_plan.is_edit:
            # المسموح للتعديل على صورة مرجعية فقط: instruct_pix2pix, gemini, local_enhance (ممنوع التحويل الصامت لـ T2I)
            allowed_edit_providers = {"instruct_pix2pix", "gemini", "local_enhance"}
            chain = [(name, runners[name]) for name in chain_names if name in runners and name in allowed_edit_providers]
            if not chain:
                for k in ("instruct_pix2pix", "gemini", "local_enhance"):
                    if k in runners and (k in ("instruct_pix2pix", "gemini") or plan.task_type in ("upscale", "color_correct", "restore")):
                        chain.append((k, runners[k]))
            if not chain:
                raise UnsupportedEditOperationError(
                    "⚠️ تعديل الصورة المرجعية يتطلب تفعيل نموذج التعديل (timbrooks/instruct-pix2pix عبر HF_TOKEN / REPLICATE_API_TOKEN أو GEMINI_API_KEY)؛ "
                    "المزودات الأخرى المهيأة حالياً تدعم توليد صور جديدة من النص فقط، ولم يتم تحويل طلبك لتوليد نصي حفاظاً على صورتك الأصلية."
                )
        else:
            chain = [(name, runners[name]) for name in chain_names if name in runners and name not in ("local_enhance", "instruct_pix2pix")]
            if not chain:
                chain = [(k, v) for k, v in runners.items() if k not in ("local_enhance", "instruct_pix2pix")]

        if not chain:
            raise RuntimeError("لا يوجد مزود صور مفعّل ومتوافق مع هذه العملية.")

        cur_prompt = plan.premium_brief.premium_prompt_en or plan.final_prompt_en
        cur_pix2pix_prompt = (
            plan.premium_brief.instruct_pix2pix_command
            or plan.edit_plan.instruct_pix2pix_instruction_en
            or cur_prompt
        )
        last_err: Optional[Exception] = None
        best_bytes: Optional[bytes] = None
        best_mime: str = "image/png"
        best_model: str = ""
        best_prov: str = ""
        quality_rep: Optional[QualityReport] = None
        review_history: List[Dict[str, Any]] = []

        max_attempts = max(1, min(3, int(max_quality_retries)))
        for attempt in range(1, max_attempts + 1):
            for prov_name, runner_fn in chain:
                try:
                    if attempt == 1:
                        stage_txt = (
                            f"🎨 Generating · جاري إنشاء الصورة بناءً على بريميو الطلب عبر {prov_name}..."
                            if not plan.edit_plan.is_edit
                            else f"🎨 Editing · جاري تعديل الصورة ({plan.task_type}) عبر {prov_name}..."
                        )
                    else:
                        stage_txt = f"🔧 Refining · نموذج التوليد يطبق ملاحظات المراجع (محاولة {attempt}/{max_attempts})..."
                    P(stage_txt)

                    prompt_for_runner = cur_pix2pix_prompt if prov_name == "instruct_pix2pix" else cur_prompt
                    img_bytes, mime, model_name = runner_fn(prompt_for_runner, ref_parts)
                    if not img_bytes:
                        continue
                    if plan.task_type in ("upscale", "color_correct") and prov_name != "local_enhance":
                        img_bytes = enhance_image_clarity(img_bytes, mode=plan.task_type)

                    P("🔎 Inspecting · نموذج المراجعة يفحص الصورة ويتحقق من خلوها من الأخطاء...")
                    q_rep = inspect_generated_image(
                        img_bytes,
                        plan,
                        original_image_bytes=orig_ref_bytes if plan.edit_plan.is_edit else None,
                        vision_audit_fn=vision_audit_fn,
                    )
                    review_history.append({
                        "attempt": attempt,
                        "provider": prov_name,
                        "model": model_name,
                        "passed": q_rep.passed,
                        "score": q_rep.score,
                        "critic_notes_ar": list(q_rep.critic_notes_ar),
                        "generator_feedback_en": q_rep.generator_feedback_en,
                    })
                    best_bytes, best_mime, best_model, best_prov, quality_rep = (
                        img_bytes,
                        (mime or "image/png"),
                        model_name,
                        prov_name,
                        q_rep,
                    )
                    if q_rep.passed:
                        break
                except Exception as e:
                    last_err = e
                    if str(e).startswith("⛔") or isinstance(e, UnsupportedEditOperationError):
                        raise
                    continue

            if best_bytes and quality_rep and quality_rep.passed:
                break
            if quality_rep and quality_rep.retry_recommended and (quality_rep.generator_feedback_en or quality_rep.refined_prompt_addition):
                feedback_note = quality_rep.generator_feedback_en or quality_rep.refined_prompt_addition
                P("📝 Critic Notes → Generator · المراجع رصد ملاحظات وأرسلها لنموذج التوليد لإعادة الرسم...")
                cur_prompt = f"{plan.final_prompt_en[:3200]} {feedback_note}".strip()
                cur_pix2pix_prompt = f"{cur_pix2pix_prompt[:350]} {feedback_note[:250]}".strip()

        if not best_bytes:
            if last_err:
                raise last_err
            raise RuntimeError("تعذر توليد أو تعديل الصورة من المزودات المتوافقة.")

        b64_uri = "data:%s;base64,%s" % (best_mime, base64.b64encode(best_bytes).decode("ascii"))
        parent_vid = orig_ver.version_id if orig_ver else None
        if plan.edit_plan.is_edit and not parent_vid:
            prev_v = self.memory.get_latest_version(user_id)
            if prev_v:
                parent_vid = prev_v.version_id

        saved_ver: ImageVersion = self.memory.save_version(
            user_id=user_id,
            image_data_uri=b64_uri,
            user_prompt=plan.raw_request,
            final_prompt_en=cur_prompt,
            operation_type=plan.task_type if plan.edit_plan.is_edit else "text_to_image",
            provider_used=best_prov,
            model_used=best_model,
            parent_version_id=parent_vid,
            original_source_id=orig_ver.original_source_id if orig_ver else None,
            plan_summary={
                "job_id": plan.job_id,
                "task_type": plan.task_type,
                "operation": plan.edit_plan.operation_type,
                "style": plan.scene.style,
                "premium_brief_ar": plan.premium_brief.brief_summary_ar,
                "protected_elements": plan.must_keep[:5],
                "target_changes": plan.must_change[:5],
                "conflicts": plan.conflicts_detected,
                "quality_score": quality_rep.score if quality_rep else 1.0,
                "preservation_score": quality_rep.preservation_score if quality_rep else 1.0,
                "review_history": review_history,
            },
        )

        caption_txt = "تم تعديل الصورة مع الحفاظ على الهوية والعناصر المحمية 🎨" if plan.edit_plan.is_edit else "تفضّل الصورة 🎨"
        short_note = best_model or best_prov or "RYN Visual Brain"
        q_dict = quality_rep.to_dict() if quality_rep else {}
        q_dict["review_history"] = review_history
        return {
            "status": "done",
            "image": b64_uri,
            "image_bytes": best_bytes,
            "mime_type": best_mime,
            "model": best_model,
            "note": short_note,
            "provider": best_prov,
            "prompt": cur_prompt,
            "premium_brief": plan.premium_brief.to_dict(),
            "version_id": saved_ver.version_id,
            "parent_version_id": saved_ver.parent_version_id,
            "original_source_id": saved_ver.original_source_id,
            "quality": q_dict,
            "review_history": review_history,
            "plan": plan.to_dict(),
            "contract": plan.to_contract_dict(),
            "text": caption_txt,
        }


_GLOBAL_BRAIN: Optional[VisualBrain] = None


def get_visual_brain() -> VisualBrain:
    global _GLOBAL_BRAIN
    if _GLOBAL_BRAIN is None:
        _GLOBAL_BRAIN = VisualBrain()
    return _GLOBAL_BRAIN
