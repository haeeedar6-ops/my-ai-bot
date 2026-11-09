"""
tts_engine.py — محرك الأصوات العصبية متعدد اللغات (Multilingual Neural TTS) لـ Ryn.

المزودات (بالترتيب، حسب TTS_PROVIDER = auto | azure | edge | gtts):
  1) azure: خدمة Azure Speech الرسمية (مفاتيح: AZURE_SPEECH_KEY + AZURE_SPEECH_REGION) — مستقرة ورسمية.
  2) edge : حزمة edge-tts (أصوات Microsoft Edge العصبية، بدون مفتاح) — مجانية لكنها غير رسمية، وممكن
            تنحجب من سيرفرات الاستضافة (خطأ 403).
  3) gtts : طوارئ أساسي فقط (Basic Emergency Fallback) عند تعطل المزودات العصبية — لا يُعرض أبداً كصوت Neural.
لو فشلوا كلهم نرجّع خطأ واضح والواجهة تنتقل لصوت المتصفح/الجهاز؛ ما في صوت تجريبي (بيب) بالإنتاج.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import threading
import wave
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("ryn.tts")

# معرّفات الأصوات القديمة (أسماء مختلقة) -> أصوات Microsoft الحقيقية المعتمدة بالكتالوج
LEGACY_VOICE_ALIASES: Dict[str, str] = {
    "ar-SY-RynNatural": "ar-SY-LaithNeural",
    "ar-SA-HamedStrong": "ar-SA-HamedNeural",
    "ar-EG-SalmaExpressive": "ar-EG-SalmaNeural",
    "ar-AE-FarisTech": "ar-AE-HamdanNeural",
    "ar-XA-Standard": "ar-SA-ZariyahNeural",
    "en-US-RynClear": "en-US-AriaNeural",
    "vibevoice-realtime-exp": "ar-SY-LaithNeural",
}

# الكتالوج المعتمد للأصوات العصبية متعددة اللغات (Allow-list صريح لمنع تمرير معرّفات وهمية)
VOICE_CATALOG: List[Dict[str, Any]] = [
    # Arabic — Levantine / Shami dialects (Syrian, Coastal/Lebanese, Jordanian) + Saudi MSA, Egyptian, UAE
    {"id": "ar-SY-LaithNeural", "name": "🎙️ ليث — شامي دمشقي (شبابي طبيعي وسلس)", "lang": "ar-SY", "gender": "male", "pitch": 1.0, "rate": 0.98, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-SY-AmanyNeural", "name": "🌷 أماني — شامية دمشقية (دافئة وناعمة)", "lang": "ar-SY", "gender": "female", "pitch": 1.0, "rate": 0.98, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-LB-LaylaNeural", "name": "🌊 ليلى — شامية ساحلية/لبنانية (سلسة وحيوية)", "lang": "ar-LB", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-LB-RamiNeural", "name": "🌲 رامي — شامي ساحلي/لبناني (هادئ وعفوي)", "lang": "ar-LB", "gender": "male", "pitch": 1.0, "rate": 0.99, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-JO-TaimNeural", "name": "🦅 تيم — شامي أردني (رزين وواضِح)", "lang": "ar-JO", "gender": "male", "pitch": 1.0, "rate": 0.99, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-JO-SanaNeural", "name": "🌿 سناء — شامية أردنية (قريبة ودافئة)", "lang": "ar-JO", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-SA-HamedNeural", "name": "🦁 حامد — فصيح جهوري قوي", "lang": "ar-SA", "gender": "male", "pitch": 1.0, "rate": 0.98, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-SA-ZariyahNeural", "name": "📖 زارية — فصيحة هادئة", "lang": "ar-SA", "gender": "female", "pitch": 1.0, "rate": 0.96, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-EG-SalmaNeural", "name": "🌸 سلمى — مصرية معبّرة", "lang": "ar-EG", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-EG-ShakirNeural", "name": "🎧 شاكر — مصري ودود", "lang": "ar-EG", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "ar-AE-HamdanNeural", "name": "⚡ حمدان — خليجي إماراتي", "lang": "ar-AE", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # English (US & UK)
    {"id": "en-US-AriaNeural", "name": "🌐 Aria — English (US Female)", "lang": "en-US", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "en-US-GuyNeural", "name": "🌐 Guy — English (US Male)", "lang": "en-US", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "en-GB-SoniaNeural", "name": "🇬🇧 Sonia — English (UK Female)", "lang": "en-GB", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "en-GB-RyanNeural", "name": "🇬🇧 Ryan — English (UK Male)", "lang": "en-GB", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # German
    {"id": "de-DE-KatjaNeural", "name": "🇩🇪 Katja — Deutsch (Female)", "lang": "de-DE", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "de-DE-ConradNeural", "name": "🇩🇪 Conrad — Deutsch (Male)", "lang": "de-DE", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # French
    {"id": "fr-FR-DeniseNeural", "name": "🇫🇷 Denise — Français (Female)", "lang": "fr-FR", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "fr-FR-HenriNeural", "name": "🇫🇷 Henri — Français (Male)", "lang": "fr-FR", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # Spanish (Spain & Mexico)
    {"id": "es-ES-ElviraNeural", "name": "🇪🇸 Elvira — Español (España Female)", "lang": "es-ES", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "es-ES-AlvaroNeural", "name": "🇪🇸 Álvaro — Español (España Male)", "lang": "es-ES", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "es-MX-DaliaNeural", "name": "🇲🇽 Dalia — Español (México Female)", "lang": "es-MX", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # Italian
    {"id": "it-IT-ElsaNeural", "name": "🇮🇹 Elsa — Italiano (Female)", "lang": "it-IT", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "it-IT-DiegoNeural", "name": "🇮🇹 Diego — Italiano (Male)", "lang": "it-IT", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # Turkish
    {"id": "tr-TR-EmelNeural", "name": "🇹🇷 Emel — Türkçe (Female)", "lang": "tr-TR", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "tr-TR-AhmetNeural", "name": "🇹🇷 Ahmet — Türkçe (Male)", "lang": "tr-TR", "gender": "male", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    # Portuguese (Brazil & Portugal)
    {"id": "pt-BR-FranciscaNeural", "name": "🇧🇷 Francisca — Português (Brasil Female)", "lang": "pt-BR", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
    {"id": "pt-PT-RaquelNeural", "name": "🇵🇹 Raquel — Português (Portugal Female)", "lang": "pt-PT", "gender": "female", "pitch": 1.0, "rate": 1.0, "provider": "microsoft-neural", "available": True, "experimental": False},
]

ALLOWED_VOICE_IDS: Dict[str, Dict[str, Any]] = {
    v["id"]: v for v in VOICE_CATALOG if v.get("available", True)
}

# جدول اللغات المعتمدة لمزود الطوارئ gTTS (فقط اللغات المتحققة، مع النطاق المحلي TLD)
GTTS_SUPPORTED_LANGUAGES: Dict[str, Tuple[str, str]] = {
    "ar-SY": ("ar", "com"),
    "ar-LB": ("ar", "com"),
    "ar-SA": ("ar", "com"),
    "ar-EG": ("ar", "com.eg"),
    "ar-AE": ("ar", "ae"),
    "ar-JO": ("ar", "com"),
    "ar": ("ar", "com"),
    "en-US": ("en", "com"),
    "en-GB": ("en", "co.uk"),
    "en": ("en", "com"),
    "de-DE": ("de", "de"),
    "de": ("de", "de"),
    "fr-FR": ("fr", "fr"),
    "fr": ("fr", "fr"),
    "es-ES": ("es", "es"),
    "es-MX": ("es", "com.mx"),
    "es": ("es", "es"),
    "it-IT": ("it", "it"),
    "it": ("it", "it"),
    "tr-TR": ("tr", "com.tr"),
    "tr": ("tr", "com.tr"),
    "pt-BR": ("pt", "com.br"),
    "pt-PT": ("pt", "pt"),
    "pt": ("pt", "com.br"),
}

DEFAULT_VOICE = "ar-SY-LaithNeural"
MAX_CHARS = 3000  # الحد الأقصى للقطعة الواحدة عند الإرسال لمزود الصوت
MAX_TOTAL_CHARS = 30000  # سقف الأمان الإجمالي للنص الكامل قبل إظهار تنبيه
MAX_TOTAL_DURATION_SEC = 600.0  # الحد الأقصى لإجمالي مدة القراءة بالثواني (10 دقائق)
_SSML_RATE_MIN, _SSML_RATE_MAX = -40, 60  # نسبة مئوية


def get_configured_default_voice() -> str:
    env_v = str(os.environ.get("TTS_DEFAULT_VOICE") or "").strip()
    env_v = LEGACY_VOICE_ALIASES.get(env_v, env_v)
    return env_v if env_v in ALLOWED_VOICE_IDS else DEFAULT_VOICE


def is_valid_voice_id(voice_id: Optional[str]) -> bool:
    v = str(voice_id or "").strip()
    v = LEGACY_VOICE_ALIASES.get(v, v)
    return v in ALLOWED_VOICE_IDS


def resolve_voice(voice_id: Optional[str], fallback: Optional[str] = None, strict: bool = False) -> str:
    """
    يتحقق من معرّف الصوت مقابل قائمة الكتالوج المعتمدة (Allow-list) حصراً.
    يرفض المعرّفات الوهمية مثل xx-YY-UnknownNeural حتى لو طابقت النمط النصي.
    """
    v = str(voice_id or "").strip()
    v = LEGACY_VOICE_ALIASES.get(v, v)
    if v in ALLOWED_VOICE_IDS:
        return v
    if strict:
        raise ValueError(f"معرّف الصوت غير مدعوم في الكتالوج المعتمد: {voice_id}")
    if fallback:
        fb = LEGACY_VOICE_ALIASES.get(str(fallback).strip(), str(fallback).strip())
        if fb in ALLOWED_VOICE_IDS:
            return fb
    return get_configured_default_voice()


# ---------------- كشف اللغة واللهجة وتوجيه الصوت المناسب ----------------
_SYRIAN_MARKERS = re.compile(
    r"\b(هلق|هلأ|شلونك|شلون|بدي|بدك|بدو|بدنا|هيك|منيح|منيحة|كرمال|مشان|عنجد|كتير|فيني|هاد|هاي|هداك|تعا|يبعتلي|شوبدك|عم)\b"
)
_EGYPTIAN_MARKERS = re.compile(
    r"\b(ازيك|إزيك|عايز|عاوز|كده|دلوقتي|بتاع|امبارح|إيه|ايه ده|عشان|فينك|عامل ايه)\b"
)
_SAUDI_GULF_MARKERS = re.compile(
    r"\b(وشلونك|ابغى|أبغى|الحين|ذحين|واجد|شلونكم|يا طويل العمر)\b"
)


def detect_text_language(text: str, hint_lang: Optional[str] = None) -> str:
    """
    يكتشف لغة النص ولهجته (BCP-47) بدقة لدعم العربية (الشامية/الفصحى/المصرية/الخليجية)،
    الإنجليزية، الألمانية، الفرنسية، الإسبانية، الإيطالية، التركية، والبرتغالية، والنصوص المختلطة.
    """
    raw = str(text or "").strip()
    hint = str(hint_lang or "").strip()
    if hint.lower() == "auto":
        hint = ""

    ar_chars = len(re.findall(r"[\u0600-\u06FF]", raw))
    latin_chars = len(re.findall(r"[A-Za-z\u00C0-\u024F]", raw))

    # إذا كان النص يحتوي على العربية بشكل أساسي أو مختلط مع مصطلحات تقنية
    if ar_chars > 0 and (ar_chars >= latin_chars * 0.25 or hint.startswith("ar")):
        if _SYRIAN_MARKERS.search(raw):
            return "ar-SY"
        if _EGYPTIAN_MARKERS.search(raw):
            return "ar-EG"
        if _SAUDI_GULF_MARKERS.search(raw):
            return "ar-SA"
        if hint in ("ar-SY", "ar-LB", "ar-SA", "ar-EG", "ar-AE", "ar-JO"):
            return hint
        return "ar-SY"

    low = f" {raw.lower()} "
    # Turkish detection
    if re.search(r"[ğşıİçöü]", raw) and re.search(r"([ğşıİ]|\b(merhaba|nasılsın|teşekkür|teşekkürler|için|değil|çok|günaydın|evet|hayır|lütfen)\b)", low):
        return "tr-TR"
    if re.search(r"\b(merhaba|nasılsın|teşekkür|teşekkürler|günaydın|lütfen|iyi günler)\b", low):
        return "tr-TR"

    # German detection
    if re.search(r"[äöüß]", low) and re.search(r"(ß|\b(ich|nicht|und|ist|für|wie|das|एक|sie|wir|mit|auf|sind|haben|werden|oder)\b)", low):
        return "de-DE"
    if re.search(r"\b(hallo|danke|bitte|guten tag|guten morgen|wie geht|ich bin|nicht|für|über)\b", low):
        return "de-DE"

    # Portuguese detection (check ã/õ and Portuguese keywords before French ê)
    if re.search(r"[ãõ]", low) or re.search(r"\b(olá|obrigado|obrigada|você|não|então|também|coração|português|bom dia|tudo bem)\b", low):
        return "pt-PT" if hint == "pt-PT" else "pt-BR"

    # Spanish detection
    if re.search(r"[ñ¿¡]", low) or re.search(r"\b(hola|gracias|cómo|está|estás|para|pero|porque|buenos días|buenas|también|español|hacer|puede)\b", low):
        return "es-MX" if hint == "es-MX" else "es-ES"

    # Italian detection
    if re.search(r"\b(ciao|grazie|perché|questo|questa|della|sono|buongiorno|tutto|bene|anche|più|italiano|come stai)\b", low):
        return "it-IT"

    # French detection
    if re.search(r"\b(bonjour|salut|merci|comment|est-ce|avec|pour|dans|vous|nous|très|c'est|j'ai|qu'il|français|être)\b", low) or re.search(r"[œçëïùû]", low):
        return "fr-FR"

    # Respect valid non-Arabic hint if no specific markers matched
    if hint:
        for v in VOICE_CATALOG:
            if v["lang"].lower() == hint.lower() or v["lang"].split("-")[0].lower() == hint.split("-")[0].lower():
                return v["lang"]

    if ar_chars > 0:
        return "ar-SY"
    return "en-GB" if hint == "en-GB" else "en-US"


def select_voice_for_language(
    text: str = "",
    preferred_voice_id: Optional[str] = None,
    detected_language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    يختار أفضل صوت عصبي من الكتالوج يطابق لغة النص أو اللهجة المكتشفة مع احترام تفضيل المستخدم (ذكر/أنثى واللهجة).
    """
    req_raw = str(preferred_voice_id or "").strip()
    mapped_req = LEGACY_VOICE_ALIASES.get(req_raw, req_raw)
    voice_valid = mapped_req in ALLOWED_VOICE_IDS or not req_raw
    base_vid = resolve_voice(preferred_voice_id)
    base_meta = ALLOWED_VOICE_IDS[base_vid]
    pref_gender = base_meta.get("gender", "male")

    hint = detected_language if (detected_language and detected_language != "auto") else (base_meta["lang"] if req_raw in ALLOWED_VOICE_IDS else None)
    lang = detect_text_language(text, hint_lang=hint) if text.strip() else (hint or base_meta["lang"])
    lang_family = lang.split("-")[0].lower()
    base_family = base_meta["lang"].split("-")[0].lower()

    chosen_vid = base_vid
    # إذا كان النص بنفس عائلة لغة الصوت المختار (مثلاً كلاهما عربي أو كلاهما إنجليزي)، نحترم اختيار المستخدم
    if lang_family == base_family:
        # إذا لم يحدد المستخدم صوتاً صريحاً والنص بلهجة مصرية أو سعودية واضحة، نختار الصوت الأنسب
        if not req_raw and lang_family == "ar" and lang != base_meta["lang"]:
            exact_matches = [v for v in VOICE_CATALOG if v.get("available", True) and v["lang"].lower() == lang.lower()]
            if exact_matches:
                gender_match = [v for v in exact_matches if v.get("gender") == pref_gender]
                chosen_vid = (gender_match[0] if gender_match else exact_matches[0])["id"]
    else:
        # لغة النص مختلفة عن لغة الصوت الافتراضي (مثلاً النص ألماني أو فرنسي أو إنجليزي والصوت عربي):
        # نوجّه تلقائياً لصوت عصبي مخصص لتلك اللغة دون إجبار en-US أو ar-SY
        exact_locale = [v for v in VOICE_CATALOG if v.get("available", True) and v["lang"].lower() == lang.lower()]
        same_lang = [v for v in VOICE_CATALOG if v.get("available", True) and v["lang"].split("-")[0].lower() == lang_family]
        candidates = exact_locale or same_lang
        if candidates:
            gender_match = [v for v in candidates if v.get("gender") == pref_gender]
            chosen_vid = (gender_match[0] if gender_match else candidates[0])["id"]

    chosen_meta = ALLOWED_VOICE_IDS[chosen_vid]
    return {
        "voice_id": chosen_vid,
        "requested_voice_id": req_raw or base_vid,
        "voice_valid": voice_valid,
        "language": chosen_meta["lang"],
        "detected_language": lang,
        "gender": chosen_meta.get("gender", "male"),
        "provider_catalog": chosen_meta.get("provider", "microsoft-neural"),
    }



