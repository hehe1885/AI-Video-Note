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
sandbox.actualValidate = run('validateAndBuildRequestBase');
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
    run('validateAndBuildRequestBase=actualValidate;isTranscriptMode=()=>true;isAgentMode=()=>true;typedApiKey=()=>"";taskWillLikelyUseSubtitles=()=>true;');
    nodes.get('sourceType').value='local';
    nodes.get('localFile').files=[];
    freshView();
    let historyVisible=false;
    nodes.get('fileField').classList.toggle=(name,visible)=>{if(name==='has-history-file')historyVisible=visible;};
    run("sourceFilename='local-recording.mp4'; updateFileInfo();");
    assert.equal(nodes.get('localFileName').textContent,'local-recording.mp4');
    assert.equal(historyVisible,true);
    sandbox.fetch=async(url,options)=>{
        assert.equal(url,'/api/summarize');
        sent.push(JSON.parse(options.body));
        return {ok:true,json:async()=>({task_id:'fresh-local'})};
    };
    await listeners.get('restartBtn/click')();
    assert.equal(sent.length,1);
    assert.equal(sent[0].source_task_id,'old-note');
    assert.equal(sent[0].processing_mode,'restart');
    assert.equal(sent[0].generation_action,'regenerate');
    assert.equal(sent[0].resume_task_id,null);
    assert.equal(toasts.length,0);
    assert.equal(nodes.get('localFileName').textContent,'local-recording.mp4');
    assert.equal(historyVisible,true);
    assert.equal(nodes.get('localFile').files.length,0);
    freshView();
    nodes.get('localFile').files=[{name:'new-video.mp4',size:1024}];
    run('updateFileInfo()');
    assert.equal(historyVisible,false);
    assert.match(nodes.get('fileInfo').textContent,/new-video.mp4/);
    run('resetSubmitLabel(true)');
    assert.equal(run('sourceFilename'),'');
    nodes.get('localFile').files=[];
    freshView();run('sourceTaskId=null;updateFileInfo()');
    assert.equal(historyVisible,false);
    assert.equal(nodes.get('localFileName').textContent,'');
    await listeners.get('restartBtn/click')();
    assert.equal(sent.length,1);
    assert.equal(toasts.at(-1),'请选择本地视频或音频文件');
    console.log('Historical local restart reuses uploaded media; changed/new sources still require a file');
})().catch(error=>{console.error(error);process.exitCode=1});
