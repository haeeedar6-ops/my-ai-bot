# -*- coding: utf-8 -*-
"""
مخطط التعديل ومسارات العمل الستة (Edit & Workflow Planner — Section 3 & Section 6)
مع توليد أمر التعديل المباشر المخصص لنموذج `timbrooks/instruct-pix2pix` وضبط `image_guidance_scale`.
"""
from __future__ import annotations
import re
from .schemas import EditPlan, IdentityLock

_OP_PATTERNS = [
    ("restore_old_image", "restore", re.compile(r"(?i)(رمّ?م|ترميم|صورة\s+قديمة|شيل\s+الخدوش|أصلح\s+التلف|\b(?:restore|restoration|old\s+photo|old\s+image|fix\s+scratches|repair\s+damage)\b)")),
    ("color_correct", "color_correct", re.compile(r"(?i)(صحّ?ح\s+الألوان|تصحيح\s+الألوان|توازن\s+الألوان|\b(?:color\s+correct|color\s+balance|white\s+balance)\b)")),
    ("enhance_upscale", "upscale", re.compile(r"(?i)(حسّ?ن\s+جودة|تحسين\s+الجودة|وضّ?ح\s+الصورة|ارفع\s+الدقة|كبّ?ر\s+الدقة|صفّ?ي\s+الصورة|\b(?:upscale|enhance\s+quality|sharpen|denoise|improve\s+clarity)\b)")),
    ("style_transfer", "style_transfer", re.compile(r"(?i)(حوّ?ل\s+(?:هالصورة|الصورة)\s+لـ?\s*(?:أنمي|انمي|كرتون|لوحة|سكتش|ثلاثي\s+الأبعاد|بكسل)|غيّ?ر\s+(?:أسلوب|ستايل)\s+الصورة|\b(?:convert\s+(?:this\s+)?(?:image|photo)\s+to|style\s+transfer|change\s+style\s+to)\b)")),
    ("outpainting", "image_edit", re.compile(r"(?i)(وسّ?ع\s+الصورة|توسيع\s+الصورة|كمّ?ل\s+حواف|خارج\s+حدودها|أعرض|\b(?:outpaint|outpainting|expand\s+image|uncrop|extend\s+borders)\b)")),
    ("multi_image_merge", "image_edit", re.compile(r"(?i)(ادمج\s+الصورتين|دمج\s+صور|ركّ?ب\s+الصورتين|اجمع\s+الصورتين|\b(?:merge\s+images|blend\s+photos|combine\s+two\s+images|composite)\b)")),
    ("shirt_color_only", "inpaint", re.compile(r"(?i)(لون\s+(?:القميص|الكنزة|الجاكيت)\s+فقط|\b(?:change\s+shirt\s+color\s+only|shirt\s+color\s+only)\b)")),
    ("background_only", "inpaint", re.compile(r"(?i)(غيّ?ر\s+الخلفية|بدّ?ل\s+الخلفية|خلفية\s+بس|الخلفية\s+فقط|خلي\s+الخلفية|خلّ?ي\s+وراه|\b(?:change\s+background|replace\s+background|background\s+only)\b)")),
    ("clothing_only", "inpaint", re.compile(r"(?i)(غيّ?ر\s+(?:اللبس|الملابس|القميص|الجاكيت|البدلة)|لبّ?س[وه]?\s+|\b(?:change\s+clothes|change\s+outfit|change\s+shirt|wear\s+a)\b)")),
    ("eye_color", "inpaint", re.compile(r"(?i)(غيّ?ر\s+لون\s+العيون|خلّ?ي\s+عيون[هو]?\s+|\b(?:change\s+eye\s+color|eyes\s+color\s+to)\b)")),
    ("lighting_adjustment", "image_edit", re.compile(r"(?i)(عدّ?ل\s+الإضاءة|غيّ?ر\s+الإضاءة|خلّ?ي\s+الإضاءة|إضاءة\s+بس|إضاءة\s+فقط|خلّ?ي\s+الجو\s+(?:ليل|غروب|صباح)|\b(?:edit\s+lighting\s+only|change\s+lighting|relight|adjust\s+lighting|make\s+it\s+night|golden\s+hour\s+lighting)\b)")),
    ("remove_object", "remove_object", re.compile(r"(?i)(احذف|شيل|أزل|ازل|امسح|\b(?:remove|delete|erase)\b)")),
    ("add_remove_object", "inpaint", re.compile(r"(?i)(ضيف|أضف|اضف|حطّ?ل?[وهي]?|\b(?:add|insert|place)\b)")),
    ("camera_reframe", "image_edit", re.compile(r"(?i)(غيّ?ر\s+زاوية\s+التصوير|من\s+زاوية\s+تانية|قرّ?ب\s+اللقطة|بعّ?د\s+اللقطة|\b(?:change\s+camera\s+angle|reframe|zoom\s+out|close-up\s+crop)\b)")),
]


