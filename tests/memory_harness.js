// اختبار مكتبة ذاكرة الجهاز (ryn_memory.js) بـ node بدون متصفح: localStorage وهمي.
const fs = require('fs'), path = require('path');
const store = {};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; },
};
const M = require(path.join(__dirname, '..', 'ryn_memory.js'));
let failed = 0, passed = 0;
function ok(cond, msg) { if (cond) passed++; else { failed++; console.error('FAIL:', msg); } }
function eq(a, b, msg) { ok(JSON.stringify(a) === JSON.stringify(b), msg + ' | got ' + JSON.stringify(a) + ' expected ' + JSON.stringify(b)); }

// 1) تطبيع وتجذير عربي: الذاكرة/ذاكرتي لازم يتشاركو الجذر
ok(M.tokens('الذاكرة').join() === M.tokens('ذاكرتي').join(), 'stem الذاكرة == ذاكرتي: ' + M.tokens('الذاكرة') + ' / ' + M.tokens('ذاكرتي'));
ok(M.tokens('القهوة')[0] === M.tokens('قهوتي')[0], 'stem القهوة == قهوتي');
ok(M.norm('أُحِبُّ إلى') === 'احب الي', 'norm tashkeel/alef: ' + M.norm('أُحِبُّ إلى'));

// 2) البحث بالترتيب
const mem = M.sanitize({ enabled: true, name: 'حيدر', custom: '', facts: [
  'أنا بحب القهوة بدون سكر',
  'اسمي حيدر وبشتغل على خرائط Craftland بالموبايل',
  'بستعمل لابتوب ويندوز للشغل',
  'الموقع بيشتغل على Render ومخزّن بـ GitHub',
  'أختي عندها كوتا صور منفصلة',
]});
eq(mem.facts.length, 5, 'sanitize keeps 5 facts');
ok(M.search(mem, 'كيف بحب القهوة؟')[0].text.includes('القهوة'), 'query قهوة → fact 1');
ok(M.search(mem, 'خريطة craftland جديدة')[0].text.includes('Craftland'), 'query craftland → fact 2');
ok(M.search(mem, 'شو نوع اللابتوب؟')[0].text.includes('ويندوز'), 'query لابتوب → fact 3');
ok(M.search(mem, 'نشر على render')[0].text.includes('Render'), 'query render → fact 4');
eq(M.search(mem, 'xyzxyz غير موجود').length, 0, 'no match → empty');

// 3) select: ميزانية + أحدث المعلومات إذا ما في تطابق
let sel = M.select(mem, 'شي مالو علاقة zzzz');
ok(sel.facts.length === 3 && sel.facts[0] === mem.facts[0], 'no match → newest 3: ' + sel.facts.length);
sel = M.select(mem, 'القهوة', { k: 2 });
ok(sel.facts.length <= 3 && sel.facts[0].includes('القهوة'), 'relevant first');
const big = M.sanitize({ facts: Array.from({ length: 300 }, (_, i) => 'معلومة رقم ' + i + ' ' + 'ا'.repeat(300)) });
sel = M.select(big, 'معلومة', { chars: 1000 });
ok(sel.facts.reduce((n, f) => n + f.length, 0) <= 1000 + 400, 'char budget respected');
eq(M.select({ enabled: false, facts: ['x'] }, 'x').facts, [], 'disabled → no facts');

// 4) كشف أوامر التذكّر
eq(M.detect('تذكر أني بحب القهوة بدون سكر'), { type: 'remember', fact: 'بحب القهوة بدون سكر' }, 'detect تذكر أني');
eq(M.detect('لو سمحت تذكّر إنو اسمي حيدر').fact, 'اسمي حيدر', 'detect polite + إنو');
eq(M.detect('لا تنسى أنو أنا من سوريا').fact, 'أنا من سوريا', 'detect لا تنسى');
eq(M.detect('remember that I use Windows').fact, 'I use Windows', 'detect remember that');
eq(M.detect('Please remember: my sister has her own quota').fact, 'my sister has her own quota', 'detect please remember:');
eq(M.detect('خلي ببالك انو الاستضافة على Render').fact, 'الاستضافة على Render', 'detect خلي ببالك');
eq(M.detect('احفظ عندك أني بدي الرد قصير').fact, 'بدي الرد قصير', 'detect احفظ عندك');
ok(M.detect('احفظ هالكود بملف') === null, 'احفظ الكود ≠ ذاكرة');
ok(M.detect('هل تتذكر شو قلتلك؟') === null, 'سؤال ≠ أمر');
ok(M.detect('انسى الموضوع') === null, 'انسى الموضوع ≠ مسح');
ok(M.detect('تذكر هالشي') === null, 'تذكر هالشي (بدون محتوى) ≠ أمر');
ok(M.detect('اكتبلي دالة تتذكر آخر قيمة') === null, 'تذكر بنص الجملة ≠ أمر');

// 5) الأسرار
ok(M.hasSecret('password: abc12345'), 'secret password');
ok(M.hasSecret('مفتاحي AIzaSyD' + 'a'.repeat(30)), 'secret AIza');
ok(M.hasSecret('كلمة السر = 12345678'), 'secret ar');
ok(M.hasSecret('Authorization: Bearer ' + 'x'.repeat(30)), 'secret bearer');
ok(!M.hasSecret('بحب القهوة بدون سكر'), 'normal text is not secret');

// 6) إضافة/تكرار/حفظ
let r = M.addFact('بحب الشاي الأخضر');
ok(r.ok, 'addFact ok');
eq(M.addFact('بحب الشاي الأخضر').reason, 'dup', 'exact dup');
eq(M.addFact('بحب الشاي الاخضر ').reason, 'dup', 'normalized dup (hamza)');
eq(M.addFact('token = sk-' + 'a'.repeat(40)).reason, 'secret', 'secret blocked');
eq(M.addFact('   ').reason, 'empty', 'empty');
ok(M.load().facts[0] === 'بحب الشاي الأخضر', 'saved to localStorage, newest first');
ok(M.captureFromText('تذكر أني بستعمل VS Code').ok, 'captureFromText saves');
ok(M.load().facts[0] === 'بستعمل VS Code', 'captured fact stored');
ok(M.captureFromText('كيف حالك') === null, 'captureFromText ignores normal text');
eq(M.removeAt(0), true, 'removeAt');
ok(M.load().facts[0] === 'بحب الشاي الأخضر', 'removed newest');

// 7) استيراد/تصدير (نفس صيغة الصفحة الرئيسية) + حدّ السعة
const exp = M.exportPayload();
ok(exp.app === 'Ryn' && Array.isArray(exp.memory.facts), 'export shape');
const before = M.load().facts.length;
const imp = M.importPayload({ memory: { name: 'سارة', facts: ['معلومة جديدة من ملف', 'بحب الشاي الأخضر'] } }, false);
ok(imp.ok && imp.added === 1 && M.load().facts.length === before + 1, 'import merges + dedupes: ' + JSON.stringify(imp));
const many = M.sanitize({ facts: Array.from({ length: 2000 }, (_, i) => 'fact ' + i) });
ok(many.facts.length === M.MAX_FACTS, 'cap at MAX_FACTS=' + M.MAX_FACTS + ' got ' + many.facts.length);

// 8) الملف نفسو آمن للحقن داخل <script>
const src = fs.readFileSync(path.join(__dirname, '..', 'ryn_memory.js'), 'utf8');
ok(!/<\/script/i.test(src), 'no </script inside ryn_memory.js');

console.log(`memory harness: ${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
