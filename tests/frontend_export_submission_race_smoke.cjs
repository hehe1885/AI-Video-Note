// Real export/submission functions with controlled responses; no network or credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '..', 'frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
const response = data => ({ ok: true, json: async () => data });
function harness() {
    function element(tag = '') {
        const classes = new Set();
        const node = { tagName: tag.toUpperCase(), value: '', hidden: false, disabled: false,
            textContent: '', innerHTML: '', children: [], dataset: {}, style: {},
            scrollWidth: 1000, scrollHeight: 1000,
            classList: {
                add: name => classes.add(name), remove: name => classes.delete(name),
                contains: name => classes.has(name),
                toggle(name, active) { const on = active ?? !classes.has(name); if (on) classes.add(name); else classes.delete(name); }
            },
            append(...items) { this.children.push(...items); },
            appendChild(item) { this.children.push(item); },
            replaceChildren(...items) { this.children = items; },
            remove() {}, scrollIntoView() {}, addEventListener() {}, setAttribute() {},
            querySelector() { return this.buttonText ??= { textContent: '' }; },
            querySelectorAll() { return []; }
        };
        if (tag === 'canvas') {
            node.getContext = () => ({ fillRect() {}, drawImage(source) { node.rendered = source.rendered; } });
            node.toBlob = callback => callback(new Blob([node.rendered || '']));
        }
        return node;
    }
    const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, element()]));
    const sandbox = { console, URL, Blob, downloads: [], failures: [], toasts: [],
        document: { addEventListener() {}, getElementById: id => nodes.get(id), createElement: element,
            body: element('body'), querySelector: () => null, querySelectorAll: () => [] },
        window: { addEventListener() {}, setTimeout: () => 1, clearTimeout() {},
            setInterval: () => 1, clearInterval() {}, confirm: () => true }
    };
    vm.createContext(sandbox);
    for (const name of ['api.js', 'tasks.js', 'settings.js', 'gpu-runtime.js', 'script.js', 'note-view.js']) {
        vm.runInContext(fs.readFileSync(path.join(frontend, name), 'utf8'), sandbox, { filename: name });
    }
    const run = code => vm.runInContext(code, sandbox);
    run(`resetElapsedTimer=()=>{}; stopElapsedTimer=()=>{}; startElapsedTimer=()=>{};
        renderTaskAdvisory=()=>{}; clearLogs=()=>{}; updateProgress=()=>{}; updateStep=()=>{};
        addLog=()=>{}; renderAgentRunInfo=()=>{}; loadRecentTasks=()=>{};
        refreshWhisperModelHints=()=>{}; toggleSourceType=()=>{}; updateGenerationSettings=()=>{};
        setSourceControlsDisabled=()=>{}; schedulePoll=()=>{};
        showToast=message=>toasts.push(message);
        failTask=message=>{failures.push(message);currentTaskStatus='failed';};
        renderMarkdown=source=>source;
        triggerBlobDownload=(blob,extension,filename)=>downloads.push({blob,extension,filename});
        flushPendingPrefs=()=>{}; selectedProcessingMode=()=> 'reuse'; isBilibiliUrl=()=>false;
        validateAndBuildRequestBase=()=>({sourceType:'url',videoUrl:'https://example.com/video',
            transcriptOnly:false,modelConfig:{name:'test'}});
        buildSummarizeConfig=()=>({});
        waitForExportImages=async()=>{};`);
    async function select(id, { completed = false, markdown = `# ${id}`, name = `Video ${id}` } = {}) {
        sandbox.selection = { id, markdown, name };
        run(`resetTaskView(); currentTaskId=selection.id; sourceTaskId=selection.id;`);
        if (completed) await run('completeTask({markdown:selection.markdown},1,selection.name)');
        else run('setTaskActive(true); currentTaskStatus="processing";');
    }
    return { sandbox, nodes, run, select };
}

