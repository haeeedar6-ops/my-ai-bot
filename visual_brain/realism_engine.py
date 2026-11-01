# -*- coding: utf-8 -*-
"""
محرك الواقعية السياقية والتفاصيل الفيزيائية (Contextual Photorealism & Physical-Detail Engine — Section 5):
- يطبّق القواعد التشريحية والفيزيائية والبصرية بحسب سياق المشهد فقط.
- قاعدة صارمة (Section 5): لا يضيف الأوساخ أو الطين أو العرق أو البلل أو التلف إلى المشهد إلا عندما يبررها المشهد
  أو يطلبها المستخدم صراحةً (البورتريه النظيف يبقى نظيفاً تماماً).
- قاعدة اليدين (Section 5): عندما تكون اليدان ظاهرتين في الإطار، يجب أن تكونا بـ 5 أصابع طبيعية مع الإبهام ومفاصل
  وقبضة متلامسة فيزيائياً؛ ولا يفرض إظهار اليدين إذا كانتا خارج إطار اللقطة (مثل Close-up portrait للوجه).
"""
from __future__ import annotations
import re
from .schemas import SceneSpec

_AI_BUZZWORDS_STRIP_RE = re.compile(
    r"(?i)\b(8k|4k|16k|hyper-?realistic|ultra-?realistic|masterpiece|ultra\s*detailed|ultra\s*hd|hdr|"
    r"3d\s*render|cgi|unreal\s*engine|octane\s*render|ray\s*tracing|flawless|award-?winning|trending\s*on\s*artstation|vivid\s*colors)\b"
)

REALISM_MODULES = {
    "lighting": (
        "single unified primary light source direction across the entire scene; "
        "shadow direction and length strictly matching the light angle; dark contact shadows (ambient occlusion) directly beneath every foot, hand, or object touching the floor, table, or surface so nothing floats; "
        "progressive shadow penumbra softening with distance from the surface; balanced dynamic range preserving crisp detail in both bright highlights and dark shadows without blown-out whites or crushed blacks; "
        "natural environmental color bounce and spill onto nearby surfaces; irregular realistic specular reflections on semi-gloss surfaces."
    ),
    "camera": (
        "shot on a natural {lens} lens at {angle} with consistent camera viewpoint, natural object scale, and coherent spatial relationships; "
        "natural progressive foreground and background depth of field with sharp focus strictly on the main subject and soft non-uniform bokeh balls in out-of-focus background lights; "
        "subtle 35mm film grain, slight perspective lens distortion near frame edges, subtle corner softness compared to center sharpness, micro chromatic aberration on high-contrast edges, and a gentle natural optical vignette; "
        "muted natural color saturation, accurate white balance for the time and place, and moderate unfiltered RAW-photo contrast."
    ),
    "human_face_and_body": (
        "plausible head-to-body proportions, natural neck and shoulder alignment, accurate joint direction, balanced posture, and physical weight compressing the ground or seat; "
        "natural skin pores, fine expression lines around eyes and mouth, ultra-fine vellus peach fuzz catching the light, subtle natural skin tone variation, and context-appropriate skin moisture with zero waxy or plastic shine; "
        "consistent eye gaze convergence on a single focal point, pupil size matching the ambient light, crisp catchlights, natural tear-film moisture, and natural facial symmetry within organic variation; "
        "gradual hairline transition, natural hair growth direction and strand grouping interacting with light, individual eyebrow hairs and eyelashes of varied length, natural lip micro-creases, natural ivory teeth, and subsurface scattering through thin ear cartilage."
    ),
    "hands_when_visible": (
        "when hands are visible in the frame: anatomically accurate five digits per hand including the thumb, natural finger lengths, joints, knuckles, curved fingernails, realistic palm structure and wrist connection, "
        "and physically grounded grip and contact pressure with held objects (with no fused, extra, or duplicated fingers)."
    ),
    "hands_not_forced": (
        "do not force hands into the frame if the composition is a close-up face portrait or hands are naturally outside the frame; if any fingers appear, render five natural digits per hand."
    ),
    "animals": (
        "plausible animal anatomy, accurate limb count and joints, believable scale relative to human subjects, natural fur strand grouping and growth direction, and physically grounded paw/hand-to-animal contact."
    ),
    "clothing": (
        "distinct micro-weave fabric texture (cotton, wool, denim, or linen threads) with accurate thickness, intact stitching seams, buttons, collars, and zippers; "
        "natural tension folds and creases at elbows, joints, and seated bends following gravity and movement without fusing fabric into skin; "
        "accessories (watches, rings, eyeglasses) resting physically on the body with subtle lens reflection and frame shadow."
    ),
    "liquids_and_water": (
        "physically accurate optical refraction through glass and water, subtle upward liquid meniscus curve in glasses, random condensation droplets on cold cups, "
        "consistent water surface reflections and shadows, and wet clothing/skin strictly where water contact or splashing physically occurs."
    ),
    "dirt_sweat_and_wear_contextual": (
        "contextually justified dirt, mud, sweat, wetness, stains, and surface wear placed strictly according to gravity, ground contact, splash trajectory, and friction "
        "(such as mud along lower hems and boots after a muddy walk, without random dirt on unrelated clean surfaces)."
    ),
    "environment_clean": (
        "level horizon line and plumb vertical walls/columns with accurate linear perspective size diminution and subtle atmospheric perspective in the distance; "
        "differentiate wood, metal, glass, water, stone, leather, and fabric through physically appropriate texture and reflections; "
        "clean background behind the subject's head (no poles or trees growing out of the head), keeping studio or clean settings free of random dirt."
    ),
    "negative": (
        "strictly organic textures with zero plastic, airbrushed, or waxy skin; "
        "no duplicated or melted facial features, no extra or fused fingers, no floating feet or objects, "
        "no artificial geometric symmetry, no cloned background faces, and no digital hype artifacts."
    ),
}