# ---------------- محرك تحسين النطق الشامي والتشكيل الصوتي السلس (Shami Phonetic Polish) ----------------
# قاموس التشكيل الصوتي للكلمات الشامية والسورية الشائعة لضمان نطقها بلهجة شامية طبيعية 100% بدل قراءتها كفصحى جامدة
_SHAMI_WORD_MAP: Dict[str, str] = {
    "رين": "رِينْ",
    "أهلا": "أَهْلًا",
    "اهلا": "أَهْلًا",
    "أهلاً": "أَهْلًا",
    "وسهلا": "وْسَهْلًا",
    "وسهلاً": "وْسَهْلًا",
    "مرحبا": "مَرْحَبَا",
    "مراحب": "مَرَاحِبْ",
    "شلونك": "شْلُونَكْ",
    "شلونكم": "شْلُونْكُنْ",
    "كيفك": "كِيفَكْ",
    "كيفكن": "كِيفْكُنْ",
    "هلق": "هَلَّقْ",
    "هلأ": "هَلَّأْ",
    "لهلق": "لَهَلَّقْ",
    "لسا": "لِسَّا",
    "عم": "عَمْ",
    "شو": "شُو",
    "ليش": "لِيشْ",
    "وين": "وِينْ",
    "وينك": "وِينَكْ",
    "إيمتى": "إِيمْتَى",
    "ايمتى": "إِيمْتَى",
    "منيح": "مْنِيحْ",
    "منيحة": "مْنِيحَة",
    "مناح": "مْنَاحْ",
    "كتير": "كْتِيرْ",
    "شوي": "شْوَيّ",
    "شوية": "شْوَيَّة",
    "هيك": "هِيكْ",
    "مشان": "مْشَانْ",
    "عشان": "عَشَانْ",
    "كرمال": "كِرْمَالْ",
    "كرمالك": "كِرْمَالَكْ",
    "بدي": "بِدِّي",
    "بدك": "بِدَّكْ",
    "بدو": "بِدُّو",
    "بدها": "بِدْهَا",
    "بدنا": "بِدْنَا",
    "بدكن": "بِدْكُنْ",
    "فيني": "فِينِي",
    "فيك": "فِيكْ",
    "فينا": "فِينَا",
    "هاد": "هَادْ",
    "هادا": "هَادَا",
    "هاي": "هَايْ",
    "هداك": "هَدَاكْ",
    "هدول": "هَدُولْ",
    "رح": "رَحْ",
    "مو": "مُو",
    "عنجد": "عَنْجَدْ",
    "والله": "وَاللّٰه",
    "يلا": "يَلَّا",
    "طيب": "طَيِّبْ",
    "تمام": "تَمَامْ",
    "مزبوط": "مَزْبُوطْ",
    "مضبوط": "مَزْبُوطْ",
    "شغال": "شَغَّالْ",
    "شغّال": "شَغَّالْ",
    "شغالة": "شَغَّالَة",
    "شغلة": "شَغْلِة",
    "شغلات": "شَغْلَاتْ",
    "تفضل": "تْفَضَّلْ",
    "تفضّل": "تْفَضَّلْ",
    "تكرم": "تِكْرَمْ",
    "عيوني": "عْيُونِي",
    "غالي": "غَالِي",
    "رفيقي": "رْفِيقِي",
    "صديقي": "صَدِيقِي",
    "حبيبي": "حَبِيبِي",
    "أخي": "أَخِي",
    "نحكي": "نِحْكِي",
    "لنحكي": "لْنِحْكِي",
    "بحكي": "بِحْكِي",
    "تحكي": "تِحْكِي",
    "يحكي": "يِحْكِي",
    "احكي": "إِحْكِي",
    "احكيلي": "إِحْكِيلِي",
    "قلي": "قِلِّي",
    "قلّي": "قِلِّي",
    "بقلك": "بْقِلَّكْ",
    "أسمعك": "إِسْمَعَكْ",
    "اسمعك": "إِسْمَعَكْ",
    "سمعتك": "سْمِعْتَكْ",
    "فهمت": "فْهِمْتْ",
    "عليك": "عَلِيكْ",
    "معك": "مَعَكْ",
    "معي": "مَعِي",
    "مع": "مَعْ",
    "بعض": "بَعْضْ",
    "جاهز": "جَاهِزْ",
    "جاهزة": "جَاهْزِة",
    "دغري": "دُغْرِي",
    "فورا": "فَوْرًا",
    "فوراً": "فَوْرًا",
    "طبعاً": "طَبْعًا",
    "طبعا": "طَبْعًا",
    "أكيد": "أَكِيدْ",
    "اكيد": "أَكِيدْ",
    "بسيطة": "بَسِيطَة",
    " ولا ": " وَلَا ",
    "يهمك": "يْهِمَّكْ",
    "جرب": "جَرِّبْ",
    "جرّب": "جَرِّبْ",
    "شوف": "شُوفْ",
    "بلش": "بَلِّشْ",
    "بلّش": "بَلِّشْ",
    "خلص": "خَلَصْ",
    "تاني": "تَانِي",
    "تانية": "تَانْيَة",
    "ثانية": "تَانْيَة",
    "إلي": "إِلِي",
    "إلك": "إِلَكْ",
    "إلو": "إِلُو",
    "فيها": "فِيهَا",
    "فيه": "فِيهْ",
    "عندي": "عِنْدِي",
    "عندك": "عِنْدَكْ",
    "سيرفر": "سِيرْفَرْ",
    "ميكروفون": "مَايْكْرُوفُونْ",
}

