const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const front = path.join(__dirname, '../frontend');
const nodes = new Map();
const sandbox = {
    console, URL, setTimeout, clearTimeout,
    document: {getElementById: id => nodes.get(id), addEventListener() {}},
    window: {addEventListener() {}}
};
vm.createContext(sandbox);
for (const name of ['api.js', 'tasks.js', 'settings.js', 'gpu-runtime.js', 'script.js']) {
    vm.runInContext(fs.readFileSync(path.join(front, name), 'utf8'), sandbox);
}
vm.runInContext('isTranscriptMode=()=>true;isAgentMode=()=>true;typedApiKey=()=>"";', sandbox);
nodes.set('localFile', {files: []});
for (const source of ['local', 'audio']) {
    nodes.set('sourceType', {value: source});
    for (const size of [2 * 1024 ** 3 + 1, 10 * 1024 ** 3, 100 * 1024 ** 3]) {
        const file = {name: source === 'audio' ? 'large.wav' : 'large.mp4', size};
        nodes.get('localFile').files = [file];
        const result = vm.runInContext('validateAndBuildRequestBase()', sandbox);
        assert.equal(result.file, file);
        assert.equal(result.sourceType, source);
    }
}
let error;
sandbox.reportError = message => {error = message; return null;};
vm.runInContext('validationError=message=>reportError(message)', sandbox);
nodes.get('localFile').files = [];
assert.equal(vm.runInContext('validateAndBuildRequestBase()', sandbox), null);
assert.equal(error, '请选择本地视频或音频文件');
nodes.get('localFile').files = [{name: 'invalid.txt', size: 1}];
assert.equal(vm.runInContext('validateAndBuildRequestBase()', sandbox), null);
assert.match(error, /请选择 MP3/);
console.log('Local video/audio validation accepts files beyond 2 GiB, 10 GiB and 100 GiB; missing-file and audio-format guards passed');