def strip_ai_buzzwords(prompt_en: str) -> str:
    """إزالة كلمات المبالغة الرقمية الممنوعة من الوصف الإنجليزي."""
    cleaned = _AI_BUZZWORDS_STRIP_RE.sub("", prompt_en or "")
    cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.")
    return cleaned


def build_realism_modules_for_scene(scene: SceneSpec) -> dict:
    """
    بناء وحدات الواقعية المخصصة لسياق المشهد فقط (Section 5):
    - لا يضيف الأوساخ/الطين/العرق إلا إذا كان `scene.has_dirt_sweat_or_wear` مفعّلاً.
    - لا يفرض ظهور اليدين في البورتريه القريب للوجه.
    """
    if scene.style not in ("photorealistic", "cinematic", "product_photography"):
        return {}
    mods = {
        "lighting": REALISM_MODULES["lighting"],
        "camera": REALISM_MODULES["camera"].format(lens=scene.lens_mm or "35mm", angle=scene.camera_angle or "eye-level"),
        "negative": REALISM_MODULES["negative"],
    }
    materials_parts = []
    if scene.has_humans:
        materials_parts.append(REALISM_MODULES["human_face_and_body"])
        if scene.has_visible_hands:
            materials_parts.append(REALISM_MODULES["hands_when_visible"])
        else:
            materials_parts.append(REALISM_MODULES["hands_not_forced"])
    if scene.has_animals:
        materials_parts.append(REALISM_MODULES["animals"])
    if scene.has_clothing:
        materials_parts.append(REALISM_MODULES["clothing"])
    if scene.has_liquids_or_glass:
        materials_parts.append(REALISM_MODULES["liquids_and_water"])
    if scene.has_dirt_sweat_or_wear and not scene.is_clean_studio_or_portrait:
        materials_parts.append(REALISM_MODULES["dirt_sweat_and_wear_contextual"])
    if materials_parts:
        mods["materials"] = " ".join(materials_parts)
    mods["environment"] = REALISM_MODULES["environment_clean"]
    return mods
