# -*- coding: utf-8 -*-
"""مزود Gemini Image المهيأ في المشروع لتوليد الصور وتعديل الصور المرجعية (I2I / Inpaint / Object Removal / Style Transfer)."""
from __future__ import annotations
from typing import Optional, List, Any, Tuple
from .base import BaseImageProvider, ProviderUnavailableError


class GeminiImageProvider(BaseImageProvider):
    name = "gemini"
    supports_text_to_image = True
    supports_reference_image = True
    supports_inpainting = True
    supports_outpainting = True

    def __init__(self, bot_module: Any, gemini_image_fn):
        self.b = bot_module
        self.gemini_image_fn = gemini_image_fn

    def is_ready(self) -> bool:
        return bool(getattr(self.b, "client", None))

    def generate(self, prompt_en: str, ref_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
        if not self.is_ready():
            raise ProviderUnavailableError("Gemini client is not configured (GEMINI_API_KEY missing)")
        data, mime, model = self.gemini_image_fn(self.b, prompt_en, ref_parts=ref_parts)
        return data, (mime or "image/png"), model
