# -*- coding: utf-8 -*-
import json, os, shutil, subprocess, unittest

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voice", "live_client.js")


def node(code):
    out = subprocess.run(["node", "-e", f"const L=require({json.dumps(JS)});{code}"], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node غير متوفر")
class TestLiveClientPure(unittest.TestCase):
    def test_downsample_48k_to_16k_sizes_and_range(self):
        r = node("""const d=L._makeDown(48000,16000,640);let tot=0,mx=0;
        for(let k=0;k<50;k++){const f=new Float32Array(960);for(let i=0;i<960;i++)f[i]=Math.sin((k*960+i)/20);
        for(const c of d.push(f)){tot+=c.length;for(const v of c)mx=Math.max(mx,Math.abs(v));}}
        console.log(JSON.stringify({tot:tot,mx:mx}))""")
        # 50*960 عينة @48k = 16000 عينة @16k → 25 إطار × 640
        self.assertEqual(r["tot"], 16000)
        self.assertLessEqual(r["mx"], 32768)
        self.assertGreater(r["mx"], 1000)

    def test_downsample_44100_ratio(self):
        r = node("""const d=L._makeDown(44100,16000,640);let tot=0;
        for(let k=0;k<100;k++){for(const c of d.push(new Float32Array(441)))tot+=c.length;}
        console.log(JSON.stringify({tot:tot}))""")
        # 44100 عينة → ~16000 عينة (بحدود إطار ناقص)
        self.assertTrue(15360 <= r["tot"] <= 16000, r)

    def test_clipping_safe(self):
        r = node("""const d=L._makeDown(16000,16000,4);const o=d.push(new Float32Array([5,-5,0.5,-0.5]));
        console.log(JSON.stringify(Array.from(o[0])))""")
        self.assertEqual(r[0], 32767)
        self.assertEqual(r[1], -32768)

    def test_pcm16_to_float32(self):
        r = node("""const ab=new Int16Array([0,16384,-16384,32767]).buffer;
        console.log(JSON.stringify(Array.from(L._pcm16ToFloat32(ab))))""")
        self.assertAlmostEqual(r[1], 0.5, places=3)
        self.assertAlmostEqual(r[2], -0.5, places=3)
        self.assertEqual(r[0], 0)

    def test_pcm16_odd_length_and_empty(self):
        r = node("""console.log(JSON.stringify([L._pcm16ToFloat32(new ArrayBuffer(3)).length,L._pcm16ToFloat32(new ArrayBuffer(0)).length,L._pcm16ToFloat32(new ArrayBuffer(1)).length]))""")
        self.assertEqual(r, [1, 0, 0])

    def test_supported_false_without_browser(self):
        r = node("console.log(JSON.stringify(L.supported()))")
        self.assertFalse(r)


if __name__ == "__main__":
    unittest.main()


HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_client_harness.js")


@unittest.skipUnless(shutil.which("node"), "node غير متوفر")
class TestLiveClientBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["node", HARNESS, JS], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr
        cls.r = json.loads(out.stdout)
        assert "crash" not in cls.r, cls.r

    def test_handshake(self):
        self.assertTrue(self.r["ok"])
        self.assertEqual(self.r["url"], "wss://ryn.test/api/v2/live/ws")
        self.assertEqual(self.r["start"]["type"], "start")
        self.assertEqual(self.r["start"]["voice"], "Orus")
        self.assertEqual(self.r["start"]["history"], [{"role": "user", "text": "x"}])

    def test_gapless_playback_and_state(self):
        self.assertEqual(self.r["nSources"], 2)
        self.assertTrue(self.r["gapless"])
        self.assertTrue(self.r["speakingState"])

    def test_server_interrupt_stops_audio_but_allows_new(self):
        self.assertTrue(self.r["stoppedOnInterrupt"])
        self.assertEqual(self.r["playedAfterInterrupt"], 3)

    def test_local_interrupt_drops_until_turn_complete(self):
        self.assertTrue(self.r["droppedAfterLocal"])
        self.assertTrue(self.r["resumedAfterTurn"])

    def test_mute_toggles_track(self):
        self.assertTrue(self.r["trackDisabled"])
        self.assertTrue(self.r["trackEnabled"])

    def test_events_and_cleanup(self):
        ev = self.r["events"]
        for e in ("ready", "u:مرحبا", "turn:سؤال|جواب", "err:connect_failed", "tc"):
            self.assertIn(e, ev)
        self.assertTrue(self.r["sentEnd"])
        self.assertTrue(self.r["micReleased"])
        self.assertFalse(self.r["active"])
