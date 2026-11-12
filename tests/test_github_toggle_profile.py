# -*- coding: utf-8 -*-
import os, tempfile, unittest
from unittest import mock

from ryn_ai_stack import GitHubFileManager as G


def mgr(tmp):
    return G(storage_path=os.path.join(tmp, "gh.json"))


class TestAnalyzeProject(unittest.TestCase):
    def test_flask_telegram_project(self):
        paths = ["bot.py", "webapp.py", "requirements.txt", "tests/test_a.py", "tests/test_b.py", "Procfile", "render.yaml",
                 ".github/workflows/ci.yml", "static/app.js", "README.md"]
        pr = G.analyze_project(paths, {"requirements.txt": "flask\npyTelegramBotAPI\ngoogle-genai\n", "README.md": "# مشروع بوت\nوصف"})
        self.assertEqual(pr["languages"][0][0], "Python")
        self.assertIn("Flask", pr["frameworks"])
        self.assertIn("Telegram bot (pyTelegramBotAPI)", pr["frameworks"])
        self.assertIn("Google Gemini SDK", pr["frameworks"])
        self.assertIn("pip", pr["package_managers"])
        self.assertIn("bot.py", pr["entry_points"])
        self.assertTrue(pr["has_tests"])
        self.assertIn("render.yaml", pr["deploy"])
        self.assertTrue(pr["ci"])
        self.assertIn("بوت", pr["readme_head"])

    def test_node_react_project_with_scripts_and_ignored_dirs(self):
        paths = ["package.json", "src/App.jsx", "src/index.js", "node_modules/x/index.js", "node_modules/y/z.js", "dist/bundle.js",
                 "src/App.test.js"]
        pj = '{"scripts":{"dev":"vite","build":"vite build"},"dependencies":{"react":"18","vite":"5"}}'
        pr = G.analyze_project(paths, {"package.json": pj})
        self.assertEqual(pr["total_files"], 4)  # بدون node_modules/dist
        self.assertIn("React", pr["frameworks"])
        self.assertIn("Vite", pr["frameworks"])
        self.assertEqual(pr["npm_scripts"], ["dev", "build"])
        self.assertTrue(pr["has_tests"])
        self.assertIn("npm/yarn", pr["package_managers"])

    def test_android_kotlin_project(self):
        paths = ["app/src/main/AndroidManifest.xml", "app/src/main/java/com/x/MainActivity.kt", "app/build.gradle.kts", "settings.gradle.kts"]
        pr = G.analyze_project(paths, {})
        self.assertEqual(pr["languages"][0][0], "Kotlin")
        self.assertIn("Android (Gradle)", pr["frameworks"])
        self.assertIn("Gradle (Kotlin DSL)", pr["package_managers"])
        self.assertIn("app/src/main/java/com/x/MainActivity.kt", pr["entry_points"])

    def test_rust_and_go(self):
        self.assertEqual(G.analyze_project(["Cargo.toml", "src/main.rs"], {})["languages"][0][0], "Rust")
        pr = G.analyze_project(["go.mod", "main.go", "pkg/a.go"], {})
        self.assertEqual(pr["languages"][0], ("Go", 2))
        self.assertIn("Go modules", pr["package_managers"])

    def test_dotnet_and_empty(self):
        self.assertIn(".NET", G.analyze_project(["App.csproj", "Program.cs"], {})["frameworks"])
        pr = G.analyze_project([], {})
        self.assertEqual(pr["total_files"], 0)
        self.assertFalse(pr["has_tests"])

    def test_format_is_arabic_and_compact(self):
        txt = G.format_project_profile(G.analyze_project(["a.py", "requirements.txt"], {"requirements.txt": "django"}))
        self.assertIn("اللغات", txt)
        self.assertIn("Django", txt)
        self.assertLess(len(txt), 1500)


