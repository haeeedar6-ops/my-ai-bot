# -*- coding: utf-8 -*-
"""
محلل المشهد (Scene Understanding):
يستخرج العناصر، الأشخاص، الخلفية، الإضاءة، الألوان، المواد، زاوية الكاميرا، والأسلوب الفني من طلب المستخدم.
"""
from __future__ import annotations
import re
from typing import List
from .schemas import SceneSpec

_HUMAN_RE = re.compile(
    r"(?i)(\b(?:person|man|woman|boy|girl|child|kid|baby|elderly|old\s+man|old\s+woman|portrait|selfie|face|eyes|smile|"
    r"hand|hands|fingers|doctor|engineer|worker|student|teacher|player|soldier|chef|driver|bride|groom|couple|family|crowd|"
    r"human|character|model|actor|singer|gamer|barber|waiter)\b|"
    r"شخص|رجل|شاب|امرأة|بنت|فتاة|طفل|ولد|صبي|ختيار|عجوز|شيخ|وجه|عيون|ابتسامة|بورتريه|سيلفي|يد|أصابع|اصابع|"
    r"طبيب|مهندس|عامل|طالب|معلم|لاعب|جندي|طباخ|سائق|عريس|عروس|عائلة|ناس|بشر|انسان|إنسان|ملامح|بشرة|شعر|لحية|"
    r"قاعد|واقف|ماشي|عم\s+ي|عم\s+ت|جالس|نايم|نائم|يقرأ|يشرب|يأكل|يضحك|يبكي|يفكر|يلعب|يركض)"
)

_CLOTH_RE = re.compile(
    r"(?i)(\b(?:shirt|jacket|coat|suit|dress|jeans|pants|sweater|hoodie|wool|cotton|denim|linen|leather|shoes|boots|sneakers|"
    r"glasses|sunglasses|eyeglasses|watch|ring|necklace|chain|bracelet|hat|cap|scarf|tie|collar|zipper|buttons)\b|"
    r"قميص|جاكيت|معطف|بدلة|فستان|جينز|بنطلون|كنزة|صوف|قطن|كتان|جلد|حذاء|بوط|نظارة|نظارات|ساعة|خاتم|سلسلة|طوق|"
    r"اسوارة|قبعة|طاقية|وشاح|كوفية|ربطة\s*عنق|ياقة|سحاب|أزرار|ازرار|ملابس|لبس|قماش|ثياب)"
)

_LIQUID_GLASS_RE = re.compile(
    r"(?i)(\b(?:cup|glass|mug|bottle|water|coffee|tea|juice|milk|drink|beverage|pour|pouring|spill|steam|smoke|vapor|"
    r"ice|cold|condensation|droplets|rain|wet|mirror|window|reflection|refraction|plate|dish|table|cafe|restaurant|kitchen|bread|food)\b|"
    r"كوب|كأس|كاسة|فنجان|قنينة|زجاجة|ماء|مي|قهوة|شاي|عصير|حليب|مشروب|بارد|مثلج|ثلج|ندى|قطرات|بخار|دخان|"
    r"مطر|مبلل|مرآة|مراية|زجاج|شباك|نافذة|انعكاس|انكسار|صحن|طبق|طاولة|مقهى|كافيه|مطعم|مطبخ|خبز|طعام|أكل|سكب)"
)

_INDOOR_RE = re.compile(
    r"(?i)(\b(?:room|living\s*room|bedroom|office|desk|chair|sofa|couch|cushion|pillow|bed|wall|floor|wood|wooden|"
    r"ceramic|house|home|apartment|shop|store|library|classroom|studio|cafe|restaurant)\b|"
    r"غرفة|صالون|مكتب|طاولة|كرسي|كنب|كنباية|أريكة|وسادة|مخدة|سرير|حائط|جدار|أرضية|خشب|سيراميك|بيت|منزل|شقة|محل|مكتبة|صف|استوديو|مقهى)"
)

