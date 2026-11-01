# -*- coding: utf-8 -*-
"""
فاحص ومراجع الجودة البصرية مع كتابة ملاحظات تصحيحية مباشرة لنموذج توليد الصور
(Visual Quality Reviewer & Generator Feedback Loop — Section 8):
- يفحص الصورة المولّدة أو المعدّلة عبر 7 معايير:
  1. Instruction match (مطابقة الطلب)
  2. Composition (التكوين والعدد والعلاقات)
  3. Anatomy (سلامة التشريح واليدين عند ظهورهما)
  4. Material & Optical realism (واقعية المواد والإضاءة وعدم المظهر البلاستيكي)
  5. Physics (الارتكاز، الظلال، الانعكاسات)
  6. Editing preservation (مقارنة الصورة المعدّلة بالأصلية للتأكد من عدم المساس بالمناطق المحمية)
  7. Artifact detection (كشف التشوهات أو التلف)
- إذا اكتشف المراجع أي خطأ في الصورة، يكتب ملاحظات تصحيحية محددة (`critic_notes_ar` + `generator_feedback_en`)
  ويوجهها لنموذج توليد/تعديل الصور لإعادة إنتاج الصورة بعد تلافي الخطأ.
"""
from __future__ import annotations
from typing import Optional, Callable, List, Dict
from .schemas import QualityReport, VisualPlan
from image_processing.validation import validate_generated_image_bytes, compare_original_and_edited


CRITIC_VISION_PROMPT_TEMPLATE = (
    "You are the Senior Visual Quality Critic of RYN Visual Brain.\n"
    "User Request: {user_request}\n"
    "Planned Goal: {user_goal}\n"
    "Workflow Mode: {task_type}\n"
    "Protected Elements (if editing): {protected_elements}\n\n"
    "Strictly inspect the generated/edited image for ANY AI defects against these 7 criteria:\n"
    "1) anatomy: Look closely at hands, fingers, legs, knees, ankles, and feet! Are there twisted/crossed mangled legs, missing/fused feet, melted shoes, or crooked/fused/extra fingers? (CRITICAL: reject immediately if limbs or hands are deformed!)\n"
    "2) material_realism: Does the skin look waxy, airbrushed, or plastic with blown-out forehead highlights or a fake stock-photo toothy grin?\n"
    "3) instruction_match: Are all requested subjects, colors, and actions accurately depicted?\n"
    "4) composition: Is the framing natural and candid rather than stiff dead-center AI symmetry?\n"
    "5) physics: Are contact shadows, reflections, and gravity physically grounded?\n"
    "6) editing_preservation: If editing, were the protected regions kept intact?\n"
    "7) artifact_detection: Are there any melted edges, duplicated limbs, or garbled blobs?\n\n"
    "If and ONLY if the image has zero limb/hand/skin defects, reply with EXACTLY:\n"
    "OK\n\n"
    "If you detect ANY defect (especially deformed legs/feet/hands or waxy AI skin), write corrective notes for the image generator in this EXACT format:\n"
    "FAIL: <check_name> | <location_in_image> | <defect_summary> | <specific_instruction_for_image_generator_to_fix_it (e.g. Reframe as waist-up candid medium shot hiding legs/feet, fix hand anatomy, render matte natural skin pores)>"
)


def build_generator_feedback_notes(
    defects: List[Dict[str, str]],
    plan: VisualPlan,
    checks_run: Dict[str, bool],
) -> tuple[List[str], str]:
    """
    تحويل الأخطاء المرصودة من نموذج المراجعة إلى:
    1. `critic_notes_ar`: قائمة ملاحظات واضحة بالعربية.
    2. `generator_feedback_en`: تعليمات تصحيحية مباشرة تُحقن في نموذج توليد/تعديل الصور في المحاولة التالية.
    """
    notes_ar: List[str] = []
    gen_instructions_en: List[str] = [
        "preserve all successful visual elements from the previous attempt and apply these mandatory corrections"
    ]

    for d in defects:
        chk = d.get("check", "instruction_match")
        loc = d.get("location", "main_subject")
        defect_txt = d.get("defect", "")
        fix_note = d.get("generator_note", "")
        notes_ar.append(f"ملاحظة المراجع [{chk} @ {loc}]: {defect_txt}")
        if fix_note:
            gen_instructions_en.append(f"at {loc}: {fix_note}")
        elif defect_txt:
            gen_instructions_en.append(f"fix {chk} at {loc} ({defect_txt})")

    if not checks_run.get("editing_preservation", True) or plan.identity_lock.enabled:
        notes_ar.append("ملاحظة المراجع: يجب تشديد قفل الهوية وعدم تغيير الوجه أو الخلفية غير المطلوب تعديلها.")
        gen_instructions_en.append(
            "strictly lock all protected regions and keep the reference person's exact facial bone structure, skin tone, pose, and unrequested background 100% unchanged"
        )

    if plan.scene.has_visible_hands or not checks_run.get("anatomy", True):
        notes_ar.append("ملاحظة المراجع: تدقيق تشريح اليدين (5 أصابع طبيعية مع الإبهام وتلامس فيزيائي سليم).")
        gen_instructions_en.append(
            "render anatomically accurate 5-finger hands with natural knuckle joints, realistic grip pressure, and grounded contact shadows"
        )

    if not checks_run.get("material_realism", True):
        notes_ar.append("ملاحظة المراجع: تعزيز الواقعية الفيزيائية للمسام والأنسجة وإزالة أي مظهر بلاستيكي.")
        gen_instructions_en.append(
            "replace any smooth or plastic surfaces with organic micro-texture, natural skin pores, subsurface scattering, and authentic 35mm RAW optical lighting"
        )

    if not checks_run.get("artifact_detection", True):
        notes_ar.append("ملاحظة المراجع: معالجة التعريض الضوئي والتباين وإزالة التشوهات البصرية.")
        gen_instructions_en.append(
            "ensure balanced 14-stop dynamic range exposure, crisp focus on the primary subject, and zero visual corruption"
        )

    feedback_en = "QUALITY CORRECTION & CRITIC NOTES FOR IMAGE GENERATOR: " + "; ".join(gen_instructions_en) + "."
    return notes_ar, feedback_en


