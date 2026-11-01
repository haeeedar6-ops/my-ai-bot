# -*- coding: utf-8 -*-
"""
محرك بناء التعليمات الموجهة للمزود (Task-Specific Prompt Builder — Section 4, 5 & 6):
- يبني التعليمات من وحدات المشهد، التكوين، الإضاءة، العدسة، المواد السياقية، الأسلوب، وقفل الهوية.
- يلتزم بعدم المبالغة أو اختراع تفاصيل لم يذكرها المستخدم (Do not over-interpret).
"""
from __future__ import annotations
from typing import Dict, Optional, Callable, List
from .schemas import SceneSpec, IdentityLock, EditPlan
from .realism_engine import build_realism_modules_for_scene, strip_ai_buzzwords

STYLE_DIRECTIVES = {
    "photorealistic": "Authentic unedited RAW candid photograph, realistic physical optics and context-appropriate organic textures.",
    "cinematic": "Cinematic 35mm film still, controlled atmospheric lighting, rich natural color grading.",
    "product_photography": "Clean studio product photography, controlled softbox reflections, crisp edge definition, accurate surface materials.",
    "anime": "High-quality anime illustration, clean expressive linework, cel-shaded lighting, cohesive color palette.",
    "cartoon": "Expressive stylized cartoon illustration, clean shapes, dynamic posing, appealing color harmony.",
    "3d_render": "High-craft 3D stylized render, physically based materials, soft global illumination, clean geometry.",
    "logo_vector": "Clean scalable flat vector logo design, balanced negative space, crisp geometric precision, minimal palette.",
    "pixel_art": "Crisp retro pixel art, intentional pixel grid, cohesive limited color palette, clean dithering.",
    "illustration": "Authentic artistic illustration texture, harmonious lighting and composition.",
}

PROMPT_COMPOSER_SYS = (
    "You are the Prompt Builder of RYN Visual Brain. "
    "Convert the structured visual plan into ONE coherent, physically plausible English prompt (100 to 190 words). "
    "Rules:\n"
    "1) Keep EVERY detail the user requested (subjects, exact counts, actions, spatial relations, colors, setting, clothing, mood). Never invent a person's identity, exact age, brand, or text that the user did not provide.\n"
    "2) Weave the provided modular directives (Composition, Lighting, Camera, Contextual Materials, Style, and Protected Elements Lock if editing) naturally.\n"
    "3) Do NOT add dirt, mud, sweat, or damage unless explicitly specified in the scene modules. A clean portrait must remain clean.\n"
    "4) NEVER use forbidden digital hype buzzwords (8K, 4K, hyper-realistic, masterpiece, ultra detailed, CGI, 3D render, flawless).\n"
    "5) Output ONLY the final English prompt."
)


def build_prompt_modules(
    scene: SceneSpec,
    identity_lock: IdentityLock,
    edit_plan: EditPlan,
    conflicts_resolutions: Optional[List[str]] = None,
) -> Dict[str, str]:
    modules: Dict[str, str] = {}

    scene_parts = [f"Primary Request: {scene.user_goal}"]
    if scene.subjects:
        scene_parts.append(f"Subjects: {', '.join(scene.subjects)}")
    if scene.actions_and_relations:
        scene_parts.append(f"Actions & Relations: {'; '.join(scene.actions_and_relations)}")
    if scene.background:
        scene_parts.append(f"Setting/Background: {scene.background}")
    if scene.colors:
        scene_parts.append(f"Key Colors: {', '.join(scene.colors)}")
    if scene.text_overlay:
        scene_parts.append(f'Visible Text: "{scene.text_overlay}"')
    modules["scene_understanding"] = ". ".join(scene_parts) + "."

    modules["style_engine"] = STYLE_DIRECTIVES.get(scene.style, STYLE_DIRECTIVES["photorealistic"])

    modules["composition_engine"] = (
        f"Framing & Composition: {scene.camera_angle} framing ({scene.composition.get('aspect_ratio', '1:1')}), "
        f"{scene.composition.get('spatial_relations', 'balanced placement')}, "
        "clean background behind the main subject's head, physically grounded placement."
    )

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
                return cleaned[:3600]
        except Exception:
            pass

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
    return strip_ai_buzzwords(" ".join(pieces))[:3600]
