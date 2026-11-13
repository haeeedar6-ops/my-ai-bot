const express = require('express');
const http = require('http');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const jwt = require('jsonwebtoken');
require('dotenv').config();

const { verifyToken, getJwtSecret } = require('./middleware/auth');
const { chatRyn, analyzeExpenses, getAIStatus, DEFAULT_TARTUS_PRICE_BOOK } = require('./services/ai');

const app = express();
const server = http.createServer(app);

const DATA_DIR = path.join(__dirname, 'data');
const STORE_FILE = path.join(DATA_DIR, 'store.json');

const DEFAULT_STORE = {
    budget: 3500000,
    categorySplits: {
        Food: 45,
        Utilities: 15,
        Transport: 12,
        Health: 10,
        Education: 8,
        Shopping: 10
    },
    preferredModel: process.env.DEFAULT_AI_MODEL || 'auto',
    autoFallback: true,
    dailyNotificationTime: '09:00',
    priceBook: DEFAULT_TARTUS_PRICE_BOOK,
    expenses: [
        { id: 1, name: 'ربطة خبز + لبن رائب + بندورة', amount: 23500, category: 'Food', date: '2026-11-10', notes: 'مقاضي بقالة يومية - طرطوس' },
        { id: 2, name: 'فواتير كهرباء وإنترنت', amount: 125000, category: 'Utilities', date: '2026-11-08', notes: 'فاتورة شهرية ثابتة' },
        { id: 3, name: 'بنزين ومواصلات سرفيس', amount: 45000, category: 'Transport', date: '2026-11-09', notes: 'مواصلات داخل طرطوس' }
    ],
    memoryFacts: [
        'المدينة المعتمدة للأسعار: طرطوس - سوريا (بالليرة السورية ل.س).',
        'عندما يشتري حيدر أي سلعة ويحدد سعرها الفعلي، يتم اعتماده فوراً كسعر أساسي في دليل أسعار طرطوس.'
    ],
    chatHistory: [
        {
            id: 'welcome-1',
            sender: 'ryn',
            text: 'أهلاً بك يا حيدر! أنا Ryn مساعدك الذكي في طرطوس. قسّمت لك الميزانية الشهرية على الفئات وجهزت لك مقترح مشتريات اليوم مع أسعار طرطوس التقريبية. أي غرض تشتريه اضغط "تم الشراء" أو أخبرني بسعره الفعلي وسأعتمده فوراً في ذاكرتي!',
            translation: 'Welcome Haidar! I am Ryn, your smart assistant in Tartus. I split your monthly budget across categories and prepared today’s shopping list with approximate Tartus prices. Whenever you buy an item, click Purchased or tell me your actual price and I will lock it in!',
            modelUsed: 'gemini-2.5-flash',
            timestamp: new Date().toISOString()
        }
    ],
    lastUpdated: new Date().toISOString()
};

function loadStore() {
    try {
        if (!fs.existsSync(DATA_DIR)) {
            fs.mkdirSync(DATA_DIR, { recursive: true });
        }
        if (fs.existsSync(STORE_FILE)) {
            const raw = fs.readFileSync(STORE_FILE, 'utf8');
            const parsed = JSON.parse(raw);
            return {
                ...DEFAULT_STORE,
                ...parsed,
                expenses: Array.isArray(parsed.expenses) ? parsed.expenses : DEFAULT_STORE.expenses,
                priceBook: Array.isArray(parsed.priceBook) ? parsed.priceBook : DEFAULT_STORE.priceBook,
                memoryFacts: Array.isArray(parsed.memoryFacts) ? parsed.memoryFacts : DEFAULT_STORE.memoryFacts,
                chatHistory: Array.isArray(parsed.chatHistory) ? parsed.chatHistory : DEFAULT_STORE.chatHistory
            };
        }
    } catch (err) {
        console.warn('Could not read store.json, using default store:', err.message);
    }
    return JSON.parse(JSON.stringify(DEFAULT_STORE));
}

