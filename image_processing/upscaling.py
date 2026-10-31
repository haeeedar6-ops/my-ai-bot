# -*- coding: utf-8 -*-
"""تحسين جودة ووضوح الصورة (Image Enhancement & Upscaling)."""
from __future__ import annotations
import io


def enhance_image_clarity(image_bytes: bytes) -> bytes:
    """تحسين الحدة والتباين الدقيق للصورة عند طلب تحسين الجودة."""
    try:
        from PIL import Image, ImageEnhance
        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            im = ImageEnhance.Sharpness(im).enhance(1.25)
            im = ImageEnhance.Contrast(im).enhance(1.05)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return image_bytes
