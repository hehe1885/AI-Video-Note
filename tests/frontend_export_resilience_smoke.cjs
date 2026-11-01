const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = {agentResultPanel: {}, taskConnectionNotice: {}, reconnectTaskBtn: {},
    networkState: {classList: {remove() {}}}};
const states = [], toasts = [];
const sandbox = {console, document: {}, window: {resetTranscriptReview() {}},
    byId: id => nodes[id], setTaskState: (...args) => states.push(args),
    showToast: (...args) => toasts.push(args), setTaskActive() {},
    updateProgress() {}, updateStep() {}, addLog() {}, setSubmitButton() {},
    loadRecentTasks() {}, refreshWhisperModelHints() {}, renderElapsedTime() {},
    showResult: async result => { assert.ok(result.markdown); return true; }};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/tasks.js'), 'utf8'), sandbox);
Object.assign(sandbox, {setTaskActive() {}, updateStep() {}, setSubmitButton() {},
    setTaskState: (...args) => states.push(args)});
vm.runInContext(`var currentTaskId='task', taskViewSequence=0, currentDownloadName='',
    elapsedTimer=null, pollTimer=null, elapsedBaseSeconds=0, elapsedSyncedAt=0, isTranscriptTask=false;`, sandbox);
(async () => {
    sandbox.result = {markdown: '# Preserved note', export_warning: 'Save failed; text preserved'};
    await vm.runInContext('completeTask(result)', sandbox);
    assert.deepEqual(states.at(-1), ['completed', '已完成（保存失败）']);
    assert.deepEqual(toasts.at(-1), [sandbox.result.export_warning, 'warning']);
    sandbox.result = {markdown: '# Normal note'};
    await vm.runInContext('completeTask(result)', sandbox);
    assert.deepEqual(states.at(-1), ['completed', '已完成']);
    assert.deepEqual(toasts.at(-1), ['视频笔记已生成', 'success']);
    console.log('Export warnings preserve completed notes and use warning feedback; normal completion stays unchanged');
})().catch(error => { console.error(error); process.exitCode = 1; });
