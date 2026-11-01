# -*- coding: utf-8 -*-
"""
نظام تحليل الطلب وإنشاء «بريميو للطلب» (Request Analysis & Premium Brief Blueprint Engine):
- المرحلة 1: يحلل طلب المستخدم بعمق (العناصر الأساسية، المتطلبات الضمنية للواقعية، إعدادات الكاميرا والعدسة، خريطة الإضاءة، وقواعد الحفاظ على الصورة الأصلية عند التعديل).
- المرحلة 2: ينشئ «بريميو الطلب» (`PremiumRequestBrief`) بالعربية والإنجليزية قبل إرساله لنموذج توليد أو تعديل الصور (`timbrooks/instruct-pix2pix` / `Gemini` / `FLUX`).
"""
from __future__ import annotations
from typing import Optional, Callable, List
from .schemas import SceneSpec, IdentityLock, EditPlan, PremiumRequestBrief
from .realism_engine import strip_ai_buzzwords


def build_premium_request_brief(
    raw_request: str,
    scene: SceneSpec,
    identity_lock: IdentityLock,
    edit_plan: EditPlan,
    final_prompt_en: str,
    ask_llm_fn: Optional[Callable[[str, str], str]] = None,
) -> PremiumRequestBrief:
    """
    بناء «بريميو الطلب» المتكامل بعد تحليل المشهد وقبل توليد أو تعديل الصورة.
    """
    req = (raw_request or "").strip()
    wf = edit_plan.workflow_type if edit_plan.is_edit else "text_to_image"

    implicit_reqs: List[str] = []
    micro_anchors: List[str] = []

    if scene.style in ("photorealistic", "cinematic", "product_photography"):
        implicit_reqs.append("نطاق ديناميكي طبيعي لمستشعر Full-Frame 35mm بدون حرق للإضاءة أو تسطيح للظلال")
        implicit_reqs.append("ظلال تلامس فيزيائية (Ambient Occlusion) تحت كل عنصر يلامس الأرض أو الطاولة")
        micro_anchors.append("حبيبات فيلم 35mm عضوية دقيقة وتدرج عزل بصري طبيعي للعدسة")

        if scene.has_humans:
            implicit_reqs.append("تشتت ضوئي تحت الجلد (Subsurface Scattering) ومسام طبيعية غير بلاستيكية")
            micro_anchors.append("مسام جلد طبيعية، وبر وجه دقيق (Peach Fuzz)، ولمعة قرنية عين موحدة الاتجاه")
            if scene.has_visible_hands:
                implicit_reqs.append("تشريح يدين دقيق بـ 5 أصابع كاملة مع الإبهام وضغط قبضة فيزيائي")
                micro_anchors.append("مفاصل أصابع وأظافر طبيعية وتلامس فيزيائي مع الجسم الممسوك")
            else:
                implicit_reqs.append("عدم إجبار ظهور اليدين إذا كانتا خارج كادر اللقطة")
        if scene.has_animals:
            implicit_reqs.append("تشريح حيواني سليم وتوزع خصلات الفرو باتجاه النمو الطبيعي")
            micro_anchors.append("فرو متعدد الطبقات وعيون ذات انعكاس ضوئي طبيعي")
        if scene.has_clothing:
            implicit_reqs.append("نسيج قماش محبوك بوضوح مع طيات تتبع الجاذبية وحركة المفاصل")
            micro_anchors.append("خيوط نسيج الملبس، الدرزات، والانثناءات الطبيعية")
        if scene.has_liquids_or_glass:
            implicit_reqs.append("انكسار ضوئي فيزيائي عبر الماء والزجاج وانعكاسات Fresnel")
            micro_anchors.append("انحناء سطح السائل (Meniscus) وانكسار الخلفية عبر الكأس")
        if scene.has_dirt_sweat_or_wear and not scene.is_clean_studio_or_portrait:
            implicit_reqs.append("توزع الطين أو البلل وفق منطق التلامس والجاذبية فقط دون تلويث عشوائي")
        elif scene.is_clean_studio_or_portrait:
            implicit_reqs.append("بورتريه نظيف تماماً خالٍ من أي أوساخ أو طين أو عرق عشوائي")

    camera_plan = (
        f"عدسة {scene.lens_mm} بفتحة {getattr(scene, 'aperture', 'f/2.0')} وزاوية {scene.camera_angle} "
        f"مع عزل بصري تدريجي يركز على العنصر الرئيسي"
    )
    lighting_plan = f"إضاءة ({scene.lighting}) بمصدر ضوء موحد الاتجاه وظلال متناسقة"

    preservation_rules: List[str] = []
    pix2pix_cmd = ""
    if edit_plan.is_edit:
        preservation_rules = list(edit_plan.protected_elements[:5])
        pix2pix_cmd = edit_plan.instruct_pix2pix_instruction_en or final_prompt_en[:350]
        if ask_llm_fn is not None:
            try:
                sys_p2p = (
                    "You write concise imperative English editing instructions for the `timbrooks/instruct-pix2pix` model. "
                    "Translate the user's edit request into ONE direct English imperative sentence (15 to 40 words) starting with an action verb "
                    "(e.g., 'Change the shirt color to navy blue while keeping the face and background intact, realistic lighting and fabric texture'). "
                    "Output ONLY the English sentence."
                )
                llm_cmd = ask_llm_fn(req, sys_p2p)
                cleaned_cmd = strip_ai_buzzwords((llm_cmd or "").strip().strip('"'))
                if cleaned_cmd and len(cleaned_cmd) >= 10:
                    pix2pix_cmd = cleaned_cmd[:350]
                    edit_plan.instruct_pix2pix_instruction_en = pix2pix_cmd
            except Exception:
                pass

    mode_ar = "تعديل صورة مرجعية" if edit_plan.is_edit else "توليد صورة فوتوغرافية جديدة"
    summary_parts = [
        f"📋 بريميو الطلب ({mode_ar}): الهدف «{scene.user_goal or req}»",
        f"📷 الكاميرا والعدسة: {camera_plan}",
        f"💡 الإضاءة: {lighting_plan}",
    ]
    if micro_anchors:
        summary_parts.append("🔬 مرتكزات الواقعية: " + "، ".join(micro_anchors[:3]))
    if edit_plan.is_edit and preservation_rules:
        summary_parts.append("🔒 العناصر المحمية أثناء التعديل: " + "، ".join(preservation_rules[:3]))

    return PremiumRequestBrief(
        raw_request=req,
        analyzed_goal=scene.user_goal or req,
        workflow_mode=wf,
        subject_breakdown=list(scene.subjects),
        implicit_realism_requirements=implicit_reqs,
        camera_and_optics_plan=camera_plan,
        lighting_and_shadow_plan=lighting_plan,
        micro_texture_anchors=micro_anchors,
        edit_preservation_rules=preservation_rules,
        instruct_pix2pix_command=pix2pix_cmd,
        brief_summary_ar=" | ".join(summary_parts),
        premium_prompt_en=final_prompt_en,
    )
