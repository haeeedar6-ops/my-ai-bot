# -*- coding: utf-8 -*-
"""الواجهة الأساسية الموحدة لجميع مزودات الصور في RYN."""
from __future__ import annotations
from typing import Optional, List, Any, Tuple


class ProviderUnavailableError(RuntimeError):
    """خطأ يُرفع عندما يكون المزود غير مفعّل أو غير متاح."""


class BaseImageProvider:
    name: str = "base"
    supports_reference_image: bool = False
    supports_inpainting: bool = False
    supports_outpainting: bool = False

    def is_ready(self) -> bool:
        return False

    def generate(self, prompt_en: str, ref_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
        """يرجع (image_bytes, mime_type, model_name)."""
        raise NotImplementedError
