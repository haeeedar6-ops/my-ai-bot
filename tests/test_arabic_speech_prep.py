# -*- coding: utf-8 -*-
import unittest
from tts_engine import clean_for_speech, number_to_arabic_words as w, prepare_arabic_for_speech as prep


class TestNumbers(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(w(0), "صفر")
        self.assertEqual(w(5), "خمسة")
        self.assertEqual(w(11), "أحد عشر")
        self.assertEqual(w(21), "واحد وعشرين")
        self.assertEqual(w(100), "مئة")
        self.assertEqual(w(250), "مئتين وخمسين")

    def test_thousands_millions(self):
        self.assertEqual(w(1000), "ألف")
        self.assertEqual(w(2000), "ألفين")
        self.assertEqual(w(3000), "ثلاثة آلاف")
        self.assertEqual(w(12000), "اثنا عشر ألف")
        self.assertEqual(w(1500000), "مليون وخمسمئة ألف")
        self.assertEqual(w(2024), "ألفين وأربعة وعشرين")

    def test_negative(self):
        self.assertTrue(w(-3).startswith("ناقص"))


class TestPrep(unittest.TestCase):
    def test_digits_in_arabic_text(self):
        out = prep("عندي 3 تفاحات و25 برتقالة")
        self.assertNotRegex(out, r"\d")
        self.assertIn("ثلاثة", out)

    def test_arabic_indic_digits(self):
        self.assertNotRegex(prep("السعر ٥٠ ليرة"), r"[0-9٠-٩]")

    def test_percent_currency_decimal(self):
        out = prep("زاد 15% وصار 2.5 دولار")
        self.assertIn("بالمية", out)
        self.assertIn("فاصلة", out)
        self.assertNotRegex(out, r"\d")
        self.assertIn("دولار", prep("دفعت $20 اليوم"))

    def test_ranges_and_tatweel(self):
        self.assertIn("إلى", prep("من 5-10 دقائق"))
        self.assertNotIn("ـ", prep("جمـــيل"))

    def test_english_text_untouched(self):
        self.assertEqual(prep("I have 3 apples"), "I have 3 apples")

    def test_mixed_mostly_english_untouched(self):
        self.assertIn("2024", prep("Release version 2024 جاهز"))

    def test_full_pipeline(self):
        out = clean_for_speech("**مرحبا** عندك 5 رسائل جديدة 🎉")
        self.assertNotRegex(out, r"[*\d]")
        self.assertIn("خمسة", out)

    def test_code_block_still_replaced(self):
        self.assertIn("كود برمجي", clean_for_speech("جرب هيك:\n```print(1)```"))


if __name__ == "__main__":
    unittest.main()
