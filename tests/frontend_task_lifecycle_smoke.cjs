// Race regressions using real cancellation, reset and polling functions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const frontend = path.join(__dirname,'..','frontend');
const page = fs.readFileSync(path.join(frontend,'index.html'),'utf8');
const nodes = new Map([...page.matchAll(/id="([^"]+)"/g)].map(([,id]) => [id,{
    value:'',textContent:'',hidden:false,disabled:false,dataset:{},style:{},
    classList:{add(){},remove(){},toggle(){}},querySelector(){return {textContent:''};}
}]));
const sandbox = {console,URL,setTimeout,clearTimeout,
    document:{addEventListener(){},getElementById:id=>nodes.get(id),querySelectorAll:()=>[]},
    window:{addEventListener(){},confirm:()=>true,setTimeout,clearTimeout}};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
// Rendering and background refreshes are outside these task lifecycle assertions.
run(`resetElapsedTimer=()=>{}; stopElapsedTimer=()=>{}; setTranscriptTaskView=()=>{};
     renderTaskAdvisory=()=>{}; clearLogs=()=>{}; updateProgress=()=>{}; updateStep=()=>{};
     addLog=()=>{}; showToast=()=>{}; loadRecentTasks=()=>{}; refreshWhisperModelHints=()=>{};
     setSourceControlsDisabled=()=>{}; syncElapsedTimer=()=>{};`);
