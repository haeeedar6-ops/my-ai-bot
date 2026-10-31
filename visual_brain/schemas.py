# -*- coding: utf-8 -*-
"""هياكل البيانات الموحدة للعقل البصري (Visual Brain Schemas) مع تحويل JSON منظم."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
import json


@dataclass
class SceneSpec:
    """توصيف المشهد المستخرج من طلب المستخدم."""
    subject: str = ""
    persons: List[str] = field(default_factory=list)
    background: str = ""
    lighting: str = ""
    colors: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    camera_angle: str = "eye-level"
    lens_mm: str = "35mm"
    style: str = "photorealistic"
    mood: str = "natural"
    text_overlay: str = ""
    has_humans: bool = False
    has_clothing: bool = False
    has_liquids_or_glass: bool = False
    has_indoor_furniture: bool = False
    has_outdoor_nature: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdentityLock:
    """قفل الحفاظ على الهوية والملامح والعناصر غير المطلوب تغييرها."""
    enabled: bool = False
    preserve_face_structure: bool = True
    preserve_eye_color: bool = True
    preserve_hair_style: bool = True
    preserve_clothing: bool = True
    preserve_body_pose: bool = True
    preserve_camera_angle: bool = True
    preserve_background: bool = False
    locked_elements: List[str] = field(default_factory=list)
    modifiable_elements: List[str] = field(default_factory=list)
    identity_descriptor: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EditPlan:
    """خطة التعديل الذكية وتحديد نوع العملية من بين العمليات العشر."""
    is_edit: bool = False
    operation_type: str = "generate"  # generate, background_only, clothing_only, eye_color, lighting_adjustment, add_remove_object, enhance_upscale, outpainting, camera_reframe, multi_image_merge, localized_inpaint
    operations_detected: List[str] = field(default_factory=list)
    target_region: str = "full_frame"
    requires_mask: bool = False
    requires_outpaint_canvas: bool = False
    requires_multi_image: bool = False
    edit_instructions_en: str = ""
    preserved_summary_en: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    """تقرير فحص جودة الصورة بعد التوليد."""
    passed: bool = True
    score: float = 1.0
    width: int = 0
    height: int = 0
    format: str = "png"
    issues: List[str] = field(default_factory=list)
    retry_recommended: bool = False
    refined_prompt_addition: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImageVersion:
    """سجل إصدار صورة في الذاكرة البصرية الدائمة لكل مستخدم."""
    version_id: str
    user_id: str
    parent_version_id: Optional[str]
    created_at: float
    operation_type: str
    user_prompt: str
    final_prompt_en: str
    provider_used: str
    model_used: str
    image_data_uri: str
    plan_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_image: bool = False) -> Dict[str, Any]:
        d = asdict(self)
        if not include_image:
            d.pop("image_data_uri", None)
        return d


@dataclass
class VisualPlan:
    """خطة التنفيذ الكاملة المنظمة بصيغة JSON التي ينتجها العقل البصري قبل إرسال أي شيء للمحرك."""
    raw_request: str
    language: str
    intent: str  # generate | edit | remove | add | relight | revert
    scene: SceneSpec = field(default_factory=SceneSpec)
    identity_lock: IdentityLock = field(default_factory=IdentityLock)
    edit_plan: EditPlan = field(default_factory=EditPlan)
    conflicts_detected: List[str] = field(default_factory=list)
    conflict_resolutions: List[str] = field(default_factory=list)
    recommended_provider: str = "flux"
    fallback_providers: List[str] = field(default_factory=list)
    prompt_modules: Dict[str, str] = field(default_factory=dict)
    final_prompt_en: str = ""
    negative_constraints_en: str = ""
    aspect_ratio_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
