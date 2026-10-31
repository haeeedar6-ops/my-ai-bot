# -*- coding: utf-8 -*-
"""تجهيز الصور المرجعية وتوسيع اللوحة لعمليات Outpainting."""
from __future__ import annotations
import io
from typing import Tuple
from .validation import detect_image_format_and_size


def prepare_reference_image(image_bytes: bytes, max_side: int = 1536) -> Tuple[bytes, str]:
    """التحقق من الصورة المرجعية وضبط أبعادها إن لزم الأمر."""
    fmt, _w, _h = detect_image_format_and_size(image_bytes)
    if fmt is None:
        raise ValueError("ملف الصورة المرجعية غير صالح أو بصيغة غير مدعومة")
    mime = f"image/{fmt}"
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as im:
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side))
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                return buf.getvalue(), "image/png"
    except Exception:
        pass
    return image_bytes, mime


def expand_canvas_for_outpaint(image_bytes: bytes, pad_ratio: float = 0.25) -> bytes:
    """توسيع حواف الصورة استعداداً لعملية Outpainting عند توفر Pillow."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            w, h = im.size
            nw, nh = int(w * (1 + pad_ratio * 2)), int(h * (1 + pad_ratio * 2))
            canvas = Image.new("RGB", (nw, nh), (128, 128, 128))
            canvas.paste(im, (int(w * pad_ratio), int(h * pad_ratio)))
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return image_bytes