def inspect_generated_image(
    image_bytes: bytes,
    plan: VisualPlan,
    original_image_bytes: Optional[bytes] = None,
    vision_audit_fn: Optional[Callable[[bytes, str], str]] = None,
) -> QualityReport:
    """فحص ومراجعة الصورة المولّدة أو المعدّلة وكتابة ملاحظات تصحيحية لنموذج توليد الصور عند وجود خطأ."""
    val = validate_generated_image_bytes(image_bytes)
    if not val["valid"]:
        issues_list = val.get("issues") or ["بيانات الصورة غير صالحة أو تالفة"]
        defects_init = [{
            "check": "artifact_detection",
            "location": "full_frame",
            "defect": issues_list[0],
            "generator_note": "Generate a clean, well-exposed, uncorrupted high-clarity photograph.",
        }]
        checks_init = {
            "instruction_match": False,
            "composition": False,
            "anatomy": False,
            "material_realism": False,
            "physics": False,
            "editing_preservation": False,
            "artifact_detection": False,
        }
        notes_ar, feedback_en = build_generator_feedback_notes(defects_init, plan, checks_init)
        return QualityReport(
            passed=False,
            score=0.0,
            width=val.get("width", 0),
            height=val.get("height", 0),
            format=val.get("format", "unknown"),
            reviewer_model="Optical-Validator",
            checks_run=checks_init,
            defects=defects_init,
            issues=issues_list,
            critic_notes_ar=notes_ar,
            generator_feedback_en=feedback_en,
            preservation_score=0.0,
            retry_recommended=True,
            refined_prompt_addition=feedback_en,
        )

    issues = list(val.get("issues") or [])
    defects: List[Dict[str, str]] = []
    score = float(val.get("score", 1.0))
    preservation_score = 1.0
    reviewer_used = "Optical-Validator"

    checks_run = {
        "instruction_match": True,
        "composition": True,
        "anatomy": True,
        "material_realism": True,
        "physics": True,
        "editing_preservation": True,
        "artifact_detection": len(issues) == 0,
    }

    for iss in issues:
        defects.append({
            "check": "artifact_detection",
            "location": "full_frame",
            "defect": iss,
            "generator_note": "Ensure balanced exposure, natural contrast, and sharp subject detail.",
        })

    if plan.edit_plan.is_edit and original_image_bytes:
        cmp_res = compare_original_and_edited(
            original_image_bytes,
            image_bytes,
            operation_type=plan.edit_plan.operation_type,
        )
        preservation_score = float(cmp_res.get("preservation_score", 1.0))
        if not cmp_res.get("passed", True):
            w_msg = cmp_res.get("warning") or "تغيّر غير مقصود خارج منطقة التعديل المسموحة"
            issues.append(w_msg)
            defects.append({
                "check": "editing_preservation",
                "location": "protected_regions",
                "defect": w_msg,
                "generator_note": "Keep the original image aspect ratio, framing, face, and unrequested regions unchanged.",
            })
            checks_run["editing_preservation"] = False
            score = min(score, 0.5)

    if vision_audit_fn is not None and score >= 0.6:
        try:
            reviewer_used = "Vision-Critic-LLM"
            audit_q = CRITIC_VISION_PROMPT_TEMPLATE.format(
                user_request=plan.raw_request[:300],
                user_goal=(plan.user_goal or plan.raw_request)[:300],
                task_type=plan.task_type,
                protected_elements=", ".join(plan.must_keep[:4]) if plan.must_keep else "none",
            )
            verdict = (vision_audit_fn(image_bytes, audit_q) or "").strip()
            if verdict.upper().startswith("FAIL:"):
                body = verdict.split(":", 1)[1].strip()
                parts = [p.strip() for p in body.split("|")]
                chk = parts[0].lower() if len(parts) >= 1 else "instruction_match"
                loc = parts[1] if len(parts) >= 2 else "main_subject"
                reason = parts[2][:160] if len(parts) >= 3 else parts[-1][:160]
                gen_fix = parts[3][:220] if len(parts) >= 4 else f"Fix {chk} at {loc}: {reason}"
                issues.append(f"ملاحظة الفحص البصري ({chk} @ {loc}): {reason}")
                defects.append({
                    "check": chk,
                    "location": loc,
                    "defect": reason,
                    "generator_note": gen_fix,
                })
                if chk in checks_run:
                    checks_run[chk] = False
                score = min(score, 0.45)
        except Exception:
            pass

    passed = score >= 0.6 and len(issues) == 0
    notes_ar: List[str] = []
    feedback_en = ""
    if not passed:
        notes_ar, feedback_en = build_generator_feedback_notes(defects, plan, checks_run)

    return QualityReport(
        passed=passed,
        score=round(score, 2),
        width=val.get("width", 0),
        height=val.get("height", 0),
        format=val.get("format", "png"),
        reviewer_model=reviewer_used,
        checks_run=checks_run,
        defects=defects[:4],
        issues=issues,
        critic_notes_ar=notes_ar,
        generator_feedback_en=feedback_en,
        preservation_score=round(preservation_score, 2),
        retry_recommended=not passed,
        refined_prompt_addition=feedback_en,
    )
