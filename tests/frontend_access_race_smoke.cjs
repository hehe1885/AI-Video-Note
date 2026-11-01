// Real UI functions, controlled responses: no network or real credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'frontend');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', hidden: true, disabled: false, textContent: '',
    scrollIntoView() {}, removeAttribute() {}, focus() {}
}]));
let saves = 0;
nodes.get('agentConnectionState').replaceChildren = function (...children) {
    this.children = children;
    this.textContent = children.map(child => child.textContent).join('');
};
const sandbox = { console, URL, setTimeout, clearTimeout, location: {origin: 'http://localhost:8765'},
    document: {addEventListener() {}, getElementById: id => nodes.get(id), createElement: () => ({})},
    window: {addEventListener() {}, setInterval: () => 1, clearInterval() {}},
    saved: () => { saves++; }
};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(root,name),'utf8')).join('\n'), sandbox);
const run = code => vm.runInContext(code, sandbox);
run('readResponse = async response => response.data; showToast = () => {}; updateBiliHint = () => {}; saveBiliLogin = async () => saved(); refreshBiliSavedLogin = async () => {};');
function deferred() {
    let resolve;
    const promise = new Promise(done => {resolve = done;});
    return {promise, resolve};
}
(async () => {
    const parent = {tagName: 'DETAILS', open: false, parentElement: null};
    nodes.get('mcpSection').parentElement = {tagName: 'DIV', parentElement: parent};
    nodes.get('mcpSection').open = false;
    run('applyMcpAccess(true)');
    assert.equal(nodes.get('mcpStatusChip').hidden, false);
    assert.equal(parent.open, false, 'service status must not expand settings');
    assert.equal(nodes.get('mcpSection').open, false);
    assert.match(html, /<div id="mcpStatusChip"[^>]*role="status"/);
    run('applyMcpAccess(false)');
    assert.equal(nodes.get('mcpStatusChip').hidden, true);

    assert.ok(html.indexOf('id="generationMode"') < html.indexOf('id="agentAccessStatus"'));
    assert.ok(html.indexOf('id="agentAccessStatus"') < html.indexOf('id="agentExecution"'));
    run("renderAgentAccessState({clients:[{name:'Codex',connected:true}],skill_clients:[{name:'Claude Code',active_requests:0,recent:true}]})");
    assert.match(nodes.get('agentConnectionState').textContent,/MCP · Codex · 已连接/);
    assert.match(nodes.get('agentConnectionState').textContent,/Skill · Claude Code · 最近调用/);
    assert.equal(nodes.get('agentConnectionState').children[0].className,'agent-access-line active');
    assert.equal(nodes.get('agentConnectionState').children[1].className,'agent-access-line');
    run("renderAgentAccessState({skill_clients:[{name:null,active_requests:1,recent:true}]})");
    assert.match(nodes.get('agentConnectionState').textContent,/未知 Agent（未提供名称） · 正在调用/);
    assert.equal(nodes.get('agentConnectionState').children[0].className,'agent-access-line active');
    run("renderAgentAccessState({clients:[{name:'Codex',connected:false,recent:true}]})");
    assert.equal(nodes.get('agentConnectionState').children[0].className,'agent-access-line');
    run('renderAgentAccessState({})');
    assert.equal(nodes.get('agentConnectionState').textContent,'尚未检测到 Agent 调用');
    assert.equal(parent.open,false,'activity updates must not open settings');

    let pending = deferred();
    let statusRequests = 0;
    sandbox.fetch = async url => {
        if (url.endsWith('/status')) { statusRequests++; return pending.promise; }
        return {data: {ok: true}};
    };
    run('biliLoginTimer = 1');
    const first = run('pollBiliLogin()');
    await run('pollBiliLogin()');
    assert.equal(statusRequests, 1, 'Slow polls must not overlap');
    await run('cancelBiliLogin()');
    pending.resolve({data: {state: 'ready', cookies: {sessdata: 'test'}}});
    await first;
    assert.equal(saves, 0, 'A response after cancellation must not save credentials');
    assert.equal(nodes.get('biliQrPanel').hidden, true);
    assert.equal(nodes.get('biliLoginStatus').textContent, '');

    pending = deferred();
    sandbox.fetch = async url => url.endsWith('/start') ? pending.promise : {data: {ok: true}};
    const starting = run('startBiliLogin()');
    await run('cancelBiliLogin()');
    pending.resolve({data: {ok: true, state: 'waiting', qr_image: 'data:image/png;base64,test'}});
    await starting;
    assert.equal(run('biliLoginTimer'), null, 'Cancelled startup must not restart polling');
    assert.equal(nodes.get('biliQrPanel').hidden, true);
    assert.equal(nodes.get('biliLoginBtn').disabled, false);

    sandbox.fetch = async () => ({data: {state: 'ready', cookies: {sessdata: 'test'}}});
    run('biliLoginTimer = 1');
    await run('pollBiliLogin()');
    assert.equal(saves, 1, 'A fresh session still saves a successful login');
    console.log('MCP status, serial QR polling and cancelled response regressions passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