async function subtitleSnapshot() {
    for (const outcome of ['success', 'failure']) {
        const h = harness();
        await h.select('A', { completed: true });
        const pending = deferred();
        let requested;
        h.sandbox.fetch = url => { requested = url; return pending.promise; };
        const exporting = h.run('exportSubtitle()');
        // Reopening the same ID must still give the new view ownership of its controls.
        await h.select('A');
        h.run("currentDownloadName='Replacement A'");
        if (outcome === 'success') pending.resolve(response({ segments: [{ start: 0, end: 1, text: 'Subtitle A' }] }));
        else pending.reject(new Error('Old subtitle request failed'));
        await exporting;
        assert.match(requested, /\/task\/A\/transcript\?output_format=json$/);
        assert.equal(h.nodes.get('exportSubtitleBtn').disabled, true, 'An old export must not enable a new processing view');
        if (outcome === 'success') {
            const result = h.sandbox.downloads.at(-1);
            assert.equal(result.filename, 'Video A.srt');
            assert.match(await result.blob.text(), /Subtitle A/);
        } else assert.equal(h.sandbox.downloads.length, 0);
    }
}

async function imageSnapshot(layout) {
    const h = harness();
    await h.select('A', { completed: true, markdown: '# Image content A' });
    h.nodes.get('imageLayoutSelect').value = layout;
    const fonts = deferred();
    h.sandbox.document.fonts = { ready: fonts.promise };
    h.sandbox.html2canvas = async sheet => ({ width: 1000, height: layout === 'portrait' ? 2500 : 1000,
        rendered: sheet.innerHTML, toBlob(callback) { callback(new Blob([this.rendered])); } });
    const exporting = h.run('exportSummaryImage()');
    await h.select('B');
    h.run("currentDownloadName='Video B'");
    h.nodes.get('imageLayoutSelect').value = layout === 'portrait' ? 'long' : 'portrait';
    fonts.resolve();
    await exporting;
    const results = h.sandbox.downloads;
    if (layout === 'portrait') {
        assert.ok(results.length > 1, 'The original portrait layout must survive a later layout change');
        for (const [index, result] of results.entries()) {
            assert.equal(result.filename, `Video A-page-${String(index + 1).padStart(3, '0')}.png`);
            assert.equal(await result.blob.text(), '# Image content A');
        }
    } else {
        assert.equal(results.length, 1, 'The original long layout must survive a later layout change');
        assert.equal(results[0].filename, 'Video A.png');
        assert.equal(await results[0].blob.text(), '# Image content A');
    }
    assert.equal(h.nodes.get('downloadMdBtn').disabled, true);
    assert.equal(h.run('isDownloading'), false);
}

async function serverDownloadSnapshot() {
    for (const format of ['markdown', 'pdf']) {
        const h = harness();
        await h.select('A', { completed: true });
        const body = deferred();
        let requested;
        h.sandbox.fetch = async url => {
            requested = url;
            return { ok: true, headers: { get: () => '' }, blob: () => body.promise };
        };
        const exporting = h.run(`downloadMarkdownFile('${format}')`);
        await h.select('B');
        h.run("currentDownloadName='Video B'");
        body.resolve(new Blob(['Original A payload']));
        await exporting;
        const result = h.sandbox.downloads.at(-1);
        const extension = format === 'markdown' ? '.md' : '.pdf';
        assert.equal(result.filename, 'Video A' + extension, 'Fallback filenames belong to the requested task');
        assert.equal(await result.blob.text(), 'Original A payload');
        assert.equal(requested, '/api/download/A' + (format === 'markdown' ? '' : '?format=pdf'));
        assert.equal(h.nodes.get('downloadMdBtn').disabled, true);
    }
}

async function downloadOwnership() {
    const h = harness();
    await h.select('A', { completed: true });
    const first = deferred(), second = deferred();
    h.sandbox.fetch = url => (url.endsWith('/A') ? first.promise : second.promise);
    const oldExport = h.run('downloadMarkdownFile()');
    await h.select('B', { completed: true });
    const newExport = h.run('downloadMarkdownFile()');
    first.resolve({ ok: true, headers: { get: () => '' }, blob: async () => new Blob(['A payload']) });
    await oldExport;
    assert.equal(h.run('isDownloading'), true, 'An old completion must not clear a new export loading state');
    assert.equal(h.nodes.get('downloadMdBtn').disabled, true);
    assert.equal(h.nodes.get('downloadMdBtn').classList.contains('is-loading'), true);
    second.resolve({ ok: true, headers: { get: () => '' }, blob: async () => new Blob(['B payload']) });
    await newExport;
    assert.equal(h.run('isDownloading'), false);
    assert.equal(h.nodes.get('downloadMdBtn').disabled, false);
    assert.equal(h.nodes.get('downloadMdBtn').classList.contains('is-loading'), false);
    assert.deepEqual(h.sandbox.downloads.map(result => result.filename), ['Video A.md', 'Video B.md']);
}

