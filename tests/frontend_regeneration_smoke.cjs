// Load scripts separately, as the browser does. Exercise real submit/lifecycle functions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '..', 'frontend');
const page = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const listeners = new Map();
const nodes = new Map([...page.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', textContent: '', hidden: false, disabled: false, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}},
    querySelector() {this.buttonText ??= {textContent: ''}; return this.buttonText;},
    addEventListener(event, handler) {listeners.set(`${id}/${event}`, handler);}
}]));
const sandbox = {console, URL, setTimeout, clearTimeout,
    document: {addEventListener() {}, getElementById: id => nodes.get(id), querySelector: () => null, querySelectorAll: () => []},
    window: {addEventListener() {}, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {}}};
vm.createContext(sandbox);
for (const name of ['api.js', 'tasks.js', 'settings.js', 'gpu-runtime.js','script.js', 'note-view.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, name), 'utf8'), sandbox, {filename: name});
}
const run = code => vm.runInContext(code, sandbox);
const sent = [], toasts = [], outputModes = [];
sandbox.captureToast = message => toasts.push(message);
sandbox.captureOutput = mode => outputModes.push(mode);
run(`initSelectPickers = () => {}; pollModelDownload = () => {}; bindEvents();
    flushPendingPrefs = () => {}; selectedProcessingMode = () => 'reuse';
    setOutputMode = mode => captureOutput(mode);
    validateAndBuildRequestBase = () => ({sourceType:'local',videoUrl:'',transcriptOnly:false,modelConfig:{name:'test'}});
    buildSummarizeConfig = (url,upload,resume,restart) => ({resume_task_id:resume,processing_mode:restart?'restart':'reuse'});
    loadRecentTasks = () => {}; addLog = () => {}; showToast = captureToast;
    schedulePoll = () => {}; renderAgentRunInfo = () => {}; clearLogs = () => {};
    refreshWhisperModelHints = () => {};
    updateProgress = () => {}; updateStep = () => {}; renderTaskAdvisory = () => {};
    setSourceControlsDisabled = () => {};
`);
const freshView = () => run(`isSubmitting=false; isTaskActive=false; currentTaskId='old-note'; sourceTaskId='old-note'; currentTaskStatus='completed'; currentMarkdown='# Previous note'; currentHtml='';`);
(async () => {
    freshView();
    let resolve;
    sandbox.fetch = (url, options) => {
        sent.push(JSON.parse(options.body));
        return new Promise(done => {resolve = done;});
    };
    assert.equal(nodes.has('regenerateBtn'), false);
    assert.equal(nodes.has('regenerateStoppedBtn'), false);
    assert.equal(nodes.has('retryBtn'), false);
    const submitted = listeners.get('submitBtn/click')();
    assert.equal(sent[0].generation_action, 'regenerate');
    assert.equal(sent[0].resume_task_id, 'old-note');
    assert.equal(sent[0].processing_mode, 'reuse');
    assert.equal(outputModes.length, 0); // The shared button respects the chosen output.
    assert.equal(run('currentMarkdown'), '# Previous note');
    assert.equal(nodes.get('submitBtn').disabled, true);
    resolve({ok:true,json:async()=>({task_id:'new-note',reused_task_id:'old-note'})});
    await submitted;
    assert.equal(run('currentTaskId'), 'new-note');

    freshView();
    sandbox.fetch = async (url, options) => {
        sent.push(JSON.parse(options.body));
        throw new Error('Submission unavailable');
    };
    await listeners.get('submitBtn/click')();
    assert.equal(run('currentTaskId'), 'old-note');
    assert.equal(run('currentMarkdown'), '# Previous note');
    assert.equal(nodes.get('submitBtn').disabled, false);
    assert.equal(toasts.at(-1), 'Submission unavailable');

    freshView();
    run("setTaskState('failed', '失败'); resetSubmitLabel();");
    assert.equal(nodes.get('submitBtn').buttonText.textContent, '继续生成');
    await listeners.get('submitBtn/click')();
    assert.equal(sent.at(-1).generation_action, 'continue');
    assert.equal(sent.at(-1).resume_task_id, 'old-note');
    freshView();
    run('resetSubmitLabel(true);');
    assert.equal(run('sourceTaskId'), null);
    assert.equal(nodes.get('submitBtn').buttonText.textContent, '开始生成');
    run(`validateAndBuildRequestBase = () => ({sourceType:'url',videoUrl:'https://youtu.be/new-source',transcriptOnly:false,modelConfig:{name:'test'}});`);
    await listeners.get('submitBtn/click')();
    assert.equal(sent.at(-1).generation_action, 'regenerate');
    assert.equal(sent.at(-1).resume_task_id, null);
    run(`markTaskCancelled('Stopped', 1)`);
    assert.equal(nodes.get('taskError').hidden, false);
    assert.equal(nodes.get('submitBtn').disabled, false);
    assert.equal(nodes.get('submitBtn').buttonText.textContent, '继续生成');
    console.log('Fresh regeneration, explicit continuation, previous-note preservation and cancelled-task actions passed');
})().catch(error => {console.error(error);process.exitCode=1;});
