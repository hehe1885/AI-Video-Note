// Exercise the real QR flow and picker positioning without contacting Bilibili.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '..', 'frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', hidden: false, disabled: false, textContent: '',
    scrollIntoView() {}, removeAttribute() {}
}]));
const requests = [];
let loginState = 'waiting';
const fakeCookies = {sessdata: 'test-session', bili_jct: 'test-jct', buvid3: 'test-device'};
const sandbox = {
    console, URL, Blob, setTimeout, clearTimeout,
    document: {addEventListener() {}, getElementById: id => nodes.get(id), documentElement: {dataset: {}}},
    window: {addEventListener() {}, setTimeout, clearTimeout, innerHeight: 800,
        setInterval: () => 7, clearInterval() {}},
    fetch: async (url, options = {}) => {
        requests.push({url, options});
        if (url.endsWith('/bili-login/start')) return {data: {ok: true, state: 'waiting', qr_image: 'data:image/png;base64,test', message: '请使用哔哩哔哩手机客户端扫描二维码'}};
        if (url.endsWith('/bili-login/status')) return {data: {ok: true, state: loginState, message: loginState === 'scanned' ? '已扫码，请在手机上确认登录' : '请使用哔哩哔哩手机客户端扫描二维码', cookies: loginState === 'ready' ? fakeCookies : null}};
        if (url.endsWith('/bili-credentials')) return {data: {ok: true, saved: true}};
        if (url.endsWith('/bili-login/cancel')) return {data: {ok: true}};
        throw new Error(`Unexpected request: ${url}`);
    }
};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'), sandbox);
vm.runInContext('readResponse = async response => response.data; showToast = () => {}; updateBiliHint = () => {};', sandbox);
const run = code => vm.runInContext(code, sandbox);
(async () => {
    run("applyFontSize('small')");
    assert.equal(sandbox.document.documentElement.dataset.fontSize, 'compact', 'Old 85% settings migrate to the new 92% tier');
    assert.equal(nodes.get('fontSize').value, 'compact');
    for (const size of ['compact', 'standard', 'comfortable', 'large', 'maximum']) {
        run(`applyFontSize('${size}')`);
        assert.equal(nodes.get('fontSize').value, size);
    }
    const bootstrapDocument = {documentElement: {dataset: {}}};
    vm.runInNewContext(fs.readFileSync(path.join(frontend, 'theme-bootstrap.js'), 'utf8'), {
        document: bootstrapDocument,
        window: {addEventListener() {}},
        localStorage: {getItem: key => key === 'vtn_prefs' ? JSON.stringify({ui: {font_size: 'small'}}) : null}
    });
    assert.equal(bootstrapDocument.documentElement.dataset.fontSize, 'compact', 'Bootstrap must use the same migration before the main script loads');
    nodes.get('credentialsSection').open = false;
    let sectionReveals = 0;
    let qrReveals = 0;
    nodes.get('credentialsSection').scrollIntoView = () => { sectionReveals++; };
    nodes.get('biliQrPanel').scrollIntoView = () => { qrReveals++; };
    await run('startBiliLogin()');
    assert.equal(nodes.get('credentialsSection').open, true, 'Starting QR login from the link hint must expand credentials');
    assert.equal(sectionReveals, 1);
    assert.equal(qrReveals, 1, 'The newly loaded QR code must be scrolled into view');
    const starts = requests.filter(request => request.url.endsWith('/bili-login/start')).length;
    nodes.get('credentialsSection').open = false;
    await run('startBiliLogin()');
    assert.equal(nodes.get('credentialsSection').open, true, 'An active QR session must still be revealed');
    assert.equal(qrReveals, 2);
    assert.equal(requests.filter(request => request.url.endsWith('/bili-login/start')).length, starts, 'Revealing an active session must not restart it');
    await run('pollBiliLogin()');
    assert.equal(nodes.get('biliQrPanel').hidden, false);
    assert.equal(nodes.get('biliLoginStatus').textContent, '', 'Only the QR caption should ask the user to scan');
    assert.equal(nodes.get('biliLoginCancelBtn').hidden, false);
    loginState = 'scanned';
    await run('pollBiliLogin()');
    assert.match(nodes.get('biliLoginStatus').textContent, /已扫码/);
    loginState = 'ready';
    await run('pollBiliLogin()');
    const saved = requests.filter(r => r.url.endsWith('/bili-credentials') && r.options.method === 'POST');
    assert.equal(saved.length, 1, 'Successful QR login must save automatically');
    assert.deepEqual(JSON.parse(saved[0].options.body), fakeCookies);
    assert.equal(nodes.get('biliQrPanel').hidden, true);
    assert.equal(nodes.get('biliLoginBtn').disabled, false);
    assert.match(nodes.get('biliLoginStatus').textContent, /已保存/);
    let bottom = 760;
    let space;
    let scrolled = false;
    sandbox.testSelect = {
        getBoundingClientRect: () => ({bottom}), matches: () => false,
        scrollIntoView: () => {scrolled = true; bottom = 450;},
        style: {setProperty: (_, value) => {space = value;}}
    };
    run('prepareSelectPicker(testSelect)');
    assert.equal(scrolled, true, 'A trigger near the bottom needs room below it');
    assert.equal(space, '338px', 'Menu height must use its new position after scrolling');
    console.log('Font tier migration, QR hints, automatic credential saving and downward picker space checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
