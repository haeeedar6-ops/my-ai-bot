# -*- coding: utf-8 -*-
"""الواجهة الأساسية الموحدة لجميع مزودات الصور المهيأة في RYN."""
from __future__ import annotations
from typing import Optional, List, Any, Tuple


class ProviderUnavailableError(RuntimeError):
    """يرفع عندما يكون المزود غير مهيأ أو غير متاح."""


class BaseImageProvider:
    name: str = "base"
    supports_text_to_image: bool = True
    supports_reference_image: bool = False
    supports_inpainting: bool = False
    supports_outpainting: bool = False

    def is_ready(self) -> bool:
        return False

    def generate(self, prompt_en: str, ref_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
        raise NotImplementedError