_TECH_ARABIC_PHONETIC: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bRyn\b", re.I), "رِينْ"),
    (re.compile(r"\bChatGPT\b", re.I), "تشات جي بي تي"),
    (re.compile(r"\bOpenAI\b", re.I), "أوبن إيه آي"),
    (re.compile(r"\bGemini\b", re.I), "جيميناي"),
    (re.compile(r"\bGroq\b", re.I), "غروك"),
    (re.compile(r"\bPython\b", re.I), "بايثون"),
    (re.compile(r"\bJavaScript\b", re.I), "جافاسكربت"),
    (re.compile(r"\bTypeScript\b", re.I), "تايب سكربت"),
    (re.compile(r"\bGitHub\b", re.I), "غيت هاب"),
    (re.compile(r"\bRender\b", re.I), "ريندر"),
    (re.compile(r"\bFlask\b", re.I), "فلاسك"),
    (re.compile(r"\bFastAPI\b", re.I), "فاست إيه بي آي"),
    (re.compile(r"\bAPI\b"), "إيه بي آي"),
    (re.compile(r"\bJSON\b"), "جيسون"),
    (re.compile(r"\bHTML\b"), "إتش تي إم إل"),
    (re.compile(r"\bCSS\b"), "سي إس إس"),
    (re.compile(r"\bSQL\b"), "إس كيو إل"),
    (re.compile(r"\bURL\b"), "يو آر إل"),
]