class TestEditToggle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = mgr(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_on_and_persisted(self):
        self.assertTrue(self.m.get_edit_enabled("u"))
        self.m.set_edit_enabled("u", False)
        self.assertFalse(self.m.get_edit_enabled("u"))
        self.assertFalse(mgr(self.tmp.name).get_edit_enabled("u"))  # انحفظ على القرص
        self.assertTrue(self.m.get_edit_enabled("other"))  # لكل مستخدم لحاله

    def test_all_write_paths_blocked_when_off(self):
        self.m.set_edit_enabled("u", False)
        with mock.patch("ryn_ai_stack.requests") as rq:
            for fn in (lambda: self.m.save_file("a.py", "x", user_id="u"),
                       lambda: self.m.delete_file("a.py", user_id="u"),
                       lambda: self.m.push_project_files({"a.py": "x"}, user_id="u"),
                       lambda: self.m.ensure_branch("b", user_id="u")):
                with self.assertRaises(PermissionError):
                    fn()
            rq.put.assert_not_called(); rq.delete.assert_not_called(); rq.post.assert_not_called()

    def test_config_exposes_flag(self):
        self.m.set_edit_enabled("u", False)
        self.assertFalse(self.m.get_config("u")["edit_enabled"])

    def test_chat_actions_stripped_and_nothing_written_when_off(self):
        self.m.set_edit_enabled("u", False)
        ans = 'تمام.\n<github_patch path="a.py" commit="x">\n<<<< SEARCH\na\n====\nb\n>>>> REPLACE\n</github_patch>\n<github_delete path="b.py" commit="y"/>'
        cfg = {"repo": "o/r", "branch": "main", "has_token": True, "edit_enabled": False}
        with mock.patch.object(G, "get_config", return_value=cfg), mock.patch.object(G, "save_file") as sf, mock.patch.object(G, "delete_file") as df:
            out, changed = self.m.apply_chat_github_actions("u", "عدل", ans)
        self.assertNotIn("<github_", out)
        self.assertIn("مطفّي", out)
        self.assertFalse(changed)
        sf.assert_not_called(); df.assert_not_called()

    def test_chat_context_read_only_instruction_and_profile(self):
        self.m.set_edit_enabled("u", False)
        cfg = {"repo": "o/r", "branch": "main", "has_token": True, "edit_enabled": False}
        files = {"files": [{"path": "bot.py"}, {"path": "requirements.txt"}]}
        with mock.patch.object(G, "get_config", return_value=cfg), mock.patch.object(G, "list_files", return_value=files), \
                mock.patch.object(G, "get_file", return_value={"content": "flask", "size": 5, "sha": "s"}):
            ctx = self.m.build_chat_github_context("u", "شوف مشروعي على github")
        self.assertIn("قراءة فقط", ctx)
        self.assertIn("ملف تعريف المشروع", ctx)
        self.assertNotIn("<github_patch path=", ctx)

    def test_chat_context_has_edit_instructions_when_on(self):
        cfg = {"repo": "o/r", "branch": "main", "has_token": True, "edit_enabled": True}
        files = {"files": [{"path": "bot.py"}]}
        with mock.patch.object(G, "get_config", return_value=cfg), mock.patch.object(G, "list_files", return_value=files), \
                mock.patch.object(G, "get_file", return_value={"content": "x", "size": 1, "sha": "s"}):
            ctx = self.m.build_chat_github_context("u", "عدل ملف bot.py على github")
        self.assertIn("<github_patch path=", ctx)

    def test_context_finds_files_in_any_language(self):
        cfg = {"repo": "o/r", "branch": "main", "has_token": True, "edit_enabled": True}
        files = {"files": [{"path": "app/src/Main.kt"}, {"path": "go.mod"}]}
        with mock.patch.object(G, "get_config", return_value=cfg), mock.patch.object(G, "list_files", return_value=files), \
                mock.patch.object(G, "get_file", return_value={"content": "fun main(){}", "size": 12, "sha": "s"}):
            ctx = self.m.build_chat_github_context("u", "اشرحلي Main.kt")
        self.assertIn("fun main", ctx)


if __name__ == "__main__":
    unittest.main()
