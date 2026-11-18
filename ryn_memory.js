/* RYN device memory — ذاكرة طويلة مخزّنة على جهاز المستخدم، مشتركة بين كل صفحات الموقع (نفس localStorage).
 *
 * - الصفحة الرئيسية (كل النماذج) وصفحة RYN Ultra (الوكلاء الخمسة) بيقرأو ويكتبو نفس المفتاح: astra_memory_v1.
 * - السيرفر ما بيخزّن شي: المتصفح بيبحث بالذاكرة (ترتيب حسب الصلة) وبيبعت بس الأنسب مع كل طلب.
 * - صيغة التخزين متوافقة مع تصدير/استيراد الصفحة الرئيسية: {enabled,name,custom,facts:[نصوص]}.
 * ما فيه أي اعتماد خارجي؛ الاختبار بـ node: tests/memory_harness.js
 */
(function (root) {
  'use strict';

  var LS_KEY = 'astra_memory_v1';
  var MAX_FACTS = 800;          // ذاكرة "طويلة": آلاف الأحرف بلا ضغط على الطلب لأنو بنبعت بس المناسب
  var MAX_FACT_CHARS = 400;
  var MAX_NAME = 120;
  var MAX_CUSTOM = 2000;
  var REQ_FACTS = 12;           // أقصى عدد معلومات بالطلب الواحد
  var REQ_CHARS = 2400;         // أقصى حجم بالأحرف بالطلب الواحد

  /* ---------- تخزين ---------- */
  function lsGet(k) { try { return root.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { root.localStorage.setItem(k, v); return true; } catch (e) { return false; } }

  function blank() { return { enabled: true, name: '', custom: '', facts: [] }; }

  function sanitize(m) {
    var out = blank();
    if (!m || typeof m !== 'object') return out;
    out.enabled = m.enabled !== false;
    out.name = String(m.name || '').slice(0, MAX_NAME);
    out.custom = String(m.custom || '').slice(0, MAX_CUSTOM);
    if (m.lastBackupAt) out.lastBackupAt = m.lastBackupAt;
    var seen = {};
    var src = Array.isArray(m.facts) ? m.facts : [];
    for (var i = 0; i < src.length && out.facts.length < MAX_FACTS; i++) {
      var f = src[i];
      var t = String(typeof f === 'string' ? f : (f && f.text) || '').replace(/\s+/g, ' ').trim().slice(0, MAX_FACT_CHARS);
      if (!t || hasSecret(t)) continue;
      var key = t.toLowerCase();
      if (seen[key]) continue;
      seen[key] = 1;
      out.facts.push(t);
    }
    return out;
  }

  function load() {
    var raw = lsGet(LS_KEY);
    if (!raw) return blank();
    try { return sanitize(JSON.parse(raw)); } catch (e) { return blank(); }
  }

  function save(mem) {
    var ok = lsSet(LS_KEY, JSON.stringify(sanitize(mem)));
    try { if (root.navigator && root.navigator.storage && root.navigator.storage.persist) root.navigator.storage.persist(); } catch (e) { /* اختياري */ }
    return ok;
  }

  /* ---------- حماية: ما منحفظ أسرار ---------- */
  var SECRET_RE = /(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|Bearer\s+[A-Za-z0-9._~+\/=\-]{16,}|(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token|كلمة\s*السر|كلمة\s*المرور|توكن)\s*[:=]\s*\S+)/i;
  function hasSecret(text) { return SECRET_RE.test(String(text || '')); }

  /* ---------- تطبيع عربي + تجذير خفيف ---------- */
  function norm(s) {
    s = String(s || '').toLowerCase();
    s = s.replace(/[\u064B-\u065F\u0670\u0640]/g, '');                  // تشكيل + تطويل
    s = s.replace(/[أإآٱ]/g, 'ا').replace(/ى/g, 'ي').replace(/ة/g, 'ه').replace(/ؤ/g, 'و').replace(/ئ/g, 'ي');
    s = s.replace(/\u06A9/g, 'ك').replace(/\u06CC/g, 'ي');              // ک ی → ك ي
    s = s.replace(/[^a-z0-9\u0621-\u063A\u0641-\u064A]+/g, ' ');
    return s.replace(/\s+/g, ' ').trim();
  }

  var STOP = {};
  ('في من الي على عن ان انو انا انت هو هي هاد هاي هيك هذا هذه شو ايش ليش كيف لا ما مع او يا كل كان عند هل لو بس كمان يعني ' +
   'the a an is are to of and in on for with i my me you it this that do does what how why').split(' ').forEach(function (w) { STOP[w] = 1; });

  var PRE = ['وال', 'بال', 'كال', 'فال', 'لل', 'ال', 'و', 'ب', 'ف', 'ل', 'ك'];
  var SUF = ['ات', 'ون', 'ين', 'ان', 'ها', 'هم', 'هن', 'كم', 'نا', 'تي', 'ي', 'ه', 'ك'];

  function stem(w) {
    if (/^[a-z0-9]+$/.test(w)) {
      if (w.length > 4 && /ing$/.test(w)) return w.slice(0, -3);
      if (w.length > 3 && /ed$/.test(w)) return w.slice(0, -2);
      if (w.length > 3 && /es$/.test(w)) return w.slice(0, -2);
      if (w.length > 3 && /s$/.test(w)) return w.slice(0, -1);
      return w;
    }
    var r = w, i, p, s;
    for (i = 0; i < PRE.length; i++) { p = PRE[i]; if (r.length - p.length >= 3 && r.indexOf(p) === 0) { r = r.slice(p.length); break; } }
    for (i = 0; i < SUF.length; i++) { s = SUF[i]; if (r.length - s.length >= 3 && r.slice(-s.length) === s) { r = r.slice(0, -s.length); break; } }
    return r;
  }

  function tokens(s) {
    var out = [], parts = norm(s).split(' ');
    for (var i = 0; i < parts.length; i++) {
      var w = parts[i];
      if (w.length < 2 || STOP[w]) continue;
      out.push(stem(w));
    }
    return out;
  }

  /* التجذير الخفيف أحياناً بيقصّ الكلمة بشكل مختلف بين السؤال والمعلومة (اللابتوب ← لابتوب ← ابتوب)،
     فبنقارن بكل الصيغ: الكلمة نفسها، بدون "ال"، والجذر. */
  var AL = ['وال', 'بال', 'كال', 'فال', 'لل', 'ال'];
  function stripAL(w) {
    for (var i = 0; i < AL.length; i++) { if (w.length - AL[i].length >= 3 && w.indexOf(AL[i]) === 0) return w.slice(AL[i].length); }
    return w;
  }
  function variantsOf(w) {
    var v = [w], a = stripAL(w), s = stem(w);
    if (a !== w) v.push(a);
    if (s !== w && s !== a) v.push(s);
    return v;
  }
  function tokenVariants(s) {
    var out = [], parts = norm(s).split(' ');
    for (var i = 0; i < parts.length; i++) {
      var w = parts[i];
      if (w.length < 2 || STOP[w]) continue;
      out.push(variantsOf(w));
    }
    return out;
  }

  /* ---------- بحث/ترتيب حسب الصلة (BM25 مبسّط) ---------- */
  function buildIndex(facts) {
    var docs = [], df = {}, i, w;
    for (i = 0; i < facts.length; i++) {
      var tf = {}, t = tokenVariants(facts[i]);
      for (var j = 0; j < t.length; j++) for (var k = 0; k < t[j].length; k++) tf[t[j][k]] = (tf[t[j][k]] || 0) + 1;
      docs.push({ tf: tf });
    }
    for (i = 0; i < docs.length; i++) for (w in docs[i].tf) df[w] = (df[w] || 0) + 1;
    return { docs: docs, df: df, N: docs.length };
  }

  function scoreDoc(q, doc, idx) {          // q = [[صيغ الكلمة الأولى], [صيغ الثانية], ...]
    var s = 0;
    for (var i = 0; i < q.length; i++) {
      var best = 0, partial = 0;
      for (var j = 0; j < q[i].length; j++) {
        var t = q[i][j];
        if (doc.tf[t]) {
          var n = idx.df[t] || 0, tf = doc.tf[t];
          var sc = Math.log(1 + (idx.N - n + 0.5) / (n + 0.5)) * (tf * 2.2) / (tf + 1.2);
          if (sc > best) best = sc;
        } else if (!partial && t.length >= 4) {         // مطابقة جزئية (كلمة محتواة بالتانية)
          for (var w in doc.tf) { if (w.length >= 4 && (w.indexOf(t) >= 0 || t.indexOf(w) >= 0)) { partial = 0.6; break; } }
        }
      }
      s += best || partial;
    }
    return s;
  }

  function search(mem, query, k) {
    var facts = (mem && mem.facts) || [];
    var q = tokenVariants(query || '');
    if (!q.length || !facts.length) return [];
    var idx = buildIndex(facts), res = [];
    for (var i = 0; i < facts.length; i++) {
      var s = scoreDoc(q, idx.docs[i], idx);
      if (s > 0) res.push({ text: facts[i], index: i, score: s + 0.15 * (1 - i / facts.length) });   // الأحدث أول القائمة
    }
    res.sort(function (a, b) { return b.score - a.score; });
    return res.slice(0, k || REQ_FACTS);
  }

  /* ما بنرسل للنموذج غير الأنسب (حدّ عدد + حدّ أحرف). بيرجّع نفس شكل الطلب القديم للصفحة الرئيسية. */
  function select(mem, query, opt) {
    opt = opt || {};
    if (!mem || mem.enabled === false) return { enabled: false, local_only: true, name: '', custom: '', facts: [] };
    var facts = (mem.facts || []).map(String).filter(Boolean);
    var picked = search({ facts: facts }, query, opt.k || REQ_FACTS).map(function (r) { return r.text; });
    var minFacts = Math.min(opt.min || 3, facts.length);
    for (var i = 0; picked.length < minFacts && i < facts.length; i++) {      // حتى لو ما في تطابق: أحدث المعلومات
      if (picked.indexOf(facts[i]) < 0) picked.push(facts[i]);
    }
    var budget = opt.chars || REQ_CHARS, out = [], used = 0;
    for (var k = 0; k < picked.length; k++) {
      if (used + picked[k].length > budget && out.length) break;
      out.push(picked[k]); used += picked[k].length;
    }
    return { enabled: true, local_only: true, name: String(mem.name || '').slice(0, MAX_NAME),
             custom: String(mem.custom || '').slice(0, 1500), facts: out };
  }

  function forRequest(query) { return select(load(), query); }

  /* ---------- إضافة/حذف ---------- */
  function jaccard(a, b) {
    var A = {}, B = {}, i, inter = 0, uni = 0;
    for (i = 0; i < a.length; i++) A[a[i]] = 1;
    for (i = 0; i < b.length; i++) B[b[i]] = 1;
    for (var k in A) { uni++; if (B[k]) inter++; }
    for (k in B) if (!A[k]) uni++;
    return uni ? inter / uni : 0;
  }

  function addFact(text, mem) {
    var t = String(text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_FACT_CHARS);
    if (!t) return { ok: false, reason: 'empty' };
    if (hasSecret(t)) return { ok: false, reason: 'secret' };
    var m = mem || load(), tk = tokens(t), low = t.toLowerCase();
    for (var i = 0; i < m.facts.length; i++) {
      if (m.facts[i].toLowerCase() === low) return { ok: false, reason: 'dup' };
      if (tk.length >= 3 && jaccard(tk, tokens(m.facts[i])) >= 0.85) return { ok: false, reason: 'dup' };
    }
    m.facts.unshift(t);
    if (m.facts.length > MAX_FACTS) m.facts.length = MAX_FACTS;
    if (!mem && !save(m)) return { ok: false, reason: 'storage' };
    return { ok: true, fact: t, memory: m };
  }

  function removeAt(index) {
    var m = load();
    if (index < 0 || index >= m.facts.length) return false;
    m.facts.splice(index, 1);
    return save(m);
  }

  /* ---------- كشف أوامر التذكّر بالرسالة (تحديدي: ما بيعتمد على النموذج) ---------- */
  var CMD = new RegExp(
    '^\\s*(?:(?:ممكن|لو\\s*سمحت|من\\s*فضلك|رجاء|يا\\s*\\S+|hey|please|pls)[\\s،,]+)?' +
    '(?:تذكّر|تذكر|ما\\s*تنسى|لا\\s*تنسى|ماتنسى|لاتنسى|خليك\\s*فاكر|خلّيك\\s*فاكر|خلي\\s*ببالك|خلّي\\s*ببالك|' +
    'احفظ\\s*(?:عندك|بالذاكرة|في\\s*الذاكرة|لي\\s*بالذاكرة)|سجّل\\s*عندك|سجل\\s*عندك|دوّن\\s*عندك|' +
    'ضيف\\s*(?:للذاكرة|بالذاكرة|على\\s*الذاكرة)|remember|keep\\s*in\\s*mind|don\'?t\\s*forget|note\\s*that|save\\s*(?:this\\s*)?to\\s*memory)' +
    '(?=[\\s:،,\\-–—]|$)[\\s:،,\\-–—]*(?:(?:أني|إني|اني|أنو|إنو|انو|أن|إن|ان|that)\\s+)?([\\s\\S]+)$', 'i');
  var DEMONSTRATIVE = /^(?:هاد|هاي|هيك|هذا|هذه|ذلك|هالشي|هالموضوع|هالمعلومه|هالمعلومة|هاد\s*الشي|this|that|it)[\s.!؟?،,]*$/i;

  function detect(text) {
    var m = CMD.exec(String(text || ''));
    if (!m) return null;
    var fact = String(m[1] || '').replace(/\s+/g, ' ').trim().replace(/^[\s:،,\-–—]+/, '');
    if (fact.length < 3 || DEMONSTRATIVE.test(fact)) return null;
    return { type: 'remember', fact: fact.slice(0, MAX_FACT_CHARS) };
  }

  var REASONS = {
    secret: '⛔ ما حفظت: فيها مفتاح سري أو كلمة مرور',
    dup: 'عندي هالمعلومة من قبل 🧠',
    storage: '⚠️ مساحة تخزين المتصفح ممتلية، ما قدرت أحفظ',
    empty: ''
  };

  function toast(msg) {
    if (!msg) return;
    try {
      if (typeof root.toast === 'function') { root.toast(msg); return; }
      var d = root.document, t = d.createElement('div');
      t.textContent = msg;
      t.setAttribute('style', 'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#1b1b2a;color:#fff;' +
        'padding:9px 14px;border-radius:12px;font:14px system-ui;z-index:99999;box-shadow:0 6px 22px rgba(0,0,0,.45);max-width:86vw;text-align:center');
      d.body.appendChild(t);
      setTimeout(function () { try { d.body.removeChild(t); } catch (e) { /* ignore */ } }, 2600);
    } catch (e) { /* ignore */ }
  }

  /* إذا الرسالة فيها "تذكر أني..." منحفظها على الجهاز فوراً (وبتضل الرسالة تروح للنموذج عادي). */
  function captureFromText(text) {
    var c = detect(text);
    if (!c) return null;
    var r = addFact(c.fact);
    toast(r.ok ? '🧠 حفظتها على جهازك: ' + c.fact.slice(0, 60) : REASONS[r.reason]);
    if (r.ok) notify();
    return r;
  }

  /* ---------- استيراد/تصدير (نفس صيغة الصفحة الرئيسية) ---------- */
  function exportPayload() {
    var m = load();
    return { app: 'Ryn', version: 1, exportedAt: new Date().toISOString(), memory: m };
  }

  function importPayload(data, replace) {
    var src = data && (data.memory || data);
    var inc = sanitize(src), cur = load(), added = 0;
    if (replace) { cur = inc; added = inc.facts.length; }
    else {
      for (var i = inc.facts.length - 1; i >= 0; i--) { if (addFact(inc.facts[i], cur).ok) added++; }
      if (!cur.name && inc.name) cur.name = inc.name;
      if (!cur.custom && inc.custom) cur.custom = inc.custom;
    }
    var ok = save(cur);
    if (ok) notify();
    return { ok: ok, added: added, total: cur.facts.length };
  }

  /* ---------- إشعار الصفحات المفتوحة + عدّاد ---------- */
  var listeners = [];
  function onChange(fn) { listeners.push(fn); }
  function notify() { for (var i = 0; i < listeners.length; i++) { try { listeners[i](); } catch (e) { /* ignore */ } } }
  try { root.addEventListener && root.addEventListener('storage', function (e) { if (e.key === LS_KEY) notify(); }); } catch (e) { /* node */ }

  function count() { var m = load(); return m.facts.length + (m.name ? 1 : 0) + (m.custom ? 1 : 0); }

  /* ---------- لوحة الإدارة (للصفحات يلي ما عندها لوحة خاصة، مثل صفحة Ultra). DOM بـ textContent فقط. ---------- */
  var STYLE_ID = 'rynMemStyle';
  function injectStyle() {
    var d = root.document;
    if (d.getElementById(STYLE_ID)) return;
    var st = d.createElement('style');
    st.id = STYLE_ID;
    st.textContent =
      '.rm-ov{position:fixed;inset:0;background:rgba(0,0,0,.62);z-index:9998;display:flex;align-items:center;justify-content:center;padding:14px;direction:rtl}' +
      '.rm-box{background:var(--panel,#14141f);color:var(--text,#eaeaf5);border:1px solid var(--line,rgba(255,255,255,.14));border-radius:18px;width:min(560px,100%);max-height:88vh;display:flex;flex-direction:column;font:14px system-ui}' +
      '.rm-h{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid var(--line,rgba(255,255,255,.12));font-weight:700}' +
      '.rm-b{padding:12px 14px;overflow:auto;display:flex;flex-direction:column;gap:10px}' +
      '.rm-b input[type=text],.rm-b textarea{width:100%;box-sizing:border-box;background:rgba(255,255,255,.06);color:inherit;border:1px solid var(--line,rgba(255,255,255,.16));border-radius:10px;padding:8px 10px;font:inherit}' +
      '.rm-row{display:flex;gap:8px}.rm-row>*{flex:1}.rm-row>button{flex:0 0 auto}' +
      '.rm-btn{background:rgba(255,255,255,.09);color:inherit;border:1px solid var(--line,rgba(255,255,255,.16));border-radius:10px;padding:8px 12px;cursor:pointer;font:inherit}' +
      '.rm-item{display:flex;gap:8px;align-items:flex-start;background:rgba(255,255,255,.05);border-radius:10px;padding:7px 9px}' +
      '.rm-item span{flex:1;word-break:break-word;white-space:pre-wrap}.rm-item button{background:none;border:0;color:#ff8a8a;cursor:pointer;font-size:16px}' +
      '.rm-list{display:flex;flex-direction:column;gap:6px;max-height:36vh;overflow:auto}.rm-small{opacity:.72;font-size:12px}';
    d.head.appendChild(st);
  }

  function el(tag, cls, text) {
    var e = root.document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function openPanel() {
    var d = root.document;
    injectStyle();
    var old = d.getElementById('rynMemOverlay');
    if (old) old.parentNode.removeChild(old);
    var ov = el('div', 'rm-ov'); ov.id = 'rynMemOverlay';
    var box = el('div', 'rm-box'); ov.appendChild(box);
    var head = el('div', 'rm-h'), title = el('span', null, '🧠 ذاكرة جهازك (لكل النماذج)');
    var close = el('button', 'rm-btn', '✕'); close.type = 'button';
    head.appendChild(title); head.appendChild(close); box.appendChild(head);
    var body = el('div', 'rm-b'); box.appendChild(body);

    var onLabel = el('label'); onLabel.style.cssText = 'display:flex;gap:8px;align-items:center';
    var on = el('input'); on.type = 'checkbox';
    onLabel.appendChild(on); onLabel.appendChild(el('span', null, 'استعمل ذاكرتي مع النماذج (محفوظة على جهازك فقط)'));
    body.appendChild(onLabel);
    var stat = el('div', 'rm-small'); body.appendChild(stat);

    var search_ = el('input'); search_.type = 'text'; search_.placeholder = 'ابحث بالذاكرة…'; body.appendChild(search_);
    var list = el('div', 'rm-list'); body.appendChild(list);

    var addRow = el('div', 'rm-row');
    var addIn = el('input'); addIn.type = 'text'; addIn.placeholder = 'أضف معلومة يتذكرها عنك دايماً…';
    var addBtn = el('button', 'rm-btn', '＋'); addBtn.type = 'button';
    addRow.appendChild(addIn); addRow.appendChild(addBtn); body.appendChild(addRow);

    var fileRow = el('div', 'rm-row');
    var exp = el('button', 'rm-btn', '💾 تنزيل الملف'); exp.type = 'button';
    var imp = el('button', 'rm-btn', '📂 رفع ملف'); imp.type = 'button';
    var inp = el('input'); inp.type = 'file'; inp.accept = '.json,application/json'; inp.hidden = true;
    fileRow.appendChild(exp); fileRow.appendChild(imp); fileRow.appendChild(inp); body.appendChild(fileRow);
    body.appendChild(el('div', 'rm-small', 'تقدر تقول بالشات: «تذكر أني…» وبتنحفظ لحالها. الذاكرة بتنبعت للنماذج على قد الصلة بسؤالك بس.'));

    function render() {
      var m = load(), q = search_.value.trim(), rows = [], i;
      on.checked = m.enabled !== false;
      if (q) { var rs = search(m, q, 50); for (i = 0; i < rs.length; i++) rows.push({ t: rs[i].text, i: rs[i].index }); }
      else for (i = 0; i < m.facts.length && i < 200; i++) rows.push({ t: m.facts[i], i: i });
      stat.textContent = m.facts.length + ' معلومة محفوظة على جهازك' + (m.name ? ' · الاسم: ' + m.name : '');
      list.textContent = '';
      if (!rows.length) list.appendChild(el('div', 'rm-small', q ? 'ما لقيت شي بهالبحث' : 'ما في ذكريات بعد'));
      rows.forEach(function (r) {
        var it = el('div', 'rm-item'), tx = el('span', null, r.t), del = el('button', null, '✕');
        del.type = 'button'; del.title = 'حذف من الجهاز';
        del.onclick = function () { removeAt(r.i); notify(); render(); };
        it.appendChild(tx); it.appendChild(del); list.appendChild(it);
      });
    }

    on.onchange = function () { var m = load(); m.enabled = on.checked; save(m); notify(); };
    search_.oninput = render;
    addBtn.onclick = function () {
      var r = addFact(addIn.value);
      if (r.ok) { addIn.value = ''; notify(); render(); toast('انضافت للذاكرة 🧠'); } else toast(REASONS[r.reason] || '');
    };
    addIn.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); addBtn.onclick(); } };
    exp.onclick = function () {
      var blob = new root.Blob([JSON.stringify(exportPayload(), null, 2)], { type: 'application/json' });
      var a = d.createElement('a'); a.href = root.URL.createObjectURL(blob); a.download = 'ryn-memory.json';
      d.body.appendChild(a); a.click(); d.body.removeChild(a);
      setTimeout(function () { root.URL.revokeObjectURL(a.href); }, 4000);
    };
    imp.onclick = function () { inp.click(); };
    inp.onchange = function () {
      var f = inp.files && inp.files[0]; if (!f) return;
      var rd = new root.FileReader();
      rd.onload = function () {
        try { var r = importPayload(JSON.parse(String(rd.result || '{}')), false); toast(r.ok ? 'انضافت ' + r.added + ' معلومة 🧠' : REASONS.storage); } catch (e) { toast('الملف مو صالح'); }
        render();
      };
      rd.readAsText(f); inp.value = '';
    };
    close.onclick = function () { ov.parentNode && ov.parentNode.removeChild(ov); };
    ov.addEventListener('click', function (e) { if (e.target === ov) close.onclick(); });
    d.body.appendChild(ov);
    render();
  }

  function mountButton(btn, countEl) {
    if (!btn) return;
    function paint() { if (countEl) countEl.textContent = String(count()); btn.classList.toggle('off', load().enabled === false); }
    btn.onclick = openPanel;
    onChange(paint);
    paint();
  }

  var api = {
    KEY: LS_KEY, MAX_FACTS: MAX_FACTS, MAX_FACT_CHARS: MAX_FACT_CHARS,
    load: load, save: save, sanitize: sanitize, hasSecret: hasSecret,
    norm: norm, tokens: tokens, search: search, select: select, forRequest: forRequest,
    addFact: addFact, removeAt: removeAt, detect: detect, captureFromText: captureFromText,
    exportPayload: exportPayload, importPayload: importPayload,
    onChange: onChange, notify: notify, count: count, openPanel: openPanel, mountButton: mountButton
  };

  root.RynMem = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
