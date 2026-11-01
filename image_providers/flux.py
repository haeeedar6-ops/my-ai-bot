# -*- coding: utf-8 -*-
"""مزود FLUX عبر Cloudflare Workers AI لتوليد الصور من النص (Text-to-Image)."""
from __future__ import annotations
from typing import Optional, List, Any, Tuple
import base64
from .base import BaseImageProvider, ProviderUnavailableError


class FluxCloudflareProvider(BaseImageProvider):
    name = "flux"
    supports_text_to_image = True
    supports_reference_image = False
    supports_inpainting = False
    supports_outpainting = False

    def __init__(self, bot_module: Any, cf_ready_fn, flux_image_fn):
        self.b = bot_module
        self.cf_ready_fn = cf_ready_fn
        self.flux_image_fn = flux_image_fn

    def is_ready(self) -> bool:
        return bool(self.cf_ready_fn())

    def generate(self, prompt_en: str, ref_parts: Optional[List[Any]] = None) -> Tuple[bytes, str, str]:
        if ref_parts:
            raise ProviderUnavailableError("Cloudflare FLUX endpoint is configured for Text-to-Image only and does not accept reference images.")
        if not self.is_ready():
            raise ProviderUnavailableError("Cloudflare FLUX is not configured")
        res = self.flux_image_fn(self.b, prompt_en, prompt_en)
        img_uri = res.get("image", "")
        if img_uri.startswith("data:image/"):
            header, b64 = img_uri.split(",", 1)
            mime = header.split(";")[0].replace("data:", "") or "image/png"
            return base64.b64decode(b64), mime, res.get("model", "FLUX.1-schnell")
        raise RuntimeError("FLUX did not return valid image bytes")