function saveStore(store) {
    try {
        if (!fs.existsSync(DATA_DIR)) {
            fs.mkdirSync(DATA_DIR, { recursive: true });
        }
        store.lastUpdated = new Date().toISOString();
        fs.writeFileSync(STORE_FILE, JSON.stringify(store, null, 2), 'utf8');
    } catch (err) {
        console.warn('Could not save store.json:', err.message);
    }
}

let appStore = loadStore();

app.use(cors());
app.use(express.json({ limit: '5mb' }));
app.use(express.static(path.join(__dirname, 'public')));

app.get('/api/health', (req, res) => {
    res.status(200).json({
        status: 'ok',
        service: 'family-expense-tracker',
        ai: getAIStatus(),
        timestamp: new Date().toISOString()
    });
});

app.post('/api/auth/login', (req, res) => {
    try {
        const { pin, username } = req.body || {};
        const requiredPin = process.env.FAMILY_PIN;

        if (requiredPin && requiredPin.trim() !== '' && String(pin || '') !== requiredPin.trim()) {
            return res.status(401).json({ error: 'رمز الدخول العائلي غير صحيح' });
        }

        const token = jwt.sign(
            { user: username || 'hydr', role: 'family_admin' },
            getJwtSecret(),
            { expiresIn: '7d' }
        );
        res.json({
            token,
            user: username || 'حيدر',
            aiStatus: getAIStatus()
        });
    } catch (error) {
        res.status(500).json({ error: 'فشل إنشاء جلسة المصادقة' });
    }
});

app.get('/api/ai/status', (req, res) => {
    res.json(getAIStatus());
});

app.use('/api/expenses', verifyToken);
app.use('/api/ryn', verifyToken);
app.use('/api/ai/analyze', verifyToken);
app.use('/api/memory', verifyToken);
app.use('/api/prices', verifyToken);

app.get('/api/expenses', (req, res) => {
    res.json({
        expenses: appStore.expenses,
        budget: appStore.budget,
        categorySplits: appStore.categorySplits,
        priceBook: appStore.priceBook,
        lastUpdated: appStore.lastUpdated
    });
});

app.post('/api/expenses', (req, res) => {
    try {
        const { name, amount, category, date, notes, adoptPrice } = req.body || {};
        const numericAmount = Number(amount);

        if (!name || typeof name !== 'string' || !name.trim() || !Number.isFinite(numericAmount) || numericAmount <= 0) {
            return res.status(400).json({ error: 'الاسم والمبلغ الرقمي (أكبر من 0) مطلوبان' });
        }

        const cleanName = name.trim();
        const newExpense = {
            id: Date.now(),
            name: cleanName,
            amount: Math.round(numericAmount),
            category: category || 'Food',
            date: date || new Date().toISOString().split('T')[0],
            notes: (notes || '').trim()
        };

        appStore.expenses.push(newExpense);

        if (adoptPrice) {
            const existingPriceIdx = appStore.priceBook.findIndex(
                p => p.name === cleanName || cleanName.includes(p.name) || p.name.includes(cleanName)
            );
            if (existingPriceIdx !== -1) {
                appStore.priceBook[existingPriceIdx].price = Math.round(numericAmount);
                appStore.priceBook[existingPriceIdx].userVerified = true;
                appStore.priceBook[existingPriceIdx].updatedAt = new Date().toISOString().split('T')[0];
            } else {
                appStore.priceBook.push({
                    id: 'custom-' + Date.now(),
                    name: cleanName,
                    unit: 'وحدة',
                    price: Math.round(numericAmount),
                    category: category || 'Food',
                    essential: true,
                    userVerified: true,
                    updatedAt: new Date().toISOString().split('T')[0]
                });
            }
        }

        saveStore(appStore);
        res.status(201).json({ expense: newExpense, priceBook: appStore.priceBook });
    } catch (error) {
        res.status(500).json({ error: 'فشل في إضافة المصروف' });
    }
});

