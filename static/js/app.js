/* ===================================================================
   Enterprise IT Support — Agentic RAG Copilot  •  Frontend Logic
   =================================================================== */

// ─── DOM refs ───
const chat = document.getElementById('chat');
const form = document.getElementById('chatForm');
const questionEl = document.getElementById('question');
const traceEl = document.getElementById('trace');
const sourceUsed = document.getElementById('sourceUsed');
const sendBtn = document.getElementById('sendBtn');
const sidebar = document.getElementById('sidebar');
const hamburger = document.getElementById('hamburger');
const sidebarOverlay = document.getElementById('sidebarOverlay');

// ─── Helpers ───
function escapeHtml(s = '') {
    return s.replace(/[&<>'"]/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
}

function formatText(s = '') {
    return escapeHtml(s).replace(/\n/g, '<br>');
}

// ─── Classify trace steps for color-coding ───
function traceClass(text) {
    const t = text.toLowerCase();
    if (t.includes('router') || t.includes('route')) return 'trace-router';
    if (t.includes('retriev')) return 'trace-retrieval';
    if (t.includes('grade')) return 'trace-grade';
    if (t.includes('web') || t.includes('tavily')) return 'trace-web';
    if (t.includes('rewrite')) return 'trace-rewrite';
    if (t.includes('answer') || t.includes('generation')) return 'trace-answer';
    if (t.includes('error') || t.includes('fail')) return 'trace-error';
    return '';
}

// ─── Add message bubble ───
function addMessage(role, text, source = '', citations = []) {
    const wrap = document.createElement('div');
    wrap.className = `message ${role}`;

    const citeHtml = citations.length
        ? `<div class="citations">
               <strong>Sources</strong>
               ${citations.map(c =>
            c.url
                ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>`
                : escapeHtml(c.title)
        ).join('<br>')}
           </div>`
        : '';

    const sourceHtml = source
        ? `<div class="answer-source">Source: ${escapeHtml(source)}</div>`
        : '';

    wrap.innerHTML = `
        <div class="avatar">AI</div>
        <div class="bubble">
            ${formatText(text)}
            ${sourceHtml}
            ${citeHtml}
        </div>`;

    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
}

// ─── Loading indicator ───
function addLoading() {
    const wrap = document.createElement('div');
    wrap.className = 'message assistant';
    wrap.id = 'loading-msg';
    wrap.innerHTML = `
        <div class="avatar">AI</div>
        <div class="bubble">
            <div class="loading-dots">
                <span></span><span></span><span></span>
            </div>
        </div>`;
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
}

function removeLoading() {
    const el = document.getElementById('loading-msg');
    if (el) el.remove();
}

// ─── Render trace ───
function renderTrace(items = []) {
    if (!items.length) {
        traceEl.innerHTML = '<div class="empty">No trace.</div>';
        return;
    }
    traceEl.innerHTML = items.map((x, i) =>
        `<div class="trace-item ${traceClass(x)}" style="animation-delay:${i * 80}ms">${escapeHtml(x)}</div>`
    ).join('');
}

// ─── Ask the agent ───
async function askAgent(q) {
    addMessage('user', q);
    questionEl.value = '';
    autoResize();
    renderTrace(['⏳ Running LangGraph workflow…']);
    sourceUsed.textContent = '⏳ Running…';
    sendBtn.disabled = true;
    addLoading();

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Request failed');

        removeLoading();
        addMessage('assistant', data.answer, data.source_used, data.citations || []);
        renderTrace(data.trace || []);
        sourceUsed.textContent = data.source_used || '—';
    } catch (e) {
        removeLoading();
        addMessage('assistant', `⚠️ Error: ${e.message}`);
        renderTrace(['❌ Request failed']);
        sourceUsed.textContent = 'Error';
    } finally {
        sendBtn.disabled = false;
        questionEl.focus();
    }
}

// ─── Auto-resize textarea ───
function autoResize() {
    questionEl.style.height = 'auto';
    questionEl.style.height = Math.min(questionEl.scrollHeight, 120) + 'px';
}

questionEl.addEventListener('input', autoResize);

// ─── Submit via form ───
form.addEventListener('submit', e => {
    e.preventDefault();
    const q = questionEl.value.trim();
    if (q) askAgent(q);
});

// ─── Enter to send, Shift+Enter for newline ───
questionEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const q = questionEl.value.trim();
        if (q) askAgent(q);
    }
});

// ─── Example buttons ───
document.querySelectorAll('.example').forEach(btn =>
    btn.addEventListener('click', () => {
        askAgent(btn.textContent.trim());
    })
);

// ─── Mobile sidebar ───
hamburger.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    sidebarOverlay.classList.toggle('active');
});

sidebarOverlay.addEventListener('click', () => {
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('active');
});

// ─── Upload modal ───
const modal = document.getElementById('uploadModal');
document.getElementById('openUpload').addEventListener('click', () => {
    modal.classList.remove('hidden');
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('active');
});

document.getElementById('closeUpload').addEventListener('click', () => {
    modal.classList.add('hidden');
});

// Close modal on backdrop click
modal.addEventListener('click', e => {
    if (e.target === modal) modal.classList.add('hidden');
});

// Close modal on Escape
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
        modal.classList.add('hidden');
    }
});

document.getElementById('uploadBtn').addEventListener('click', async () => {
    const file = document.getElementById('fileInput').files[0];
    const key = document.getElementById('adminKey').value;
    const status = document.getElementById('uploadStatus');

    if (!file) {
        status.textContent = '⚠️ Choose a file first.';
        return;
    }

    status.textContent = '⏳ Indexing document…';
    const fd = new FormData();
    fd.append('file', file);

    try {
        const r = await fetch('/api/ingest', {
            method: 'POST',
            headers: { 'X-Admin-Key': key },
            body: fd
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Upload failed');
        status.textContent = `✅ Indexed ${d.file}: ${d.chunks} chunks created.`;
    } catch (e) {
        status.textContent = `❌ Error: ${e.message}`;
    }
});