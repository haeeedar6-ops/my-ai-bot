# -*- coding: utf-8 -*-
"""
مزود نموذج `timbrooks/instruct-pix2pix` لتعديل الصور المرجعية بالتعليمات النصية (Instruction-Based Image Editing):
- يدعم التشغيل عبر:
  1. Hugging Face Inference API (`HF_TOKEN` أو `HUGGINGFACE_API_KEY`) للنموذج `timbrooks/instruct-pix2pix`.
  2. Replicate API (`REPLICATE_API_TOKEN`) للنموذج `timbrooks/instruct-pix2pix`.
  3. نقطة نهاية مخصصة (`INSTRUCT_PIX2PIX_URL`) إن وجدت.
- يحافظ على بنية الصورة الأصلية وملامح الوجه عبر معامل `image_guidance_scale` (افتراضياً 1.6).
"""
from __future__ import annotations
import os
import time
import base64
import requests
from typing import Optional, List, Any, Tuple
from .base import BaseImageProvider, ProviderUnavailableError


def _extract_first_image_bytes(ref_parts: Optional[List[Any]]) -> Tuple[Optional[bytes], str]:
    if not ref_parts:
        return None, "image/png"
    for p in ref_parts:
        if isinstance(p, (bytes, bytearray)) and len(p) > 16:
            return bytes(p), "image/png"
        inl = getattr(p, "inline_data", None)
        if inl is not None:
            data = getattr(inl, "data", None)
            mime = getattr(inl, "mime_type", None) or "image/png"
            if isinstance(data, (bytes, bytearray)) and len(data) > 16:
                return bytes(data), mime
            if isinstance(data, str) and data:
                try:
                    return base64.b64decode(data), mime
                except Exception:
                    pass
    return None, "image/png"


