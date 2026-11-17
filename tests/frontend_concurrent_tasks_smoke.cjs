const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const frontend = path.join(__dirname, '..', 'frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const ready = [];
let activeElement = null;

class Element {
    constructor(tag = 'div', id = '') {
        this.tagName = tag;
        this.id = id;
        this.className = '';
        this.children = [];
        this.events = {};
        this.hidden = false;
        this.disabled = false;
        this.checked = false;
        this.readOnly = false;
        this.dataset = {};
        this.attributes = {};
        this.value = '';
        this.title = '';
        this.textContent = '';
        this.files = [];
        this.paused = true;
        this.currentTime = 0;
        this.readyState = 0;
        this.duration = NaN;
        this.style = { setProperty() {} };
        const classes = new Set();
        this.classList = {
            add: (...names) => names.forEach(n => classes.add(n)),
            remove: (...names) => names.forEach(n => classes.delete(n)),
            toggle: (name, force) => (force === undefined ? (classes.has(name) ? classes.delete(name) : classes.add(name)) : (force ? classes.add(name) : classes.delete(name))),
            contains: name => classes.has(name)
        };
    }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = [...items]; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, callback) { this.events[name] = callback; }
    focus() { activeElement = this; }
    reportValidity() { return Boolean(this.value.trim()); }
    pause() { if (!this.paused) { this.paused = true; this.events.pause?.(); } }
    play() { this.paused = false; this.readyState = 4; this.events.play?.(); return Promise.resolve(); }
    load() {}
    scrollIntoView() {}
    querySelector(selector) {
        if (selector === '.btn-text') {
            this.btnText ??= new Element('span');
            return this.btnText;
        }
        return this.querySelectorAll(selector)[0] || null;
    }
    querySelectorAll(selector) {
        const all = [];
        const walk = node => {
            for (const child of node.children) {
                if (typeof child === 'object' && child) {
                    if (child.tagName === 'textarea' || (selector.includes('button') && child.tagName === 'button' && !child.disabled)) {
                        all.push(child);
                    }
                    walk(child);
                }
            }
        };
        walk(this);
        return all;
    }
}

const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, new Element('div', id)]));
nodes.get('sourceType').value = 'url';
nodes.get('transcriptReviewModal').hidden = true;
nodes.get('recentTasksPanel').hidden = true;
nodes.get('activeTasksBadge').hidden = true;

const toasts = [];
const confirmations = [];
const serverTasks = new Map();
const reviewPayloads = new Map();
const reviewConfirmPosts = [];
const cancelPosts = [];
let confirmNextDuplicate = false;
let seq = 0;

const sandbox = {
    console, URL, Blob, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    document: {
        title: 'AI Video Note - 本地视频笔记',
        getElementById: id => nodes.get(id),
        createElement: tag => new Element(tag),
        querySelector: selector => (selector.includes('outputMode') ? { value: 'note' } : selector.includes('processingMode') ? { value: 'reuse' } : null),
        querySelectorAll: () => [],
        addEventListener: (name, fn) => { if (name === 'DOMContentLoaded') ready.push(fn); },
        get activeElement() { return activeElement; }
    },
    window: {
        addEventListener() {},
        confirm: msg => { confirmations.push(msg); return msg.includes('重复提交') ? confirmNextDuplicate : true; },
        setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}
    },
    captureToast: (msg, type) => toasts.push({ msg, type })
};

