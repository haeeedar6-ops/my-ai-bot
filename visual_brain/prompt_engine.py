# -*- coding: utf-8 -*-
"""
محرك كتابة التعليمات المعياري (Modular Prompt Engine):
يبني البرومبت من 7 وحدات مستقلة بدلاً من قالب واحد ثابت:
1. Scene Understanding (فهم المشهد)
2. Composition Engine (توزيع العناصر والزاوية)
3. Lighting Engine (الإضاءة والظلال)
4. Camera Engine (العدسة والمنظور)
5. Material Engine (البشرة، الشعر، الأقمشة، المعادن، الماء)
6. Style Engine (فوتوغرافي، سينمائي، أنمي، ثلاثي الأبعاد، شعار...)
7. Negative Constraints (منع الأخطاء غير المرغوبة)
"""
from __future__ import annotations
from typing import Dict, Optional, Callable, List
from .schemas import SceneSpec, IdentityLock, EditPlan
from .realism_engine import build_realism_modules_for_scene, strip_ai_buzzwords

STYLE_DIRECTIVES = {
    "photorealistic": "Authentic unedited RAW candid photograph, realistic physical optics and organic textures.",
    "cinematic": "Cinematic 35mm film still, anamorphic depth, controlled atmospheric lighting, rich natural color grading.",
    "anime": "High-quality anime illustration, clean expressive linework, cel-shaded lighting, vibrant cohesive color palette.",
    "cartoon": "Expressive stylized cartoon illustration, clean shapes, dynamic posing, appealing color harmony.",
    "3d_render": "High-craft 3D stylized render, physically based materials, soft global illumination, clean geometry.",
    "logo_vector": "Clean scalable flat vector logo design, balanced negative space, crisp geometric precision, minimal palette.",
    "pixel_art": "Crisp retro pixel art, intentional pixel grid, cohesive limited color palette, clean dithering.",
    "painting": "Authentic traditional painting texture, visible brushwork and canvas grain, harmonious artistic lighting.",
}

PROMPT_COMPOSER_SYS = (
    "You are the Prompt Engine of RYN Visual Brain. "
    "Convert the structured modular visual plan into ONE cohesive, vivid, physically accurate English prompt (110 to 200 words). "
    "Rules:\n"
    "1) Include EVERY detail from the user's request (subject, count, action, colors, setting, clothing, mood).\n"
    "2) Weave the provided modular directives (Composition, Lighting, Camera, Materials, Style, and Identity Lock if editing) naturally into the scene description.\n"
    "3) If CONFLICT RESOLUTIONS are provided, follow the resolved instruction.\n"
    "4) NEVER use forbidden digital hype buzzwords (8K, 4K, hyper-realistic, masterpiece, ultra detailed, CGI, 3D render, flawless, smooth skin, plastic).\n"
    "5) Output ONLY the final English prompt."
)


def build_prompt_modules(
    scene: SceneSpec,
    identity_lock: IdentityLock,
    edit_plan: EditPlan,
    conflicts_resolutions: Optional[List[str]] = None,
) -> Dict[str, str]:
    """تجميع الوحدات السبع المستقلة للبرومبت حسب محتوى الصورة."""
    modules: Dict[str, str] = {}

    # 1. Scene Understanding
    scene_parts = [f"Primary Subject: {scene.subject}"]
    if scene.persons:
        scene_parts.append(f"People/Characters: {', '.join(scene.persons)}")
    if scene.background:
        scene_parts.append(f"Setting/Background: {scene.background}")
    if scene.colors:
        scene_parts.append(f"Key Colors: {', '.join(scene.colors)}")
    if scene.text_overlay:
        scene_parts.append(f'Visible Short Text: "{scene.text_overlay}"')
    modules["scene_understanding"] = ". ".join(scene_parts) + "."

    # 2. Style Engine
    modules["style_engine"] = STYLE_DIRECTIVES.get(scene.style, STYLE_DIRECTIVES["photorealistic"])

    # 3. Composition Engine
    modules["composition_engine"] = (
        f"Framing & Composition: {scene.camera_angle} framing, natural asymmetrical lived-in balance, "
        "clean background behind the main subject's head, physically grounded placement."
    )

    # 4-7. Lighting, Camera, Material, Negative Constraints (تُفعّل حسب نوع الأسلوب والعناصر)
    realism_mods = build_realism_modules_for_scene(scene)
    if realism_mods:
        modules["lighting_engine"] = f"Scene Lighting ({scene.lighting}): " + realism_mods["lighting"]
        modules["camera_engine"] = realism_mods["camera"]
        if "materials" in realism_mods:
            modules["material_engine"] = realism_mods["materials"]
        if "environment" in realism_mods:
            modules["environment_physics"] = realism_mods["environment"]
        modules["negative_constraints"] = realism_mods["negative"]
    else:
        modules["lighting_engine"] = f"Lighting: {scene.lighting}, coherent shading matching the {scene.style} style."
        modules["camera_engine"] = f"Perspective: {scene.camera_angle} view."
        modules["negative_constraints"] = "No anatomical distortion, no blurry artifacts, no unintended text or watermarks."

    # قفل الهوية وخطة التعديل عند وجود تعديل
    if edit_plan and edit_plan.is_edit:
        modules["edit_operation"] = edit_plan.edit_instructions_en
    if identity_lock and identity_lock.enabled:
        from .identity_engine import format_identity_lock_directive
        modules["identity_lock"] = format_identity_lock_directive(identity_lock)
    if conflicts_resolutions:
        modules["conflict_resolution"] = " ".join(conflicts_resolutions)

    return modules


def synthesize_final_prompt(
    user_text: str,
    modules: Dict[str, str],
    ask_llm_fn: Optional[Callable[[str, str], str]] = None,
) -> str:
    """دمج الوحدات السبع في برومبت إنجليزي نهائي متماسك (عبر الموديل اللغوي أو الدمج الهيكلي الاحتياطي)."""
    structured_input = (
        f"USER ORIGINAL REQUEST:\n{user_text}\n\n"
        "MODULAR VISUAL BRAIN DIRECTIVES:\n"
        + "\n".join(f"[{k.upper()}]: {v}" for k, v in modules.items())
    )
    if ask_llm_fn is not None:
        try:
            out = ask_llm_fn(structured_input, PROMPT_COMPOSER_SYS)
            cleaned = strip_ai_buzzwords((out or "").strip().strip('"'))
            if cleaned and len(cleaned) >= 25:
                return cleaned[:1900]
        except Exception:
            pass

    # دمج هيكلي مباشر احتياطي بدون الحاجة لموديل نصي خارجي
    ordered_keys = [
        "edit_operation",
        "identity_lock",
        "scene_understanding",
        "style_engine",
        "composition_engine",
        "lighting_engine",
        "material_engine",
        "environment_physics",
        "camera_engine",
        "conflict_resolution",
        "negative_constraints",
    ]
    pieces = [modules[k] for k in ordered_keys if k in modules and modules[k]]
    return strip_ai_buzzwords(" ".join(pieces))[:1900]
