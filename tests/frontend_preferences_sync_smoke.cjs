// Authoritative workspace settings and failures, using the actual frontend functions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const frontend = path.join(process.cwd(), 'frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function boot(cache = null) {
    const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
        value: '', textContent: '', hidden: true, disabled: false, dataset: {},
        classList: {add(){}, remove(){}, toggle(){}}
    }]));
    const storage = new Map(cache === null ? [] : [['vtn_prefs', typeof cache === 'string' ? cache : JSON.stringify(cache)]]);
    const sandbox = {console: {log(){}, warn(){}, debug(){}, error(){}}, URL, setTimeout, clearTimeout,
        localStorage: {getItem:key=>storage.get(key) ?? null, setItem:(key,value)=>storage.set(key,value), removeItem:key=>storage.delete(key)},
        document: {addEventListener(){}, getElementById:id=>nodes.get(id)},
        window: {addEventListener(){}, setTimeout, clearTimeout}};
    vm.createContext(sandbox);
    for (const file of ['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js']) {
        vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), sandbox);
    }
    const run = code => vm.runInContext(code, sandbox);
    run('applyPreferencesToForm=()=>{};');
    return {nodes, storage, sandbox, run};
}
function reply(data, status=200) { return {ok:status===200,status,json:async()=>data}; }
function packet(preferences, revision='r1') {
    return {exists:preferences!==null,schema:3,revision:preferences===null?null:revision,preferences};
}
async function settle(ui) {
    for (let i=0; i<20 && ui.run('workspacePreferences.saving'); i++) await tick();
    assert.equal(ui.run('workspacePreferences.saving'),false);
}
function network(ui, handler) {
    ui.sandbox.handler = handler;
    ui.run('fetchWithTimeout=(url,options)=>handler(url,options);');
}
(async()=>{
    // A stale browser cache must never replace existing workspace settings.
    const ui = boot({schema:2,global:{use_gpu:true},ui:{theme:'light'}});
    let disk = {schema:3,global:{use_gpu:false,text_detail:'brief',future_option:0},ui:{theme:'dark'},future_section:{value:'keep'}};
    const requests = [];
    network(ui, async (url, options={}) => {
        requests.push({url,options});
        if (!options.method) return reply(packet(disk));
        const body=JSON.parse(options.body);
        assert.equal(body.expected_revision,'r1');
        disk = body.preferences;
        return reply(packet(disk,'r2'));
    });
    await ui.run('loadWorkspacePreferences()');
    ui.run('initPreferences()');
    await settle(ui);
    assert.equal(ui.run('prefs.global.use_gpu'),false);
    assert.equal(ui.run('prefs.ui.theme'),'dark');
    assert.equal(disk.global.future_option,0);
    assert.equal(disk.future_section.value,'keep');
    assert.equal(JSON.parse(ui.storage.get('vtn_prefs')).ui.theme,'dark');
    assert.equal(requests[0].options.method,undefined);
    // Missing browser storage (e.g. a different port) still restores templates.
    const newPort=boot();
    network(newPort, async (_,options={}) => options.method ? reply(packet(JSON.parse(options.body).preferences,'r3'))
        : reply(packet({...disk,templates:[{id:'t',name:'saved',settings:{text_detail:'brief'}}]},'r2')));
    await newPort.run('loadWorkspacePreferences()'); newPort.run('initPreferences()'); await settle(newPort);
    assert.equal(newPort.run('prefs.templates[0].name'),'saved');
    assert.equal(newPort.run('prefs.ui.theme'),'dark');
    // Do not replace the legacy browser record before first import is acknowledged.
    const legacy={schema:2,global:{use_gpu:true},ui:{theme:'dark'},future_section:{value:'legacy'}};
    const first=boot(legacy);
    let finish;
    let importBody;
    network(first, async (_,options={}) => {
        if (!options.method) return reply(packet(null));
        importBody=JSON.parse(options.body);
        return await new Promise(resolve=>finish=resolve);
    });
    await first.run('loadWorkspacePreferences()'); first.run('initPreferences()');
    assert.deepEqual(JSON.parse(first.storage.get('vtn_prefs')),legacy);
    assert.equal(importBody.expected_revision,null);
    assert.equal(importBody.import_source.schema,2);
    assert.equal(importBody.preferences.future_section.value,'legacy');
    finish(reply(packet(importBody.preferences,'imported'))); await settle(first);
    // Coalesce rapid edits, and use the revision from the previous acknowledged save.
    let release;
    let bodies=[];
    network(first, async (_,options) => {
        const body=JSON.parse(options.body); bodies.push(body);
        if (bodies.length===1) return await new Promise(resolve=>release=()=>resolve(reply(packet(body.preferences,'a'))));
        return reply(packet(body.preferences,'b'));
    });
    first.run("prefs.ui.theme='light'; persistPrefs(); prefs.ui.theme='dark'; persistPrefs(); prefs.ui.theme='system'; persistPrefs();");
    assert.equal(bodies.length,1);
    release(); await settle(first);
    assert.equal(bodies.length,2);
    assert.equal(bodies[1].expected_revision,'a');
    assert.equal(bodies[1].preferences.ui.theme,'system');
    // A stale tab is blocked rather than overwriting newer settings automatically.
    network(first, async()=>reply({detail:{message:'设置已被另一页面修改，请重新读取后再保存'}},409));
    first.run("prefs.ui.theme='dark'; persistPrefs()"); await settle(first);
    assert.equal(first.run('workspacePreferences.mode'),'blocked');
    assert.equal(first.nodes.get('preferencesStorageNotice').hidden,false);
    const protectedCache=first.storage.get('vtn_prefs');
    first.run("prefs.ui.theme='light'; persistPrefs()");
    assert.equal(first.storage.get('vtn_prefs'),protectedCache);
    // Corrupt/newer settings and failed reads never initialize defaults on disk.
    for (const cache of ['{broken', {schema:999,global:{use_gpu:true}}]) {
        const bad=boot(cache); let writes=0;
        network(bad,async(_,options={})=>{if(options.method)writes++; return reply(packet(null));});
        const before=bad.storage.get('vtn_prefs');
        await bad.run('loadWorkspacePreferences()'); bad.run('initPreferences()'); await tick();
        assert.equal(writes,0); assert.equal(bad.storage.get('vtn_prefs'),before);
        assert.equal(bad.run('workspacePreferences.mode'),'blocked');
    }
    for (const status of [409,503]) {
        const bad=boot(legacy); let writes=0;
        network(bad,async(_,options={})=>{if(options.method)writes++; return reply({detail:{message:'设置暂时无法读写，已有文件保持不变'}},status);});
        await bad.run('loadWorkspacePreferences()'); bad.run('initPreferences()'); await tick();
        assert.equal(writes,0); assert.deepEqual(JSON.parse(bad.storage.get('vtn_prefs')),legacy);
    }
    // Failed save remains retryable without silently claiming it was saved.
    const retry=boot(); let fail=false;
    network(retry,async(_,options={})=>{
        if(!options.method)return reply(packet({schema:3,ui:{theme:'dark'}}));
        if(fail)throw new Error('offline');
        return reply(packet(JSON.parse(options.body).preferences,'saved'));
    });
    await retry.run('loadWorkspacePreferences()'); retry.run('initPreferences()'); await settle(retry);
    fail=true; retry.run("prefs.ui.theme='light'; persistPrefs()"); await settle(retry);
    assert.equal(retry.run('workspacePreferences.mode'),'error');
    assert.equal(retry.nodes.get('retryPreferencesBtn').hidden,false);
    fail=false; await retry.run('retryWorkspacePreferencesSave()'); await settle(retry);
    assert.equal(retry.run('workspacePreferences.mode'),'ready');
    assert.equal(retry.nodes.get('preferencesStorageNotice').hidden,true);
    console.log('Workspace settings import, port changes, unknown fields, queued saves, failures and stale-tab protection passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
