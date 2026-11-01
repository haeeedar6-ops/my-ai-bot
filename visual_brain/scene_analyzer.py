# -*- coding: utf-8 -*-
"""
محلل المشهد السياقي (Contextual Scene Plan Builder — Section 4 & Section 5):
- يستخرج الأشخاص، الحيوانات، الأجسام، الأفعال والعلاقات المكانية.
- يتحقق سياقياً: هل اليدان ظاهرتان أو تمسكان بشيء؟ هل المشهد يبرر وجود طين/أوساخ/عرق/ماء أم أنه بورتريه نظيف؟
- يحدد العدسة وفتحة العدسة الفيزيائية المناسبة لنوع اللقطة.
"""
from __future__ import annotations
import re
from typing import List
from .schemas import SceneSpec

_HUMAN_RE = re.compile(
    r"(?i)(\b(?:person|man|woman|boy|girl|child|kid|baby|elderly|old\s+man|old\s+woman|portrait|selfie|face|eyes|smile|"
    r"hand|hands|fingers|doctor|engineer|worker|student|teacher|player|soldier|chef|driver|bride|groom|couple|family|crowd|"
    r"human|character|model|actor|singer|gamer|barber|waiter|hiker)\b|"
    r"شخص|رجل|شاب|امرأة|بنت|فتاة|طفل|ولد|صبي|ختيار|عجوز|شيخ|وجه|عيون|ابتسامة|بورتريه|سيلفي|يد|أصابع|اصابع|"
    r"طبيب|مهندس|عامل|طالب|معلم|لاعب|جندي|طباخ|سائق|عريس|عروس|عائلة|ناس|بشر|انسان|إنسان|ملامح|بشرة|شعر|لحية|"
    r"قاعد|واقف|ماشي|عم\s+ي|عم\s+ت|جالس|نايم|نائم|يقرأ|يشرب|يأكل|يضحك|يبكي|يفكر|يلعب|يركض|يعبر)"
)

_VISIBLE_HANDS_RE = re.compile(
    r"(?i)(\b(?:hand|hands|finger|fingers|holding|grip|gripping|touching|carrying|cup|mug|glass|phone|pen|book|petting|wave|waving)\b|"
    r"يد|إيد|أصابع|اصابع|كف|يمسك|ماسك|حامل|يحمل|يلمس|يشرب|يكتب|كاسة|كوب|فنجان)"
)

_ANIMAL_RE = re.compile(
    r"(?i)(\b(?:dog|puppy|cat|kitten|horse|bird|lion|tiger|wolf|bear|rabbit|deer|animal|pet)\b|"
    r"كلب|قطة|بسينة|حصان|خيل|طير|عصفور|أسد|نمر|ذئب|أرنب|غزال|حيوان)"
)

_CLOTH_RE = re.compile(
    r"(?i)(\b(?:shirt|jacket|coat|suit|dress|jeans|pants|sweater|hoodie|wool|cotton|denim|linen|leather|shoes|boots|sneakers|"
    r"glasses|sunglasses|eyeglasses|watch|ring|necklace|chain|bracelet|hat|cap|scarf|tie|collar|zipper|buttons|clothing|clothes)\b|"
    r"قميص|جاكيت|معطف|بدلة|فستان|جينز|بنطلون|كنزة|صوف|قطن|كتان|جلد|حذاء|بوط|نظارة|نظارات|ساعة|خاتم|سلسلة|طوق|"
    r"اسوارة|قبعة|طاقية|وشاح|كوفية|ربطة\s*عنق|ياقة|سحاب|أزرار|ازرار|ملابس|لبس|قماش|ثياب)"
)

