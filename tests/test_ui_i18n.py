"""اختبار ترجمة الواجهة: نشغّل منطق rynTr من الصفحة الفعلية بـ node (بدون متصفح) ونتأكد:
- كل نص عربي ظاهر له ترجمة إنجليزية.
- الترجمة الإنجليزية ما بتحتوي حروف عربية.
- أسماء الموديلات و Ryn ما بتنترجم.
- رسائل التوست مع أجزاء متغيّرة بتنترجم بالبادئة.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AR = re.compile(r"[\u0600-\u06FF]")


@unittest.skipUnless(shutil.which("node"), "node غير متوفر")
class UiTranslation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("WEB_PASSWORD", "x")
        os.environ.setdefault("RYN_DATA_DIR", tempfile.mkdtemp())
        import webapp
        html = webapp.INDEX_HTML.replace("__APP_NAME__", "Ryn")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
        js = max(scripts, key=len)
        start = js.index("let rynUiLang=")
        end = js.index("function applyRynUiLanguage(){")
        snippet = js[start:end]
        stub = (
            "const __out={};"
            "const localStorage={getItem(){return null},setItem(){}};"
            "const document={getElementById(){return null},documentElement:{},title:''};"
            "const NodeFilter={};const requestAnimationFrame=f=>0;"
            "class MutationObserver{constructor(){}observe(){}disconnect(){}};"
        )
        probe = (
            stub + snippet +
            "\nrynUiLang='en';"
            "__out.keys=Object.keys(rynUiText);"
            "__out.map=Object.fromEntries(__out.keys.map(k=>[k,rynTr(k)]));"
            "__out.model=rynTr('Gemini 3.8 Flash');"
            "__out.ryn=rynTr('Ryn mix المحسّن');"
            "__out.toast=rynTr('تخطّيت: fileX.pdf');"
            "__out.toast2=rynTr('فشل الرفع: 500 error');"
            "__out.untouched=rynTr('نص مو موجود بالقاموس');"
            "rynUiLang='ar';__out.arMode=rynTr('أهلاً فيك 👋');"
            "console.log(JSON.stringify(__out));"
        )
        cls.dir = tempfile.mkdtemp()
        path = os.path.join(cls.dir, "probe.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(probe)
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[:800])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_every_dictionary_key_translates_to_english(self):
        for k in self.out["keys"]:
            en = self.out["map"][k]
            self.assertIsInstance(en, str)
            self.assertNotEqual(en.strip(), "", k)

    def test_english_values_have_no_arabic_letters(self):
        leftovers = [(k, v) for k, v in self.out["map"].items() if AR.search(v)]
        self.assertEqual(leftovers, [], leftovers[:5])

    def test_model_names_and_brand_are_not_translated(self):
        self.assertEqual(self.out["model"], "Gemini 3.8 Flash")
        self.assertIn("Ryn", self.out["ryn"])
        self.assertIn("Enhanced Ryn mix", self.out["ryn"])

    def test_prefix_toasts_translate_and_keep_variable_part(self):
        self.assertEqual(self.out["toast"], "Skipped: fileX.pdf")
        self.assertEqual(self.out["toast2"], "Upload failed: 500 error")

    def test_unknown_text_is_left_alone_and_arabic_mode_is_untouched(self):
        self.assertEqual(self.out["untouched"], "نص مو موجود بالقاموس")
        self.assertEqual(self.out["arMode"], "أهلاً فيك 👋")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "node غير متوفر")
class UiFragments(unittest.TestCase):
    def test_fragments_inside_ui_text_translate_with_word_boundaries(self):
        import webapp
        html = webapp.INDEX_HTML.replace("__APP_NAME__", "Ryn")
        js = max(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S), key=len)
        snippet = js[js.index("let rynUiLang="):js.index("function applyRynUiLanguage(){")]
        stub = ("const localStorage={getItem(){return null},setItem(){}};"
                "const document={getElementById(){return null},documentElement:{},title:''};"
                "const NodeFilter={};const requestAnimationFrame=f=>0;"
                "class MutationObserver{constructor(){}observe(){}disconnect(){}};")
        probe = stub + snippet + (
            "\nrynUiLang='en';"
            "const out=[rynFrag('KB | النوع: PDF'),rynFrag('معلومة (10 دقايق) محفوظة'),rynFrag('ملفات الكمبيوتر'),rynFrag('تيم — شامي أردني ✨')];"
            "console.log(JSON.stringify(out));")
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "f.js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(probe)
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[:500])
            a, b, c, e = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(a, "KB | Type: PDF")
        self.assertEqual(b, "معلومة (10 minutes) محفوظة")
        self.assertEqual(c, "ملفات الكمبيوتر")        # ما بينكسر داخل كلمة أطول
        self.assertIn("Tim", e)