sandbox.fetch = async (url, options = {}) => {
    const clean = String(url).replace(/^\/api/, '');
    if (clean === '/summarize' && options.method === 'POST') {
        const body = JSON.parse(options.body || '{}');
        const id = `task-${++seq}`;
        serverTasks.set(id, {
            task_id: id,
            status: 'processing',
            progress: 10 * seq,
            title: `Video ${seq}`,
            source_url: body.video_url,
            created_at: `2026-11-16T10:0${seq}:00Z`,
            logs: [`Started ${id}`],
            transcript_review: null
        });
        return { ok: true, status: 200, json: async () => ({ task_id: id }) };
    }
    if (clean.startsWith('/tasks')) {
        const parsed = new URL(url, 'http://localhost');
        const statusFilter = parsed.searchParams.get('status') || '';
        const all = Array.from(serverTasks.values());
        const filtered = statusFilter === 'active'
            ? all.filter(t => ['pending', 'queued', 'processing', 'cancelling', 'awaiting_agent'].includes(t.status))
            : all;
        // Real /api/tasks rows are compact: they never carry source_url or uploaded filenames.
        const compact = filtered.map(({source_url, uploaded_filename, ...row}) => row);
        return {
            ok: true,
            status: 200,
            json: async () => ({ tasks: compact, total: compact.length, has_more: false, next_offset: compact.length, max_concurrent_tasks: 3 })
        };
    }
    const taskMatch = clean.match(/^\/task\/([^/]+)$/);
    if (taskMatch) {
        const t = serverTasks.get(decodeURIComponent(taskMatch[1]));
        return { ok: true, status: 200, json: async () => t };
    }
    const cancelMatch = clean.match(/^\/task\/([^/]+)\/cancel$/);
    if (cancelMatch && options.method === 'POST') {
        const id = decodeURIComponent(cancelMatch[1]);
        cancelPosts.push(id);
        const t = serverTasks.get(id);
        if (t) t.status = 'cancelled';
        return { ok: true, status: 200, json: async () => ({ cancelled: true, status: 'cancelled', elapsed_seconds: 12 }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
};

vm.createContext(sandbox);
for (const name of ['api.js', 'tasks.js', 'settings.js', 'gpu-runtime.js', 'script.js', 'note-view.js', 'transcript-review.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, name), 'utf8'), sandbox, { filename: name });
}
const run = code => vm.runInContext(code, sandbox);
ready.at(-1)();

run(`
    flushPendingPrefs = () => {};
    renderSelectedModelPaths = () => {};
    renderNoteSaveLocation = () => {};
    updateVisionCheckbox = () => {};
    updateFileInfo = () => {};
    toggleSourceType = () => {};
    updateGenerationSettings = () => {};
    renderAgentRunInfo = () => {};
    renderTaskAdvisory = () => {};
    startElapsedTimer = () => {};
    stopElapsedTimer = () => {};
    armRecentTasksElapsedTicker = () => {};
    recentTaskMetaText = t => t.status;
    updateProgress = () => {};
    updateStep = () => {};
    updateLogs = () => {};
    addLog = () => {};
    clearLogs = () => {};
    refreshWhisperModelHints = () => {};
    setTranscriptTaskView = () => {};
    showResult = async () => true;
    showToast = captureToast;
    schedulePoll = () => {};
    scheduleBackgroundTaskSync = () => {};
    validateAndBuildRequestBase = () => ({sourceType: byId('sourceType').value, videoUrl: byId('videoUrl').value, transcriptOnly: false, modelConfig: {name: 'deepseek'}});
    buildSummarizeConfig = url => ({video_url: url});
`);
sandbox.fetchWithTimeout = async (url, options = {}) => {
    const match = String(url).match(/\/task\/([^/]+)\/transcript-review(?:\?|$)/);
    if (match) {
        const taskId = decodeURIComponent(match[1]);
        if (options.method === 'POST') {
            reviewConfirmPosts.push(taskId);
            const t = serverTasks.get(taskId);
            if (t) t.transcript_review = { status: 'confirmed', revision: t.transcript_review?.revision || 'r1' };
            return { confirmed: true };
        }
        return reviewPayloads.get(taskId);
    }
    return sandbox.fetch(url, options);
};
run('fetchWithTimeout = globalThis.fetchWithTimeout; readResponse = async (res) => (res && typeof res.json === "function" ? res.json() : res);');

const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    assert.equal(sandbox.document.title, 'AI Video Note - 本地视频笔记');

    // Submit 3 tasks sequentially in one tab: 1 and 2 leave submission unlocked; 3 reaches cap.
    nodes.get('videoUrl').value = 'https://example.com/v1';
    run('updateClearVideoUrlButton();');
    assert.equal(nodes.get('clearVideoUrlBtn').hidden, false);
    await run('startSummary()');
    assert.equal(run('currentTaskId'), 'task-1');
    assert.equal(nodes.get('submitBtn').disabled, false, 'Submission stays unlocked after task 1');
    assert.equal(nodes.get('videoUrl').disabled, false);
    assert.equal(nodes.get('activeTasksBadge').hidden, false);
    assert.equal(nodes.get('activeTasksBadge').textContent, '正在处理 1/3');
    assert.equal(sandbox.document.title, '（正在处理 1/3）AI Video Note - 本地视频笔记');

    // Trying to accidentally resubmit exact same running URL prompts confirmation and cancels when declined.
    confirmNextDuplicate = false;
    await run('startSummary()');
    assert.equal(serverTasks.size, 1, 'Declining duplicate confirmation prevents resubmitting same running video');
    assert.ok(confirmations.some(msg => msg.includes('重复提交')));

    // The duplicate guard must survive background syncs: server rows do not carry source_url.
    await run('syncActiveTasks()');
    await tick();
    confirmations.length = 0;
    await run('startSummary()');
    assert.equal(serverTasks.size, 1, 'Duplicate guard still works after a background sync replaced the task rows');
    assert.ok(confirmations.some(msg => msg.includes('重复提交')), 'Duplicate confirmation is still shown after sync');

    // Clear URL button prepares cleanly for the next task.
    nodes.get('biliPagesPanel').hidden = false;
    run("biliPagesCurrent = { url: 'https://example.com/v1', payload: {} }; clearVideoUrlForNextTask();");
    assert.equal(nodes.get('videoUrl').value, '');
    assert.equal(nodes.get('clearVideoUrlBtn').hidden, true);
    assert.equal(nodes.get('biliPagesPanel').hidden, true);
    assert.equal(run('sourceTaskId'), null);

    nodes.get('videoUrl').value = 'https://example.com/v2';
    await run('startSummary()');
    assert.equal(run('currentTaskId'), 'task-2');
    assert.equal(nodes.get('submitBtn').disabled, false, 'Submission stays unlocked after task 2');
    assert.equal(nodes.get('activeTasksBadge').textContent, '正在处理 2/3');
    assert.equal(sandbox.document.title, '（正在处理 2/3）AI Video Note - 本地视频笔记');

    nodes.get('videoUrl').value = 'https://example.com/v3';
    await run('startSummary()');
    assert.equal(run('currentTaskId'), 'task-3');
    assert.equal(nodes.get('submitBtn').disabled, true, 'Submission locks when 3 concurrent tasks are running');
    assert.equal(nodes.get('videoUrl').disabled, true);
    assert.equal(nodes.get('clearVideoUrlBtn').disabled, true);
    assert.equal(nodes.get('activeTasksBadge').textContent, '正在处理 3/3');

    // A 4th submission attempt is rejected with concurrency toast.
    await run('startSummary()');
    assert.equal(serverTasks.size, 3);
    assert.match(toasts.at(-1).msg, /3/);

    // Viewing a stopped historical task while 3 tasks are active must keep the concurrency limit label on submitBtn.
    run("showStoppedTask({ status: 'cancelled', elapsed_seconds: 12 })");
    assert.equal(nodes.get('submitBtn').querySelector('.btn-text').textContent, '最多同时处理 3 个任务');
    run("setTaskActive(true); setTaskState('processing', '处理中');");

    // Typing a new draft URL while viewing a running task is preserved when opening another task in history.
    nodes.get('videoUrl').value = 'https://example.com/draft-next';
    run('sourceDraftDirty = true;');
    await run("openRecentTask('task-1')");
    assert.equal(run('currentTaskId'), 'task-1');
    assert.equal(nodes.get('videoUrl').value, 'https://example.com/draft-next', 'Checking history preserves user draft URL');

    // Background tasks 1 and 2 enter transcript review simultaneously -> only task-1 opens & title reflects pending review.
    serverTasks.get('task-1').transcript_review = { status: 'pending', revision: 'r1' };
    serverTasks.get('task-2').transcript_review = { status: 'pending', revision: 'r2' };
    serverTasks.get('task-3').progress = 55;
    reviewPayloads.set('task-1', { status: 'pending', revision: 'r1', items: [{ id: 's1', segment_index: 0, start: 0, end: 5, original: 'A', suggestion: 'A1', confidence: 0.9, reason: 'r' }], next_offset: null });
    reviewPayloads.set('task-2', { status: 'pending', revision: 'r2', items: [{ id: 's2', segment_index: 0, start: 0, end: 5, original: 'B', suggestion: 'B1', confidence: 0.9, reason: 'r' }], next_offset: null });

    await run('syncActiveTasks()');
    await tick();
    run("recentTasksSyncedAt = 1000; updateLiveHistoryRows([{ task_id: 'task-3', status: 'processing', progress: 55, elapsed_seconds: 30, _syncedAt: 1030 }]);");
    assert.equal(run("recentTaskRows.get('task-3').task._syncedAt"), 1030);
    assert.equal(nodes.get('transcriptReviewModal').hidden, false);
    assert.equal(nodes.get('transcriptReviewTaskName').textContent, 'Video 1');
    assert.equal(nodes.get('transcriptReviewQueueHint').hidden, false);
    assert.equal(sandbox.document.title, '【待核对字幕】AI Video Note - 本地视频笔记');
    assert.equal(run('recentTaskRows.get("task-2").status.textContent'), '待确认字幕');
    assert.equal(run('recentTaskRows.get("task-3").status.textContent'), '处理中 55%');
    assert.equal(run('recentTaskRows.get("task-2").cancelBtn.dataset.cancelTaskId'), 'task-2', 'Running rows expose inline cancel button');

    // Switch main view to task-3 while task-1 review is open -> task-2 still must not pop up.
    await run("openRecentTask('task-3')");
    await tick();
    assert.equal(nodes.get('transcriptReviewModal').hidden, false);
    assert.equal(nodes.get('transcriptReviewTaskName').textContent, 'Video 1');

    // Deferring task-1 closes the modal, then clicking task-1 in history re-opens the head review modal directly.
    nodes.get('transcriptReviewLaterBtn').events.click();
    await tick();
    assert.equal(nodes.get('transcriptReviewModal').hidden, true);
    await run("openRecentTask('task-1')");
    await tick();
    assert.equal(nodes.get('transcriptReviewModal').hidden, false, 'Clicking deferred pending task in history opens its review modal');
    assert.equal(nodes.get('transcriptReviewTaskName').textContent, 'Video 1');

    // Inline cancelling a background task (task-3) works without switching main view.
    await run("cancelTaskById('task-3')");
    assert.deepEqual(cancelPosts, ['task-3']);
    assert.equal(nodes.get('activeTasksBadge').textContent, '正在处理 2/3');

    // Submit task-1 review -> task-2 pops up immediately.
    nodes.get('transcriptReviewKeepBtn').events.click();
    await tick(); await tick();
    assert.deepEqual(reviewConfirmPosts, ['task-1']);
    assert.equal(nodes.get('transcriptReviewModal').hidden, false);
    assert.equal(nodes.get('transcriptReviewTaskName').textContent, 'Video 2');

    // Submit task-2 review -> modal closes cleanly and document title returns to active count.
    nodes.get('transcriptReviewKeepBtn').events.click();
    await tick(); await tick();
    assert.deepEqual(reviewConfirmPosts, ['task-1', 'task-2']);
    assert.equal(nodes.get('transcriptReviewModal').hidden, true);
    assert.equal(sandbox.document.title, '（正在处理 2/3）AI Video Note - 本地视频笔记');

    // Complete remaining active tasks -> scheduledPoll wakes viewed task-1, and completing it resets title to idle.
    serverTasks.get('task-1').status = 'completed';
    serverTasks.get('task-1').result = { markdown: '# Video 1 Note' };
    serverTasks.get('task-2').status = 'completed';
    serverTasks.get('task-2').progress = 100;
    let pollWoken = '';
    run("schedulePoll = id => { globalThis.__pollWoken = id; };");
    await run('syncActiveTasks()');
    await tick();
    assert.equal(run('globalThis.__pollWoken'), 'task-1', 'syncActiveTasks immediately triggers poll for newly finished viewed task');
    await run("pollTask('task-1')");
    await tick();
    assert.equal(nodes.get('activeTasksBadge').hidden, true);
    assert.equal(sandbox.document.title, 'AI Video Note - 本地视频笔记');
    assert.equal(nodes.get('submitBtn').disabled, false, 'Submission unlocks cleanly');
    assert.ok(toasts.some(t => t.msg.includes('Video 2') && t.type === 'success'), 'Background completion triggers toast');

    console.log('Single-tab 3-concurrent-task lifecycle, live history progress and serialized transcript review queue passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
