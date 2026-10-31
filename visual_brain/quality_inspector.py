# -*- coding: utf-8 -*-
"""
فاحص جودة الصورة بعد التوليد (Post-Generation Quality Inspector):
يفحص النتيجة بعد كل عملية توليد أو تعديل:
1. الفحص التقني والبصري السريع (صحة الملف، الأبعاد، عدم التشوه أو السواد/البياض الكامل، والتباين).
2. الفحص الدلالي الاختياري عبر نموذج الرؤية (هل نفّذ طلب المستخدم؟ هل تغيرت الملامح؟ هل توجد أخطاء بالأيدي؟ هل الإضاءة متناسقة؟).
3. إذا فشلت النتيجة، يحدد سبب الفشل ويقترح إعادة المحاولة بتعليمات محسّنة (مع عدد محاولات محدود).
"""
from __future__ import annotations
from typing import Optional, Callable
from .schemas import QualityReport, VisualPlan
from image_processing.validation import validate_generated_image_bytes


def inspect_generated_image(
    image_bytes: bytes,
    plan: VisualPlan,
    vision_Audit_fn: Optional[Callable[[bytes, str], str]] = None,
) -> QualityReport:
    """فحص الصورة المولّدة وإصدار تقرير الجودة واقتراح تحسين البرومبت عند الحاجة."""
    val = validate_generated_image_bytes(image_bytes)
    if not val["valid"]:
        return QualityReport(
            passed=False,
            score=0.0,
            width=val.get("width", 0),
            height=val.get("height", 0),
            format=val.get("format", "unknown"),
            issues=val.get("issues") or ["بيانات الصورة غير صالحة أو تالفة"],
            retry_recommended=True,
            refined_prompt_addition="QUALITY CORRECTION: Ensure clean, well-exposed, high-clarity image output without corruption.",
        )

    issues = list(val.get("issues") or [])
    score = float(val.get("score", 1.0))

    # فحص دلالي عبر نموذج الرؤية إذا تم تمريره
    if vision_Audit_fn is not None and score >= 0.6:
        try:
            audit_q = (
                f"User requested: {plan.raw_request[:300]}.\n"
                "Check rapidly: 1) Did it follow the request? 2) Any deformed hands/extra fingers? "
                "3) Any waxy/plastic skin or broken lighting? "
                "Reply 'OK' if good, or 'FAIL: <short reason>' if severely flawed."
            )
            verdict = (vision_Audit_fn(image_bytes, audit_q) or "").strip()
            if verdict.upper().startswith("FAIL:"):
                reason = verdict.split(":", 1)[1].strip()[:160]
                issues.append(f"ملاحظة فحص الرؤية: {reason}")
                score = min(score, 0.45)
        except Exception:
            pass

    passed = score >= 0.6 and len(issues) == 0
    refined_add = ""
    if not passed:
        fixes = ["strictly natural lighting and sharp subject focus"]
        if plan.scene.has_humans:
            fixes.append("anatomically accurate 5-finger hands, natural skin pores, and consistent facial identity")
        if plan.identity_lock.enabled:
            fixes.append("strictly preserve the reference person's exact facial bone structure and untouched regions")
        refined_add = "QUALITY CORRECTION: " + ", ".join(fixes) + "."

    return QualityReport(
        passed=passed,
        score=round(score, 2),
        width=val.get("width", 0),
        height=val.get("height", 0),
        format=val.get("format", "png"),
        issues=issues,
        retry_recommended=not passed,
        refined_prompt_addition=refined_add,
    )
