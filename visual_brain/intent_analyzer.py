# -*- coding: utf-8 -*-
"""
محلل الطلبات والنوايا والتعارضات والغموض (Request Understanding & Intent Analyzer — Section 3 & 4):
- يصنّف الطلب بدقة إلى أحد مسارات العمل المطلوبة:
  text_to_image | image_edit | inpaint | remove_object | restore | upscale | color_correct | style_transfer | revert
- يكتشف الغموض (Ambiguous request) ويولّد افتراضاً آمناً واضحاً أو سؤال توضيح مختصراً.
- يكتشف التعليمات المتعارضة ويحلها منطقياً دون اختراع تفاصيل لم يطلبها المستخدم.
"""
from __future__ import annotations
import re
from typing import Dict, List, Tuple, Optional

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

_REVERT_RE = re.compile(
    r"(?i)(ارجع\s+للصورة|رجّ?ع\s+الصورة|الصورة\s+القبلها|الصورة\s+السابقة|الصورة\s+الأولى|النسخة\s+الأولى|النسخة\s+السابقة|"
    r"تراجع\s+عن\s+التعديل|\b(?:revert|undo\s+edit|previous\s+version|original\s+image|first\s+version)\b)"
)

_REMOVE_RE = re.compile(
    r"(?i)(احذف|شيل|أزل|ازل|امسح|فرّغ|بدون\s+الـ?|خفي|إزالة|ازالة|"
    r"\b(?:remove|delete|erase|strip\s+out|get\s+rid\s+of|clear\s+the)\b)"
)

_RESTORE_RE = re.compile(
    r"(?i)(رمّ?م|ترميم|صورة\s+قديمة|إصلاح\s+الصورة\s+القديمة|شيل\s+الخدوش|أصلح\s+التلف|"
    r"\b(?:restore|restoration|old\s+photo|old\s+image|fix\s+scratches|repair\s+damage|denoise)\b)"
)

_UPSCALE_RE = re.compile(
    r"(?i)(حسّ?ن\s+جودة|تحسين\s+الجودة|وضّ?ح\s+الصورة|ارفع\s+الدقة|كبّ?ر\s+الدقة|صفّ?ي\s+الصورة|"
    r"\b(?:upscale|enhance\s+quality|sharpen|high\s+resolution|improve\s+clarity)\b)"
)

_COLOR_CORRECT_RE = re.compile(
    r"(?i)(صحّ?ح\s+الألوان|تصحيح\s+الألوان|توازن\s+الألوان|عدّ?ل\s+التباين|تفتيح\s+بسيط|"
    r"\b(?:color\s+correct|color\s+balance|white\s+balance|adjust\s+contrast|color\s+grade)\b)"
)

_STYLE_TRANSFER_RE = re.compile(
    r"(?i)(حوّ?ل\s+(?:هالصورة|الصورة)\s+لـ?\s*(?:أنمي|انمي|كرتون|لوحة|سكتش|ثلاثي\s+الأبعاد|بكسل)|"
    r"غيّ?ر\s+(?:أسلوب|ستايل)\s+الصورة|"
    r"\b(?:convert\s+(?:this\s+)?(?:image|photo)\s+to\s+(?:anime|cartoon|painting|sketch|3d|pixel\s*art)|style\s+transfer|change\s+style\s+to)\b)"
)

_LOCAL_INPAINT_RE = re.compile(
    r"(?i)(غيّ?ر\s+(?:لون\s+)?(?:القميص|الكنزة|الجاكيت|الملابس|العيون|الخلفية)\s+فقط|بس\s+(?:القميص|الإضاءة|الخلفية|العيون)|"
    r"عدّ?ل\s+بس|عدّ?ل\s+فقط|بمنطقة\s+الـ?|"
    r"\b(?:only\s+change|change\s+only|change\s+shirt\s+color\s+only|edit\s+lighting\s+only|inpaint)\b)"
)

_ADD_RE = re.compile(
    r"(?i)(ضيف|أضف|اضف|إضافة|حطّ?ل?[وهي]?|ركّ?ب|لبّ?س[وه]?|زوّ?د|"
    r"\b(?:add|insert|put\s+a|place\s+a|include\s+a|wear\s+a)\b)"
)

_RELIGHT_RE = re.compile(
    r"(?i)(عدّ?ل\s+الإضاءة|غيّ?ر\s+الإضاءة|إضاءة\s+بس|إضاءة\s+فقط|خلّ?ي\s+الجو\s+ليل|خلّ?ي\s+الوقت\s+غروب|شروق|"
    r"\b(?:relight|edit\s+lighting\s+only|change\s+only\s+the\s+lighting|change\s+lighting|adjust\s+lighting)\b)"
)

