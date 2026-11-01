const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '..', 'frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value:'', checked:false, disabled:false, hidden:false, open:false, textContent:'', dataset:{}, listeners:{},
    removeAttribute(name) { delete this[name]; },
    showModal() { this.open = true; },
    close() { this.open = false; this.listeners.close?.(); }
}]));
nodes.get('whisperModel').value = 'base';
const callbacks = []; const timers = new Map(); let timerId = 0;
const sandbox = {
    console, API_BASE:'/api', byId:id => nodes.get(id), showToast() {},
    selectedGpuBackend:() => nodes.get('useGpu').checked ? 'auto' : 'cuda',
    bindListener:(id, event, callback) => { nodes.get(id).listeners[event] = callback; },
    document:{addEventListener:(event, callback) => callbacks.push(callback)},
    setTimeout:(fn, ms) => { const id = ++timerId; timers.set(id, {fn, ms}); return id; },
    clearTimeout:id => timers.delete(id),
    readResponse:async response => { if (!response.ok) throw new Error('request failed'); return response.json(); }
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(frontend, 'gpu-runtime.js'), 'utf8'), sandbox);
callbacks.forEach(callback => callback());
const run = code => vm.runInContext(code, sandbox);
const reply = data => ({ok:true, json:async () => data});
const flush = async () => { for (let n = 0; n < 8; n++) await Promise.resolve(); };
async function tick() {
    assert.equal(timers.size, 1);
    const [id, timer] = [...timers][0]; timers.delete(id);
    await timer.fn(); await flush();
}

(async () => {
    let requests = [];
    sandbox.fetch = async (url, options = {}) => {
        requests.push([url, options.method || 'GET']);
        if (url.includes('gpu-device')) return reply({detected:true, backend:'cuda', runtime:{status:'missing'}});
        return reply({status:'idle'});
    };
    nodes.get('gpuRuntimeManualBtn').listeners.click();
    assert.equal(nodes.get('gpuRuntimeManualDialog').open, true);
    assert.equal(requests.length, 0, 'Manual help does not initiate downloads or installation');
    assert.equal(nodes.get('useGpu').checked, false);
    nodes.get('gpuRuntimeManualCloseBtn').listeners.click();
    assert.equal(nodes.get('gpuRuntimeManualDialog').open, false);
    await nodes.get('gpuRuntimeQuickBtn').listeners.click();
    assert.equal(nodes.get('gpuRuntimeDialog').open, true);
    assert.ok(requests.every(([, method]) => method === 'GET'), 'Opening the window alone does not install');
    assert.equal(nodes.get('gpuRuntimeInstallBtn').disabled, false);
    assert.equal(nodes.get('gpuRuntimeCudaSteps').hidden, false);

    let job = {id:'one', engine:'cuda', status:'awaiting_admin'};
    let posted;
    sandbox.fetch = async (url, options = {}) => {
        if (options.method === 'POST') posted = JSON.parse(options.body);
        return reply(job);
    };
    await nodes.get('gpuRuntimeInstallBtn').listeners.click(); await flush();
    assert.equal(posted.backend, 'cuda');
    assert.equal(Object.keys(posted).length, 1, 'No destination paths or download URLs are accepted from the UI');
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('管理员'));
    assert.equal(nodes.get('gpuRuntimeInstallBtn').disabled, true);
    job = {id:'one', engine:'cuda', status:'downloading', received:1e6, total:5e6};
    await tick();
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('1.0 / 5.0 MB'));
    assert.equal(nodes.get('gpuRuntimeInstallProgress').value, 1e6);
    job = {id:'one', engine:'cuda', status:'completed', verified:true};
    await tick();
    assert.equal(timers.size, 0);
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('安装完成'));
    assert.equal(nodes.get('useGpu').checked, false, 'Installation never enables GPU on behalf of the user');
    run('closeGpuRuntimeDialog()');
    // Belle's author GGML export uses Vulkan and needs the same runtime preparation.
    nodes.get('whisperModel').value = 'belle-turbo-zh';
    nodes.get('useGpu').checked = true;
    run("gpuRuntimeJob=null; gpuRuntimeLastPrompt=''; renderGpuRuntime({engine:'vulkan',status:'download_on_first_use'})");
    assert.equal(nodes.get('gpuRuntimeDialog').open, true);
    run('closeGpuRuntimeDialog()');
    nodes.get('whisperModel').value = 'base';
    nodes.get('useGpu').checked = false;

    // A closed window must not be reopened by a delayed POST response.
    run("gpuRuntimeSelectedEngine='vulkan'; gpuRuntimeJob=null;");
    nodes.get('gpuRuntimeInstallBtn').disabled = false;
    nodes.get('gpuRuntimeDialog').showModal();
    let finishPost;
    sandbox.fetch = () => new Promise(resolve => { finishPost = resolve; });
    const pending = run('startGpuRuntimeInstall()');
    run('closeGpuRuntimeDialog()');
    finishPost(reply({id:'late', engine:'vulkan', status:'checking'}));
    await pending; await flush();
    assert.equal(nodes.get('gpuRuntimeDialog').open, false);
    assert.equal(timers.size, 0);

    // Network failures while polling remain retryable without restarting installation.
    run("gpuRuntimeJob={id:'retry',engine:'vulkan',status:'installing'}");
    sandbox.fetch = async () => { throw new Error('offline'); };
    await run('pollGpuInstall(gpuRuntimeInstallSequence)');
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('自动重试'));
    assert.equal([...timers.values()][0].ms, 2500);
    sandbox.fetch = async () => reply({id:'retry', engine:'vulkan', status:'failed', error:'bad hash'});
    await tick();
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('bad hash'));
    assert.equal(nodes.get('gpuRuntimeInstallBtn').disabled, false);
    assert.equal(timers.size, 0);

    // An absent driver keeps the install action disabled and leaves manual help available.
    sandbox.fetch = async url => reply(url.includes('gpu-device') ? {detected:false, backend:'cpu'} : {status:'idle'});
    await run('openGpuInstallDialog()');
    assert.equal(nodes.get('gpuRuntimeInstallBtn').disabled, true);
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('手动安装说明'));
    nodes.get('gpuRuntimeManualOpenBtn').listeners.click();
    assert.equal(nodes.get('gpuRuntimeManualDialog').open, true);
    assert.equal(nodes.get('useGpu').checked, false);
    run('closeGpuRuntimeDialog()');
    console.log('One-click progress, manual help, preference preservation, retry and stale-response checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
