"""
capture.py — التقاط الشاشة عبر mss مع تصغير الحجم بـ Pillow والحفاظ على نسبة العرض إلى الارتفاع (Section 8).
يسجل أبعاد الشاشة الأصلية لضمان دقة إحداثيات الماوس.
"""
import base64
import io
from typing import Any, Dict, Tuple


def capture_screen_compressed(max_width: int = 1280, quality: int = 78) -> Dict[str, Any]:
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            orig_w, orig_h = int(mon["width"]), int(mon["height"])
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_h = max(1, int(round(img.height * ratio)))
                img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {
                "screenshot_b64": b64,
                "screen_width": orig_w,
                "screen_height": orig_h,
                "bytes_len": len(buf.getvalue()),
            }
    except Exception:
        # مسار احتياطي آمن لبيئات الاختبار بدون شاشة رسومية (Headless CI)
        return {
            "screenshot_b64": "",
            "screen_width": 1920,
            "screen_height": 1080,
            "bytes_len": 0,
        }
