# -*- coding: utf-8 -*-
"""filegen — توليد ملفات التحميل لموقع Ryn:
- detect_file_request(text): بيحدد امتداد الملف المطلوب من رسالة المستخدم (أو None).
- make_file(ext, text, request_text): بيرجّع (اسم الملف, MIME, البيانات).

الصيغ المدعومة: txt / md / html / json / csv / docx (Word) + ملفات الكود
(py, js, ts, css, sql, java, cpp, cs, php, sh, lua, yaml, xml).
PDF غير مدعوم (البوت بيقترح بديل)."""
import io
import json
import re
import time
import zipfile
from html import escape as _escape_html

# ---------- كشف نوع الملف من طلب المستخدم ----------
_INTENT_AR = (
    "ملف", "ملفات", "مستند", "وثيقة", "كملف", "بملف", "تحميل", "حمّل", "حملي",
    "نزّل", "نزلي", "احفظ", "حفظ", "صفحة", "موقع", "برنامج", "تطبيق", "ابلكيشن",
)
_INTENT_EN = re.compile(r"\b(file|files|save|download)\b", re.I)

# أفعال طلب: لملف بدون صيغة واضحة (مثل «بدي ملف») بيعمل TXT
_REQ = ("اعمل", "سوي", "اكتب", "بدي", "ابعت", "نشئ", "حول", "صيغ", "اطلع",
        "حط", "خلي", "طلع", "فوت", "جهز")

# أنواع بتنباع بذاتها (طلبها يعني ملف) حتى بدون كلمة «ملف» — بس إذا مو سؤال
_SELF_EVIDENT = (
    (r"word|docx|\bdoc\b|وورد|(?<![\u0600-\u06FF])ورد(?![\u0600-\u06FF])", "docx"),
    (r"csv|excel|اكسل|إكسل|اكسيل", "csv"),
    (r"json|جسون", "json"),
)

# كل الصيغ بالترتيب (الأولوية للأول). صيغ الكود بدها كلمة نية (ملف/حمّل/...)
_LANG = (
    (r"جافاسكربت|جافا سكربت|javascript|node\.?js|typescript", "js"),
    (r"type\s?script", "ts"),
    (r"بايثون|python", "py"),
    (r"html|htm|صفحة ويب", "html"),
    (r"css|ستايل|stylesheet", "css"),
    (r"sql|استعلام|قاعدة بيانات", "sql"),
    (r"markdown|ماركداون|\bmd\b", "md"),
    (r"\btxt\b|ملف نصي|نصي", "txt"),
    (r"c\+\+|cpp", "cpp"),
    (r"c#|csharp|سي شارب", "cs"),
    (r"جافا|\bjava\b", "java"),
    (r"\bphp\b", "php"),
    (r"\bbash\b|shell|شيل", "sh"),
    (r"\blua\b|لوا", "lua"),
    (r"\byaml\b|yml", "yaml"),
    (r"\bxml\b", "xml"),
)

_QUESTION = re.compile(
    r"[؟?]|^(شو|شنو|ليش|لماذا|كيف|وين|مين|متى|امتى|هل|what|why|how|who|where|when|is|are|do)\s",
    re.I,
)


def _match(patterns, low):
    for pat, ext in patterns:
        if re.search(pat, low):
            return ext
    return None


def _has_intent(low):
    return any(w in low for w in _INTENT_AR) or bool(_INTENT_EN.search(low))


def detect_file_request(text):
    """بيرجّع امتداد الملف المطلوب (بدون نقطة) أو None إذا الطلب مو ملف."""
    low = (text or "").strip().lower()
    if not low:
        return None
    if re.search(r"\bpdf\b|بي دي اف|بي دي إف", low):
        return None  # PDF غير مدعوم؛ البوت بيقترح البديل بالشات
    if _QUESTION.search(low) and not _has_intent(low):
        return None
    ev = _match(_SELF_EVIDENT, low)
    if ev:
        return ev
    if not _has_intent(low):
        return None
    ext = _match(_LANG, low)
    if ext:
        return ext
    if any(w in low for w in _REQ):
        return "txt"  # طلب ملف واضح بدون صيغة → نص عادي
    return None