_EDIT_RE = re.compile(
    r"(?i)(عدّ?ل|تعديل|غيّ?ر|تغيير|بدّ?ل|تبديل|حسّ?ن|تحسين|خلّ?ي|اجعل|طبّ?ق|حوّ?ل|"
    r"بالصورة|عالـ?صورة|بهالصورة|على\s+الصورة|نفس\s+الصورة|وسّ?ع\s+الصورة|كبّ?ر\s+الصورة|ادمج|"
    r"\b(?:edit|modify|change|replace|swap|enhance|upscale|outpaint|expand|inpaint|merge|blend|reframe|restore|this\s+photo|this\s+image)\b)"
)

_VAGUE_REQUEST_RE = re.compile(
    r"^(?:ارسم|ارسملي|صورة|اعمل\s+صورة|عدّلها|عدلها|صلحها|حسّنها|غيرها|make\s+an?\s+image|draw\s+something|edit\s+this|fix\s+it|make\s+it\s+better)\s*[.!؟?]*$",
    re.I,
)

_CONFLICT_RULES: List[Tuple[re.Pattern, re.Pattern, str, str]] = [
    (
        re.compile(r"(?i)(ليل\s+دامس|منتصف\s+الليل|ظلام\s+كامل|\b(?:pitch\s+black\s+night|midnight|dark\s+night)\b)"),
        re.compile(r"(?i)(شمس\s+ساطعة|شعاع\s+شمس|ظهر\s+مشمس|\b(?:bright\s+midday\s+sun|direct\s+sunbeams|noon\s+sunlight)\b)"),
        "تعارض إضاءة: طلب «ليل/ظلام» مع «شمس ساطعة/شعاع شمس» في نفس المشهد.",
        "تم اعتماد الإضاءة الليلية مع استبدال شعاع الشمس بضوء قمر أو إضاءة شوارع/نافذة متناسقة فيزيائياً."
    ),
    (
        re.compile(r"(?i)(أنمي|انمي|كرتون|بكسل\s+آرت|شعار\s+مسطح|\b(?:anime|cartoon|pixel\s*art|flat\s*logo|vector)\b)"),
        re.compile(r"(?i)(صورة\s+حقيقية|فوتوغرافية|كاميرا\s+حقيقية|مسام\s+جلد\s+واقعية|\b(?:raw\s+photo|photorealistic|real\s+camera\s+photo)\b)"),
        "تعارض أسلوب فني: الجمع بين نمط مرسوم (أنمي/كرتون/شعار) وصورة فوتوغرافية RAW حقيقية.",
        "تم تغليب الأسلوب الفني المحدد صراحةً من المستخدم مع ضبط الإضاءة والتفاصيل بما يناسبه."
    ),
    (
        re.compile(r"(?i)(عزل\s+قوي\s+للخلفية|خلفية\s+مغبشة\s+بالكامل|\b(?:extreme\s+bokeh|blurry\s+background|f/1\.2)\b)"),
        re.compile(r"(?i)(كل\s+تفاصيل\s+الخلفية\s+واضحة|وضوح\s+كامل\s+للخلفية|\b(?:deep\s+focus|everything\s+in\s+sharp\s+focus|f/16)\b)"),
        "تعارض بصري في العدسة: طلب عزل خلفية قوي (Bokeh) مع وضوح حاد لكامل الخلفية في آنٍ واحد.",
        "تم اعتماد عزل تدريجي طبيعي يبرز العنصر الرئيسي مع إبقاء معالم الخلفية مقروءة بصرياً."
    ),
    (
        re.compile(r"(?i)(بدون\s+أي\s+شخص|مكان\s+فارغ\s+تماماً|خالي\s+من\s+البشر|\b(?:empty\s+room|no\s+people|nobody)\b)"),
        re.compile(r"(?i)(شاب|رجل|امرأة|بنت|طفل|بورتريه|سيلفي|\b(?:man|woman|boy|girl|portrait|selfie)\b)"),
        "تعارض عناصر: طلب مكان خالٍ من البشر مع وصف شخص داخل المشهد.",
        "تم تضمين الشخص الموصوف كعنصر رئيسي وإخلاء الخلفية من أي أشخاص إضافيين."
    ),
]


def detect_language(text: str) -> str:
    t = (text or "").strip()
    has_ar = bool(_ARABIC_RE.search(t))
    has_en = bool(re.search(r"[A-Za-z]{3,}", t))
    if has_ar and has_en:
        return "mixed"
    return "ar" if has_ar else "en"