def polish_text_for_voice(text: str, voice: str = DEFAULT_VOICE) -> str:
    """
    يحسّن انسيابية النص وطبيعته الصوتية قبل إرساله لمزود النطق العصبي (edge-tts / Azure):
    - في الأصوات العربية الشامية (`ar-SY`, `ar-LB`, `ar-JO`): يضبط تشكيل الكلمات الشامية الشائعة،
      يمنع التنوين الفصيح المصطنع في أواخر الجمل، ويحوّل المصطلحات التقنية الشائعة لنطق صوتي سلس.
    """
    t = str(text or "").strip()
    if not t:
        return t
    if not voice.startswith("ar-"):
        return t

    # تحويل العبارات الفصيحة الجامدة الشائعة في الرسائل الترحيبية إلى لهجة شامية دافئة عند استخدام صوت شامي
    if voice.startswith(("ar-SY", "ar-LB", "ar-JO")):
        t = re.sub(r"\bأهلاً بك\b", "أَهْلًا فِيكْ", t)
        t = re.sub(r"\bاهلا بك\b", "أَهْلًا فِيكْ", t)
        t = re.sub(r"\bكيف يمكنني مساعدتك\b", "كِيفْ فِينِي سَاعْدَكْ", t)
        t = re.sub(r"\bكيف أستطيع مساعدتك\b", "شُو فِينِي سَاعْدَكْ", t)

        # تعريب صوتي للمصطلحات الإنجليزية المفردة وسط الكلام العربي لمنع التهجئة المتقطعة
        if len(re.findall(r"[\u0600-\u06FF]", t)) > len(re.findall(r"[A-Za-z]", t)):
            for pat, ar_phon in _TECH_ARABIC_PHONETIC:
                t = pat.sub(ar_phon, t)

        # تطبيق التشكيل الشامي للكلمات المفتاحية
        tokens = re.split(r"(\s+|[،,.!?؟؛:]+)", t)
        out_tokens: List[str] = []
        for tok in tokens:
            if tok in _SHAMI_WORD_MAP:
                out_tokens.append(_SHAMI_WORD_MAP[tok])
            else:
                out_tokens.append(tok)
        t = "".join(out_tokens)

    # تحسين فواصل التنفس الطبيعية ताकि لا يدمج الكلمات بسرعة آلية
    t = re.sub(r"\s*([،,؛;])\s*", r"\1 ", t)
    t = re.sub(r"\s*([.!?؟]+)\s*", r"\1 ", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_for_speech(text: str, max_chars: Optional[int] = None) -> str:
    """
    ينظف الكود والماركداون والروابط مع الحفاظ على علامات الترقيم وفواصل الفقرات للنطق الطبيعي.
    لا يقتطع النص صامتاً عند 3000 حرف إلا إذا طُلب حد أقصى صريح عبر max_chars.
    """
    t = str(text or "")
    t = re.sub(r"```[\s\S]*?```", " كود برمجي جاهز بالشات. ", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " رابط ", t)
    t = re.sub(r"(?m)^\s*(?:[-*•]+|\d+[.)])\s+", " ، ", t)
    t = re.sub(r"[*#_~>|•—–]+", " ، ", t)
    t = re.sub(r'[()\[\]{}"/\\]+', " ", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", " ", t)  # إيموجي
    # الحفاظ على فواصل الأسطر كوقفة طبيعية بين الفقرات
    t = re.sub(r"\n{2,}", " . ", t)
    t = re.sub(r"\n", " ، ", t)
    t = re.sub(r"(?:\s*،\s*){2,}", " ، ", t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    if max_chars is not None and max_chars > 0:
        return t[:max_chars]
    return t


def split_into_sentence_chunks(
    text: str,
    max_chunk_chars: int = MAX_CHARS,
    max_total_chars: int = MAX_TOTAL_CHARS,
    max_duration_sec: float = MAX_TOTAL_DURATION_SEC,
    speed: float = 1.0,
) -> Dict[str, Any]:
    """
    يقسم النص المنظّف إلى جمل متناسقة تحت سقف المزود (MAX_CHARS) مع الحفاظ على علامات الترقيم.
    لا يقتطع أي جزء بصمت؛ وإذا تجاوز النص سقف الأمان الكلي يرجّع تحذيراً واضحاً (warning).
    """
    clean = clean_for_speech(text)
    if not clean:
        return {
            "clean_text": "",
            "chunks": [],
            "truncated": False,
            "warning": "",
            "estimated_duration_sec": 0.0,
            "total_chars": 0,
        }

    spd = max(0.5, min(2.0, float(speed or 1.0)))
    max_chars_by_dur = int((max_duration_sec * spd) / 0.06)
    effective_cap = min(max_total_chars, max(max_chunk_chars, max_chars_by_dur))

    truncated = False
    warning = ""
    if len(clean) > effective_cap:
        truncated = True
        clean = clean[:effective_cap].rsplit(" ", 1)[0] or clean[:effective_cap]
        warning = f"تم الوصول إلى الحد الأقصى المسموح لطول القراءة الصوتية ({effective_cap} حرف / {int(max_duration_sec)} ثانية)."

    limit = max(100, min(MAX_CHARS, int(max_chunk_chars or MAX_CHARS)))
    # تقسيم ذكي حسب الجمل مع الحفاظ على علامات الوقف
    raw_sentences = re.findall(r"[^.!?؟؛;\n]+[.!?؟؛;\n]*", clean) or [clean]
    chunks: List[str] = []
    current = ""

    for sent in raw_sentences:
        s = sent.strip()
        if not s:
            continue
        if len(s) > limit:
            if current:
                chunks.append(current)
                current = ""
            # تقسيم الجملة الطويلة جداً عند الفواصل أو المسافات بدون ضياع أي حرف
            clauses = re.findall(r"[^،,:]+[,،:]*", s) or [s]
            sub_cur = ""
            for cl in clauses:
                cl = cl.strip()
                if not cl:
                    continue
                if len(cl) > limit:
                    if sub_cur:
                        chunks.append(sub_cur)
                        sub_cur = ""
                    for idx in range(0, len(cl), limit):
                        part = cl[idx:idx + limit].strip()
                        if part:
                            chunks.append(part)
                elif sub_cur and len(sub_cur) + 1 + len(cl) > limit:
                    chunks.append(sub_cur)
                    sub_cur = cl
                else:
                    sub_cur = f"{sub_cur} {cl}".strip() if sub_cur else cl
            if sub_cur:
                current = sub_cur
        elif current and len(current) + 1 + len(s) > limit:
            chunks.append(current)
            current = s
        else:
            current = f"{current} {s}".strip() if current else s

    if current:
        chunks.append(current)

    est_dur = round(max(0.25, (len(clean) * 0.06) / spd), 2)
    return {
        "clean_text": clean,
        "chunks": chunks,
        "truncated": truncated,
        "warning": warning,
        "estimated_duration_sec": est_dur,
        "total_chars": len(clean),
    }


def _rate_percent(speed: float) -> int:
    try:
        pct = int(round((float(speed) - 1.0) * 100))
    except Exception:
        pct = 0
    return max(_SSML_RATE_MIN, min(_SSML_RATE_MAX, pct))


# ---------------- التحقق الصارم من صيغة وحاوية الصوت ----------------
def validate_audio_payload(audio: bytes, expected_format: Optional[str] = None) -> Dict[str, str]:
    """
    يتحقق من أن بايتات الصوت غير فارغة وتحمل توقيع حاوية صوتية معروفةوصحيحة (MP3 / WAV / OGG).
    يرمي ValueError على البيانات الفارغة أو التالفة أو غير المدعومة.
    """
    if not isinstance(audio, (bytes, bytearray)) or len(audio) < 4:
        raise ValueError("بيانات الصوت فارغة أو قصيرة جداً")
    raw = bytes(audio)
    head4 = raw[:4]
    head3 = raw[:3]

    # WAV: RIFF....WAVE
    if head4 == b"RIFF":
        if len(raw) < 12 or raw[8:12] != b"WAVE":
            raise ValueError("ترويسة WAV (RIFF) تالفة أو غير مكتملة")
        fmt, mime = "wav", "audio/wav"
    # MP3: ID3 tag or MPEG frame sync (0xFFE0..0xFFFF)
    elif head3 == b"ID3" or (raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
        fmt, mime = "mp3", "audio/mpeg"
    # OGG / Opus
    elif head4 == b"OggS":
        fmt, mime = "ogg", "audio/ogg"
    else:
        raise ValueError("صيغة حاوية الصوت غير معروفة أو البيانات تالفة")

    if expected_format:
        exp = expected_format.lower().strip().lstrip(".")
        if exp in ("mp3", "mpeg") and fmt != "mp3":
            raise ValueError(f"صيغة الصوت الناتجة ({fmt}) لا تطابق الصيغة المتوقعة (mp3)")
        if exp == "wav" and fmt != "wav":
            raise ValueError(f"صيغة الصوت الناتجة ({fmt}) لا تطابق الصيغة المتوقعة (wav)")
    return {"output_format": fmt, "mime_type": mime}


def sniff_mime(audio: bytes) -> str:
    """يكشف نوع MIME الحقيقي من توقيع الحاوية ويرفض الحمولات الفارغة أو التالفة."""
    return validate_audio_payload(audio)["mime_type"]


def _merge_audio_chunks(chunks_bytes: List[bytes], output_format: str) -> bytes:
    if not chunks_bytes:
        return b""
    if len(chunks_bytes) == 1:
        return chunks_bytes[0]
    if output_format == "wav":
        try:
            out_buf = io.BytesIO()
            params = None
            frames = bytearray()
            for b in chunks_bytes:
                with wave.open(io.BytesIO(b), "rb") as wf:
                    if params is None:
                        params = wf.getparams()
                    frames.extend(wf.readframes(wf.getnframes()))
            if params is not None:
                with wave.open(out_buf, "wb") as out_wf:
                    out_wf.setparams(params)
                    out_wf.writeframes(bytes(frames))
                return out_buf.getvalue()
        except Exception:
            pass
    return b"".join(chunks_bytes)


class _LRU:
    def __init__(self, max_items: int = 64, max_bytes: int = 24 * 1024 * 1024):
        self._d: "OrderedDict[str, bytes]" = OrderedDict()
        self._bytes = 0
        self._max_items, self._max_bytes = max_items, max_bytes
        self._lock = threading.Lock()

    def get(self, k: str) -> Optional[bytes]:
        with self._lock:
            v = self._d.get(k)
            if v is not None:
                self._d.move_to_end(k)
            return v

    def put(self, k: str, v: bytes) -> None:
        with self._lock:
            if k in self._d:
                self._bytes -= len(self._d.pop(k))
            self._d[k] = v
            self._bytes += len(v)
            while self._d and (len(self._d) > self._max_items or self._bytes > self._max_bytes):
                _, old = self._d.popitem(last=False)
                self._bytes -= len(old)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0


_CACHE = _LRU()
_PROVIDER_BY_KEY: Dict[str, str] = {}
_META_BY_KEY: Dict[str, Dict[str, Any]] = {}
_CACHE_CONFIG_VERSION: str = ""
_CACHE_LOCK = threading.Lock()


def _current_config_fingerprint() -> str:
    mode = os.environ.get("TTS_PROVIDER", "auto").strip().lower()
    model = os.environ.get("TTS_MODEL", "microsoft-neural").strip()
    fmt = os.environ.get("TTS_OUTPUT_FORMAT", "mp3").strip().lower()
    az_reg = os.environ.get("AZURE_SPEECH_REGION", "").strip()
    az_key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    az_hash = hashlib.sha256(az_key.encode("utf-8")).hexdigest()[:8] if az_key else "none"
    def_v = os.environ.get("TTS_DEFAULT_VOICE", DEFAULT_VOICE).strip()
    paid = "paid" if paid_providers_allowed() else "free"
    return f"v3|{mode}|{paid}|{model}|{fmt}|{az_reg}|{az_hash}|{def_v}"


def clear_cache() -> None:
    """يمسح كاش الصوت تماماً عند تغيير إعدادات المزود."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _PROVIDER_BY_KEY.clear()
        _META_BY_KEY.clear()


def _ensure_cache_version() -> str:
    global _CACHE_CONFIG_VERSION
    fp = _current_config_fingerprint()
    with _CACHE_LOCK:
        if _CACHE_CONFIG_VERSION and _CACHE_CONFIG_VERSION != fp:
            _CACHE.clear()
            _PROVIDER_BY_KEY.clear()
            _META_BY_KEY.clear()
        _CACHE_CONFIG_VERSION = fp
    return fp


def build_cache_key(
    provider: str,
    model_version: str,
    output_format: str,
    locale: str,
    voice_id: str,
    rate_pct: int,
    normalized_text: str,
) -> str:
    """
    يبني مفتاح الكاش شاملاً هوية المزود، الإصدار/النموذج، صيغة الخرج، اللغة/المحلية،
    معرّف الصوت، السرعة، والنص المنظف.
    """
    raw = f"{provider}|{model_version}|{output_format}|{locale}|{voice_id}|{rate_pct:+d}|{normalized_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------- Azure Speech (رسمي) ----------------
def _azure_synthesize(text: str, voice: str, speed: float) -> bytes:
    import requests  # موجودة بالمتطلبات

    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not key or not region:
        raise RuntimeError("Azure Speech غير مضبوط")
    lang = "-".join(voice.split("-")[:2])
    from xml.sax.saxutils import escape
    spoken_text = polish_text_for_voice(text, voice)
    ssml = (
        f"<speak version='1.0' xml:lang='{lang}' xmlns='http://www.w3.org/2001/10/synthesis'>"
        f"<voice name='{voice}'><prosody rate='{_rate_percent(speed):+d}%'>{escape(spoken_text)}</prosody></voice></speak>"
    )
    r = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "ryn-tts",
        },
        data=ssml.encode("utf-8"),
        timeout=30,
    )
    if r.status_code != 200 or not r.content:
        raise RuntimeError(f"Azure Speech HTTP {r.status_code}")
    return r.content


# ---------------- edge-tts (بدون مفتاح) ----------------
async def _edge_async(text: str, voice: str, speed: float) -> bytes:
    import edge_tts  # type: ignore

    spoken_text = polish_text_for_voice(text, voice)
    comm = edge_tts.Communicate(spoken_text, voice, rate=f"{_rate_percent(speed):+d}%")
    out = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            out.extend(chunk["data"])
    return bytes(out)


def _edge_synthesize(text: str, voice: str, speed: float) -> bytes:
    try:
        import edge_tts  # noqa: F401
    except Exception:
        raise RuntimeError("حزمة edge-tts غير مثبتة (pip install edge-tts)")
    data = asyncio.run(asyncio.wait_for(_edge_async(text, voice, speed), timeout=40))
    if not data:
        raise RuntimeError("edge-tts رجّع صوتاً فاضياً")
    return data


# ---------------- gTTS (طوارئ أساسي فقط — Basic Emergency Fallback) ----------------
def resolve_gtts_language(voice_or_lang: str) -> Tuple[str, str]:
    """
    يحوّل اللغة أو الصوت إلى كود لغة gTTS عبر جدول اللغات المدعومة صراحةً.
    يرفض اللغات غير الموجودة بالجدول بدلاً من القص العشوائي.
    """
    raw = str(voice_or_lang or "").strip()
    if raw in ALLOWED_VOICE_IDS:
        locale = ALLOWED_VOICE_IDS[raw]["lang"]
    elif "-".join(raw.split("-")[:2]) in GTTS_SUPPORTED_LANGUAGES:
        locale = "-".join(raw.split("-")[:2])
    else:
        locale = raw

    if locale in GTTS_SUPPORTED_LANGUAGES:
        return GTTS_SUPPORTED_LANGUAGES[locale]
    base = locale.split("-")[0].lower()
    if base in GTTS_SUPPORTED_LANGUAGES:
        return GTTS_SUPPORTED_LANGUAGES[base]
    raise ValueError(f"لغة غير مدعومة في جدول طوارئ gTTS: {voice_or_lang}")


def _gtts_synthesize(text: str, voice: str, speed: float) -> bytes:
    try:
        from gtts import gTTS  # type: ignore
    except Exception:
        raise RuntimeError("حزمة gTTS غير مثبتة (pip install gTTS)")

    gtts_lang, gtts_tld = resolve_gtts_language(voice)
    buf = io.BytesIO()
    gTTS(text=text, lang=gtts_lang, tld=gtts_tld, slow=float(speed) < 0.85).write_to_fp(buf)
    data = buf.getvalue()
    if not data:
        raise RuntimeError("gTTS رجّع صوتاً فاضياً")
    return data


def paid_providers_allowed() -> bool:
    """Azure (مدفوع) ما بيشتغل إلا بموافقة صريحة: TTS_ALLOW_PAID=1 أو TTS_PROVIDER=azure."""
    return os.environ.get("TTS_ALLOW_PAID", "").strip().lower() in ("1", "true", "yes", "on")


def provider_order() -> list:
    """ترتيب مجاني أولاً. Azure المدفوع ما يدخل تلقائياً حتى لو المفاتيح موجودة."""
    mode = os.environ.get("TTS_PROVIDER", "auto").strip().lower()
    has_azure = bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))
    if mode in ("azure", "edge", "gtts"):
        return [mode]
    order = ["edge", "gtts"]
    if has_azure and paid_providers_allowed():
        order.append("azure")  # آخر خيار احتياطي، فقط بعد تفعيل صريح
    return order


def _synthesize_single_chunk(clean_chunk: str, voice: str, speed: float) -> Tuple[bytes, Dict[str, Any]]:
    _ensure_cache_version()
    voice_meta = ALLOWED_VOICE_IDS.get(voice) or ALLOWED_VOICE_IDS[DEFAULT_VOICE]
    locale = voice_meta["lang"]
    rate_pct = _rate_percent(speed)
    model_ver = os.environ.get("TTS_MODEL", "microsoft-neural").strip() or "microsoft-neural"
    expected_fmt = os.environ.get("TTS_OUTPUT_FORMAT", "mp3").strip().lower() or "mp3"
    order = provider_order()

    # Check cache per provider in current provider_order so switching provider config causes a cache miss
    for candidate_prov in order:
        ckey = build_cache_key(
            provider=candidate_prov,
            model_version=model_ver if candidate_prov != "gtts" else "gtts-basic-v1",
            output_format=expected_fmt,
            locale=locale,
            voice_id=voice,
            rate_pct=rate_pct,
            normalized_text=clean_chunk,
        )
        hit = _CACHE.get(ckey)
        if hit is not None:
            meta = dict(_META_BY_KEY.get(ckey) or {})
            meta["cache_hit"] = True
            meta["cache_key"] = ckey
            return hit, meta

    errors = []
    for name in order:
        try:
            fn = {"azure": _azure_synthesize, "edge": _edge_synthesize, "gtts": _gtts_synthesize}[name]
            raw_audio = fn(clean_chunk, voice, speed)
            fmt_info = validate_audio_payload(raw_audio)
            actual_fmt = fmt_info["output_format"]
            actual_mime = fmt_info["mime_type"]

            is_gtts = name == "gtts"
            gtts_lang = resolve_gtts_language(voice)[0] if is_gtts else locale
            eff_voice = f"gtts-{gtts_lang}-basic" if is_gtts else voice
            eff_model = "gtts-basic-v1" if is_gtts else model_ver

            ckey = build_cache_key(
                provider=name,
                model_version=eff_model,
                output_format=actual_fmt,
                locale=locale,
                voice_id=voice,
                rate_pct=rate_pct,
                normalized_text=clean_chunk,
            )
            meta: Dict[str, Any] = {
                "provider": name,
                "model": eff_model,
                "output_format": actual_fmt,
                "mime_type": actual_mime,
                "locale": locale,
                "requested_voice_id": voice,
                "voice_id": eff_voice,
                "is_neural": not is_gtts,
                "fallback_provider": "gtts" if is_gtts else "",
                "fallback_quality": "basic_emergency_non_neural" if is_gtts else "neural",
                "cache_hit": False,
                "cache_key": ckey,
            }
            _CACHE.put(ckey, raw_audio)
            _PROVIDER_BY_KEY[ckey] = name
            _META_BY_KEY[ckey] = meta
            return raw_audio, meta
        except Exception as e:
            errors.append(f"{name}: {str(e)[:120]}")
            log.warning("TTS provider %s failed: %s", name, e)
    raise RuntimeError("فشل توليد الصوت — " + " | ".join(errors))


def synthesize_with_metadata(
    text: str,
    voice_id: Optional[str] = None,
    speed: float = 1.0,
    auto_route_language: bool = False,
    detected_language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    يولّد الصوت مع تقسيم النصوص الطويلة إلى جمل متتالية دون اقتطاع صامت،
    ويُرجع البايتات مع كامل البيانات الوصفية (المزود، الصيغة، نوع MIME، اللغة، حالة الطوارئ، والقطع).
    """
    chunk_plan = split_into_sentence_chunks(text, max_chunk_chars=MAX_CHARS, speed=speed)
    if not chunk_plan["clean_text"] or not chunk_plan["chunks"]:
        raise ValueError("لا يوجد نص صالح للقراءة")

    if auto_route_language:
        routing = select_voice_for_language(chunk_plan["clean_text"], preferred_voice_id=voice_id, detected_language=detected_language)
        voice = routing["voice_id"]
        detected_lang = routing["detected_language"]
    else:
        voice = resolve_voice(voice_id)
        detected_lang = detected_language or detect_text_language(chunk_plan["clean_text"], hint_lang=ALLOWED_VOICE_IDS[voice]["lang"])

    chunk_results: List[Dict[str, Any]] = []
    audio_parts: List[bytes] = []
    first_meta: Dict[str, Any] = {}
    spd = max(0.5, min(2.0, float(speed or 1.0)))

    for idx, ch_txt in enumerate(chunk_plan["chunks"]):
        ch_bytes, ch_meta = _synthesize_single_chunk(ch_txt, voice, spd)
        if not first_meta:
            first_meta = ch_meta
        audio_parts.append(ch_bytes)
        ch_dur = round(max(0.2, (len(ch_txt) * 0.06) / spd), 2)
        chunk_results.append({
            "chunk_index": idx,
            "total_chunks": len(chunk_plan["chunks"]),
            "text": ch_txt,
            "audio_bytes": ch_bytes,
            "mime_type": ch_meta["mime_type"],
            "output_format": ch_meta["output_format"],
            "duration_sec": ch_dur,
            "provider": ch_meta["provider"],
        })

    out_fmt = first_meta.get("output_format", "mp3")
    merged_audio = _merge_audio_chunks(audio_parts, out_fmt)
    validate_audio_payload(merged_audio, expected_format=out_fmt)

    result = {
        "audio_bytes": merged_audio,
        "provider": first_meta.get("provider", ""),
        "model": first_meta.get("model", ""),
        "output_format": out_fmt,
        "mime_type": first_meta.get("mime_type", "audio/mpeg"),
        "requested_voice_id": str(voice_id or voice),
        "voice_id": first_meta.get("voice_id", voice),
        "canonical_voice_id": voice,
        "locale": first_meta.get("locale", ALLOWED_VOICE_IDS[voice]["lang"]),
        "detected_language": detected_lang,
        "is_neural": bool(first_meta.get("is_neural", True)),
        "fallback_provider": first_meta.get("fallback_provider", ""),
        "fallback_quality": first_meta.get("fallback_quality", "neural"),
        "cache_hit": all(c.get("cache_hit", False) for c in [first_meta]),
        "cache_key": first_meta.get("cache_key", ""),
        "clean_text": chunk_plan["clean_text"],
        "chunks": chunk_results,
        "chunk_count": len(chunk_results),
        "truncated": chunk_plan["truncated"],
        "warning": chunk_plan["warning"],
        "duration_sec": chunk_plan["estimated_duration_sec"],
    }

    synthesize.last_provider = result["provider"]  # type: ignore[attr-defined]
    synthesize.last_format = result["output_format"]  # type: ignore[attr-defined]
    synthesize.last_mime = result["mime_type"]  # type: ignore[attr-defined]
    synthesize.last_metadata = result  # type: ignore[attr-defined]
    return result


def synthesize(text: str, voice_id: Optional[str] = None, speed: float = 1.0) -> bytes:
    """يرجّع بايتات الصوت المولّدة والمتحققة. يرمي RuntimeError لو كل المزودات فشلت."""
    res = synthesize_with_metadata(text=text, voice_id=voice_id, speed=speed, auto_route_language=False)
    return res["audio_bytes"]


synthesize.last_provider = ""  # type: ignore[attr-defined]
synthesize.last_format = "mp3"  # type: ignore[attr-defined]
synthesize.last_mime = "audio/mpeg"  # type: ignore[attr-defined]
synthesize.last_metadata = {}  # type: ignore[attr-defined]


def get_provider_fn() -> Callable[[str, str, float], bytes]:
    """الدالة اللي بتتحقن بـ VoiceService(tts_provider_fn=...)."""
    return synthesize


# ---------------- سجل قدرات اللهجات (Phase C) ----------------
# status: "native_catalog" = صوت لهجة موجود بكتالوج Microsoft ومضاف عنا (لسا بدّو اختبار سمعي)
#         "fallback"       = ما في صوت أصلي مختبَر؛ منستخدم أقرب صوت ومنقول للمستخدم
# ما بنعلّم أي لهجة "verified" إلا بعد اختبار سمعي حقيقي (listening_verified).
DIALECT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "syrian":      {"name": "سوري",   "locale": "ar-SY", "voice": "ar-SY-LaithNeural", "status": "native_catalog", "listening_verified": False, "fallback_locale": "ar-LB"},
    "lebanese":    {"name": "لبناني", "locale": "ar-LB", "voice": "ar-LB-RamiNeural",  "status": "native_catalog", "listening_verified": False, "fallback_locale": "ar-SY"},
    "jordanian":   {"name": "أردني",  "locale": "ar-JO", "voice": "ar-JO-TaimNeural",  "status": "native_catalog", "listening_verified": False, "fallback_locale": "ar-SY"},
    "egyptian":    {"name": "مصري",   "locale": "ar-EG", "voice": "ar-EG-ShakirNeural","status": "native_catalog", "listening_verified": False, "fallback_locale": "ar-SA"},
    "gulf":        {"name": "خليجي",  "locale": "ar-AE", "voice": "ar-AE-HamdanNeural","status": "native_catalog", "listening_verified": False, "fallback_locale": "ar-SA"},
    "msa":         {"name": "فصحى",   "locale": "ar-SA", "voice": "ar-SA-HamedNeural", "status": "native_catalog", "listening_verified": False, "fallback_locale": "ar-SA"},
    "palestinian": {"name": "فلسطيني","locale": None,    "voice": "ar-JO-TaimNeural",  "status": "fallback", "listening_verified": False, "fallback_locale": "ar-JO"},
    "iraqi":       {"name": "عراقي",  "locale": None,    "voice": "ar-AE-HamdanNeural","status": "fallback", "listening_verified": False, "fallback_locale": "ar-AE"},
    "moroccan":    {"name": "مغربي",  "locale": None,    "voice": "ar-SA-HamedNeural", "status": "fallback", "listening_verified": False, "fallback_locale": "ar-SA"},
}


def get_dialect_capability(dialect: Optional[str]) -> Dict[str, Any]:
    """يرجّع الصوت المناسب للهجة + إفصاح صادق إذا كان تقريبي. ما بيخترع معرّفات."""
    key = str(dialect or "syrian").strip().lower()
    entry = DIALECT_REGISTRY.get(key) or DIALECT_REGISTRY["syrian"]
    voice = entry["voice"] if entry["voice"] in ALLOWED_VOICE_IDS else DEFAULT_VOICE
    approximate = entry["status"] != "native_catalog"
    note = ""
    if approximate:
        note = f"ما في صوت {entry['name']} مختبَر؛ عم نستخدم أقرب صوت ({voice}). النص بيضل باللهجة المطلوبة."
    elif not entry["listening_verified"]:
        note = "الصوت موجود بالكتالوج بس لسا ما انعمله اختبار سمعي."
    return {"dialect": key if key in DIALECT_REGISTRY else "syrian", "voice_id": voice, "approximate": approximate,
            "listening_verified": entry["listening_verified"], "status": entry["status"], "note": note}
