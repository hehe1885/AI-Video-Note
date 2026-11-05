const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('frontend/script.js', 'utf8');
const start = script.indexOf('function showAgentWaiting(');
const end = script.indexOf('\n}', start) + 2;
const nodes = new Map();
const sandbox = {byId(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    window: {}, setTaskActive() {}, setTaskState() {}, setSubmitButton() {}};
vm.createContext(sandbox);
vm.runInContext(script.slice(start, end), sandbox);
for (const task of [
    {advisory:'未按要求插入截图，已保留材料', agent_auto_running:false},
    {screenshot_warning:'没有合适截图，本次生成文字笔记', agent_auto_running:true},
    {},
]) {
    sandbox.task = task;
    vm.runInContext('showAgentWaiting(task)', sandbox);
    assert.equal(nodes.get('taskAdvisory').hidden, !(task.advisory || task.screenshot_warning));
    assert.equal(nodes.get('agentAutoRetryBtn').disabled, Boolean(task.agent_auto_running));
}
console.log('Agent failure and screenshot notices remain visible; retry reflects worker state.');