_OUTDOOR_RE = re.compile(
    r"(?i)(\b(?:street|road|alley|city|building|park|garden|tree|trees|plant|plants|leaf|leaves|grass|sky|horizon|"
    r"sun|sunlight|sunbeam|sunset|sunrise|morning|afternoon|night|beach|sea|mountain|desert|forest|outdoor)\b|"
    r"شارع|طريق|حارة|مدينة|بناية|مبنى|حديقة|شجرة|أشجار|نبات|نباتات|ورق\s*شجر|عشب|سماء|أفق|شمس|شعاع|غروب|شروق|صباح|ليل|بحر|شاطئ|جبل|صحراء|غابة|طبيعة)"
)

_COLOR_MAP = {
    "أحمر": "red", "احمر": "red", "red": "red",
    "أزرق": "blue", "ازرق": "blue", "blue": "blue",
    "كحلي": "navy blue", "navy": "navy blue",
    "أخضر": "green", "اخضر": "green", "green": "green",
    "زيتي": "olive green", "olive": "olive green",
    "أصفر": "yellow", "اصفر": "yellow", "yellow": "yellow",
    "ذهبي": "golden", "gold": "golden",
    "برتقالي": "orange", "orange": "orange",
    "أسود": "black", "اسود": "black", "black": "black",
    "أبيض": "white", "ابيض": "white", "white": "white",
    "رمادي": "gray", "رصاصي": "gray", "gray": "gray", "grey": "gray",
    "بني": "brown", "عسلي": "hazel brown", "brown": "brown",
    "بيج": "beige", "beige": "beige",
    "وردي": "pink", "زهري": "pink", "pink": "pink",
    "بنفسجي": "purple", "purple": "purple",
    "فضي": "silver", "silver": "silver",
}

_STYLE_PATTERNS = [
    ("anime", re.compile(r"(?i)\b(anime|manga|أنمي|انمي|مانغا)\b")),
    ("cartoon", re.compile(r"(?i)\b(cartoon|comic|chibi|كرتون|كوميك)\b")),
    ("3d_render", re.compile(r"(?i)\b(3d|pixar|disney\s*style|low\s*poly|isometric|ثلاثي\s*الأبعاد|ثري\s*دي)\b")),
    ("logo_vector", re.compile(r"(?i)\b(logo|vector|icon|flat\s*design|شعار|لوغو|لوجو|فيكتور|أيقونة)\b")),
    ("pixel_art", re.compile(r"(?i)\b(pixel\s*art|8-bit|16-bit|بكسل)\b")),
    ("painting", re.compile(r"(?i)\b(watercolor|oil\s*painting|sketch|line\s*art|لوحة\s*زيتية|ألوان\s*مائية|رسمة\s*يدوية|سكتش)\b")),
    ("cinematic", re.compile(r"(?i)\b(cinematic|movie\s*still|film\s*scene|سينمائي|مشهد\s*فيلم)\b")),
]


