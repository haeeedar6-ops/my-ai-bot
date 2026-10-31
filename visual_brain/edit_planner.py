# -*- coding: utf-8 -*-
"""
مخطط عمليات التعديل البصري العشر (Edit Planner):
يكتشف نوع التعديل المطلوب بدقة:
1. تغيير الخلفية فقط (background_only)
2. تغيير الملابس فقط (clothing_only)
3. تغيير لون العيون (eye_color)
4. تعديل الإضاءة (lighting_adjustment)
5. إزالة وإضافة العناصر (add_remove_object)
6. تحسين جودة الصورة (enhance_upscale)
7. توسيع الصورة خارج حدودها (outpainting)
8. تغيير زاوية المشهد أو تكوينه (camera_reframe)
9. دمج صور متعددة (multi_image_merge)
10. تعديل جزء محدد مع حماية بقية الصورة (localized_inpaint)
"""
from __future__ import annotations
import re
from .schemas import EditPlan, IdentityLock

_OP_PATTERNS = [
    ("outpainting", re.compile(r"(?i)(وسّ?ع\s+الصورة|توسيع\s+الصورة|كمّ?ل\s+حواف|خارج\s+حدودها|أعرض|\b(?:outpaint|outpainting|expand\s+image|uncrop|extend\s+borders)\b)")),
    ("multi_image_merge", re.compile(r"(?i)(ادمج\s+الصورتين|دمج\s+صور|ركّ?ب\s+الصورتين|اجمع\s+الصورتين|\b(?:merge\s+images|blend\s+photos|combine\s+two\s+images|composite)\b)")),
    ("enhance_upscale", re.compile(r"(?i)(حسّ?ن\s+جودة|تحسين\s+الجودة|وضّ?ح\s+الصورة|ارفع\s+الدقة|كبّ?ر\s+الدقة|صفّ?ي\s+الصورة|\b(?:upscale|enhance\s+quality|sharpen|denoise|restore\s+photo)\b)")),
    ("eye_color", re.compile(r"(?i)(لون\s+العيون|خلّ?ي\s+عيون[هو]?|غيّ?ر\s+عيون[هو]?|\b(?:eye\s+color|change\s+eyes\s+to|blue\s+eyes|green\s+eyes|hazel\s+eyes)\b)")),
    ("background_only", re.compile(r"(?i)(غيّ?ر\s+الخلفية|بدّ?ل\s+الخلفية|الخلفية\s+بس|خلّ?ي\s+الخلفية|خلّ?ي\s+وراه|\b(?:change\s+background|replace\s+background|background\s+only|new\s+background)\b)")),
    ("clothing_only", re.compile(r"(?i)(غيّ?ر\s+الملابس|بدّ?ل\s+الملابس|بدّ?ل\s+كنزة|بدّ?ل\s+القميص|لبّ?س[وه]?|خلّ?يه\s+لابس|\b(?:change\s+clothes|swap\s+outfit|change\s+shirt|change\s+jacket|wear\s+a)\b)")),
    ("lighting_adjustment", re.compile(r"(?i)(عدّ?ل\s+الإضاءة|غيّ?ر\s+الإضاءة|خلّ?ي\s+الإضاءة|خلّ?ي\s+الجو|وقت\s+الغروب|بالليل|إضاءة\s+دافئة|\b(?:change\s+lighting|relight|adjust\s+light|night\s+lighting|sunset\s+light)\b)")),
    ("camera_reframe", re.compile(r"(?i)(غيّ?ر\s+زاوية|زاوية\s+المشهد|زاوية\s+الكاميرا|تكوين\s+الصورة|قرّ?ب\s+الكاميرا|بعّ?د\s+الكاميرا|\b(?:change\s+angle|reframe|camera\s+perspective|zoom\s+out|close-up\s+view)\b)")),
    ("add_remove_object", re.compile(r"(?i)(احذف|شيل|أزل|ازل|امسح|ضيف|أضف|اضف|حطّ?ل?[وهي]?|\b(?:remove|delete|erase|add\s+a|insert\s+a|put\s+a)\b)")),
]


def plan_edit_operation(user_text: str, is_edit_like: bool, identity_lock: IdentityLock, ref_count: int = 0) -> EditPlan:
    """بناء خطة التعديل الدقيقة وتحديد نوع العملية والأدوات المطلوبة (Mask / Outpaint / Multi-image)."""
    t = (user_text or "").strip()
    if not is_edit_like and ref_count == 0:
        return EditPlan(is_edit=False, operation_type="generate")

    detected = []
    for op_name, pat in _OP_PATTERNS:
        if pat.search(t):
            detected.append(op_name)

    if ref_count > 1 and "multi_image_merge" not in detected:
        detected.insert(0, "multi_image_merge")

    if not detected:
        detected.append("localized_inpaint")

    primary_op = detected[0]
    # إذا طلب تعديلاً مركباً (مثلاً ملابس + إضاءة) نصنفه localized_inpaint مع قائمة العمليات الفرعية
    if len(detected) > 1 and primary_op in ("background_only", "clothing_only", "eye_color"):
        primary_op = "localized_inpaint"

    target_map = {
        "background_only": "background region only (keep foreground subject 100% untouched)",
        "clothing_only": "garment/clothing region only (keep face, hands, and background untouched)",
        "eye_color": "iris region of the eyes only (keep face, skin, and lighting untouched)",
        "lighting_adjustment": "global scene illumination, color temperature, and shadows (keep geometry and identity locked)",
        "add_remove_object": "specified target object region only with seamless background/lighting inpainting",
        "enhance_upscale": "full frame micro-detail restoration and optical sharpening without altering structure",
        "outpainting": "outer frame borders expansion matching perspective, lighting, and texture continuity",
        "camera_reframe": "camera viewpoint and composition reframing while preserving subject identity",
        "multi_image_merge": "seamless composite blending subjects and lighting across reference images",
        "localized_inpaint": "targeted regions requested by the user while strictly protecting the rest of the image",
    }

    requires_mask = primary_op in ("background_only", "clothing_only", "eye_color", "add_remove_object", "localized_inpaint")
    requires_outpaint = primary_op == "outpainting"
    requires_multi = primary_op == "multi_image_merge" or ref_count > 1

    preserved_en = "; ".join(identity_lock.locked_elements) if (identity_lock and identity_lock.enabled) else "all unrequested regions"
    edit_inst_en = (
        f"OPERATION: {primary_op} ({', '.join(detected)}). "
        f"TARGET REGION: {target_map.get(primary_op, 'requested region')}. "
        f"PROTECTED REGIONS: {preserved_en}."
    )

    return EditPlan(
        is_edit=True,
        operation_type=primary_op,
        operations_detected=detected,
        target_region=target_map.get(primary_op, "targeted region"),
        requires_mask=requires_mask,
        requires_outpaint_canvas=requires_outpaint,
        requires_multi_image=requires_multi,
        edit_instructions_en=edit_inst_en,
        preserved_summary_en=preserved_en,
    )