_LIQUID_WATER_RE = re.compile(
    r"(?i)(\b(?:cup|glass|mug|bottle|water|river|stream|sea|ocean|lake|puddle|coffee|tea|juice|milk|drink|beverage|"
    r"pour|pouring|spill|steam|smoke|vapor|ice|cold|condensation|droplets|rain|wet|crossing\s+a\s+river|splash)\b|"
    r"كوب|كأس|كاسة|فنجان|قنينة|زجاجة|ماء|مي|نهر|بحر|بحيرة|جدول|قهوة|شاي|عصير|حليب|مشروب|بارد|مثلج|ثلج|ندى|قطرات|بخار|دخان|مطر|مبلل|سكب)"
)

_DIRT_SWEAT_WEAR_RE = re.compile(
    r"(?i)(\b(?:dirt|dirty|mud|muddy|dust|dusty|sweat|sweaty|worn|weathered|rust|rusty|scratched|stained|stain|"
    r"old\s+image|damaged|tattered|grime|after\s+a\s+muddy\s+walk|work\s+boots|hiking)\b|"
    r"طين|وحل|موحل|متسخ|وسخ|غبار|مغبر|عرق|متعرق|مهترئ|قديم|صدأ|مصدّي|مخدوش|بقع|ملطخ)"
)

_CLEAN_PORTRAIT_RE = re.compile(
    r"(?i)(\b(?:close-up\s+portrait|studio\s+portrait|headshot|clean\s+portrait|passport\s+photo|corporate\s+portrait)\b|"
    r"بورتريه\s+قريب|صورة\s+استوديو|بورتريه\s+نظيف|صورة\s+شخصية\s+رسمية)"
)

_ACTION_REL_RE = re.compile(
    r"(?i)(\b(?:holding\s+[a-z0-9\s]+|crossing\s+[a-z0-9\s]+|with\s+a\s+[a-z0-9\s]+|in\s+a\s+[a-z0-9\s]+|"
    r"sitting\s+[a-z0-9\s]+|standing\s+[a-z0-9\s]+|walking\s+[a-z0-9\s]+|touching\s+[a-z0-9\s]+)\b|"
    r"(?:يمسك|ماسك|يحمل|يعبر|قاعد|واقف|يمشي|يشرب|مع)\s+[^\n،,.]{2,35})"
)

_INDOOR_RE = re.compile(
    r"(?i)(\b(?:room|living\s*room|bedroom|office|desk|chair|sofa|couch|cushion|pillow|bed|wall|floor|wood|wooden|"
    r"ceramic|house|home|apartment|shop|store|library|classroom|studio|cafe|restaurant)\b|"
    r"غرفة|صالون|مكتب|طاولة|كرسي|كنب|كنباية|أريكة|وسادة|مخدة|سرير|حائط|جدار|أرضية|خشب|سيراميك|بيت|منزل|شقة|محل|مكتبة|صف|استوديو|مقهى)"
)

_OUTDOOR_RE = re.compile(
    r"(?i)(\b(?:street|road|alley|city|building|park|garden|tree|trees|plant|plants|leaf|leaves|grass|sky|horizon|"
    r"sun|sunlight|sunbeam|sunset|sunrise|morning|afternoon|night|beach|sea|river|mountain|desert|forest|outdoor|muddy\s+walk)\b|"
    r"شارع|طريق|حارة|مدينة|بناية|مبنى|حديقة|شجرة|أشجار|نبات|نباتات|ورق\s*شجر|عشب|سماء|أفق|شمس|شعاع|غروب|شروق|صباح|ليل|بحر|نهر|شاطئ|جبل|صحراء|غابة|طبيعة)"
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
    ("illustration", re.compile(r"(?i)\b(illustration|watercolor|oil\s*painting|sketch|line\s*art|لوحة\s*زيتية|ألوان\s*مائية|رسمة\s*يدوية|سكتش)\b")),
    ("product_photography", re.compile(r"(?i)\b(product\s*photography|commercial\s*packshot|تصوير\s*منتجات)\b")),
    ("cinematic", re.compile(r"(?i)\b(cinematic|movie\s*still|film\s*scene|سينمائي|مشهد\s*فيلم)\b")),
]