class InstructPix2PixProvider(BaseImageProvider):
    name = "instruct_pix2pix"
    model_id = "timbrooks/instruct-pix2pix"
    supports_text_to_image = False
    supports_reference_image = True
    supports_inpainting = True
    supports_outpainting = False

    def __init__(self, bot_module: Any = None, custom_runner_fn=None):
        self.b = bot_module
        self.custom_runner_fn = custom_runner_fn

    @staticmethod
    def _hf_token() -> str:
        return (
            os.environ.get("HF_TOKEN", "").strip()
            or os.environ.get("HUGGINGFACE_API_KEY", "").strip()
            or os.environ.get("HUGGINGFACEHUB_API_TOKEN", "").strip()
        )

    @staticmethod
    def _replicate_token() -> str:
        return os.environ.get("REPLICATE_API_TOKEN", "").strip()

    @staticmethod
    def _custom_url() -> str:
        return os.environ.get("INSTRUCT_PIX2PIX_URL", "").strip()

    def is_ready(self) -> bool:
        return bool(self._hf_token() or self._replicate_token() or self._custom_url() or self.custom_runner_fn)

    def _run_hf_inference(
        self,
        prompt_en: str,
        img_bytes: bytes,
        image_guidance_scale: float = 1.6,
        guidance_scale: float = 7.5,
    ) -> Tuple[bytes, str, str]:
        token = self._hf_token()
        if not token:
            raise ProviderUnavailableError("HF_TOKEN is not configured for timbrooks/instruct-pix2pix")

        b64_img = base64.b64encode(img_bytes).decode("ascii")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "image/png",
        }
        payload = {
            "inputs": b64_img,
            "parameters": {
                "prompt": prompt_en[:500],
                "image_guidance_scale": float(image_guidance_scale),
                "guidance_scale": float(guidance_scale),
                "num_inference_steps": 28,
            },
        }
        urls = [
            f"https://router.huggingface.co/hf-inference/models/{self.model_id}",
            f"https://api-inference.huggingface.co/models/{self.model_id}",
        ]
        last_err = None
        for url in urls:
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=75)
                if r.status_code == 200 and r.content and len(r.content) > 64:
                    ctype = (r.headers.get("Content-Type") or "image/png").split(";")[0].strip()
                    if ctype.startswith("image/"):
                        return r.content, ctype, self.model_id
                    # قد يرجع JSON يحتوي على base64
                    try:
                        j = r.json()
                        if isinstance(j, dict) and j.get("image"):
                            return base64.b64decode(j["image"]), "image/png", self.model_id
                    except Exception:
                        pass
                last_err = RuntimeError(f"HF instruct-pix2pix HTTP {r.status_code}: {r.text[:180]}")
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("فشل الاتصال بـ HuggingFace لتشغيل timbrooks/instruct-pix2pix")

    def _run_replicate(
        self,
        prompt_en: str,
        img_bytes: bytes,
        mime_type: str = "image/png",
        image_guidance_scale: float = 1.6,
        guidance_scale: float = 7.5,
    ) -> Tuple[bytes, str, str]:
        token = self._replicate_token()
        if not token:
            raise ProviderUnavailableError("REPLICATE_API_TOKEN is not configured for timbrooks/instruct-pix2pix")

        data_uri = f"data:{mime_type};base64,{base64.b64encode(img_bytes).decode('ascii')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "wait=55",
        }
        payload = {
            "version": "30c1d0b916a6f8efce20493f5d61ee27491ab2a60437c13c588468b9810ec23f",
            "input": {
                "image": data_uri,
                "prompt": prompt_en[:500],
                "image_guidance_scale": float(image_guidance_scale),
                "guidance_scale": float(guidance_scale),
                "num_inference_steps": 30,
            },
        }
        r = requests.post("https://api.replicate.com/v1/predictions", headers=headers, json=payload, timeout=70)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Replicate instruct-pix2pix HTTP {r.status_code}: {r.text[:200]}")
        pred = r.json()
        status = pred.get("status")
        get_url = (pred.get("urls") or {}).get("get")
        t0 = time.time()
        while status in ("starting", "processing") and get_url and (time.time() - t0 < 75):
            time.sleep(2.0)
            pr = requests.get(get_url, headers={"Authorization": f"Bearer {token}"}, timeout=25)
            if pr.status_code == 200:
                pred = pr.json()
                status = pred.get("status")
        output = pred.get("output")
        out_url = output[0] if isinstance(output, list) and output else (output if isinstance(output, str) else None)
        if not out_url:
            raise RuntimeError("Replicate timbrooks/instruct-pix2pix لم يرجع رابط صورة.")
        img_r = requests.get(out_url, timeout=45)
        img_r.raise_for_status()
        ctype = (img_r.headers.get("Content-Type") or "image/png").split(";")[0].strip()
        return img_r.content, ctype, self.model_id

    def _run_custom_endpoint(
        self,
        prompt_en: str,
        img_bytes: bytes,
        image_guidance_scale: float = 1.6,
        guidance_scale: float = 7.5,
    ) -> Tuple[bytes, str, str]:
        url = self._custom_url()
        if not url:
            raise ProviderUnavailableError("INSTRUCT_PIX2PIX_URL is not configured")
        b64_img = base64.b64encode(img_bytes).decode("ascii")
        r = requests.post(
            url,
            json={
                "model": self.model_id,
                "prompt": prompt_en[:500],
                "image": b64_img,
                "image_guidance_scale": float(image_guidance_scale),
                "guidance_scale": float(guidance_scale),
            },
            timeout=75,
        )
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype.startswith("image/"):
            return r.content, ctype, self.model_id
        j = r.json()
        b64_out = j.get("image") or ""
        if b64_out.startswith("data:image/"):
            b64_out = b64_out.split(",", 1)[1]
        return base64.b64decode(b64_out), "image/png", self.model_id

    def generate(
        self,
        prompt_en: str,
        ref_parts: Optional[List[Any]] = None,
        image_guidance_scale: float = 1.6,
        guidance_scale: float = 7.5,
    ) -> Tuple[bytes, str, str]:
        img_bytes, mime = _extract_first_image_bytes(ref_parts)
        if not img_bytes:
            raise ProviderUnavailableError(
                "نموذج timbrooks/instruct-pix2pix مخصص لتعديل الصور ويتطلب إرفاق صورة مرجعية."
            )
        if self.custom_runner_fn is not None:
            return self.custom_runner_fn(prompt_en, ref_parts)

        errors = []
        if self._custom_url():
            try:
                return self._run_custom_endpoint(prompt_en, img_bytes, image_guidance_scale, guidance_scale)
            except Exception as e:
                errors.append(str(e))
        if self._replicate_token():
            try:
                return self._run_replicate(prompt_en, img_bytes, mime, image_guidance_scale, guidance_scale)
            except Exception as e:
                errors.append(str(e))
        if self._hf_token():
            try:
                return self._run_hf_inference(prompt_en, img_bytes, image_guidance_scale, guidance_scale)
            except Exception as e:
                errors.append(str(e))

        if not errors:
            raise ProviderUnavailableError(
                "نموذج timbrooks/instruct-pix2pix غير مفعّل (أضف HF_TOKEN أو REPLICATE_API_TOKEN أو INSTRUCT_PIX2PIX_URL)."
            )
        raise RuntimeError("فشل timbrooks/instruct-pix2pix: " + " | ".join(errors[:2]))