def analyze_intent(text: str, has_attached_image: bool = False, has_memory_image: bool = False) -> Dict[str, object]:
    """
    تحليل نية المستخدم وتحديد:
    - intent: generate | edit | remove | add | relight | enhance | style_transfer | revert
    - task_type: text_to_image | image_edit | inpaint | remove_object | restore | upscale | color_correct | style_transfer
    """
    t = (text or "").strip()
    lang = detect_language(t)

    if _REVERT_RE.search(t):
        return {
            "language": lang,
            "intent": "revert",
            "task_type": "image_edit",
            "is_edit_like": True,
            "uses_reference_image": True,
        }

    is_restore = bool(_RESTORE_RE.search(t))
    is_upscale = bool(_UPSCALE_RE.search(t))
    is_color_corr = bool(_COLOR_CORRECT_RE.search(t))
    is_style_tx = bool(_STYLE_TRANSFER_RE.search(t))
    is_remove = bool(_REMOVE_RE.search(t))
    is_add = bool(_ADD_RE.search(t))
    is_relight = bool(_RELIGHT_RE.search(t))
    is_local_inpaint = bool(_LOCAL_INPAINT_RE.search(t))
    is_edit = bool(_EDIT_RE.search(t)) or is_restore or is_upscale or is_color_corr or is_style_tx

    has_ref_context = has_attached_image or (has_memory_image and (is_edit or is_remove or is_add or is_relight))

    if has_ref_context or is_edit:
        if is_restore:
            task_type = "restore"
            intent = "enhance"
        elif is_upscale:
            task_type = "upscale"
            intent = "enhance"
        elif is_color_corr:
            task_type = "color_correct"
            intent = "enhance"
        elif is_style_tx:
            task_type = "style_transfer"
            intent = "style_transfer"
        elif is_remove and not is_add:
            task_type = "remove_object"
            intent = "remove"
        elif is_relight:
            task_type = "image_edit"
            intent = "relight"
        elif is_local_inpaint:
            task_type = "inpaint"
            intent = "edit"
        elif is_add and not is_remove:
            task_type = "inpaint"
            intent = "add"
        else:
            task_type = "image_edit"
            intent = "edit"
        return {
            "language": lang,
            "intent": intent,
            "task_type": task_type,
            "is_edit_like": True,
            "uses_reference_image": bool(has_attached_image or has_memory_image),
        }

    return {
        "language": lang,
        "intent": "generate",
        "task_type": "text_to_image",
        "is_edit_like": False,
        "uses_reference_image": False,
    }


def detect_ambiguity_and_assumptions(text: str, is_edit: bool, has_ref_image: bool) -> Tuple[List[str], List[str], Optional[str]]:
    """
    فحص الغموض في الطلب (Section 4 & Section 13 - Ambiguous request):
    لا نخترع هوية أو عمراً أو علامة تجارية أو نصاً لم يذكره المستخدم، ونحدد الغموض والافتراضات الآمنة.
    """
    t = (text or "").strip()
    words = t.split()
    uncertainties: List[str] = []
    safe_assumptions: List[str] = []
    clarification_q: Optional[str] = None

    if _VAGUE_REQUEST_RE.match(t) or len(words) <= 2:
        if is_edit or has_ref_image:
            uncertainties.append("لم يتم تحديد العنصر المطلوب تعديله في الصورة (الإضاءة، الخلفية، الألوان، أو تحسين الجودة).")
            safe_assumptions.append("تحسين الوضوح والتوازن اللوني الطبيعي للصورة مع الحفاظ الكامل على الهوية والعناصر.")
            clarification_q = "شو التعديل المحدد يلي بتفضله على الصورة (تغيير الخلفية، تعديل الإضاءة، تحسين الجودة، أو إزالة عنصر)؟"
        else:
            uncertainties.append("وصف المشهد مختصر جداً ولا يحدد البيئة أو الإضاءة أو زاوية التصوير.")
            safe_assumptions.append("اعتماد لقطة فوتوغرافية طبيعية بمستوى العين (35mm) وإضاءة نهارية متوازنة وخلفية بسيطة متناسقة.")
            clarification_q = "ممكن تحددلي العنصر الرئيسي والجو العام للصورة المطلوبة؟"
    elif is_edit and not has_ref_image:
        uncertainties.append("الطلب بصيغة تعديل صورة ولكن لم يتم إرفاق صورة مرجعية في الرسالة.")
        clarification_q = "طلبت تعديل صورة بس ما في صورة مرفقة؛ ارفع الصورة يلي بدك تعدلها مع طلبك."

    return uncertainties, safe_assumptions, clarification_q


def detect_conflicts(text: str) -> Tuple[List[str], List[str]]:
    t = (text or "").strip()
    conflicts: List[str] = []
    resolutions: List[str] = []
    for pat_a, pat_b, desc, res in _CONFLICT_RULES:
        if pat_a.search(t) and pat_b.search(t):
            conflicts.append(desc)
            resolutions.append(res)
    return conflicts, resolutions
