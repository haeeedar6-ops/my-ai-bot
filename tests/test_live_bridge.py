# -*- coding: utf-8 -*-
import asyncio, json, os, types, unittest
from unittest import mock

from voice import live_bridge as lb
from voice.live_bridge import IDLE, LiveBridge, LiveLimits, LiveRegistry


class FakeSession:
    def __init__(self, script=None, model="fake-live", fail_after=None):
        self.model, self.audio, self.texts, self.closed = model, [], [], False
        self.q = asyncio.Queue()
        for ev in (script or []):
            self.q.put_nowait(ev)
        self.fail_after = fail_after

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def send_text(self, t):
        self.texts.append(t)

    async def events(self):
        n = 0
        while True:
            ev = await self.q.get()
            if ev == "CLOSE":
                raise ConnectionError("closed by server")
            if ev == "END":
                return
            n += 1
            yield ev

    async def aclose(self):
        self.closed = True


class Harness:
    def __init__(self, sessions, limits=None):
        self.sessions = list(sessions)
        self.opened = []
        self.sent = []
        self.inbox = asyncio.Queue()
        self.limits = limits or LiveLimits()
        self.limits.idle_timeout_sec = 5

    async def open_session(self, si, voice):
        self.opened.append((si, voice))
        if not self.sessions:
            raise ConnectionError("no more")
        s = self.sessions.pop(0)
        if isinstance(s, Exception):
            raise s
        return s

    async def recv(self):
        try:
            return await asyncio.wait_for(self.inbox.get(), timeout=0.05)
        except asyncio.TimeoutError:
            return IDLE

    async def send(self, x):
        self.sent.append(x)

    def msgs(self):
        return [json.loads(x) for x in self.sent if isinstance(x, str)]

    def run(self, start=None, driver=None):
        async def main():
            br = LiveBridge(self.open_session, self.limits)
            task = asyncio.ensure_future(br.run(self.recv, self.send, start or {}))
            if driver:
                await driver(self)
            return await asyncio.wait_for(task, timeout=5)
        return asyncio.run(main())


class TestBridge(unittest.TestCase):
    def test_audio_forwarded_both_ways_and_turns_flushed(self):
        sess = FakeSession([
            {"kind": "user_text", "text": "مرحبا "}, {"kind": "audio", "data": b"\x01\x02"},
            {"kind": "bot_text", "text": "أهلين"}, {"kind": "turn_complete"},
        ])
        h = Harness([sess])

        async def drv(h):
            await h.inbox.put(b"\x00" * 640)
            await asyncio.sleep(0.2)
            await h.inbox.put(json.dumps({"type": "end"}))
        stats = h.run({}, drv)
        self.assertEqual(sess.audio, [b"\x00" * 640])
        self.assertIn(b"\x01\x02", h.sent)
        turns = [m for m in h.msgs() if m["type"] == "turn"]
        self.assertEqual(turns, [{"type": "turn", "user": "مرحبا", "bot": "أهلين"}])
        self.assertEqual(stats["end"], "client_end")
        self.assertTrue(sess.closed)

    def test_interrupted_event_forwarded(self):
        sess = FakeSession([{"kind": "interrupted"}])
        h = Harness([sess])

        async def drv(h):
            await asyncio.sleep(0.2)
            await h.inbox.put(None)
        h.run({}, drv)
        self.assertIn("interrupted", [m["type"] for m in h.msgs()])

    def test_connect_failure_reported_without_secret(self):
        h = Harness([ConnectionError("bad key AIza" + "x" * 35)])
        stats = h.run({})
        self.assertEqual(stats["end"], "connect_failed")
        blob = json.dumps(h.msgs())
        self.assertIn("connect_failed", blob)
        self.assertNotIn("x" * 35, blob)

    def test_server_close_triggers_reconnect_with_context(self):
        s1 = FakeSession([{"kind": "user_text", "text": "سؤال"}, {"kind": "bot_text", "text": "جواب"}, {"kind": "turn_complete"}, "CLOSE"])
        s2 = FakeSession([])
        h = Harness([s1, s2])

        async def drv(h):
            await asyncio.sleep(0.4)
            await h.inbox.put(b"\x00" * 320)
            await asyncio.sleep(0.2)
            await h.inbox.put(None)
        stats = h.run({}, drv)
        self.assertEqual(stats["reconnects"], 1)
        types_ = [m["type"] for m in h.msgs()]
        self.assertIn("reconnecting", types_)
        self.assertTrue(any(m.get("resumed") for m in h.msgs() if m["type"] == "ready"))
        self.assertIn("سؤال", h.opened[1][0])  # ملخص الحوار انحقن بالجلسة الجديدة
        self.assertEqual(s2.audio, [b"\x00" * 320])

    def test_reconnect_limit(self):
        sessions = [FakeSession(["CLOSE"]) for _ in range(4)]
        lim = LiveLimits()
        lim.max_reconnects = 2
        h = Harness(sessions, lim)
        stats = h.run({})
        self.assertEqual(stats["end"], "max_reconnects")

    def test_reconnect_failure_ends_cleanly(self):
        h = Harness([FakeSession(["CLOSE"]), ConnectionError("down")])
        stats = h.run({})
        self.assertEqual(stats["end"], "reconnect_failed")

    def test_oversized_frame_dropped(self):
        sess = FakeSession([])
        h = Harness([sess])

        async def drv(h):
            await h.inbox.put(b"\x00" * (lb.MAX_FRAME_BYTES + 1))
            await h.inbox.put(b"\x00" * 100)
            await asyncio.sleep(0.2)
            await h.inbox.put(None)
        h.run({}, drv)
        self.assertEqual(sess.audio, [b"\x00" * 100])

    def test_text_control_and_ping(self):
        sess = FakeSession([])
        h = Harness([sess])

        async def drv(h):
            await h.inbox.put(json.dumps({"type": "text", "text": "هلا"}))
            await h.inbox.put(json.dumps({"type": "ping"}))
            await h.inbox.put("not json")
            await asyncio.sleep(0.2)
            await h.inbox.put(None)
        h.run({}, drv)
        self.assertEqual(sess.texts, ["هلا"])
        self.assertIn("pong", [m["type"] for m in h.msgs()])

    def test_idle_timeout(self):
        h = Harness([FakeSession([])])
        h.limits.idle_timeout_sec = 0.3
        stats = h.run({})
        self.assertEqual(stats["end"], "idle_timeout")

    def test_voice_validated_and_start_config_used(self):
        h = Harness([FakeSession([])])

        async def drv(h):
            await asyncio.sleep(0.1)
            await h.inbox.put(None)
        h.run({"voice": "NotAVoice", "dialect": "lebanese", "memory": "اسمي أبو قمر"}, drv)
        si, voice = h.opened[0]
        self.assertEqual(voice, lb.DEFAULT_LIVE_VOICE)
        self.assertIn("اللبنانية", si)
        self.assertIn("أبو قمر", si)


