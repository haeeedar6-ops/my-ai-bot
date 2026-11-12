// محاكاة اتصال مباشر متصفح ← Google (بدون شبكة)
global.window = global;
global.location = { protocol: 'https:', host: 'ryn.test' };
global.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
const sockets = [];
let behavior = (ws, msg, idx) => 'accept';           // يتحكم فيه كل سيناريو
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.idx = sockets.length; sockets.push(this);
    setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 0); }
  send(x) {
    this.sent.push(x);
    const o = (() => { try { return JSON.parse(x); } catch (e) { return null; } })();
    if (o && o.setup) {
      const act = behavior(this, o, this.idx);
      setTimeout(() => {
        if (act === 'accept') this.recv(new Blob([JSON.stringify({ setupComplete: {} })]));
        else if (act === 'reject') { this.readyState = 3; this.onclose && this.onclose({ code: 1007, reason: 'Unknown name responseModalities' }); }
      }, 0);
    }
  }
  close() { this.readyState = 3; }
  recv(d) { this.onmessage && this.onmessage({ data: d }); }
  drop() { this.readyState = 3; this.onclose && this.onclose({ code: 1011, reason: 'bye' }); }
}
global.WebSocket = FakeWS;
const track = { enabled: true, readyState: 'live', stopped: false, stop() { this.stopped = true; this.readyState = 'ended'; } };
const stream = { getAudioTracks: () => [track], getTracks: () => [track] };
Object.defineProperty(global, 'navigator', { value: { userAgent: 'x', mediaDevices: { getUserMedia: async () => stream } }, configurable: true, writable: true });
let sources = [];
class FakeCtx {
  constructor() { this.currentTime = 0; this.sampleRate = 48000; this.state = 'running'; this.destination = {}; global.lastCtx = this; }
  resume() { return Promise.resolve(); } close() {}
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  createAnalyser() { return { fftSize: 0, connect() {}, getFloatTimeDomainData(a) { a.fill(0); } }; }
  createGain() { return { gain: {}, connect() {} }; }
  createScriptProcessor() { const n = { connect() {}, disconnect() {} }; this.sp = n; return n; }
  createBuffer(c, n, r) { return { duration: n / r, copyToChannel() {} }; }
  createBufferSource() { const s = { stopped: false, connect() {}, start() {}, stop() { this.stopped = true; } }; sources.push(s); return s; }
}
global.AudioContext = FakeCtx;
const L = require(process.argv[2]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const b64 = (n) => Buffer.from(new Int16Array(n).buffer).toString('base64');
const blob = (o) => new Blob([JSON.stringify(o)]);
const ev = [];
let cfgCalls = 0;
const cfg = (ephemeral) => async () => { cfgCalls++; return { endpoint: 'wss://g.test/ws', auth_param: ephemeral ? 'access_token' : 'key', token: 'T0KEN', ephemeral,
  models: ['m-one', 'm-two'], voice: 'Charon', system_instruction: 'SI' }; };
const mk = (extra) => Object.assign({ mode: 'direct', localBargeIn: false,
  onState: (s) => ev.push('state:' + s), onReady: (m) => ev.push('ready:' + m.model + ':' + m.variant), onUserText: (t) => ev.push('u:' + t),
  onBotText: (t) => ev.push('b:' + t), onTurn: (u, b) => ev.push('turn:' + u + '|' + b), onError: (c) => ev.push('err:' + c), onTurnComplete: () => ev.push('tc') }, extra);

(async () => {
  const out = {};
  // سيناريو 1: الصياغة الأولى مرفوضة → الثانية بتنجح
  behavior = (ws, msg, idx) => (idx === 0 ? 'reject' : 'accept');
  out.ok = await L.start(mk({ getDirectConfig: cfg(true) }));
  await sleep(60);
  out.nSockets = sockets.length;
  out.urlEphemeral = sockets[0].url;
  out.variant1HasGenCfg = !!JSON.parse(sockets[0].sent[0]).setup.generationConfig && !!JSON.parse(sockets[0].sent[0]).setup.generationConfig.speechConfig;
  out.variant2Minimal = !JSON.parse(sockets[1].sent[0]).setup.generationConfig.speechConfig;
  out.model = JSON.parse(sockets[1].sent[0]).setup.model;
  out.si = JSON.parse(sockets[1].sent[0]).setup.systemInstruction.parts[0].text;
  const ws = sockets[1];
  // إرسال صوت
  const sp = global.lastCtx.sp; sp.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(48000).fill(0.1) } });
  const audioMsgs = ws.sent.map((x) => { try { return JSON.parse(x); } catch (e) { return null; } }).filter((o) => o && o.realtimeInput && o.realtimeInput.audio);
  out.audioSent = audioMsgs.length > 0 && audioMsgs[0].realtimeInput.audio.mimeType === 'audio/pcm;rate=16000' && audioMsgs[0].realtimeInput.audio.data.length > 100;
  // استقبال (Blob) صوت + نصوص + نهاية الدور
  ws.recv(blob({ serverContent: { inputTranscription: { text: 'مرحبا ' } } }));
  ws.recv(blob({ serverContent: { modelTurn: { parts: [{ inlineData: { data: b64(2400), mimeType: 'audio/pcm;rate=24000' } }] } } }));
  ws.recv(blob({ serverContent: { outputTranscription: { text: 'أهلين' } } }));
  ws.recv(JSON.stringify({ serverContent: { turnComplete: true } }));  // نص عادي (مو Blob) كمان لازم يشتغل
  await sleep(40);
  out.played = sources.length === 1;
  out.turnDelivered = ev.includes('turn:مرحبا|أهلين');
  // interrupted
  ws.recv(blob({ serverContent: { modelTurn: { parts: [{ inlineData: { data: b64(2400) } }] } } }));
  await sleep(20);
  ws.recv(blob({ serverContent: { interrupted: true } }));
  await sleep(20);
  out.stoppedOnInterrupt = sources.every((s) => s.stopped);
  // كتم
  L.setMuted(true);
  const before = ws.sent.length;
  sp.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(48000).fill(0.1) } });
  out.noAudioWhenMuted = ws.sent.length === before;
  out.audioStreamEnd = ws.sent.some((x) => x.includes('audioStreamEnd'));
  L.setMuted(false);
  // انقطاع الاتصال → إعادة اتصال بتوكن جديد
  const callsBefore = cfgCalls;
  ws.drop();
  await sleep(1000);
  out.reconnected = cfgCalls === callsBefore + 1 && sockets.length >= 3;
  out.events = ev.slice();
  L.stop();
  out.micReleased = track.stopped;

  // سيناريو 2: كل المحاولات بتفشل → connect_failed
  sockets.length = 0; ev.length = 0; behavior = () => 'reject';
  track.stopped = false; track.readyState = 'live';
  await L.start(mk({ getDirectConfig: cfg(false) }));
  await sleep(120);
  out.failAttempts = sockets.length;
  out.failEvent = ev.includes('err:connect_failed');
  out.urlRaw = sockets[0].url;
  L.stop();

  // سيناريو 3: فشل جلب التوكن
  ev.length = 0;
  await L.start(mk({ getDirectConfig: async () => { throw new Error('403'); } }));
  await sleep(30);
  out.tokenFailed = ev.includes('err:token_failed');
  L.stop();
  console.log(JSON.stringify(out));
})().catch((e) => console.log(JSON.stringify({ crash: String((e && e.stack) || e) })));
