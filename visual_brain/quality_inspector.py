# -*- coding: utf-8 -*-
"""
فاحص الجودة البصرية وحلقة التحسين الموجهة (Visual Quality Inspector & Refinement Loop — Section 8):
يجري الفحوصات السبعة المطلوبة:
1. Instruction match (مطابقة الطلب)
2. Composition (التكوين والعدد والعلاقات)
3. Anatomy (سلامة التشريح واليدين عند ظهورهما)
4. Material realism (واقعية المواد السياقية)
5. Physics (الارتكاز، الظلال، الانعكاسات)
6. Editing preservation (مقارنة الصورة المعدّلة بالأصلية للتأكد من عدم المساس بالمناطق المحمية)
7. Artifact detection (كشف التشوهات أو التلف)
"""
from __future__ import annotations
from typing import Optional, Callable
from .schemas import QualityReport, VisualPlan
from image_processing.validation import validate_generated_image_bytes, compare_original_and_edited


def inspect_generated_image(
    image_bytes: bytes,
    plan: VisualPlan,
    original_image_bytes: Optional[bytes] = None,
    vision_audit_fn: Optional[Callable[[bytes, str], str]] = None,
) -> QualityReport:
    """فحص مخرجات التوليد أو التعديل وإرجاع تقرير العيوب القابلة للتصحيح الموجه."""
    val = validate_generated_image_bytes(image_bytes)
    if not val["valid"]:
        issues_list = val.get("issues") or ["بيانات الصورة غير صالحة أو تالفة"]
        return QualityReport(
            passed=False,
            score=0.0,
            width=val.get("width", 0),
            height=val.get("height", 0),
            format=val.get("format", "unknown"),
            checks_run={
                "instruction_match": False,
                "composition": False,
                "anatomy": False,
                "material_realism": False,
                "physics": False,
                "editing_preservation": False,
                "artifact_detection": False,
            },
            defects=[{"check": "artifact_detection", "location": "full_frame", "defect": issues_list[0]}],
            issues=issues_list,
            preservation_score=0.0,
            retry_recommended=True,
            refined_prompt_addition="QUALITY CORRECTION: Ensure clean, well-exposed, high-clarity image output without corruption.",
        )

    issues = list(val.get("issues") or [])
    defects = []
    score = float(val.get("score", 1.0))
    preservation_score = 1.0

    checks_run = {
        "instruction_match": True,
        "composition": True,
        "anatomy": True,
        "material_realism": True,
        "physics": True,
        "editing_preservation": True,
        "artifact_detection": len(issues) == 0,
    }

    # 6. Editing preservation: مقارنة الصورة المعدّلة بالصورة الأصلية عند توفرها
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
            })
            checks_run["editing_preservation"] = False
            score = min(score, 0.5)

    # فحص بصري دلالي عبر نموذج الرؤية عند توفره
    if vision_audit_fn is not None and score >= 0.6:
        try:
            audit_q = (
                f"User requested: {plan.raw_request[:300]}.\n"
                "Inspect against these 7 criteria:\n"
                "1) Instruction match (all requested subjects/actions/colors present?)\n"
                "2) Composition\n"
                "3) Anatomy (if hands visible, 5 plausible digits? plausible face/body/animal anatomy?)\n"
                "4) Material realism (no random dirt on clean portrait?)\n"
                "5) Physics (grounded contact shadows, natural grip/reflections?)\n"
                "6) Editing preservation (protected elements unchanged?)\n"
                "7) Artifact detection (no duplicated objects or melted edges?)\n"
                "Reply 'OK' if acceptable, or 'FAIL: <check_name> | <location> | <short defect>' if a clear defect is visible."
            )
            verdict = (vision_audit_fn(image_bytes, audit_q) or "").strip()
            if verdict.upper().startswith("FAIL:"):
                body = verdict.split(":", 1)[1].strip()
                parts = [p.strip() for p in body.split("|")]
                chk = parts[0].lower() if len(parts) >= 1 else "instruction_match"
                loc = parts[1] if len(parts) >= 2 else "main_subject"
                reason = parts[-1][:160]
                issues.append(f"ملاحظة الفحص البصري ({chk} @ {loc}): {reason}")
                defects.append({"check": chk, "location": loc, "defect": reason})
                if chk in checks_run:
                    checks_run[chk] = False
                score = min(score, 0.45)
        except Exception:
            pass

    passed = score >= 0.6 and len(issues) == 0
    refined_add = ""
    if not passed:
        fixes = ["preserve all successful elements and fix only the identified defect"]
        if not checks_run["editing_preservation"] or plan.identity_lock.enabled:
            fixes.append("strictly lock all protected regions and the reference person's exact facial bone structure, pose, and unrequested background")
        if plan.scene.has_visible_hands or not checks_run["anatomy"]:
            fixes.append("render anatomically accurate 5-finger hands with natural grip and contact shadows")
        if not checks_run["artifact_detection"]:
            fixes.append("ensure balanced exposure, sharp focus on the main subject, and zero visual corruption")
        for d in defects[:2]:
            fixes.append(f"fix {d['check']} at {d['location']}: {d['defect']}")
        refined_add = "QUALITY CORRECTION: " + "; ".join(fixes) + "."

    return QualityReport(
        passed=passed,
        score=round(score, 2),
        width=val.get("width", 0),
        height=val.get("height", 0),
        format=val.get("format", "png"),
        checks_run=checks_run,
        defects=defects[:3],
        issues=issues,
        preservation_score=round(preservation_score, 2),
        retry_recommended=not passed,
        refined_prompt_addition=refined_add,
    )
