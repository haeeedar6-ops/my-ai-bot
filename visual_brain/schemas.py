# -*- coding: utf-8 -*-
"""
هياكل البيانات وعقود JSON الموحدة لـ RYN Visual Brain (Section 4 & Section 11 Data Contracts).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
import json
import uuid


@dataclass
class ProviderCapability:
    """توصيف القدرات الفعلية الموثّقة لكل مزود صور مهيأ في المستودع (Section 7)."""
    provider_id: str
    display_name: str
    configured: bool
    text_to_image: bool = False
    image_to_image: bool = False
    inpainting_mask: bool = False
    remove_object: bool = False
    enhance_restore: bool = False
    style_transfer: bool = False
    max_reference_images: int = 0
    accepted_formats: List[str] = field(default_factory=lambda: ["image/png", "image/jpeg", "image/webp"])
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SceneSpec:
    """تمثيل المشهد المنظم المستخرج من طلب المستخدم (Section 4 & Section 5)."""
    user_goal: str = ""
    subjects: List[str] = field(default_factory=list)
    actions_and_relations: List[str] = field(default_factory=list)
    persons: List[str] = field(default_factory=list)
    animals: List[str] = field(default_factory=list)
    objects: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    composition: Dict[str, str] = field(default_factory=dict)
    camera: Dict[str, str] = field(default_factory=dict)
    lighting: str = ""
    colors: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    camera_angle: str = "eye-level"
    lens_mm: str = "35mm"
    style: str = "photorealistic"
    mood: str = "natural"
    text_overlay: str = ""
    has_humans: bool = False
    has_visible_hands: bool = False
    has_animals: bool = False
    has_clothing: bool = False
    has_liquids_or_glass: bool = False
    has_dirt_sweat_or_wear: bool = False
    has_indoor_furniture: bool = False
    has_outdoor_nature: bool = False
    is_clean_studio_or_portrait: bool = False

    @property
    def subject(self) -> str:
        return self.user_goal

    @property
    def background(self) -> str:
        return self.environment.get("background", "")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdentityLock:
    """قفل الحفاظ على الهوية والعناصر المحمية أثناء التعديل (Section 6)."""
    enabled: bool = False
    preserve_face_structure: bool = True
    preserve_eye_color: bool = True
    preserve_hair_style: bool = True
    preserve_clothing: bool = True
    preserve_body_pose: bool = True
    preserve_camera_angle: bool = True
    preserve_background: bool = False
    preserve_text_and_logos: bool = True
    locked_elements: List[str] = field(default_factory=list)
    modifiable_elements: List[str] = field(default_factory=list)
    identity_descriptor: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EditPlan:
    """خطة التعديل المهيكلة (Section 4, Section 6 & Section 11)."""
    is_edit: bool = False
    operation_type: str = "text_to_image"
    workflow_type: str = "text_to_image"  # text_to_image | image_edit | inpaint | remove_object | restore | upscale | color_correct | style_transfer
    operations_detected: List[str] = field(default_factory=list)
    target_element: str = ""
    target_changes: List[str] = field(default_factory=list)
    protected_elements: List[str] = field(default_factory=list)
    expected_side_effects: List[str] = field(default_factory=list)
    allowed_edit_area: str = "full_frame"
    target_region: str = "full_frame"
    requires_reference_image: bool = False
    requires_mask: bool = False
    requires_outpaint_canvas: bool = False
    requires_multi_image: bool = False
    can_run_local_enhancement: bool = False
    edit_instructions_en: str = ""
    preserved_summary_en: str = ""

    @property
    def mask_required(self) -> bool:
        return self.requires_mask

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    """تقرير فحص جودة الصورة ومطابقة الطلب وحفظ المناطق المحمية (Section 8)."""
    passed: bool = True
    score: float = 1.0
    width: int = 0
    height: int = 0
    format: str = "png"
    checks_run: Dict[str, bool] = field(default_factory=dict)
    defects: List[Dict[str, str]] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    preservation_score: float = 1.0
    retry_recommended: bool = False
    refined_prompt_addition: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImageVersion:
    """إصدار صورة محفوظ في الذاكرة البصرية الدائمة لكل مستخدم (Section 6 & Section 9)."""
    version_id: str
    user_id: str
    parent_version_id: Optional[str]
    original_source_id: Optional[str]
    is_original_upload: bool
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
    """عقد البيانات الكامل بين مراحل RYN Visual Brain (Section 4 & Section 11)."""
    raw_request: str
    language: str
    intent: str  # generate | edit | remove | add | relight | enhance | style_transfer | revert
    task_type: str = "text_to_image"  # text_to_image | image_edit | inpaint | remove_object | restore | upscale | color_correct | style_transfer
    job_id: str = field(default_factory=lambda: "vb_" + uuid.uuid4().hex[:10])
    user_goal: str = ""
    reference_image_id: Optional[str] = None
    scene: SceneSpec = field(default_factory=SceneSpec)
    identity_lock: IdentityLock = field(default_factory=IdentityLock)
    edit_plan: EditPlan = field(default_factory=EditPlan)
    must_keep: List[str] = field(default_factory=list)
    must_change: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    safe_assumptions: List[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    conflicts_detected: List[str] = field(default_factory=list)
    conflict_resolutions: List[str] = field(default_factory=list)
    provider_requirements: Dict[str, Any] = field(default_factory=dict)
    quality_checks: List[str] = field(default_factory=lambda: [
        "instruction_match",
        "anatomy_if_visible",
        "material_realism",
        "lighting_consistency",
        "edit_preservation",
    ])
    retry_policy: Dict[str, Any] = field(default_factory=lambda: {
        "max_attempts": 2,
        "targeted_refinement_only": True,
    })
    recommended_provider: str = "flux"
    fallback_providers: List[str] = field(default_factory=list)
    prompt_modules: Dict[str, str] = field(default_factory=dict)
    final_prompt_en: str = ""
    negative_constraints_en: str = ""
    aspect_ratio_hint: str = ""

    def to_contract_dict(self) -> Dict[str, Any]:
        """إرجاع العقد القياسي المطابق تماماً للقسم 11 من المواصفات."""
        return {
            "job_id": self.job_id,
            "task_type": self.task_type,
            "user_request": self.raw_request,
            "user_goal": self.user_goal or self.raw_request,
            "reference_image_id": self.reference_image_id,
            "scene_plan": {
                "subjects": self.scene.subjects,
                "actions": self.scene.actions_and_relations,
                "environment": self.scene.environment,
                "composition": self.scene.composition,
                "camera": self.scene.camera,
                "lighting": {"description": self.scene.lighting, "mood": self.scene.mood},
                "materials": self.scene.materials,
                "style": self.scene.style,
            },
            "edit_plan": {
                "workflow_type": self.edit_plan.workflow_type,
                "operation_type": self.edit_plan.operation_type,
                "target_element": self.edit_plan.target_element,
                "target_changes": self.must_change or self.edit_plan.target_changes,
                "protected_elements": self.must_keep or self.edit_plan.protected_elements,
                "expected_side_effects": self.edit_plan.expected_side_effects,
                "allowed_edit_area": self.edit_plan.allowed_edit_area,
                "mask_required": self.edit_plan.requires_mask,
            },
            "provider_requirements": self.provider_requirements,
            "quality_checks": self.quality_checks,
            "retry_policy": self.retry_policy,
            "uncertainties": self.uncertainties,
            "safe_assumptions": self.safe_assumptions,
            "clarification_question": self.clarification_question,
            "conflicts_detected": self.conflicts_detected,
            "conflict_resolutions": self.conflict_resolutions,
            "recommended_provider": self.recommended_provider,
            "fallback_providers": self.fallback_providers,
            "final_prompt_en": self.final_prompt_en,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["contract"] = self.to_contract_dict()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_contract_dict(), ensure_ascii=False, indent=2)
