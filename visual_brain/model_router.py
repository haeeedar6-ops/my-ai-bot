# -*- coding: utf-8 -*-
"""
موجّه النماذج والمزودات المبني على القدرات الموثّقة (Verified Provider Router — Section 7):
- يدعم نموذج `timbrooks/instruct-pix2pix` (`instruct_pix2pix`) و `gemini` لمهام تعديل الصور.
- يطبّق قاعدة No silent downgrade بصرامة:
  إذا طلب المستخدم تعديل صورة مرجعية ولم يتوفر أي مزود مفعّل يدعم التعديل، يُرجع `unsupported`
  بدلاً من تحويل الطلب بصمت إلى توليد صورة جديدة من النص (T2I).
"""
from __future__ import annotations
from typing import Dict, List, Tuple
from .schemas import EditPlan, SceneSpec
from .provider_capabilities import get_provider_capabilities, verify_provider_for_task


TASK_ROUTING_TABLE: Dict[str, List[str]] = {
    "text_to_image_general": ["flux", "gemini", "together", "pollinations"],
    "text_to_image_photorealistic": ["flux", "gemini", "together", "pollinations"],
    "image_edit": ["instruct_pix2pix", "gemini"],
    "inpaint": ["instruct_pix2pix", "gemini"],
    "remove_object": ["instruct_pix2pix", "gemini"],
    "style_transfer": ["instruct_pix2pix", "gemini"],
    "restore": ["gemini", "instruct_pix2pix", "local_enhance"],
    "upscale": ["local_enhance", "gemini"],
    "color_correct": ["local_enhance", "instruct_pix2pix", "gemini"],
}


def route_model_for_task(
    scene: SceneSpec,
    edit_plan: EditPlan,
    available_providers: Dict[str, bool],
    has_ref_image: bool = False,
    preferred_provider: str = "auto",
) -> Tuple[str, List[str], str]:
    """
    يرجع (المزود الرئيسي، قائمة المزودات البديلة المتوافقة مع نفس نوع المهمة فقط، تصنيف المهمة).
    """
    norm_avail = dict(available_providers or {})
    norm_avail.setdefault("local_enhance", True)
    caps = get_provider_capabilities(norm_avail)

    if edit_plan.is_edit or has_ref_image:
        wf = edit_plan.workflow_type or "image_edit"
        if wf in ("upscale", "color_correct", "restore"):
            task_cat = wf
        elif wf in ("inpaint", "remove_object", "style_transfer"):
            task_cat = wf
        else:
            task_cat = "image_edit"
        requires_ref = True
    else:
        task_cat = "text_to_image_photorealistic" if scene.style in ("photorealistic", "cinematic") else "text_to_image_general"
        requires_ref = False

    candidates = list(TASK_ROUTING_TABLE.get(task_cat, TASK_ROUTING_TABLE["text_to_image_general"]))

    pref_map = {
        "instruct_pix2pix": "instruct_pix2pix",
        "instruct-pix2pix": "instruct_pix2pix",
        "timbrooks/instruct-pix2pix": "instruct_pix2pix",
        "pix2pix": "instruct_pix2pix",
        "gemini": "gemini",
        "cloudflare": "flux",
        "flux": "flux",
        "together": "together",
        "pollinations": "pollinations",
    }
    mapped_pref = pref_map.get((preferred_provider or "auto").lower())
    if mapped_pref and mapped_pref in candidates:
        candidates.remove(mapped_pref)
        candidates.insert(0, mapped_pref)

    verified_chain: List[str] = []
    check_task = "text_to_image" if not requires_ref else (edit_plan.workflow_type or "image_edit")
    for prov_id in candidates:
        ok, _reason = verify_provider_for_task(prov_id, check_task, requires_ref, caps)
        if ok:
            verified_chain.append(prov_id)

    if not verified_chain:
        if not any(norm_avail.get(k, False) for k in ("instruct_pix2pix", "gemini", "flux", "together", "pollinations")):
            theoretical = [p for p in candidates if p != "local_enhance"] or candidates
            return theoretical[0], theoretical[1:], task_cat
        return "unsupported", [], task_cat

    return verified_chain[0], verified_chain[1:], task_cat
