# -*- coding: utf-8 -*-
"""
محرك الواقعية السياقية والتفاصيل الفيزيائية الفائقة (Deep Contextual Photorealism & Optical Physics Engine — Section 5):
- يعزز الواقعية الفوتوغرافية عبر فيزياء المستشعر الضوئي (Full-Frame CMOS 14-stop Dynamic Range)،
  وتشتت الضوء تحت الجلد (Subsurface Scattering)، والخشونة المجهرية للأنسجة والجلد والعدسات.
- قاعدة صارمة (Section 5): لا يضيف الأوساخ أو الطين أو العرق أو البلل أو التلف إلى المشهد إلا عندما يبررها المشهد
  أو يطلبها المستخدم صراحةً (البورتريه النظيف يبقى نظيفاً تماماً).
- قاعدة اليدين (Section 5): عندما تكون اليدان ظاهرتين في الإطار، يجب أن تكونا بـ 5 أصابع طبيعية مع الإبهام ومفاصل
  وقبضة متلامسة فيزيائياً؛ ولا يفرض إظهار اليدين إذا كانتا خارج إطار اللقطة.
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
        "single unified primary light source direction across the entire scene with physically accurate inverse-square light falloff; "
        "shadow direction and length strictly matching the light angle; dark contact shadows (ambient occlusion) directly beneath every foot, hand, or object touching the floor, table, or surface so nothing floats; "
        "progressive shadow penumbra softening with distance from the contact point; 14-stop full-frame sensor dynamic range preserving natural highlight roll-off and deep shadow micro-detail without blown-out whites or crushed blacks; "
        "natural radiosity color bounce and spill onto nearby surfaces; irregular realistic Fresnel specular reflections on semi-gloss surfaces."
    ),
    "camera": (
        "captured on a full-frame camera with a prime {lens} lens at {aperture} from {angle}, consistent camera viewpoint, natural object scale, and coherent spatial relationships; "
        "authentic optical point-spread function with sharp focus strictly on the main subject's focal plane, smooth progressive focus falloff, and organic elliptical cat's-eye bokeh near frame corners; "
        "fine organic 35mm Kodak Portra luminance grain (zero digital sharpening halos), subtle rectilinear lens breathing near frame edges, micro chromatic aberration on high-contrast transitions, and gentle natural optical vignette; "
        "true-to-life color science, accurate Kelvin white balance for the environment, and natural unedited RAW-photo micro-contrast."
    ),
    "human_face_and_body": (
        "plausible head-to-body proportions, natural neck sternocleidomastoid tension and shoulder alignment, accurate joint direction, balanced posture, and physical weight compressing the ground or seat; "
        "authentic multi-layer skin subsurface scattering (warm translucent light diffusion through earlobes, nostrils, and thin skin edges), natural skin pore micro-geometry varying across forehead, nose bridge, and cheeks, fine expression lines around eyes and mouth, ultra-fine vellus peach fuzz catching grazing light, subtle natural skin tone variation, and context-appropriate skin moisture with zero waxy, airbrushed, or plastic shine; "
        "consistent binocular eye gaze convergence on a single focal point, pupil size matching the ambient light, crisp cornea catchlights reflecting the actual light source, wet tear-film meniscus along the lower eyelid, and natural facial asymmetry within organic human variation; "
        "gradual scalp-to-hairline transition with stray flyaway strands, natural anisotropic hair strand grouping interacting with light, individual eyebrow hairs and eyelashes of varied length, natural lip micro-creases, and natural ivory teeth."
    ),
    "hands_when_visible": (
        "when hands are visible in the frame: anatomically accurate five digits per hand including the thumb, natural phalangeal finger lengths, distinct knuckle skin creases, curved fingernails with subtle lunula and nail bed translucency, realistic palm structure, extensor tendons, and wrist connection, "
        "and physically grounded grip and skin-blanching contact pressure with held objects (with no fused, extra, or duplicated fingers)."
    ),
    "hands_not_forced": (
        "do not force hands into the frame if the composition is a close-up face portrait or hands are naturally outside the frame; if any fingers appear, render five natural digits per hand."
    ),
    "animals": (
        "plausible animal anatomy, accurate limb count and skeletal joints, believable scale relative to human subjects, layered multi-directional fur clumping and guard-hair sheen, wet nose leather texture, clear cornea reflections, and physically grounded paw/hand-to-animal contact."
    ),
    "clothing": (
        "distinct micro-weave fabric thread relief (visible warp and weft of cotton, wool, denim, or linen) with accurate material thickness, intact stitching seams, buttonholes, collars, and zippers; "
        "gravity-accurate tension folds, compression wrinkles at elbows and joints, and natural cloth drape over the body without fusing fabric into skin; "
        "accessories (watches, rings, eyeglasses) resting with real physical weight on the body, showing anti-reflective lens coating tint and subtle frame shadow."
    ),
    "liquids_and_water": (
        "physically accurate index-of-refraction (IOR 1.333 for water, 1.52 for glass) bending background lines through glass and water, subtle upward liquid meniscus curve in glasses, random varied condensation droplets on cold cups, "
        "consistent Fresnel water surface reflections and shadows, and wet darkened clothing/skin strictly where water contact or splashing physically occurs."
    ),
    "dirt_sweat_and_wear_contextual": (
        "contextually justified dirt, mud, sweat, wetness, stains, and surface wear placed strictly according to gravity, ground contact, splash trajectory, and friction "
        "(such as mud along lower hems and boots after a muddy walk, without random dirt on unrelated clean surfaces)."
    ),
    "environment_clean": (
        "level horizon line and plumb vertical walls/columns with accurate 3-point linear perspective size diminution and subtle aerial atmospheric perspective in the distance; "
        "physically distinct surface BRDF micro-roughness differentiating wood grain, brushed metal, glass, water, stone, leather, and fabric; "
        "clean background behind the subject's head (no poles or trees growing out of the head), keeping studio or clean settings free of random dirt."
    ),
    "negative": (
        "strictly organic textures with zero plastic, airbrushed, silicone, or waxy skin; "
        "no oversharpened digital halos, no oversaturated neon cast, no duplicated or melted facial features, no extra or fused fingers, no floating feet or objects, "
        "no artificial geometric symmetry, no cloned background faces, and no CGI rendering artifacts."
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
        "camera": REALISM_MODULES["camera"].format(
            lens=scene.lens_mm or "35mm",
            aperture=getattr(scene, "aperture", "f/2.0") or "f/2.0",
            angle=scene.camera_angle or "eye-level",
        ),
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
