# -*- coding: utf-8 -*-
"""
موجّه النماذج الذكي (Smart Model Router):
يختار المحرك الأنسب وسلسلة البدائل (Fallback Chain) حسب نوع المهمة:
- توليد صور عامة -> FLUX (أو Gemini Nano Banana كبديل/أساسي حسب المتاح)
- تعديل صور معقد -> FLUX / Nano Banana (مع تمرير الصورة المرجعية لمحرك الرؤية والتعديل)
- صور واقعية بديلة -> FLUX
- تعديل موضعي (Inpainting) -> نموذج يدعم التعديل الموضعي بالصورة المرجعية والقناع (nano_banana / flux)
- توسيع الصور (Outpainting) -> نموذج يدعم توسيع الحواف (nano_banana / flux)
"""
from __future__ import annotations
from typing import Dict, List, Tuple
from .schemas import EditPlan, SceneSpec


TASK_ROUTING_TABLE: Dict[str, List[str]] = {
    "generate_general": ["flux", "nano_banana", "together", "pollinations"],
    "generate_photorealistic": ["flux", "nano_banana", "together", "pollinations"],
    "complex_edit": ["nano_banana", "flux", "together", "pollinations"],
    "localized_inpaint": ["nano_banana", "flux", "together"],
    "outpainting": ["nano_banana", "flux", "together"],
    "multi_image_merge": ["nano_banana", "flux"],
}


def route_model_for_task(
    scene: SceneSpec,
    edit_plan: EditPlan,
    available_providers: Dict[str, bool],
    has_ref_image: bool = False,
    preferred_provider: str = "auto",
) -> Tuple[str, List[str], str]:
    """
    يحدد (المزود الرئيسي المقترح، قائمة المزودات الاحتياطية بالترتيب، تصنيف المهمة).
    ويراعي قدرة المزود الفعلية على استقبال صورة مرجعية (ref_parts) عند التعديل.
    """
    if edit_plan.is_edit or has_ref_image:
        if edit_plan.operation_type == "outpainting":
            task_category = "outpainting"
        elif edit_plan.operation_type == "multi_image_merge":
            task_category = "multi_image_merge"
        elif edit_plan.requires_mask or edit_plan.operation_type in ("background_only", "clothing_only", "eye_color", "add_remove_object", "localized_inpaint"):
            task_category = "localized_inpaint"
        else:
            task_category = "complex_edit"
    else:
        if scene.style in ("photorealistic", "cinematic"):
            task_category = "generate_photorealistic"
        else:
            task_category = "generate_general"

    ideal_order = list(TASK_ROUTING_TABLE.get(task_category, TASK_ROUTING_TABLE["generate_general"]))

    # إذا كان المستخدم في وضع تعديل صورة مرفقة فعلية، فإن nano_banana (Gemini Image) يتفوق لأنه يستقبل بكسل الصورة المرجعية مباشرة
    if has_ref_image and "nano_banana" in ideal_order:
        ideal_order.remove("nano_banana")
        ideal_order.insert(0, "nano_banana")

    # احترام التفضيل الصريح في الإعدادات إن لم يكن auto
    pref_map = {"gemini": "nano_banana", "cloudflare": "flux", "flux": "flux", "together": "together", "pollinations": "pollinations"}
    mapped_pref = pref_map.get((preferred_provider or "auto").lower())
    if mapped_pref and mapped_pref in ideal_order and not has_ref_image:
        ideal_order.remove(mapped_pref)
        ideal_order.insert(0, mapped_pref)

    active_chain = [p for p in ideal_order if available_providers.get(p, False)]
    if not active_chain:
        # في الاختبارات أو عند عدم تفعيل مزودات خارجية نعيد الترتيب المثالي
        active_chain = ideal_order

    primary = active_chain[0]
    fallbacks = active_chain[1:]
    return primary, fallbacks, task_category