const response=status=>({ok:true,json:async()=>({status,elapsed_seconds:1})});
function freshTask(id) {
    run(`resetTaskView(); currentTaskId=${JSON.stringify(id)}; setTaskActive(true); setTaskState('processing','处理中');`);
}
(async()=>{
    freshTask('old');
    sandbox.fetch=async()=>response('cancelling');
    await run('cancelCurrentTask()');
    assert.equal(nodes.get('cancelTaskBtn').textContent,'取消请求已发送');
    assert.equal(nodes.get('cancelTaskBtn').disabled,true);
    freshTask('new');
    assert.equal(nodes.get('cancelTaskBtn').textContent,'取消任务');
    assert.equal(nodes.get('cancelTaskBtn').disabled,false);
    // A cancellation response arriving after a new task must not overwrite it.
    let finish;
    sandbox.fetch=()=>new Promise(resolve=>finish=resolve);
    const cancel=run('cancelCurrentTask()');
    freshTask('newer');
    finish(response('cancelled'));
    await cancel;
    assert.equal(run('currentTaskId'),'newer');
    assert.equal(run('isTaskActive'),true);
    assert.equal(nodes.get('taskState').textContent,'处理中');
    assert.equal(nodes.get('cancelTaskBtn').textContent,'取消任务');
    // A failed cancellation restores the button instead of claiming it was sent.
    sandbox.fetch=async()=>{throw new Error('offline');};
    await run('cancelCurrentTask()');
    assert.equal(nodes.get('cancelTaskBtn').textContent,'取消任务');
    assert.equal(nodes.get('cancelTaskBtn').disabled,false);
    // Polling for the old task may still be in flight after its timer was cleared.
    sandbox.fetchWithTimeout=()=>new Promise(resolve=>finish=resolve);
    run('fetchWithTimeout=globalThis.fetchWithTimeout');
    const poll=run("pollTask('newer')");
    freshTask('latest');
    finish(response('cancelled'));
    await poll;
    assert.equal(run('currentTaskId'),'latest');
    assert.equal(run('isTaskActive'),true);
    assert.equal(nodes.get('taskState').textContent,'处理中');
    // Disconnection is an unknown status, never a failed generation.
    const timers = new Map();
    let nextTimer = 0;
    sandbox.window.setTimeout = (fn, delay) => {
        const id = ++nextTimer; timers.set(id, {fn, delay}); return id;
    };
    sandbox.window.clearTimeout = id => timers.delete(id);
    run(`startElapsedTimer=()=>{}; updateLogs=()=>{}; showResult=async()=>true;`);
    const requests = [];
    sandbox.fetchWithTimeout = async (url, options) => {
        requests.push({url, options}); throw new Error('offline');
    };
    run('fetchWithTimeout=globalThis.fetchWithTimeout');
    async function disconnect(id) {
        freshTask(id);
        for (let i=0; i<run('MAX_POLL_ERRORS'); i++) {
            run('stopPolling()');
            await run(`pollTask(${JSON.stringify(id)})`);
            if (i < run('MAX_POLL_ERRORS')-1) assert.equal(timers.size,1);
        }
        assert.equal(timers.size,0,'Pause automatic polling after the retry limit');
        assert.equal(run('currentTaskId'),id);
        assert.equal(run('currentTaskStatus'),'processing');
        assert.equal(run('isTaskActive'),true);
        assert.equal(nodes.get('taskError').hidden,true);
        assert.equal(nodes.get('taskConnectionNotice').hidden,false);
        assert.equal(nodes.get('submitBtn').disabled,true,'Do not enable duplicate submission');
        assert.equal(nodes.get('networkState').textContent,'连接中断，任务状态待确认');
    }
    async function reconnect() {
        run('reconnectCurrentTask(); reconnectCurrentTask();');
        assert.equal(timers.size,1,'Repeated clicks must not overlap reconnect requests');
        assert.equal(nodes.get('reconnectTaskBtn').disabled,true);
        const [id,timer] = timers.entries().next().value;
        assert.equal(timer.delay,0);
        timers.delete(id);
        await timer.fn();
    }
    await disconnect('retained');
    await reconnect(); // Still offline: retain the task and allow another attempt.
    assert.equal(nodes.get('reconnectTaskBtn').disabled,false);
    assert.equal(nodes.get('taskConnectionNotice').hidden,false);
    assert.equal(run('currentTaskStatus'),'processing');
    sandbox.fetchWithTimeout = async (url, options) => {
        requests.push({url,options});
        return response('processing');
    };
    await reconnect();
    assert.equal(nodes.get('taskConnectionNotice').hidden,true);
    assert.equal(run('pollErrorCount'),0);
    assert.equal(run('currentTaskId'),'retained');
    assert.equal(timers.size,1,'Resume normal polling on reconnection');
    assert.ok(requests.every(({url,options})=>url.endsWith('/task/retained') && !options.method),
        'Reconnect only reads the original task; never submit or regenerate');
    run('stopPolling()');
    // A completed task while offline must render normally after reconnection.
    sandbox.fetchWithTimeout = async()=>{throw new Error('offline');};
    await disconnect('completed-offline');
    sandbox.fetchWithTimeout = async()=>({ok:true,json:async()=>({status:'completed',result:{}})});
    await reconnect();
    assert.equal(run('currentTaskStatus'),'completed');
    assert.equal(run('isTaskActive'),false);
    assert.equal(nodes.get('taskConnectionNotice').hidden,true);
    assert.equal(timers.size,0);
    // Confirmed backend terminal states still unlock continue generation.
    for (const status of ['failed','cancelled']) {
        sandbox.fetchWithTimeout = async()=>{throw new Error('offline');};
        await disconnect(status+'-offline');
        sandbox.fetchWithTimeout = async()=>response(status);
        await reconnect();
        assert.equal(run('currentTaskStatus'),status);
        assert.equal(run('isTaskActive'),false);
        assert.equal(nodes.get('taskConnectionNotice').hidden,true);
    }
    // An old reconnect response cannot change a newly selected task.
    sandbox.fetchWithTimeout = async()=>{throw new Error('offline');};
    await disconnect('stale-reconnect');
    sandbox.fetchWithTimeout = ()=>new Promise(resolve=>finish=resolve);
    const reconnecting = reconnect();
    freshTask('replacement');
    finish(response('failed'));
    await reconnecting;
    assert.equal(run('currentTaskId'),'replacement');
    assert.equal(run('currentTaskStatus'),'processing');
    assert.equal(nodes.get('taskConnectionNotice').hidden,true);
    assert.equal(nodes.get('reconnectTaskBtn').disabled,false);
    assert.equal(timers.size,0);
    console.log('Task cancellation, reconnection and stale response checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
