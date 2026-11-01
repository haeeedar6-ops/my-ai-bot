# -*- coding: utf-8 -*-
"""
محرك الحفاظ على الهوية والعناصر المحمية أثناء التعديل (Identity & Protected Elements Engine — Section 6):
- يحافظ على الهوية، الوجه، الوضعية، الملابس، الخلفية، زاوية الكاميرا، النصوص، والشعارات ما لم يطلب المستخدم تغييرها صراحةً.
- لتعديل الإضاءة فقط: يقفل الوجه، الجسم، الملابس، والخلفية بالكامل.
- لتعديل لون القميص/الملابس فقط: يحافظ على نسيج القماش، الطيات، الخياطة، شكل الجسم، وإضاءة المشهد.
"""
from __future__ import annotations
import re
from typing import Optional, Callable
from .schemas import IdentityLock

_CHANGE_BG_RE = re.compile(r"(?i)(خلفية|الخلفية|المكان|البيئة|وراه|حوليه|\b(?:background|backdrop|setting|environment|behind)\b)")
_CHANGE_CLOTH_RE = re.compile(r"(?i)(ملابس|لبس|قميص|جاكيت|كنزة|بدلة|فستان|بنطلون|قبعة|نظارة|\b(?:clothes|clothing|outfit|shirt|jacket|suit|dress|glasses|hat)\b)")
_CHANGE_EYE_RE = re.compile(r"(?i)(لون\s+العيون|عيون[هو]?|عدسات|\b(?:eye\s+color|eyes\s+to|iris)\b)")
_CHANGE_HAIR_RE = re.compile(r"(?i)(شعر[هو]?|تسريحة|لحية|ذقن|\b(?:hair|hairstyle|haircut|beard)\b)")
_CHANGE_POSE_RE = re.compile(r"(?i)(وضعية|وقفة|جلسة|خليه\s+واقف|خليه\s+قاعد|\b(?:pose|posture|standing|sitting)\b)")
_CHANGE_ANGLE_RE = re.compile(r"(?i)(زاوية\s+التصوير|زاوية\s+الكاميرا|من\s+فوق|من\s+الجنب|وسّ?ع\s+الصورة|\b(?:camera\s+angle|perspective|outpaint|expand|reframe)\b)")
_CHANGE_FACE_RE = re.compile(r"(?i)(ملامح\s+الوجه|غيّ?ر\s+الشخص|استبدل\s+الوجه|\b(?:change\s+the\s+person|swap\s+face|different\s+person)\b)")
_LIGHTING_ONLY_RE = re.compile(r"(?i)(الإضاءة\s+فقط|بس\s+الإضاءة|\b(?:lighting\s+only|only\s+the\s+lighting|edit\s+lighting\s+only)\b)")
_SHIRT_COLOR_ONLY_RE = re.compile(r"(?i)(لون\s+(?:القميص|الكنزة|الجاكيت|الملابس)\s+فقط|\b(?:shirt\s+color\s+only|clothing\s+color\s+only|change\s+shirt\s+color\s+only)\b)")