app.put('/api/expenses/:id', (req, res) => {
    try {
        const id = Number(req.params.id);
        const index = appStore.expenses.findIndex(e => Number(e.id) === id);

        if (index === -1) {
            return res.status(404).json({ error: 'المصروف غير موجود لتعديله' });
        }

        const { name, amount, category, date, notes } = req.body || {};
        const existing = appStore.expenses[index];
        const updatedAmount = amount !== undefined ? Number(amount) : existing.amount;

        if (!Number.isFinite(updatedAmount) || updatedAmount <= 0) {
            return res.status(400).json({ error: 'المبلغ المعدل يجب أن يكون رقماً أكبر من 0' });
        }

        appStore.expenses[index] = {
            ...existing,
            name: name !== undefined && String(name).trim() ? String(name).trim() : existing.name,
            amount: Math.round(updatedAmount),
            category: category !== undefined ? category : existing.category,
            date: date !== undefined ? date : existing.date,
            notes: notes !== undefined ? String(notes).trim() : existing.notes
        };

        saveStore(appStore);
        res.json(appStore.expenses[index]);
    } catch (error) {
        res.status(500).json({ error: 'فشل في تعديل المصروف' });
    }
});

app.delete('/api/expenses/:id', (req, res) => {
    try {
        const id = Number(req.params.id);
        const index = appStore.expenses.findIndex(e => Number(e.id) === id);

        if (index === -1) {
            return res.status(404).json({ error: 'المصروف غير موجود' });
        }

        const removed = appStore.expenses.splice(index, 1)[0];
        saveStore(appStore);
        res.json({ message: 'تم الحذف بنجاح', deleted: removed });
    } catch (error) {
        res.status(500).json({ error: 'فشل في حذف المصروف' });
    }
});

app.put('/api/expenses/budget/limit', (req, res) => {
    const numericBudget = Number(req.body?.budget);
    if (!Number.isFinite(numericBudget) || numericBudget <= 0) {
        return res.status(400).json({ error: 'قيمة الميزانية غير صالحة' });
    }
    appStore.budget = Math.round(numericBudget);
    if (req.body?.categorySplits && typeof req.body.categorySplits === 'object') {
        appStore.categorySplits = { ...appStore.categorySplits, ...req.body.categorySplits };
    }
    saveStore(appStore);
    res.json({ budget: appStore.budget, categorySplits: appStore.categorySplits });
});

app.post('/api/prices/adopt', (req, res) => {
    const { id, name, price, unit, category } = req.body || {};
    const numPrice = Number(price);
    if (!Number.isFinite(numPrice) || numPrice <= 0) {
        return res.status(400).json({ error: 'السعر غير صالح' });
    }
    let idx = appStore.priceBook.findIndex(p => (id && p.id === id) || (name && p.name === name));
    if (idx !== -1) {
        appStore.priceBook[idx].price = Math.round(numPrice);
        appStore.priceBook[idx].userVerified = true;
        appStore.priceBook[idx].updatedAt = new Date().toISOString().split('T')[0];
    } else if (name) {
        appStore.priceBook.push({
            id: 'custom-' + Date.now(),
            name: String(name).trim(),
            unit: unit || 'وحدة',
            price: Math.round(numPrice),
            category: category || 'Food',
            essential: true,
            userVerified: true,
            updatedAt: new Date().toISOString().split('T')[0]
        });
    }
    saveStore(appStore);
    res.json({ priceBook: appStore.priceBook });
});

