# -*- coding: utf-8 -*-
"""
سجل القدرات الفعلي والموثّق للمزودات المهيأة في المشروع (Section 7 Provider Capability Registry):
- لا يحتوي على أي إشارة إلى Unconfigured Provider (قاعدة No Unconfigured Provider الصارمة).
- يعتمد فقط على المسارات الموجودة فعلياً في المستودع:
  1. gemini: تكامل Gemini Image الموجود في المشروع (يدعم T2I ويدعم التعديل بالصورة المرجعية I2I / Inpaint / Remove / Style Transfer).
  2. flux: تكامل Cloudflare Workers AI FLUX (يدعم T2I فقط في المسار المهيأ حالياً، ولا يدعم تمرير صورة مرجعية).
  3. together: تكامل Together AI FLUX (يدعم T2I فقط في المسار المهيأ حالياً).
  4. pollinations: تكامل Pollinations (يدعم T2I فقط عبر رابط النص).
  5. local_enhance: معالج الصور المحلي عبر Pillow (يدعم تحسين الحدة، إزالة التشويش، تصحيح الألوان، والترميم الأساسي بدون إعادة توليد المشهد).
"""
from __future__ import annotations
from typing import Dict, List, Tuple
from .schemas import ProviderCapability


def get_provider_capabilities(configured_flags: Dict[str, bool] | None = None) -> Dict[str, ProviderCapability]:
    """بناء مصفوفة قدرات المزودات بناءً على حالة الإعداد الفعلية في البيئة."""
    flags = configured_flags or {}
    return {
        "gemini": ProviderCapability(
            provider_id="gemini",
            display_name="Gemini Image",
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
        return False, f"{cap.display_name} لا يدعم التوليد من النص."

    # مهام التحسين والترميم وتصحيح الألوان على صورة مرفقة
    if task_type in ("upscale", "color_correct", "restore"):
        if cap.enhance_restore:
            return True, "يدعم تحسين وترميم الصور."
        return False, f"{cap.display_name} لا يدعم تحسين أو ترميم صورة مرجعية."

    # مهام التعديل البصري، الحذف، التعديل الموضعي، أو تحويل النمط على صورة مرجعية
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
