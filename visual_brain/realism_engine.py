# -*- coding: utf-8 -*-
"""
محرك الواقعية الفيزيائية والبصرية (Realism Engine):
يحتوي على القواعد التفصيلية للإضاءة، العدسة، تشريح البشر، الأقمشة، السوائل، والبيئة،
ويختار منها ديناميكياً ما يناسب عناصر المشهد فقط.
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
        "shadow direction and length strictly matching the light angle; dark contact shadows (ambient occlusion) directly beneath every object touching the floor, table, or surface; "
        "progressive shadow penumbra softening with distance from the surface; balanced dynamic range preserving crisp detail in both bright highlights and dark shadows without blown-out whites or crushed blacks; "
        "natural environmental color bounce and spill onto nearby surfaces; irregular realistic specular reflections on semi-gloss surfaces (wood, ceramic, leather)."
    ),
    "camera": (
        "shot on a natural {lens} lens at {angle} with a subtle candid handheld feel and natural edge framing; "
        "natural progressive depth of field with sharp focus strictly on the main subject and soft non-uniform bokeh balls in the out-of-focus background; "
        "subtle 35mm film grain / organic sensor noise; slight natural lens distortion near frame edges, subtle corner softness, micro chromatic aberration on high-contrast edges, and a gentle natural optical vignette; "
        "muted natural color saturation, accurate white balance for the time and place, and moderate unfiltered RAW-photo contrast."
    ),
    "human": (
        "natural skin texture with visible pores, fine expression lines around eyes and mouth, and ultra-fine vellus peach fuzz catching the light; "
        "natural skin tone variation (subtle redness, small moles/freckles, faint capillaries, slight sun-tan difference between face, neck, and hands, and natural skin blanching/redness at grip pressure points); "
        "subtle natural sebum sheen on forehead and nose tip (never waxy); natural subtle asymmetry between left and right facial halves; "
        "gradual hairline transition with subtle scalp visibility at the part and random stray flyaway hairs; "
        "individual eyebrow hairs and eyelashes of varying length and direction; natural micro-creases on lips (not glossy glass) and natural off-white ivory teeth with organic alignment; "
        "crisp catchlights in the eyes, natural tear-film moisture and faint micro-capillaries in the sclera, unified gaze convergence on a single focal point, and pupil size matching the ambient light; "
        "subsurface scattering of light through thin ear cartilage and fingertips, plus a subtle rim light along hair edges and shoulders; "
        "anatomically correct hands with exactly 5 fingers per hand, knuckle wrinkles, natural fingernail curvature with subtle white free-edges, and realistic veins/tendons in hands and neck matching movement; "
        "physically grounded body weight naturally compressing cushions/chair/ground; candid mid-action facial expression and hand gesture with slight motion blur on moving extremities."
    ),
    "clothing": (
        "distinct micro-weave fabric texture (cotton, wool, denim, or linen threads) with subtle natural pilling, stray lint threads, and lived-in wear on clothes and shoes; "
        "natural tension folds and creases at joints, elbows, and seated bends with gravity-accurate drape; "
        "continuous intact stitching seams, buttons, collars, and zippers without melting or breaks; "
        "accessories (watches, rings, chains, eyeglasses) resting physically on skin without clipping, and subtle distorted reflections on eyeglass lenses casting a soft frame shadow on the face."
    ),
    "liquids": (
        "physically accurate optical refraction of background objects through glass cups and water; "
        "subtle upward liquid meniscus curve where liquid meets glass walls; random condensation droplets and dew on cold glasses; "
        "turbulent wispy steam or smoke rising and dissipating naturally into the air; casual liquid rings, water drops, or tiny bread crumbs near plates; "
        "accurate geometry-matching reflections in mirrors, glass, and water."
    ),
    "environment": (
        "level horizon line and plumb vertical walls/columns with accurate linear perspective and subtle atmospheric perspective haze fading distant background contrast; "
        "natural everyday lived-in clutter (crumpled napkins, mismatched dishes, casually placed items) and authentic surface wear (fine wood scratches, light dust, faint fingerprints on glass, slightly worn non-razor-sharp corners); "
        "tiny dust motes visible only inside direct sunbeams, natural warm window sunlight mixing with cool indoor light, and organic plant leaves with slight tip yellowing or tiny pinholes; "
        "clean background behind the subject's head (no poles or trees growing out of the head) and background text/signage softly out of focus."
    ),
    "negative": (
        "strictly organic matte/natural textures with zero plastic, airbrushed, or waxy shine on faces or surfaces; "
        "asymmetrical lived-in arrangement of objects (no artificial geometric symmetry and no cloned/repeated faces or props in the background); "
        "every object physically grounded by gravity with complete supporting legs/bases touching the floor; "
        "no extra fingers, no fused limbs, no floating objects, and no digital hype artifacts."
    ),
}


def strip_ai_buzzwords(prompt_en: str) -> str:
    """إزالة كلمات المبالغة الرقمية الممنوعة من البرومبت الإنجليزي."""
    cleaned = _AI_BUZZWORDS_STRIP_RE.sub("", prompt_en or "")
    cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.")
    return cleaned


def build_realism_modules_for_scene(scene: SceneSpec) -> dict:
    """بناء وحدات الواقعية المخصصة لمحتوى المشهد فقط."""
    if scene.style not in ("photorealistic", "cinematic"):
        return {}
    mods = {
        "lighting": REALISM_MODULES["lighting"],
        "camera": REALISM_MODULES["camera"].format(lens=scene.lens_mm or "35mm", angle=scene.camera_angle or "eye-level"),
        "negative": REALISM_MODULES["negative"],
    }
    materials_parts = []
    if scene.has_humans:
        materials_parts.append(REALISM_MODULES["human"])
    if scene.has_clothing:
        materials_parts.append(REALISM_MODULES["clothing"])
    if scene.has_liquids_or_glass:
        materials_parts.append(REALISM_MODULES["liquids"])
    if materials_parts:
        mods["materials"] = " ".join(materials_parts)
    if scene.has_indoor_furniture or scene.has_outdoor_nature or scene.has_humans or not scene.has_liquids_or_glass:
        mods["environment"] = REALISM_MODULES["environment"]
    return mods