class TestHelpers(unittest.TestCase):
    def test_normalize_server_message(self):
        ns = types.SimpleNamespace
        msg = ns(
            server_content=ns(
                model_turn=ns(parts=[ns(inline_data=ns(data=b"\x01\x02")), ns(inline_data=None)]),
                input_transcription=ns(text="مرحبا"), output_transcription=ns(text="أهلين"),
                interrupted=True, turn_complete=True,
            ),
            go_away=None,
        )
        kinds = [e["kind"] for e in lb.normalize_server_message(msg)]
        self.assertEqual(kinds, ["audio", "user_text", "bot_text", "interrupted", "turn_complete"])
        self.assertEqual(lb.normalize_server_message(ns(server_content=None, go_away=ns())), [{"kind": "go_away"}])
        self.assertEqual(lb.normalize_server_message(ns()), [])

    def test_config_variants_progressively_simpler(self):
        v = lb.build_config_variants("sys", "Charon")
        self.assertEqual(len(v), 3)
        self.assertIn("speech_config", v[0])
        self.assertNotIn("speech_config", v[1])
        self.assertEqual(set(v[2]), {"response_modalities", "system_instruction"})

    def test_registry_limits(self):
        r = LiveRegistry(max_concurrent=2)
        self.assertTrue(r.acquire("a"))
        self.assertFalse(r.acquire("a"))  # جلسة وحدة لكل مستخدم
        self.assertTrue(r.acquire("b"))
        self.assertFalse(r.acquire("c"))  # سقف التزامن
        r.release("a")
        self.assertTrue(r.acquire("c"))
        self.assertEqual(r.count(), 2)

    def test_candidate_models_env_then_discovery_then_fallback(self):
        class M:  # موديل وهمي
            def __init__(self, n, acts):
                self.name, self.supported_actions = n, acts
        client = mock.Mock()
        client.models.list.return_value = [M("models/gemini-2.5-flash", ["generateContent"]),
                                           M("models/gemini-live-2.5-flash", ["bidiGenerateContent"]),
                                           M("models/gemini-3.1-flash-live-preview", ["bidiGenerateContent"])]
        lb.GeminiLiveFactory._disc.update(t=0.0, models=[])
        with mock.patch.dict(os.environ, {"GEMINI_LIVE_MODEL": "my-model"}):
            f = lb.GeminiLiveFactory("k", client=client)
            c = f.candidate_models()
        self.assertEqual(c[0], "my-model")
        self.assertEqual(c[1], "gemini-3.1-flash-live-preview")  # الأحدث أولاً
        self.assertNotIn("gemini-2.5-flash", c)
        self.assertIn(lb.FALLBACK_MODELS[0], c)

    def test_factory_tries_next_candidate_on_failure(self):
        calls = []

        class CM:
            def __init__(self, ok): self.ok = ok
            async def __aenter__(self):
                if not self.ok: raise ValueError("bad config")
                return "SESSION"
            async def __aexit__(self, *a): return False

        client = mock.Mock()
        def connect(model, config):
            calls.append((model, sorted(config)))
            return CM(ok=(len(calls) == 3))
        client.aio.live.connect.side_effect = connect
        client.models.list.return_value = []
        lb.GeminiLiveFactory._disc.update(t=0.0, models=[])
        with mock.patch.dict(os.environ, {"GEMINI_LIVE_MODEL": "m1"}):
            f = lb.GeminiLiveFactory("k", client=client)
            lim = LiveLimits()
            ad = asyncio.run(f.open("sys", "Charon", lim))
        self.assertEqual(ad.model, "m1")
        self.assertEqual(len(calls), 3)  # full → basic → minimal على نفس الموديل
        self.assertEqual(calls[2][1], ["response_modalities", "system_instruction"])

    def test_factory_gives_up_after_max_attempts(self):
        class CM:
            async def __aenter__(self): raise ValueError("nope AIza" + "z" * 35)
            async def __aexit__(self, *a): return False
        client = mock.Mock()
        client.aio.live.connect.side_effect = lambda **k: CM()
        client.models.list.return_value = []
        lb.GeminiLiveFactory._disc.update(t=0.0, models=[])
        f = lb.GeminiLiveFactory("k", client=client)
        with self.assertRaises(ConnectionError) as ctx:
            asyncio.run(f.open("sys", "Charon", LiveLimits()))
        self.assertNotIn("z" * 35, str(ctx.exception))
        self.assertLessEqual(client.aio.live.connect.call_count, LiveLimits().max_attempts)


