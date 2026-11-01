// Use real history loading, result rendering and completion with controlled async responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '..', 'frontend');
function element() {
    return { value: '', hidden: false, disabled: false, textContent: '', children: [], dataset: {}, style: {},
        classList: { add() {}, remove() {}, toggle() {} },
        append(...items) { this.children.push(...items); },
        replaceChildren(...items) { this.children = items; },
        scrollIntoView() {}, querySelector() { return element(); } };
}
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, element()]));
const sandbox = { console, URL, failures: [], toasts: [],
    document: { addEventListener() {}, getElementById: id => nodes.get(id), createElement: element },
    window: { addEventListener() {}, setTimeout: () => 1, clearTimeout() {}, clearInterval() {} } };
vm.createContext(sandbox);
vm.runInContext(['api.js', 'tasks.js', 'settings.js', 'gpu-runtime.js', 'script.js', 'note-view.js']
    .map(name => fs.readFileSync(path.join(frontend, name), 'utf8')).join('\n'), sandbox);
const run = code => vm.runInContext(code, sandbox);
run(`resetElapsedTimer=()=>{}; stopElapsedTimer=()=>{}; renderTaskAdvisory=()=>{};
    clearLogs=()=>{}; updateProgress=()=>{}; updateStep=()=>{}; addLog=()=>{};
    renderAgentRunInfo=()=>{}; loadRecentTasks=()=>{}; refreshWhisperModelHints=()=>{};
    setSubmitButton=()=>{}; toggleSourceType=()=>{}; updateGenerationSettings=()=>{};
    setTaskActive=value=>{isTaskActive=value;}; setTaskState=value=>{currentTaskStatus=value;};
    showToast=message=>toasts.push(message); failTask=message=>{failures.push(message);currentTaskStatus='failed';};
    renderMarkdown=source=>source;`);
function fresh(identifier, markdown = '# Current task') {
    sandbox.nextId = identifier;
    sandbox.nextMarkdown = markdown;
    run(`resetTaskView(); currentTaskId=nextId; currentMarkdown=nextMarkdown;
        currentTaskStatus='processing'; isTaskActive=true;`);
    nodes.get('outputPath').textContent = 'current-directory';
}
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
const response = data => ({ ok: true, json: async () => data });

(async () => {
    // Both late success and late failure must leave the selected task untouched.
    for (const outcome of ['success', 'failure']) {
        fresh('old');
        const request = deferred();
        sandbox.fetch = () => request.promise;
        const rendering = run("completeTask({output:'transcript',output_directory:'old-directory'})");
        fresh('new');
        const previousFailures = sandbox.failures.length;
        if (outcome === 'success') request.resolve(response({ text: '# Old transcript\n[00:01] Old content' }));
        else request.reject(new Error('Old request failed'));
        await rendering;
        assert.equal(run('currentTaskId'), 'new');
        assert.equal(run('currentMarkdown'), '# Current task');
        assert.equal(nodes.get('outputPath').textContent, 'current-directory');
        assert.equal(run('currentTaskStatus'), 'processing');
        assert.equal(run('isTaskActive'), true);
        assert.equal(sandbox.failures.length, previousFailures);
    }
    // Reopening the same ID still creates a distinct view revision.
    fresh('same');
    const previous = deferred();
    sandbox.fetch = () => previous.promise;
    const oldView = run("showResult({output:'transcript'})");
    fresh('same', '# Reopened');
    previous.resolve(response({ text: '# Previous view' }));
    assert.equal(await oldView, false);
    assert.equal(run('currentMarkdown'), '# Reopened');

    // Even synchronous note rendering has an await boundary in completeTask.
    fresh('note-old');
    const finishing = run("completeTask({markdown:'# Old note'})");
    fresh('note-new');
    await finishing;
    assert.equal(run('currentTaskStatus'), 'processing');
    assert.equal(run('isTaskActive'), true);

    // Requests for history entries can finish in the reverse of click order.
    fresh('original');
    const first = deferred(), second = deferred();
    sandbox.fetch = url => url.endsWith('/first') ? first.promise : second.promise;
    const firstSelection = run("openRecentTask('first')");
    const secondSelection = run("openRecentTask('second')");
    second.resolve(response({ status: 'completed', result: { markdown: '# Second note' } }));
    await secondSelection;
    first.resolve(response({ status: 'completed', result: { markdown: '# First note' } }));
    await firstSelection;
    assert.equal(run('currentTaskId'), 'second');
    assert.equal(run('currentMarkdown'), '# Second note');
    assert.equal(run('currentTaskStatus'), 'completed');

    // A reset while a history request is pending must not resurrect the old view.
    const history = deferred();
    sandbox.fetch = () => history.promise;
    const selecting = run("openRecentTask('stale')");
    fresh('replacement');
    history.resolve(response({ status: 'completed', result: { markdown: '# Stale' } }));
    await selecting;
    assert.equal(run('currentTaskId'), 'replacement');
    assert.equal(run('currentMarkdown'), '# Current task');

    // The current transcript still renders and completes normally.
    sandbox.fetch = async () => response({ text: '# Current transcript\n[00:01] Current content' });
    await run("completeTask({output:'transcript',output_directory:'transcript-directory'})");
    assert.match(run('currentMarkdown'), /Current transcript/);
    assert.equal(nodes.get('outputPath').textContent, 'transcript-directory');
    assert.equal(run('currentTaskStatus'), 'completed');
    assert.equal(sandbox.failures.length, 0);
    console.log('Transcript success/failure, reopened views, completion and history-selection races passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