# ---------- توليد الملفات ----------
_MIME = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "py": "text/x-python; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "ts": "text/plain; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "sql": "text/plain; charset=utf-8",
    "java": "text/x-java; charset=utf-8",
    "cpp": "text/plain; charset=utf-8",
    "cs": "text/plain; charset=utf-8",
    "php": "text/plain; charset=utf-8",
    "sh": "text/plain; charset=utf-8",
    "lua": "text/plain; charset=utf-8",
    "yaml": "text/yaml; charset=utf-8",
    "xml": "text/xml; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_CODE_EXTS = {"py", "js", "ts", "css", "sql", "java", "cpp", "cs", "php", "sh", "lua", "yaml", "xml"}


def extract_code(text, lang=None):
    """بسحب محتوى بلوك الكود ``` (بنفس اللغة إذا انعطت) وإلا بيرجّع النص كما هو."""
    t = (text or "").strip()
    m = None
    if lang:
        m = re.search(r"```\s*" + re.escape(lang) + r"[^\n]*\n([\s\S]*?)```", t, re.I)
    if not m:
        m = re.search(r"```[^\n]*\n([\s\S]*?)```", t)
    return m.group(1).strip() if m else t


def _strip_fences(text):
    """بيشيل علامات ``` وبخلّي محتوى البلوك جواه."""
    def rep(m):
        inner = re.sub(r"^```[^\n]*\n?", "", m.group(0))
        return re.sub(r"\n?```\s*$", "", inner)
    return re.sub(r"```[^\n]*\n[\s\S]*?```", rep, text or "")


def _clean_plain(text):
    t = _strip_fences(text)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    lines = [ln.rstrip() for ln in t.strip().splitlines()]
    return "\n".join(lines).strip()


def _first_heading(text):
    m = re.search(r"^#+\s*(.+)", (text or ""), re.M)
    return m.group(1).strip()[:80] if m else ""


def _slug(request_text, ext):
    """اسم ملف لطيف من أول كلمات الطلب + تاريخ ووقت."""
    stop = {"اعمل", "اعملي", "عمل", "سوي", "اكتب", "اكتبلي", "بدي", "ابعت", "ابعتلي",
            "انشئ", "حول", "صيغ", "اطلع", "اطلعلي", "حط", "حطلي", "خلي", "طلع", "طلعلي",
            "فوت", "جهز", "جهزلي", "ملف", "ملفات", "كملف", "بملف", "مستند", "وثيقة",
            "حمّل", "حملي", "نزّل", "نزلي", "احفظ", "حفظ", "تحميل", "عن", "على", "لي",
            "ليي", "اياي", "الي", "يلي", "لازم", "ممكن", "مشان", "file", "files",
            "download", "save", "the", "a", "an", "of", "to", "for", "me", "my", "in"}
    t = re.sub(r"[\u064B-\u0652\u0640]", "", request_text or "")  # شيل التشكيل والتطويل
    t = re.sub(r"[^\w\u0600-\u06FF]+", " ", t).strip()
    words = [w for w in t.split() if w.lower() not in stop][:5]
    base = "-".join(words)[:44] or "file"
    return f"{base}-{time.strftime('%y%m%d-%H%M')}.{ext}"


def _run(style, txt, sz=None):
    t = _escape_html(txt, quote=False)
    p = []
    if style == "b":
        p.append("<w:b/>")
    if style == "code":
        p.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>')
        p.append('<w:shd w:val="clear" w:fill="F1F3F7"/>')
    if sz:
        p.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (sz, sz))
    rpr = ("<w:rPr>" + "".join(p) + "</w:rPr>") if p else ""
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, t)


def _inline(txt):
    """نص فقرة مع دعم **غامق** و `كود`."""
    out = []
    for part in re.split(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)", txt or ""):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append(_run("b", part[2:-2]))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(_run("code", part[1:-1]))
        elif part:
            out.append(_run("n", part))
    return "".join(out)