if __name__ == "__main__":
    unittest.main()


import queue as _q
import threading as _th


class FakeWs:
    def __init__(self):
        self.inq, self.sent, self.closed = _q.Queue(), [], False

    def receive(self, timeout=None):
        try:
            m = self.inq.get(timeout=timeout or 0.05)
        except _q.Empty:
            return None
        if m == "__CLOSE__":
            raise ConnectionError("closed")
        return m

    def send(self, x):
        self.sent.append(x)


class TestServeWs(unittest.TestCase):
    def _open(self, session):
        async def open_session(si, voice):
            return session
        return open_session

    def test_full_roundtrip(self):
        ws = FakeWs()
        sess = FakeSession([{"kind": "audio", "data": b"\x09\x09"}, {"kind": "turn_complete"}])
        reg = LiveRegistry(2)
        lim = LiveLimits()
        res = {}
        th = _th.Thread(target=lambda: res.update(lb.serve_ws(ws, "u", self._open(sess), reg, lim, start_timeout=3)))
        th.start()
        ws.inq.put(json.dumps({"type": "start", "voice": "Orus"}))
        ws.inq.put(b"\x00" * 640)
        import time as _t
        _t.sleep(0.6)
        ws.inq.put(json.dumps({"type": "end"}))
        th.join(timeout=6)
        self.assertFalse(th.is_alive())
        self.assertEqual(sess.audio, [b"\x00" * 640])
        self.assertIn(b"\x09\x09", ws.sent)
        self.assertEqual(res["end"], "client_end")
        self.assertEqual(reg.count(), 0)  # انحرر السلوت

    def test_busy_when_user_already_in_call(self):
        reg = LiveRegistry(2)
        reg.acquire("u")
        ws = FakeWs()
        out = lb.serve_ws(ws, "u", self._open(FakeSession([])), reg)
        self.assertEqual(out["end"], "busy")
        self.assertIn("busy", ws.sent[0])

    def test_start_timeout_releases_slot(self):
        reg = LiveRegistry(1)
        ws = FakeWs()
        out = lb.serve_ws(ws, "u", self._open(FakeSession([])), reg, start_timeout=0.3)
        self.assertEqual(out["end"], "start_timeout")
        self.assertEqual(reg.count(), 0)

    def test_client_drops_before_start(self):
        reg = LiveRegistry(1)
        ws = FakeWs()
        ws.inq.put("__CLOSE__")
        out = lb.serve_ws(ws, "u", self._open(FakeSession([])), reg, start_timeout=2)
        self.assertEqual(out["end"], "client_closed_before_start")
        self.assertEqual(reg.count(), 0)

    def test_client_disconnect_mid_call_cleans_up(self):
        reg = LiveRegistry(1)
        ws = FakeWs()
        sess = FakeSession([])
        res = {}
        th = _th.Thread(target=lambda: res.update(lb.serve_ws(ws, "u", self._open(sess), reg, LiveLimits(), start_timeout=3)))
        th.start()
        ws.inq.put(json.dumps({"type": "start"}))
        import time as _t
        _t.sleep(0.4)
        ws.inq.put("__CLOSE__")
        th.join(timeout=6)
        self.assertFalse(th.is_alive())
        self.assertTrue(sess.closed)
        self.assertEqual(res["end"], "client_closed")
        self.assertEqual(reg.count(), 0)
