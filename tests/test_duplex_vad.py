# -*- coding: utf-8 -*-
import json, os, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "voice", "duplex_vad.js")


def run_vad(frames, profile="normal", dt=30):
    script = (
        "const d=require(%s);const p=Object.assign({},d.PROFILES[%s]);"
        "const v=d.createVad(p);let fired=-1;"
        "const f=%s;for(let i=0;i<f.length;i++){if(v.step(f[i],%d)){fired=i;break;}}"
        "console.log(JSON.stringify({fired:fired}));"
    ) % (json.dumps(JS), json.dumps(profile), json.dumps(frames), dt)
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["fired"]


@unittest.skipUnless(shutil.which("node"), "node غير متوفر")
class TestDuplexVad(unittest.TestCase):
    def test_silence_never_fires(self):
        self.assertEqual(run_vad([0.004] * 200), -1)

    def test_steady_noise_absorbed(self):
        self.assertEqual(run_vad([0.03] * 200), -1)

    def test_short_click_ignored(self):
        frames = [0.004] * 40 + [0.3] * 3 + [0.004] * 40
        self.assertEqual(run_vad(frames), -1)

    def test_sustained_speech_fires(self):
        frames = [0.004] * 40 + [0.2] * 20
        idx = run_vad(frames)
        self.assertGreaterEqual(idx, 40)
        self.assertLess(idx, 60)

    def test_no_fire_during_warmup(self):
        self.assertEqual(run_vad([0.4] * 20), -1)  # 600ms < warmup 700ms

    def test_fires_only_once(self):
        script = (
            "const d=require(%s);const v=d.createVad(d.PROFILES.normal);let n=0;"
            "for(let i=0;i<100;i++){if(v.step(0.3,30))n++;}console.log(n);"
        ) % json.dumps(JS)
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.stdout.strip(), "1")

    def test_headphones_more_sensitive_than_speaker(self):
        frames = [0.004] * 40 + [0.06] * 30
        self.assertGreaterEqual(run_vad(frames, "headphones"), 0)
        self.assertEqual(run_vad(frames, "speaker"), -1)


class TestWebIntegration(unittest.TestCase):
    def test_script_inlined_and_hooked(self):
        with open(os.path.join(ROOT, "webapp.py"), encoding="utf-8") as _f:
            src = _f.read()
        self.assertIn("__RYN_DUPLEX_JS__", src)
        self.assertIn("RynDuplex.arm", src)
        self.assertIn("RynDuplex.close()", src)
        self.assertTrue(os.path.exists(JS))


if __name__ == "__main__":
    unittest.main()
