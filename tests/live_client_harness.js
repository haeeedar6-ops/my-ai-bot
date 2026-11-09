// محاكاة متصفح بسيطة لاختبار منطق RynLive (بدون جهاز حقيقي)
const log = [];
global.window = global;
global.location = { protocol: 'https:', host: 'ryn.test' };
global.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
let wsInst = null;
class FakeWS {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; wsInst = this; setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 0); }
  send(x) { this.sent.push(x); }
  close() { this.readyState = 3; }
  recv(x) { this.onmessage && this.onmessage({ data: x }); }
}
FakeWS.OPEN = 1; global.WebSocket = FakeWS;
const track = { enabled: true, readyState: 'live', stopped: false, stop() { this.stopped = true; this.readyState = 'ended'; } };
const stream = { getAudioTracks: () => [track], getTracks: () => [track] };
Object.defineProperty(global, 'navigator', { value: { userAgent: 'x', mediaDevices: { getUserMedia: async () => stream } }, configurable: true, writable: true });
let sources = [];
class FakeCtx {
  constructor() { this.currentTime = 0; this.sampleRate = 48000; this.state = 'running'; this.destination = {}; }
  resume() { return Promise.resolve(); } close() { this.closed = true; }
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  createAnalyser() { return { fftSize: 0, connect() {}, getFloatTimeDomainData(a) { a.fill(0); } }; }
  createGain() { return { gain: {}, connect() {} }; }
  createScriptProcessor() { const n = { connect() {}, disconnect() {} }; this.sp = n; return n; }
  createBuffer(c, n, r) { return { duration: n / r, copyToChannel() {}, n }; }
  createBufferSource() { const s = { started: null, stopped: false, buffer: null, connect() {}, start(t) { this.started = t; }, stop() { this.stopped = true; } }; sources.push(s); return s; }
}
global.AudioContext = FakeCtx;
const L = require(process.argv[2]);
const ev = [];
const pcm = (n) => new Int16Array(n).buffer;
(async () => {
  const ok = await L.start({
    voice: 'Orus', dialect: 'syrian', memory: 'm', localBargeIn: false, getHistory: () => [{ role: 'user', text: 'x' }],
    onState: (s) => ev.push('state:' + s), onReady: () => ev.push('ready'), onUserText: (t) => ev.push('u:' + t), onBotText: (t) => ev.push('b:' + t),
    onTurn: (u, b) => ev.push('turn:' + u + '|' + b), onError: (c) => ev.push('err:' + c), onClosed: (r) => ev.push('closed:' + r), onTurnComplete: () => ev.push('tc'),
  });
  await new Promise((r) => setTimeout(r, 10));
  const out = { ok, url: wsInst.url, start: JSON.parse(wsInst.sent[0]) };
  wsInst.recv(JSON.stringify({ type: 'ready', model: 'm' }));
  wsInst.recv(JSON.stringify({ type: 'user_text', text: 'مرحبا' }));
  wsInst.recv(pcm(2400)); wsInst.recv(pcm(2400));
  out.nSources = sources.length;
  out.gapless = sources[1].started >= sources[0].started + 0.099;
  out.speakingState = ev.includes('state:speaking');
  wsInst.recv(JSON.stringify({ type: 'interrupted' }));
  out.stoppedOnInterrupt = sources.every((s) => s.stopped);
  wsInst.recv(pcm(2400));
  out.playedAfterInterrupt = sources.length;           // لازم يشتغل صوت جديد بعد interrupted (مو محجوب)
  L.interrupt();                                       // مقاطعة محلية → drop لحد turn_complete
  const before = sources.length;
  wsInst.recv(pcm(2400));
  out.droppedAfterLocal = sources.length === before;
  wsInst.recv(JSON.stringify({ type: 'turn_complete' }));
  wsInst.recv(pcm(2400));
  out.resumedAfterTurn = sources.length === before + 1;
  wsInst.recv(JSON.stringify({ type: 'turn', user: 'سؤال', bot: 'جواب' }));
  L.setMuted(true); out.trackDisabled = track.enabled === false;
  L.setMuted(false); out.trackEnabled = track.enabled === true;
  wsInst.recv(JSON.stringify({ type: 'error', code: 'connect_failed', message: 'x' }));
  const ws = wsInst;
  L.stop();
  out.sentEnd = ws.sent.some((x) => typeof x === 'string' && x.includes('"end"'));
  out.micReleased = track.stopped === true;
  out.active = L.isActive();
  out.events = ev;
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crash: String(e && e.stack || e) })); });
