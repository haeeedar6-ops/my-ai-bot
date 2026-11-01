# -*- coding: utf-8 -*-
"""
سجل القدرات الفعلي والموثّق للمزودات المهيأة في المشروع (Section 7 Provider Capability Registry):
1. instruct_pix2pix: نموذج `timbrooks/instruct-pix2pix` المخصص لتعديل الصور بالتعليمات النصية (Image-to-Image / Inpaint / Remove / Style Transfer) مع الحفاظ العالي على بنية الصورة الأصلية.
2. gemini: تكامل Gemini Image لتوليد الصور وتعديل الصور المرجعية.
3. flux: تكامل Cloudflare Workers AI FLUX لتوليد الصور من النص (T2I).
4. together: تكامل Together AI FLUX لتوليد الصور من النص (T2I).
5. pollinations: تكامل Pollinations لتوليد الصور من النص (T2I).
6. local_enhance: معالج الصور المحلي عبر Pillow (لتحسين الدقة وتصحيح الألوان والترميم الأساسي).
"""
from __future__ import annotations
from typing import Dict, Tuple
from .schemas import ProviderCapability


def get_provider_capabilities(configured_flags: Dict[str, bool] | None = None) -> Dict[str, ProviderCapability]:
    """بناء مصفوفة قدرات المزودات بناءً على حالة الإعداد الفعلية في البيئة."""
    flags = configured_flags or {}
    return {
        "instruct_pix2pix": ProviderCapability(
            provider_id="instruct_pix2pix",
            display_name="InstructPix2Pix (timbrooks/instruct-pix2pix)",
            model_id="timbrooks/instruct-pix2pix",
            configured=bool(flags.get("instruct_pix2pix", False)),
            text_to_image=False,
            image_to_image=True,
            inpainting_mask=True,
            remove_object=True,
            enhance_restore=True,
            style_transfer=True,
            max_reference_images=1,
            accepted_formats=["image/png", "image/jpeg", "image/webp"],
            notes="نموذج timbrooks/instruct-pix2pix المتخصص في تعديل الصور المرجعية عبر أوامر التعديل المباشرة مع التحكم في معامل الحفاظ على الصورة الأصلية (image_guidance_scale).",
        ),
        "gemini": ProviderCapability(
            provider_id="gemini",
            display_name="Gemini Image",
            model_id="gemini-2.5-flash-image",
            configured=bool(flags.get("gemini", False)),
            text_to_image=True,
            image_to_image=True,
            inpainting_mask=True,
            remove_object=True,
            enhance_restore=True,
            style_transfer=True,
            max_reference_images=4,
            accepted_formats=["image/png", "image/jpeg", "image/webp"],
            notes="يدعم توليد الصور من النص وتعديل الصور المرجعية (I2I / Inpaint / Object Removal / Restoration / Style Transfer) عبر تمرير أجزاء الصورة المرجعية.",
        ),
        "flux": ProviderCapability(
            provider_id="flux",
            display_name="Cloudflare FLUX",
            model_id="@cf/black-forest-labs/flux-2-klein-4b",
            configured=bool(flags.get("flux", False)),
            text_to_image=True,
            image_to_image=False,
            inpainting_mask=False,
            remove_object=False,
            enhance_restore=False,
            style_transfer=False,
            max_reference_images=0,
            accepted_formats=["image/png", "image/jpeg"],
            notes="مسار Cloudflare Workers AI الحالي مهيأ لتوليد الصور من النص (Text-to-Image) فقط، ولا يستقبل صورة مرجعية للتعديل.",
        ),
        "together": ProviderCapability(
            provider_id="together",
            display_name="Together FLUX",
            model_id="black-forest-labs/FLUX.1-schnell-Free",
            configured=bool(flags.get("together", False)),
            text_to_image=True,
            image_to_image=False,
            inpainting_mask=False,
            remove_object=False,
            enhance_restore=False,
            style_transfer=False,
            max_reference_images=0,
            accepted_formats=["image/png"],
            notes="مسار Together AI الحالي مهيأ لتوليد الصور من النص (Text-to-Image) فقط.",
        ),
        "pollinations": ProviderCapability(
            provider_id="pollinations",
            display_name="Pollinations FLUX",
            model_id="flux",
            configured=bool(flags.get("pollinations", False)),
            text_to_image=True,
            image_to_image=False,
            inpainting_mask=False,
            remove_object=False,
            enhance_restore=False,
            style_transfer=False,
            max_reference_images=0,
            accepted_formats=["image/jpeg", "image/png"],
            notes="مسار Pollinations الحالي مهيأ لتوليد الصور من النص (Text-to-Image) فقط.",
        ),
        "local_enhance": ProviderCapability(
            provider_id="local_enhance",
            display_name="Local Image Enhancer (Pillow)",
            model_id="pillow-enhancer",
            configured=bool(flags.get("local_enhance", True)),
            text_to_image=False,
            image_to_image=False,
            inpainting_mask=False,
            remove_object=False,
            enhance_restore=True,
            style_transfer=False,
            max_reference_images=1,
            accepted_formats=["image/png", "image/jpeg", "image/webp"],
            notes="معالجة محلية حتمية لتحسين الجودة، رفع الدقة، تصحيح التباين والألوان، وتقليل التشويش دون إعادة رسم المشهد.",
        ),
    }


def verify_provider_for_task(
    provider_id: str,
    task_type: str,
    requires_reference_image: bool,
    capabilities: Dict[str, ProviderCapability],
) -> Tuple[bool, str]:
    """
    التحقق مما إذا كان المزود يدعم نوع المهمة المطلوبة فعلياً (تطبيق صارم لقاعدة No silent downgrade).
    """
    cap = capabilities.get(provider_id)
    if cap is None:
        return False, f"المزود {provider_id} غير معرّف في سجل القدرات."
    if not cap.configured:
        return False, f"المزود {cap.display_name} غير مفعّل في متغيرات البيئة الحالية."

    if task_type == "text_to_image" and not requires_reference_image:
        if cap.text_to_image:
            return True, "يدعم التوليد من النص (T2I)."
        return False, f"{cap.display_name} مخصص لتعديل الصور ولا يدعم التوليد من النص وحده."

    if task_type in ("upscale", "color_correct", "restore"):
        if cap.enhance_restore:
            return True, "يدعم تحسين وترميم الصور."
        return False, f"{cap.display_name} لا يدعم تحسين أو ترميم صورة مرجعية."

    if requires_reference_image or task_type in ("image_edit", "inpaint", "remove_object", "style_transfer"):
        if task_type == "inpaint" and not cap.inpainting_mask:
            return False, f"{cap.display_name} لا يدعم التعديل الموضعي (Inpainting) على صورة مرجعية."
        if task_type == "remove_object" and not cap.remove_object:
            return False, f"{cap.display_name} لا يدعم إزالة العناصر من صورة مرجعية."
        if task_type == "style_transfer" and not cap.style_transfer:
            return False, f"{cap.display_name} لا يدعم تحويل النمط مع الحفاظ على الصورة المرجعية."
        if not cap.image_to_image:
            return False, f"{cap.display_name} يدعم توليد صور جديدة من النص فقط ولا يدعم تعديل صورة مرجعية."
        return True, "يدعم تعديل الصور المرجعية."

    return cap.text_to_image, "فحص افتراضي."