function applyConversationalActions(actions = []) {
    const executed = [];
    if (!Array.isArray(actions)) return executed;

    for (const act of actions) {
        if (!act || !act.type) continue;

        if (act.type === 'purchase_and_learn_price') {
            const amt = Number(act.actualPrice);
            const itemName = String(act.itemName || 'مشتريات يومية').trim();
            if (itemName && Number.isFinite(amt) && amt > 0) {
                const created = {
                    id: Date.now() + Math.floor(Math.random() * 1000),
                    name: itemName,
                    amount: Math.round(amt),
                    category: act.category || 'Food',
                    date: new Date().toISOString().split('T')[0],
                    notes: 'تم الشراء واعتماد السعر الفعلي (طرطوس)'
                };
                appStore.expenses.push(created);

                const pIdx = appStore.priceBook.findIndex(
                    p => p.name === itemName || itemName.includes(p.name) || p.name.includes(itemName)
                );
                if (pIdx !== -1) {
                    appStore.priceBook[pIdx].price = Math.round(amt);
                    appStore.priceBook[pIdx].userVerified = true;
                } else {
                    appStore.priceBook.push({
                        id: 'learned-' + Date.now(),
                        name: itemName,
                        unit: 'وحدة',
                        price: Math.round(amt),
                        category: act.category || 'Food',
                        essential: true,
                        userVerified: true
                    });
                }
                executed.push({ type: 'purchase_and_learn_price', item: created });
            }
        } else if (act.type === 'update_price_book' && act.itemName) {
            const amt = Number(act.newPrice);
            if (Number.isFinite(amt) && amt > 0) {
                const pIdx = appStore.priceBook.findIndex(
                    p => p.name.includes(act.itemName) || act.itemName.includes(p.name)
                );
                if (pIdx !== -1) {
                    appStore.priceBook[pIdx].price = Math.round(amt);
                    appStore.priceBook[pIdx].userVerified = true;
                } else {
                    appStore.priceBook.push({
                        id: 'learned-' + Date.now(),
                        name: act.itemName,
                        unit: 'وحدة',
                        price: Math.round(amt),
                        category: 'Food',
                        essential: true,
                        userVerified: true
                    });
                }
                executed.push({ type: 'update_price_book', itemName: act.itemName, price: amt });
            }
        } else if (act.type === 'add_expense' && act.expense) {
            const amt = Number(act.expense.amount);
            if (act.expense.name && Number.isFinite(amt) && amt > 0) {
                const created = {
                    id: Date.now() + Math.floor(Math.random() * 1000),
                    name: String(act.expense.name).trim(),
                    amount: Math.round(amt),
                    category: act.expense.category || 'Food',
                    date: act.expense.date || new Date().toISOString().split('T')[0],
                    notes: act.expense.notes || 'أُضيف عبر محادثة Ryn'
                };
                appStore.expenses.push(created);
                executed.push({ type: 'add_expense', item: created });
            }
        } else if (act.type === 'update_expense') {
            let idx = -1;
            if (act.id !== undefined) {
                idx = appStore.expenses.findIndex(e => Number(e.id) === Number(act.id));
            }
            if (idx === -1 && act.matchName) {
                idx = appStore.expenses.findIndex(e => e.name && (e.name.includes(act.matchName) || act.matchName.includes(e.name)));
            }
            if (idx !== -1 && act.updates) {
                const curr = appStore.expenses[idx];
                const nextAmt = act.updates.amount !== undefined ? Number(act.updates.amount) : curr.amount;
                appStore.expenses[idx] = {
                    ...curr,
                    name: act.updates.name ? String(act.updates.name).trim() : curr.name,
                    amount: Number.isFinite(nextAmt) && nextAmt > 0 ? Math.round(nextAmt) : curr.amount,
                    category: act.updates.category || curr.category,
                    date: act.updates.date || curr.date,
                    notes: act.updates.notes !== undefined ? act.updates.notes : curr.notes
                };
                executed.push({ type: 'update_expense', item: appStore.expenses[idx] });
            }
        } else if (act.type === 'delete_expense') {
            let idx = -1;
            if (act.id !== undefined) {
                idx = appStore.expenses.findIndex(e => Number(e.id) === Number(act.id));
            }
            if (idx === -1 && act.matchName) {
                idx = appStore.expenses.findIndex(e => e.name && (e.name.includes(act.matchName) || act.matchName.includes(e.name)));
            }
            if (idx !== -1) {
                const deleted = appStore.expenses.splice(idx, 1)[0];
                executed.push({ type: 'delete_expense', item: deleted });
            }
        } else if (act.type === 'set_budget') {
            const b = Number(act.budget);
            if (Number.isFinite(b) && b > 0) {
                appStore.budget = Math.round(b);
                executed.push({ type: 'set_budget', budget: appStore.budget });
            }
        } else if (act.type === 'add_memory' && act.fact) {
            const factStr = String(act.fact).trim();
            if (factStr && !appStore.memoryFacts.includes(factStr)) {
                appStore.memoryFacts.push(factStr);
                executed.push({ type: 'add_memory', fact: factStr });
            }
        }
    }

    if (executed.length > 0) {
        saveStore(appStore);
    }
    return executed;
}

