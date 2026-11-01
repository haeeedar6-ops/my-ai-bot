# -*- coding: utf-8 -*-
"""
تحسين وترميم وتصحيح ألوان الصور (Workflow E: Image Enhancement / Restoration / Upscaling / Color Correction — Section 3).
يعالج الصورة محلياً عبر Pillow دون إعادة توليد المشهد من الصفر عندما يكون المطلوب تحسيناً أو تصحيح ألوان.
"""
from __future__ import annotations
import io


def enhance_image_clarity(image_bytes: bytes, mode: str = "upscale") -> bytes:
    """تنفيذ التحسين المطلوب (sharpening, denoising, upscaling, color_correct, restore) بأمان."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            w, h = im.size
            if mode == "upscale" and max(w, h) <= 1024 and max(w, h) >= 16:
                resample = getattr(Image, "Resampling", Image).LANCZOS
                im = im.resize((int(w * 1.5), int(h * 1.5)), resample)
                im = ImageEnhance.Sharpness(im).enhance(1.3)
            elif mode == "color_correct":
                im = ImageEnhance.Color(im).enhance(1.12)
                im = ImageEnhance.Contrast(im).enhance(1.08)
            elif mode == "restore":
                im = im.filter(ImageFilter.MedianFilter(size=3))
                im = ImageEnhance.Sharpness(im).enhance(1.2)
                im = ImageEnhance.Contrast(im).enhance(1.06)
            else:
                im = ImageEnhance.Sharpness(im).enhance(1.25)
                im = ImageEnhance.Contrast(im).enhance(1.05)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return image_bytes