def build_instruct_pix2pix_directive(user_text: str, compat_op: str, protected_list: list) -> tuple[str, float, float]:
    """
    يبني تعليمة تحرير صريحة ومختصرة مصممة خصيصاً لنموذج `timbrooks/instruct-pix2pix`
    مع ضبط `image_guidance_scale` للحفاظ على بنية الصورة الأصلية.
    """
    t = (user_text or "").strip()
    # كلما زاد image_guidance_scale (مثلاً 1.6 - 1.85) حافظ instruct-pix2pix أكثر على تفاصيل وبنية الصورة الأصلية
    scale_map = {
        "lighting_adjustment": (1.75, 7.5),
        "clothing_only": (1.65, 7.5),
        "eye_color": (1.85, 7.0),
        "background_only": (1.55, 7.5),
        "add_remove_object": (1.65, 7.5),
        "restore_old_image": (1.80, 6.5),
        "color_correct": (1.85, 6.5),
        "style_transfer": (1.35, 8.0),
    }
    img_g, txt_g = scale_map.get(compat_op, (1.60, 7.5))
    keep_short = ", ".join(protected_list[:3]) if protected_list else "the subject's exact face, identity, and unrequested regions"
    directive = (
        f"Edit the image according to this instruction: {t}. "
        f"Strictly preserve {keep_short}, maintaining realistic lighting, natural skin texture, and optical coherence."
    )
    return directive[:500], img_g, txt_g