app.post('/api/ryn/chat', async (req, res) => {
    try {
        const { message, model, autoFallback, apiKey } = req.body || {};
        if (!message || !String(message).trim()) {
            return res.status(400).json({ error: 'الرسالة مطلوبة' });
        }

        const userMsgEntry = {
            id: 'u-' + Date.now(),
            sender: 'user',
            text: String(message).trim(),
            timestamp: new Date().toISOString()
        };
        appStore.chatHistory.push(userMsgEntry);

        const aiResult = await chatRyn({
            message: String(message).trim(),
            expenses: appStore.expenses,
            budget: appStore.budget,
            priceBook: appStore.priceBook,
            memory: appStore.memoryFacts,
            history: appStore.chatHistory.slice(-10),
            model: model || appStore.preferredModel || 'auto',
            autoFallback: autoFallback !== false,
            apiKey
        });

        const actionsExecuted = applyConversationalActions(aiResult.actions);

        const rynMsgEntry = {
            id: 'r-' + (Date.now() + 1),
            sender: 'ryn',
            text: aiResult.text,
            translation: aiResult.translation || '',
            modelUsed: aiResult.modelUsed,
            fallbackUsed: aiResult.fallbackUsed,
            actionsExecuted,
            timestamp: new Date().toISOString()
        };
        appStore.chatHistory.push(rynMsgEntry);
        saveStore(appStore);

        res.json({
            text: aiResult.text,
            translation: aiResult.translation || '',
            modelUsed: aiResult.modelUsed,
            fallbackUsed: aiResult.fallbackUsed,
            actionsExecuted,
            state: {
                expenses: appStore.expenses,
                budget: appStore.budget,
                priceBook: appStore.priceBook,
                memoryFacts: appStore.memoryFacts
            }
        });
    } catch (error) {
        console.error('RYN Chat Error:', error);
        res.status(500).json({ error: 'حدث خطأ أثناء التواصل مع المساعد الذكي Ryn' });
    }
});

app.post('/api/ai/analyze', async (req, res) => {
    try {
        const { expenses, model, autoFallback, apiKey } = req.body || {};
        const targetExpenses = Array.isArray(expenses) && expenses.length > 0 ? expenses : appStore.expenses;

        if (!targetExpenses || targetExpenses.length === 0) {
            return res.status(400).json({ error: 'قائمة المصاريف فارغة حالياً' });
        }

        const analysis = await analyzeExpenses(targetExpenses, {
            model: model || appStore.preferredModel || 'auto',
            autoFallback: autoFallback !== false,
            budget: appStore.budget,
            memory: appStore.memoryFacts,
            apiKey
        });
        res.json(analysis);
    } catch (error) {
        res.status(500).json({ error: 'فشل تحليل المصاريف' });
    }
});

app.get('/api/memory', (req, res) => {
    res.json(appStore);
});

app.post('/api/memory/sync', (req, res) => {
    try {
        const incoming = req.body || {};
        if (Array.isArray(incoming.expenses)) appStore.expenses = incoming.expenses;
        if (Number.isFinite(Number(incoming.budget)) && Number(incoming.budget) > 0) {
            appStore.budget = Number(incoming.budget);
        }
        if (Array.isArray(incoming.priceBook)) appStore.priceBook = incoming.priceBook;
        if (incoming.categorySplits) appStore.categorySplits = incoming.categorySplits;
        if (Array.isArray(incoming.memoryFacts)) appStore.memoryFacts = incoming.memoryFacts;
        if (Array.isArray(incoming.chatHistory)) appStore.chatHistory = incoming.chatHistory;
        if (incoming.preferredModel) appStore.preferredModel = incoming.preferredModel;

        saveStore(appStore);
        res.json({ message: 'تمت مزامنة الذاكرة بنجاح', store: appStore });
    } catch (error) {
        res.status(500).json({ error: 'فشل مزامنة ملف الذاكرة' });
    }
});

app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

const PORT = process.env.PORT || 3000;
if (require.main === module) {
    const assert = require('assert');
    assert.strictEqual(typeof verifyToken, 'function', 'Verify token middleware must be a function');

    server.listen(PORT, () => {
        console.log(`✅ Server running on port ${PORT}`);
    });
}

module.exports = { app, server };