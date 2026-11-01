# -*- coding: utf-8 -*-
"""مزود Pollinations الاحتياطي لتوليد الصور من النص (Text-to-Image)."""
from __future__ import annotations
from typing import Optional, List, Any, Tuple
import base64
from .base import BaseImageProvider, ProviderUnavailableError


class PollinationsProvider(BaseImageProvider):
    name = "pollinations"
    supports_text_to_image = True
    supports_reference_image = False

    def __init__(self, bot_module: Any, fallback_on_fn, pollinations_fn):
        self.b = bot_module
        self.fallback_on_fn = fallback_on_fn
        self.pollinations_fn = pollinations_fn

    def is_ready(self) -> bool:
        return bool(self.fallback_on_fn())

    def generate(self, prompt_en: str, ref_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
        if ref_parts:
            raise ProviderUnavailableError("Pollinations endpoint is Text-to-Image only and does not support reference-image editing.")
        if not self.is_ready():
            raise ProviderUnavailableError("Pollinations fallback is disabled")
        res = self.pollinations_fn(self.b, prompt_en, prompt_en)
        img_uri = res.get("image", "")
        if img_uri.startswith("data:image/"):
            header, b64 = img_uri.split(",", 1)
            mime = header.split(";")[0].replace("data:", "") or "image/jpeg"
            return base64.b64decode(b64), mime, res.get("model", "Pollinations-FLUX")
        raise RuntimeError("Pollinations did not return valid image bytes")