def plan_edit_operation(
    user_text: str,
    is_edit_like: bool,
    identity_lock: IdentityLock,
    ref_count: int = 0,
    task_type_override: str = "",
) -> EditPlan:
    t = (user_text or "").strip()
    if not is_edit_like and ref_count == 0:
        return EditPlan(
            is_edit=False,
            operation_type="text_to_image",
            workflow_type="text_to_image",
            requires_reference_image=False,
        )

    detected_ops = []
    detected_workflows = []
    for op_name, wf_name, pat in _OP_PATTERNS:
        if pat.search(t):
            detected_ops.append(op_name)
            detected_workflows.append(wf_name)

    if ref_count > 1 and "multi_image_merge" not in detected_ops:
        detected_ops.insert(0, "multi_image_merge")
        detected_workflows.insert(0, "image_edit")

    if not detected_ops:
        detected_ops.append("localized_inpaint")
        detected_workflows.append(task_type_override or "image_edit")

    primary_op = detected_ops[0]
    workflow_type = task_type_override if (task_type_override and task_type_override != "image_edit") else detected_workflows[0]

    compat_op = "add_remove_object" if primary_op == "remove_object" else (
        "clothing_only" if primary_op == "shirt_color_only" else primary_op
    )

    if len(detected_ops) > 1 and compat_op in ("background_only", "clothing_only", "eye_color"):
        compat_op = "localized_inpaint"

    area_map = {
        "background_only": "background region only (foreground subject 100% protected)",
        "clothing_only": "garment/shirt region only (preserve fabric texture, folds, seams, face, body, and background)",
        "eye_color": "iris region of the eyes only (preserve pupil, catchlight, face, and skin)",
        "lighting_adjustment": "scene illumination, color temperature, and shadow behavior only (do NOT redesign face, body, clothing, or background)",
        "add_remove_object": "target object bounding box only; reconstruct background consistently with nearby texture, perspective, depth, and light",
        "restore_old_image": "surface damage, scratches, and noise only; reduce damage without inventing facial features or changing identity",
        "enhance_upscale": "full frame micro-detail sharpening and denoising without regenerating or altering scene geometry",
        "color_correct": "global color balance and contrast without changing objects or identity",
        "style_transfer": "visual rendering style across the frame while strictly preserving subject identity, pose, composition, and objects",
        "outpainting": "outer frame borders expansion matching perspective, lighting, and texture continuity",
        "camera_reframe": "camera viewpoint and composition reframing while preserving subject identity",
        "multi_image_merge": "seamless composite blending subjects and lighting across reference images",
        "localized_inpaint": "targeted regions requested by the user while strictly protecting the rest of the image",
    }

    side_effects_map = {
        "background_only": ["update edge spill lighting on subject borders to match new background"],
        "clothing_only": ["maintain existing shadow falloff across garment folds"],
        "lighting_adjustment": ["update shadow softness, reflections, and ambient color bounce to match new light"],
        "add_remove_object": ["remove object's cast shadow and reconstruct occluded background surface"],
        "restore_old_image": ["remove scratches/grain while keeping original facial proportions intact"],
    }

    requires_mask = compat_op in ("background_only", "clothing_only", "eye_color", "add_remove_object", "localized_inpaint") or workflow_type in ("inpaint", "remove_object")
    requires_outpaint = compat_op == "outpainting"
    requires_multi = compat_op == "multi_image_merge" or ref_count > 1
    can_local = workflow_type in ("upscale", "color_correct", "restore") or compat_op in ("enhance_upscale", "color_correct", "restore_old_image")

    protected_list = list(identity_lock.locked_elements) if (identity_lock and identity_lock.enabled) else ["all unrequested regions"]
    target_changes = list(identity_lock.modifiable_elements) if (identity_lock and identity_lock.modifiable_elements) else [primary_op]
    allowed_area = area_map.get(compat_op, area_map.get(primary_op, "targeted region"))
    side_effects = side_effects_map.get(compat_op, ["preserve coherent lighting and contact shadows"])

    pix2pix_inst, img_g_scale, txt_g_scale = build_instruct_pix2pix_directive(t, compat_op, protected_list)

    preserved_en = "; ".join(protected_list)
    edit_inst_en = (
        f"WORKFLOW: {workflow_type} | OPERATION: {primary_op}. "
        f"ALLOWED EDIT AREA: {allowed_area}. "
        f"PROTECTED ELEMENTS (DO NOT ALTER): {preserved_en}."
    )

    return EditPlan(
        is_edit=True,
        operation_type=compat_op,
        workflow_type=workflow_type,
        operations_detected=detected_ops,
        target_element=primary_op,
        target_changes=target_changes,
        protected_elements=protected_list,
        expected_side_effects=side_effects,
        allowed_edit_area=allowed_area,
        target_region=allowed_area,
        requires_reference_image=True,
        requires_mask=requires_mask,
        requires_outpaint_canvas=requires_outpaint,
        requires_multi_image=requires_multi,
        can_run_local_enhancement=can_local,
        edit_instructions_en=edit_inst_en,
        instruct_pix2pix_instruction_en=pix2pix_inst,
        image_guidance_scale=img_g_scale,
        guidance_scale=txt_g_scale,
        preserved_summary_en=preserved_en,
    )