def build_docx(text, title=""):
    """ملف Word حقيقي (.docx) من نص بـ Markdown خفيف — بدون أي مكتبة خارجية."""
    paras = []
    in_code = False
    for raw in (text or "").splitlines():
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            paras.append(("code", raw.rstrip()))
            continue
        line = raw.strip()
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            paras.append(("h", len(m.group(1)), m.group(2)))
            continue
        m2 = re.match(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.*)", raw)
        if m2:
            paras.append(("b", m2.group(1)))
            continue
        paras.append(("p", raw.rstrip()))

    body = []
    for p in paras:
        if p[0] == "code":
            body.append('<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
                        + _run("code", p[1]) + "</w:p>")
        elif p[0] == "h":
            lvl, txt = p[1], p[2]
            sz = {1: 36, 2: 32, 3: 28}.get(lvl, 26)
            body.append('<w:p><w:pPr><w:bidi/><w:spacing w:before="200" w:after="120"/></w:pPr>'
                        + _run("b", txt, sz=sz) + "</w:p>")
        elif p[0] == "b":
            body.append('<w:p><w:pPr><w:bidi/><w:ind w:left="360"/><w:spacing w:after="40"/></w:pPr>'
                        + _inline("• " + p[1]) + "</w:p>")
        elif p[1].strip():
            body.append('<w:p><w:pPr><w:bidi/><w:spacing w:after="80"/></w:pPr>'
                        + _inline(p[1]) + "</w:p>")
        else:
            body.append('<w:p><w:pPr><w:bidi/></w:pPr></w:p>')

    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           "<w:body>" + "".join(body)
           + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
             '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
             'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
           "</w:body></w:document>")
    return _zip_docx(xml, title)


def _zip_docx(document_xml, title=""):
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
          "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>%s</dc:title>"
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            "</cp:coreProperties>") % (_escape_html(title or ""),
                                       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           "<Application>Ryn</Application></Properties>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
    return buf.getvalue()


_HTML_TMPL = (
    '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>{title}</title>"
    "<style>body{{font-family:'Segoe UI',Tahoma,sans-serif;background:#0b0a1e;color:#eef0ff;"
    "max-width:760px;margin:30px auto;padding:24px;line-height:1.9}}"
    "pre{{white-space:pre-wrap;font-family:inherit}}</style>"
    "</head><body><pre>{body}</pre></body></html>"
)


def make_file(ext, text, request_text=""):
    """بيرجّع (اسم الملف, MIME, البيانات)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty")
    ext = (ext or "").lower().lstrip(".")
    mime = _MIME.get(ext)
    if not mime:
        raise ValueError("unsupported: " + ext)

    if ext == "docx":
        title = _first_heading(text) or _slug(request_text, "docx")[:-5].replace("-", " ")
        return _slug(request_text, ext), mime, build_docx(text, title=title)

    if ext in _CODE_EXTS:
        return _slug(request_text, ext), mime, extract_code(text, ext).encode("utf-8")

    if ext == "html":
        content = extract_code(text, "html")
        if not re.search(r"<\s*(!doctype|html)", content, re.I):
            content = _HTML_TMPL.format(
                title=_escape_html(_first_heading(text) or "ملف Ryn"),
                body=_escape_html(_strip_fences(text)))
        return _slug(request_text, ext), mime, content.encode("utf-8")

    if ext == "json":
        content = extract_code(text, "json")
        try:
            content = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
        except Exception:
            pass  # بنحفظه كما هو إذا مو JSON سليم
        return _slug(request_text, ext), mime, content.encode("utf-8")

    if ext == "csv":
        return _slug(request_text, ext), mime, extract_code(text, "csv").encode("utf-8")

    if ext == "md":
        return _slug(request_text, ext), mime, text.encode("utf-8")

    if ext == "txt":
        return _slug(request_text, ext), mime, _clean_plain(text).encode("utf-8")

    return _slug(request_text, ext), mime, text.encode("utf-8")