def analyze_scene(text: str, is_edit: bool = False, has_ref_image: bool = False, aspect_hint: str = "") -> SceneSpec:
    """بناء SceneSpec المنظم وفق القسم 4 والقسم 5 من المواصفات."""
    t = (text or "").strip()
    spec = SceneSpec(user_goal=t[:260])

    spec.style = "photorealistic"
    for style_name, pat in _STYLE_PATTERNS:
        if pat.search(t):
            spec.style = style_name
            break

    persons_found: List[str] = []
    for m in _HUMAN_RE.finditer(t):
        tok = m.group(0).strip()
        if tok and tok not in persons_found:
            persons_found.append(tok)
    spec.persons = persons_found[:6]

    animals_found: List[str] = []
    for m in _ANIMAL_RE.finditer(t):
        tok = m.group(0).strip()
        if tok and tok not in animals_found:
            animals_found.append(tok)
    spec.animals = animals_found[:4]
    spec.has_animals = bool(animals_found)

    actions_found: List[str] = []
    for m in _ACTION_REL_RE.finditer(t):
        act = m.group(0).strip()
        if act and act not in actions_found:
            actions_found.append(act)
    spec.actions_and_relations = actions_found[:5]

    spec.has_visible_hands = bool(_VISIBLE_HANDS_RE.search(t))
    spec.has_humans = bool(persons_found) or spec.has_visible_hands or is_edit or has_ref_image
    spec.is_clean_studio_or_portrait = bool(_CLEAN_PORTRAIT_RE.search(t)) and not bool(_DIRT_SWEAT_WEAR_RE.search(t))
    spec.has_dirt_sweat_or_wear = bool(_DIRT_SWEAT_WEAR_RE.search(t)) and not spec.is_clean_studio_or_portrait

    colors_found: List[str] = []
    t_low = t.lower()
    for k, en_col in _COLOR_MAP.items():
        if k in t_low and en_col not in colors_found:
            colors_found.append(en_col)
    spec.colors = colors_found[:6]

    spec.has_clothing = bool(_CLOTH_RE.search(t)) or spec.has_humans
    spec.has_liquids_or_glass = bool(_LIQUID_WATER_RE.search(t))
    spec.has_indoor_furniture = bool(_INDOOR_RE.search(t))
    spec.has_outdoor_nature = bool(_OUTDOOR_RE.search(t))

    subjects_all = list(spec.persons) + list(spec.animals)
    for obj_m in re.finditer(r"(?i)\b(cup|glass|mug|table|chair|car|tree|forest|river|shirt|jacket|book|phone|كاسة|كوب|طاولة|كرسي|شجرة|غابة|نهر|قميص|جاكيت)\b", t):
        w_obj = obj_m.group(0).strip()
        if w_obj not in subjects_all:
            subjects_all.append(w_obj)
            spec.objects.append(w_obj)
    spec.subjects = subjects_all[:10] or ([t[:80]] if t else [])

    materials: List[str] = []
    if spec.has_humans:
        materials.extend(["skin_subsurface_scattering", "hair_strands", "eyes_tear_film"])
    if spec.has_animals:
        materials.append("fur_micro_strands")
    if spec.has_clothing:
        materials.append("woven_fabric_threads")
    if spec.has_liquids_or_glass:
        materials.extend(["water_refraction", "glass_fresnel"])
    if spec.has_dirt_sweat_or_wear:
        materials.append("context_dirt_or_moisture")
    if spec.has_indoor_furniture:
        materials.extend(["natural_wood_grain", "matte_surface"])
    if re.search(r"(?i)(ذهب|فضة|معدن|حديد|ساعة|خاتم|نظارة|\b(?:metal|gold|silver|steel|watch|glasses)\b)", t):
        materials.append("metal_and_optical_lenses")
    spec.materials = materials

    if re.search(r"(?i)(ليل|مساء|قمر|نيون|\b(?:night|evening|moonlight|neon)\b)", t):
        spec.lighting = "night ambient & practical warm/cool mixed lighting with realistic inverse-square falloff"
        spec.mood = "moody nocturnal"
    elif re.search(r"(?i)(صباح|شروق|صباحية|\b(?:morning|soft\s+morning\s+light|sunrise)\b)", t):
        spec.lighting = "soft directional morning key light with gentle natural shadows and warm bounce"
        spec.mood = "fresh morning"
    elif re.search(r"(?i)(غروب|العصر|ذهبية|دافي|دافئة|\b(?:sunset|golden\s+hour|afternoon|warm\s+light)\b)", t):
        spec.lighting = "warm golden-hour directional sunlight with long soft penumbra shadows and rim backlight"
        spec.mood = "warm inviting"
    elif re.search(r"(?i)(مطر|غائم|ضباب|شتاء|\b(?:rain|rainy|overcast|fog|cloudy|winter)\b)", t):
        spec.lighting = "soft diffused overcast sky light with wet Fresnel surface reflections"
        spec.mood = "atmospheric calm"
    elif re.search(r"(?i)(شباك|نافذة|غرفة|مقهى|\b(?:window|cafe|indoor|room)\b)", t):
        spec.lighting = "natural side window key light mixing softly with ambient indoor fill light"
        spec.mood = "intimate natural"
    else:
        spec.lighting = "single unified natural key light with accurate ambient occlusion contact shadows and soft penumbra"

    bg_desc = "clean context-appropriate background with natural optical depth of field"
    if spec.has_indoor_furniture and spec.has_outdoor_nature:
        bg_desc = "indoor setting with natural window view to outdoor environment"
    elif spec.has_indoor_furniture:
        bg_desc = "authentic lived-in indoor environment with physically grounded furniture"
    elif spec.has_outdoor_nature:
        bg_desc = "natural outdoor environment with coherent horizon and atmospheric depth"

    spec.environment = {
        "background": bg_desc,
        "setting": "indoor" if spec.has_indoor_furniture and not spec.has_outdoor_nature else ("outdoor" if spec.has_outdoor_nature else "contextual"),
        "weather_or_atmosphere": "wet/muddy" if spec.has_dirt_sweat_or_wear else "clear natural",
    }

    if re.search(r"(?i)(من\s+فوق|زاوية\s+علوية|\b(?:top-down|bird's-eye|overhead|high\s+angle)\b)", t):
        spec.camera_angle = "high-angle overhead"
        spec.lens_mm = "35mm"
        spec.aperture = "f/2.8"
    elif re.search(r"(?i)(من\s+تحت|زاوية\s+منخفضة|\b(?:low\s+angle|worm's-eye)\b)", t):
        spec.camera_angle = "low-angle"
        spec.lens_mm = "35mm"
        spec.aperture = "f/2.8"
    elif re.search(r"(?i)(قريب|بورتريه|وجه|ملامح|سيلفي|\b(?:close-up|portrait|macro|headshot|selfie)\b)", t):
        spec.camera_angle = "eye-level close-up portrait"
        spec.lens_mm = "50mm"
        spec.aperture = "f/1.8"
    else:
        spec.camera_angle = "eye-level candid medium shot"
        spec.lens_mm = "35mm"
        spec.aperture = "f/2.0"

    spec.camera = {
        "lens_mm": spec.lens_mm,
        "aperture": spec.aperture,
        "sensor": "full-frame 35mm CMOS RAW sensor",
        "viewpoint": spec.camera_angle,
        "depth_of_field": "natural progressive optical depth of field focused strictly on the main subject",
    }
    spec.composition = {
        "framing": spec.camera_angle,
        "aspect_ratio": aspect_hint or "1:1",
        "spatial_relations": "; ".join(spec.actions_and_relations) if spec.actions_and_relations else "natural balanced subject placement",
    }

    q_match = re.search(r'["«“]([^"»”]{1,40})["»”]', t)
    if q_match:
        spec.text_overlay = q_match.group(1).strip()

    return spec