def build_identity_lock(user_text: str, is_edit: bool, has_ref_image: bool) -> IdentityLock:
    """بناء قفل الهوية والعناصر المحمية وفق قواعد القسم 6."""
    t = (user_text or "").strip()
    if not (is_edit or has_ref_image):
        return IdentityLock(enabled=False)

    if _LIGHTING_ONLY_RE.search(t):
        return IdentityLock(
            enabled=True,
            preserve_face_structure=True,
            preserve_eye_color=True,
            preserve_hair_style=True,
            preserve_clothing=True,
            preserve_body_pose=True,
            preserve_camera_angle=True,
            preserve_background=True,
            preserve_text_and_logos=True,
            locked_elements=[
                "exact face and personal identity",
                "body shape and pose",
                "clothing design, color, and folds",
                "background layout and objects",
                "camera angle and composition",
            ],
            modifiable_elements=["scene lighting direction, color temperature, and shadow softness only"],
        )

    if _SHIRT_COLOR_ONLY_RE.search(t):
        return IdentityLock(
            enabled=True,
            preserve_face_structure=True,
            preserve_eye_color=True,
            preserve_hair_style=True,
            preserve_clothing=False,
            preserve_body_pose=True,
            preserve_camera_angle=True,
            preserve_background=True,
            preserve_text_and_logos=True,
            locked_elements=[
                "exact face, skin, hair, and identity",
                "garment fabric micro-texture, existing folds, seams, and collar structure",
                "body shape and posture",
                "scene lighting and entire background",
            ],
            modifiable_elements=["shirt/garment color pigment only"],
        )

    change_bg = bool(_CHANGE_BG_RE.search(t))
    change_cloth = bool(_CHANGE_CLOTH_RE.search(t))
    change_eye = bool(_CHANGE_EYE_RE.search(t))
    change_hair = bool(_CHANGE_HAIR_RE.search(t))
    change_pose = bool(_CHANGE_POSE_RE.search(t))
    change_angle = bool(_CHANGE_ANGLE_RE.search(t))
    change_face = bool(_CHANGE_FACE_RE.search(t))

    lock = IdentityLock(
        enabled=True,
        preserve_face_structure=not change_face,
        preserve_eye_color=not change_eye,
        preserve_hair_style=not change_hair,
        preserve_clothing=not change_cloth,
        preserve_body_pose=not change_pose,
        preserve_camera_angle=not change_angle,
        preserve_background=not change_bg,
        preserve_text_and_logos=True,
    )

    locked, modifiable = [], []
    if lock.preserve_face_structure:
        locked.append("exact facial bone structure, nose bridge, jawline, lip shape, skin tone, and personal identity")
    else:
        modifiable.append("facial features")

    if lock.preserve_eye_color:
        locked.append("exact eye color, iris pattern, and gaze direction")
    else:
        modifiable.append("eye color")

    if lock.preserve_hair_style:
        locked.append("hairline, hair texture, hairstyle, and facial hair")
    else:
        modifiable.append("hairstyle/hair")

    if lock.preserve_clothing:
        locked.append("current clothing, fabric texture, folds, seams, colors, and worn accessories")
    else:
        modifiable.append("clothing/outfit")

    if lock.preserve_body_pose:
        locked.append("exact body posture, head tilt, and hand position")
    else:
        modifiable.append("body pose")

    if lock.preserve_camera_angle:
        locked.append("exact camera angle, lens perspective, subject framing, and proportions")
    else:
        modifiable.append("camera framing/angle")

    if lock.preserve_background:
        locked.append("existing background elements, room/street geometry, and surrounding props")
    else:
        modifiable.append("background/environment")

    locked.append("any existing text, signage, and logos")
    lock.locked_elements = locked
    lock.modifiable_elements = modifiable
    return lock


def extract_reference_identity_signature(ref_parts, ask_vision_fn: Optional[Callable] = None) -> str:
    if not ref_parts or ask_vision_fn is None:
        return ""
    try:
        vision_prompt = (
            "Describe the main subject's immutable visual identity in 45 concise English words so an image editor "
            "can lock their exact appearance: facial bone structure, approximate age, skin tone, eye shape/color, "
            "eyebrows, nose/lip shape, hair color/parting/texture, beard/glasses if any, body pose, and camera angle. "
            "Output ONLY the factual description."
        )
        desc = (ask_vision_fn(ref_parts, vision_prompt) or "").strip()
        return desc[:450]
    except Exception:
        return ""


def format_identity_lock_directive(lock: IdentityLock) -> str:
    if not lock or not lock.enabled:
        return ""
    lines = [
        "IDENTITY & STRUCTURAL LOCK (MANDATORY):",
        "- Strictly LOCK and keep unchanged: " + "; ".join(lock.locked_elements) + ".",
    ]
    if lock.modifiable_elements:
        lines.append("- ONLY allow modifications to: " + ", ".join(lock.modifiable_elements) + ".")
    if lock.identity_descriptor:
        lines.append("- Reference Subject Biometric Anchor: " + lock.identity_descriptor)
    lines.append("- Do NOT re-imagine or alter the person's face, age, ethnicity, or unrequested regions.")
    return "\n".join(lines)
