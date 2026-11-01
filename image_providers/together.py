# -*- coding: utf-8 -*-
"""مزود Together AI (FLUX.1-schnell) لتوليد الصور من النص (Text-to-Image)."""
from __future__ import annotations
import os
import base64
from typing import Optional, List, Any, Tuple
from .base import BaseImageProvider, ProviderUnavailableError


class TogetherFluxProvider(BaseImageProvider):
    name = "together"
    supports_text_to_image = True
    supports_reference_image = False

    def __init__(self, bot_module: Any, together_fn):
        self.b = bot_module
        self.together_fn = together_fn

    def is_ready(self) -> bool:
        return bool(os.environ.get("TOGETHER_API_KEY", "").strip())

    def generate(self, prompt_en: str, ref_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
        if ref_parts:
            raise ProviderUnavailableError("Together FLUX endpoint is configured for Text-to-Image only.")
        if not self.is_ready():
            raise ProviderUnavailableError("TOGETHER_API_KEY is not set")
        res = self.together_fn(self.b, prompt_en, prompt_en)
        img_uri = res.get("image", "")
        if img_uri.startswith("data:image/"):
            header, b64 = img_uri.split(",", 1)
            mime = header.split(";")[0].replace("data:", "") or "image/png"
            return base64.b64decode(b64), mime, res.get("model", "Together-FLUX")
        raise RuntimeError("Together did not return valid image bytes")