def analyze_scene(text: str, is_edit: bool = False, has_ref_image: bool = False) -> SceneSpec:
    """استخراج مواصفات المشهد الكاملة من النص."""
    t = (text or "").strip()
    spec = SceneSpec(subject=t[:240])

    # تحديد الأسلوب الفني
    spec.style = "photorealistic"
    for style_name, pat in _STYLE_PATTERNS:
        if pat.search(t):
            spec.style = style_name
            break

    # استخراج الأشخاص
    persons_found: List[str] = []
    for m in _HUMAN_RE.finditer(t):
        tok = m.group(0).strip()
        if tok and tok not in persons_found:
            persons_found.append(tok)
    spec.persons = persons_found[:6]
    spec.has_humans = bool(persons_found) or is_edit or has_ref_image

    # استخراج الألوان
    colors_found: List[str] = []
    t_low = t.lower()
    for k, en_col in _COLOR_MAP.items():
        if k in t_low and en_col not in colors_found:
            colors_found.append(en_col)
    spec.colors = colors_found[:6]

    # تحديد فئات المواد والبيئة
    spec.has_clothing = bool(_CLOTH_RE.search(t)) or spec.has_humans
    spec.has_liquids_or_glass = bool(_LIQUID_GLASS_RE.search(t))
    spec.has_indoor_furniture = bool(_INDOOR_RE.search(t))
    spec.has_outdoor_nature = bool(_OUTDOOR_RE.search(t))

    materials: List[str] = []
    if spec.has_humans:
        materials.extend(["skin", "hair", "eyes"])
    if spec.has_clothing:
        materials.append("fabric")
    if spec.has_liquids_or_glass:
        materials.extend(["liquid", "glass"])
    if spec.has_indoor_furniture:
        materials.extend(["wood", "surface"])
    if re.search(r"(?i)(ذهب|فضة|معدن|حديد|ساعة|خاتم|نظارة|\b(?:metal|gold|silver|steel|watch|glasses)\b)", t):
        materials.append("metal_and_lenses")
    spec.materials = materials

    # الإضاءة والوقت
    if re.search(r"(?i)(ليل|مساء|قمر|نيون|\b(?:night|evening|moonlight|neon)\b)", t):
        spec.lighting = "night ambient & practical warm/cool mixed lighting"
        spec.mood = "moody nocturnal"
    elif re.search(r"(?i)(غروب|شروق|العصر|ذهبية|دافي|دافئة|\b(?:sunset|sunrise|golden\s+hour|afternoon|warm\s+light)\b)", t):
        spec.lighting = "warm golden-hour directional sunlight with long soft shadows"
        spec.mood = "warm inviting"
    elif re.search(r"(?i)(مطر|غائم|ضباب|شتاء|\b(?:rain|rainy|overcast|fog|cloudy|winter)\b)", t):
        spec.lighting = "soft diffused overcast light with wet surface reflections"
        spec.mood = "atmospheric calm"
    elif re.search(r"(?i)(شباك|نافذة|غرفة|مقهى|\b(?:window|cafe|indoor|room)\b)", t):
        spec.lighting = "natural side window key light mixing softly with ambient indoor light"
        spec.mood = "intimate natural"
    else:
        spec.lighting = "single unified natural key light with accurate contact shadows and soft penumbra"

    # الخلفية
    if spec.has_indoor_furniture and spec.has_outdoor_nature:
        spec.background = "indoor setting with window view to outdoor environment"
    elif spec.has_indoor_furniture:
        spec.background = "authentic lived-in indoor environment"
    elif spec.has_outdoor_nature:
        spec.background = "natural outdoor environment with atmospheric depth"
    else:
        spec.background = "clean context-appropriate background with natural depth of field"

    # زاوية التصوير والعدسة
    if re.search(r"(?i)(من\s+فوق|زاوية\s+علوية|\b(?:top-down|bird's-eye|overhead|high\s+angle)\b)", t):
        spec.camera_angle = "high-angle overhead"
    elif re.search(r"(?i)(من\s+تحت|زاوية\s+منخفضة|\b(?:low\s+angle|worm's-eye)\b)", t):
        spec.camera_angle = "low-angle"
    elif re.search(r"(?i)(قريب|بورتريه|وجه|ملامح|سيلفي|\b(?:close-up|portrait|macro|headshot|selfie)\b)", t):
        spec.camera_angle = "eye-level close-up portrait"
        spec.lens_mm = "50mm"
    else:
        spec.camera_angle = "eye-level candid medium shot"
        spec.lens_mm = "35mm"

    # استخراج أي نص مكتوب مطلوب داخل الصورة
    q_match = re.search(r'["«“]([^"»”]{1,40})["»”]', t)
    if q_match:
        spec.text_overlay = q_match.group(1).strip()

    return spec
