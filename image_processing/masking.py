# -*- coding: utf-8 -*-
"""
بناء أقنعة التعديل الموضعي (Localized Masking & Region Isolation):
يولّد توصيف القناع الموضعي وقناع PNG للعمليات الموضعية (الخلفية، الملابس، العيون، الحذف/الإضافة)
بحيث لا يتم إعادة توليد المناطق المحمية من الصورة.
"""
from __future__ import annotations
import io
from typing import Optional


def build_region_mask_hint(operation_type: str, user_text: str = "") -> str:
    """إرشادات العزل الموضعي الدقيقة لكل نوع من عمليات التعديل العشر."""
    hints = {
        "background_only": (
            "[LOCALIZED MASK: BACKGROUND ONLY] Isolate the foreground subject with a sharp alpha boundary; "
            "replace ONLY the background pixels and match edge spill lighting while keeping 100% of the subject pixels intact."
        ),
        "clothing_only": (
            "[LOCALIZED MASK: GARMENT ONLY] Mask strictly the torso/clothing region; "
            "keep the face, neck, hands, pose, and entire background pixel-locked."
        ),
        "eye_color": (
            "[LOCALIZED MICRO-MASK: IRIS ONLY] Mask strictly the iris area of both eyes; "
            "preserve natural pupil size, catchlight reflection, sclera, eyelids, and entire face."
        ),
        "add_remove_object": (
            "[LOCALIZED INPAINT MASK] Inpaint strictly the bounding box of the target object to add/remove; "
            "blend surrounding surface texture and contact shadows seamlessly without altering the rest of the scene."
        ),
        "localized_inpaint": (
            "[SELECTIVE REGION INPAINT] Modify strictly the user-specified elements while locking all untouched regions."
        ),
    }
    return hints.get(operation_type, "")


def generate_focus_mask_bytes(width: int = 512, height: int = 512, region: str = "center") -> Optional[bytes]:
    """توليد قناع أبيض وأسود (Mask PNG) عند الحاجة لأدوات Inpainting."""
    try:
        from PIL import Image, ImageDraw
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        if region == "background":
            mask = Image.new("L", (width, height), 255)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((int(width * 0.2), int(height * 0.1), int(width * 0.8), int(height * 0.9)), fill=0)
        elif region == "clothing":
            draw.rectangle((int(width * 0.2), int(height * 0.45), int(width * 0.8), int(height * 0.98)), fill=255)
        else:
            draw.rectangle((int(width * 0.15), int(height * 0.15), int(width * 0.85), int(height * 0.85)), fill=255)
        buf = io.BytesIO()
        mask.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None
