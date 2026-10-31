# -*- coding: utf-8 -*-
"""
محلل النوايا والتعارضات (Intent & Conflict Analyzer):
- يفهم الطلب بالعربية والإنجليزية.
- يكتشف هل المطلوب توليد (generate)، تعديل (edit)، إزالة (remove)، إضافة (add)، تغيير إضاءة (relight)، أو رجوع لنسخة سابقة (revert).
- يكتشف التعليمات المتعارضة ويحلها بذكاء قبل إرسالها لمحرك الصور.
"""
from __future__ import annotations
import re
from typing import Dict, List, Tuple

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

_REVERT_RE = re.compile(
    r"(?i)(ارجع\s+للصورة|رجّ?ع\s+الصورة|الصورة\s+القبلها|الصورة\s+السابقة|الصورة\s+الأولى|النسخة\s+الأولى|النسخة\s+السابقة|"
    r"تراجع\s+عن\s+التعديل|\b(?:revert|undo\s+edit|previous\s+version|original\s+image|first\s+version)\b)"
)

_REMOVE_RE = re.compile(
    r"(?i)(احذف|شيل|أزل|ازل|امسح|فرّغ|بدون\s+الـ?|خفي|إزالة|ازالة|"
    r"\b(?:remove|delete|erase|strip\s+out|get\s+rid\s+of|clear\s+the)\b)"
)

_ADD_RE = re.compile(
    r"(?i)(ضيف|أضف|اضف|إضافة|حطّ?ل?[وهي]?|ركّ?ب|لبّ?س[وه]?|زوّ?د|"
    r"\b(?:add|insert|put\s+a|place\s+a|include\s+a|wear\s+a)\b)"
)

_RELIGHT_RE = re.compile(
    r"(?i)(عدّ?ل\s+الإضاءة|غيّ?ر\s+الإضاءة|إضاءة|اضاءة|خلّ?ي\s+الجو\s+ليل|خلّ?ي\s+الوقت\s+غروب|شروق|ظلال|نيون|دافئة|باردة|"
    r"\b(?:relight|change\s+lighting|adjust\s+lighting|make\s+it\s+night|golden\s+hour\s+lighting|dramatic\s+lighting)\b)"
)

_EDIT_RE = re.compile(
    r"(?i)(عدّ?ل|تعديل|غيّ?ر|تغيير|بدّ?ل|تبديل|حسّ?ن|تحسين|خلّ?ي|اجعل|طبّ?ق|حوّ?ل|"
    r"بالصورة|عالـ?صورة|بهالصورة|على\s+الصورة|نفس\s+الصورة|وسّ?ع\s+الصورة|كبّ?ر\s+الصورة|ادمج|"
    r"\b(?:edit|modify|change|replace|swap|enhance|upscale|outpaint|expand|inpaint|merge|blend|reframe|this\s+photo|this\s+image)\b)"
)

# جدول القواعد المتعارضة الشائعة في طلبات الصور وكيفية حلها
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
    """تحديد لغة الطلب (ar / en / mixed)."""
    t = (text or "").strip()
    has_ar = bool(_ARABIC_RE.search(t))
    has_en = bool(re.search(r"[A-Za-z]{3,}", t))
    if has_ar and has_en:
        return "mixed"
    return "ar" if has_ar else "en"


def analyze_intent(text: str, has_attached_image: bool = False, has_memory_image: bool = False) -> Dict[str, object]:
    """
    تحليل نية المستخدم واكتشاف نوع المهمة الأساسي:
    generate | edit | remove | add | relight | revert
    """
    t = (text or "").strip()
    lang = detect_language(t)

    if _REVERT_RE.search(t):
        return {
            "language": lang,
            "intent": "revert",
            "is_edit_like": True,
            "uses_reference_image": True,
        }

    explicit_edit = bool(_EDIT_RE.search(t))
    explicit_remove = bool(_REMOVE_RE.search(t))
    explicit_add = bool(_ADD_RE.search(t))
    explicit_relight = bool(_RELIGHT_RE.search(t))

    # إذا أرفق المستخدم صورة، فإن أي طلب تعديل/إزالة/إضافة/إضاءة يعتبر تعديلاً على المرفق
    is_edit_context = has_attached_image or (has_memory_image and (explicit_edit or explicit_remove or explicit_add or explicit_relight))

    if is_edit_context or explicit_edit:
        if explicit_remove and not explicit_add:
            intent = "remove"
        elif explicit_add and not explicit_remove and has_attached_image:
            intent = "add"
        elif explicit_relight and (has_attached_image or explicit_edit):
            intent = "relight"
        else:
            intent = "edit"
        return {
            "language": lang,
            "intent": intent,
            "is_edit_like": True,
            "uses_reference_image": bool(has_attached_image or has_memory_image),
        }

    return {
        "language": lang,
        "intent": "generate",
        "is_edit_like": False,
        "uses_reference_image": False,
    }


def detect_conflicts(text: str) -> Tuple[List[str], List[str]]:
    """اكتشاف التعليمات المتعارضة في طلب المستخدم واقتراح الحلول المنطقية."""
    t = (text or "").strip()
    conflicts: List[str] = []
    resolutions: List[str] = []
    for pat_a, pat_b, desc, res in _CONFLICT_RULES:
        if pat_a.search(t) and pat_b.search(t):
            conflicts.append(desc)
            resolutions.append(res)
    return conflicts, resolutions