async function submissionOwnership() {
    for (const outcome of ['success', 'failure']) {
        const h = harness();
        await h.select('A', { completed: true });
        h.run("currentTaskStatus='failed'");
        const pending = deferred();
        let submitted;
        h.sandbox.fetch = (url, options) => {
            if (url.endsWith('/summarize')) { submitted = JSON.parse(options.body); return pending.promise; }
            return Promise.resolve(response({ status: 'completed', result: { markdown: '# Note B' }, download_name: 'Video B' }));
        };
        const submitting = h.run("startSummary({resumeCurrent:true,generationAction:'continue'})");
        assert.equal(h.run('isSubmitting'), true);
        await h.run("openRecentTask('B')");
        if (outcome === 'success') pending.resolve(response({ task_id: 'A-created' }));
        else pending.reject(new Error('Old submission failed'));
        await submitting;
        assert.equal(submitted.generation_action, 'continue');
        assert.equal(h.run('currentTaskId'), 'B', 'Late submissions must not take over a newer selection');
        assert.equal(h.run('currentMarkdown'), '# Note B');
        assert.equal(h.run('currentDownloadName'), 'Video B');
        assert.equal(h.run('currentTaskStatus'), 'completed');
        assert.equal(h.run('isSubmitting'), false);
        assert.equal(h.nodes.get('submitBtn').disabled, false);
        assert.equal(h.sandbox.failures.length, 0);
    }
}

async function uploadConfigSnapshot() {
    const h = harness();
    await h.select('A', { completed: true });
    const upload = deferred(), submitted = deferred();
    h.sandbox.uploadPromise = upload.promise;
    h.nodes.get('customPrompt').value = 'A instructions';
    h.nodes.get('noteLanguage').value = 'zh-CN';
    h.nodes.get('generationMode').value = 'agent';
    h.run(`validateAndBuildRequestBase=()=>({sourceType:'local',videoUrl:'',file:{name:'A.mp4'},
            transcriptOnly:false,modelConfig:{name:'A model'}});
        buildSummarizeConfig=()=>({custom_prompt:byId('customPrompt').value,
            note_language:byId('noteLanguage').value,generation_mode:byId('generationMode').value});
        screenshotsEnabled=()=>false; uploadLocalFile=()=>uploadPromise;`);
    let payload;
    h.sandbox.fetch = (_url, options) => { payload = JSON.parse(options.body); return submitted.promise; };
    const submitting = h.run('startSummary()');
    await h.select('B', { completed: true });
    h.nodes.get('customPrompt').value = 'B instructions';
    h.nodes.get('noteLanguage').value = 'en';
    h.nodes.get('generationMode').value = 'api';
    upload.resolve({ task_id: 'upload-A', filename: 'A.mp4' });
    // Resolving the request first is safe: startSummary still consumes it only after upload.
    submitted.resolve(response({ task_id: 'A-created' }));
    await submitting;
    assert.deepEqual(payload, { custom_prompt: 'A instructions', note_language: 'zh-CN',
        generation_mode: 'agent', generation_action: 'regenerate', upload_task_id: 'upload-A' });
    assert.equal(h.run('currentTaskId'), 'B');
    assert.equal(h.run('currentMarkdown'), '# B');
    assert.equal(h.run('currentDownloadName'), 'Video B');
    assert.equal(h.run('isSubmitting'), false);
}

(async () => {
    await subtitleSnapshot();
    await imageSnapshot('long');
    await imageSnapshot('portrait');
    await serverDownloadSnapshot();
    await downloadOwnership();
    await submissionOwnership();
    await uploadConfigSnapshot();
    console.log('Export snapshots, pagination, loading ownership, late submissions and upload configuration races passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
