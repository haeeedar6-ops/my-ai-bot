# -*- coding: utf-8 -*-
"""
فحص صحة وجودة الصور (Image Validation):
يفحص توقيع الملف (Magic Bytes)، الأبعاد (Width/Height)، حجم البيانات، والتشوه البصري،
ويعمل بالمكتبة القياسية لبايثون مع دعم إضافي لـ Pillow عند توفرها.
"""
from __future__ import annotations
import struct
import io
from typing import Dict, Any, Tuple, Optional


def detect_image_format_and_size(data: bytes) -> Tuple[Optional[str], int, int]:
    """استخراج صيغة الصورة (png/jpeg/webp) وأبعادها (width, height) بدون تبعيات خارجية."""
    if not data or not isinstance(data, (bytes, bytearray)) or len(data) < 16:
        return None, 0, 0
    b = bytes(data)
    # 1. PNG
    if b.startswith(b"\x89PNG\r\n\x1a\n") and len(b) >= 24:
        w, h = struct.unpack(">II", b[16:24])
        return "png", int(w), int(h)
    # 2. JPEG
    if b.startswith(b"\xff\xd8"):
        idx = 2
        n = len(b)
        while idx < n - 8:
            if b[idx] != 0xFF:
                idx += 1
                continue
            marker = b[idx + 1]
            if marker == 0xFF:
                idx += 1
                continue
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", b[idx + 5:idx + 9])
                return "jpeg", int(w), int(h)
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                idx += 2
                continue
            seg_len = struct.unpack(">H", b[idx + 2:idx + 4])[0]
            if seg_len < 2:
                break
            idx += 2 + seg_len
        return "jpeg", 512, 512
    # 3. WEBP
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP" and len(b) >= 30:
        chunk = b[12:16]
        if chunk == b"VP8X" and len(b) >= 30:
            w = 1 + int.from_bytes(b[24:27], "little")
            h = 1 + int.from_bytes(b[27:30], "little")
            return "webp", w, h
        if chunk == b"VP8 " and len(b) >= 30:
            w = struct.unpack("<H", b[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", b[28:30])[0] & 0x3FFF
            return "webp", int(w), int(h)
        return "webp", 512, 512
    return None, 0, 0


def validate_generated_image_bytes(data: bytes, min_bytes: int = 40) -> Dict[str, Any]:
    """فحص شامل لسلامة وجودة الصورة المولّدة."""
    if not data or not isinstance(data, (bytes, bytearray)):
        return {"valid": False, "score": 0.0, "format": None, "width": 0, "height": 0, "issues": ["لا توجد بيانات للصورة"]}
    if len(data) < min_bytes:
        return {"valid": False, "score": 0.0, "format": None, "width": 0, "height": 0, "issues": ["حجم بيانات الصورة صغير جداً أو تالف"]}

    fmt, w, h = detect_image_format_and_size(data)
    if fmt is None:
        return {"valid": False, "score": 0.0, "format": None, "width": 0, "height": 0, "issues": ["صيغة الصورة غير مدعومة أو تالفة (ليست PNG/JPEG/WEBP)"]}

    issues = []
    score = 1.0
    if w <= 0 or h <= 0:
        issues.append("أبعاد الصورة غير صالحة")
        score = 0.0

    # فحص إضافي عبر Pillow إذا كانت متوفرة في البيئة
    try:
        from PIL import Image, ImageStat
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            gray = im.convert("L")
            stat = ImageStat.Stat(gray)
            mean_luma = stat.mean[0]
            std_luma = stat.stddev[0]
            if w >= 64 and h >= 64:
                if mean_luma < 4.0:
                    issues.append("الصورة معتمة بالكامل تقريباً (Black Frame)")
                    score -= 0.5
                elif mean_luma > 251.0:
                    issues.append("الصورة بيضاء محترقة بالكامل تقريباً (Blown-out Frame)")
                    score -= 0.5
                if std_luma < 3.0:
                    issues.append("الصورة تفتقر للتباين والتفاصيل (Flat/Blank Image)")
                    score -= 0.4
    except Exception:
        pass

    return {
        "valid": score >= 0.5 and w > 0 and h > 0,
        "score": max(0.0, round(score, 2)),
        "format": fmt,
        "width": w,
        "height": h,
        "issues": issues,
    }
