// Exercise the real browser settings and submission code without a live LLM or vault.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '..', 'frontend');
const page = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const nodes = new Map([...page.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', checked: false, hidden: false, disabled: false, textContent: '', dataset: {}
}]));
let outputMode = 'note';
let gpuRuntimeDialogOpens = 0;
const runtimeDialog = nodes.get('gpuRuntimeDialog');
runtimeDialog.open = false;
runtimeDialog.showModal = function () { this.open = true; gpuRuntimeDialogOpens++; };
runtimeDialog.close = function () { this.open = false; };
const sandbox = { console, URL, Blob, setTimeout, clearTimeout,
    document: {
        addEventListener() {},
        createElement() { return {textContent: "", get innerHTML() { return this.textContent.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }}; },
        getElementById: (id) => nodes.get(id),
        querySelector: (selector) => selector.includes('outputMode') ? { value: outputMode } : null
    }, window: { setTimeout, clearTimeout, addEventListener() {} } };
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'), sandbox);
const run = (code) => vm.runInContext(code, sandbox);
const set = (id, value) => { nodes.get(id).value = value; };
for (const retired of ['tiny', 'tiny.en', 'base.en', 'small.en', 'medium.en', 'paraformer-zh']) {
    assert.ok(!page.includes(`value="${retired}"`));
    assert.equal(run(`normalizeWhisperModel('${retired}')`), 'base');
    assert.equal(run(`mergePrefs({global:{whisper_model:'${retired}',use_gpu:true}}).global.whisper_model`), 'base');
    assert.equal(run(`mergePrefs({global:{whisper_model:'${retired}',use_gpu:true}}).global.use_gpu`), true);
}
for (const [model, size] of [['base', '142MB'], ['small', '466MB'], ['medium', '1.5GB']]) {
    for (const status of ['cached', 'missing', 'incomplete', 'unknown', 'unsupported']) {
        const label = run(`modelOptionLabel('English', '${model}', '${status}')`);
        assert.ok(label.includes(size));
        assert.equal(label.includes('已安装'), status === 'cached');
        if (status === 'unsupported') assert.ok(label.includes('重启'));
    }
}

assert.equal(run("normalizeVideoInput('推荐观看：https://youtu.be/jNQXAC9IVRw 看这个视频')"), 'https://youtu.be/jNQXAC9IVRw');
assert.ok(run("modelOptionLabel('中文模型', 'belle-turbo-zh', 'cached', false)").includes('已安装'));
assert.ok(run("modelOptionLabel('中文模型', 'belle-turbo-zh', 'cached', false)").includes('关闭 GPU'));
assert.ok(run("modelOptionLabel('base', 'base', 'cached', true, 'missing')").includes('当前加速格式需下载'));
assert.ok(page.indexOf('id="formatSelect"') < page.indexOf('id="videoUrl"'));
assert.ok(!page.includes('name="outputMode" value="obsidian"'));
assert.ok(!page.split('id="resultArea"')[1].includes('id="formatSelect"'));
set('summaryStyle', 'faithful');
outputMode = 'note';
set('formatSelect', 'obsidian');
set('obsidianVault', 'D:/Notes/Vault');
set('obsidianNoteFolder', '学习/视频');
set('obsidianAttachmentFolder', 'pictures');
for (const [mode, target] of [
    ['vault_root', 'D:/Notes/Vault'],
    ['note_folder', 'D:/Notes/Vault/学习/视频'],
    ['note_subfolder', 'D:/Notes/Vault/学习/视频/pictures'],
    ['specified_folder', 'D:/Notes/Vault/pictures']
]) {
    set('obsidianAttachmentMode', mode);
    run('updateObsidianSettings()');
    assert.equal(nodes.get('obsidianSettings').hidden, false);
    assert.ok(nodes.get('obsidianPathPreview').textContent.endsWith(`图片保存位置：${target}`));
    assert.equal(nodes.get('obsidianAttachmentFolderField').hidden, ['vault_root', 'note_folder'].includes(mode));
}
outputMode = 'transcript';
run('updateObsidianSettings()');
// Rendering stays intact inside the collapsed inert note-settings region;
// transcript submission must still ignore the saved Obsidian format.
assert.equal(run('isObsidianNote()'), false);
outputMode = 'note';
set('formatSelect', 'obsidian');
run(`getSelectedModelConfig = () => ({provider: 'custom', baseUrl: 'http://localhost/v1', model: 'test'});
     typedApiKey = () => 'test-key'; normalizeScreenshotInterval = () => 30;
     biliPagesPanelVisible = () => false;`);
set('customPrompt', '  添加复习题  ');
set('whisperModel', 'base');
set('reasoningEffort', 'off');
const request = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
assert.equal(request.custom_prompt, '添加复习题');
assert.equal(request.obsidian.vault, 'D:/Notes/Vault');
assert.equal(request.obsidian.attachment_mode, 'specified_folder');
const transcript = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null, null, false, true))"));
assert.equal(transcript.output, 'transcript');
assert.equal(transcript.obsidian, undefined);
assert.equal(transcript.custom_prompt, undefined);
assert.equal(transcript.generation_mode, 'api');
assert.deepEqual(transcript.llm_config, {model_type:'custom',base_url:'http://localhost/v1',model:'test',api_key:'test-key'});
outputMode = 'note';
set('formatSelect', 'markdown');
set('summaryStyle', 'faithful');
const ordinary = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
assert.equal(ordinary.obsidian, undefined);
assert.equal(ordinary.custom_prompt, '添加复习题');
for (const format of ['markdown', 'docx', 'html', 'json', 'txt', 'png', 'pdf', 'enex']) {
    set('formatSelect', format);
    const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
    assert.equal(config.summary_style, 'faithful');
    assert.equal(config.obsidian, undefined);
}
run(`renderModelStorage({directory: 'D:/models', default_directory: 'D:/default',
     models: [{id: 'base', status: 'incomplete', managed_paths: ['D:/models/base']}]})`);
assert.equal(nodes.get('modelDirectoryCurrent').textContent, 'D:/models');
assert.equal(nodes.get('modelDirectoryDefault').textContent, 'D:/default');
assert.equal(nodes.get('deleteModelBtn').disabled, false);
run('isTaskActive = true; renderSelectedModelPaths()');
assert.equal(nodes.get('deleteModelBtn').disabled, true);
assert.equal(nodes.get('saveModelDirectoryBtn').disabled, true);
for (const density of ['none', 'few', 'medium', 'many']) {
    set('screenshotDensity', density);
    set('videoResolution', '1080');
    set('videoDownloadDirectory', 'D:/Videos/Notes');
    const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
    assert.equal(config.screenshot_density, density);
    assert.equal(config.include_screenshots, density !== 'none');
    assert.equal(config.screenshot_interval, undefined);
    assert.equal(config.video_resolution, 1080);
    assert.equal(config.video_download_directory, 'D:/Videos/Notes');
}
for (const model of ['base', 'small', 'medium', 'turbo', 'belle-turbo-zh', 'large-v3']) {
    assert.ok(page.includes(`value="${model}"`));
    set('whisperModel', model);
    const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
    assert.equal(config.whisper_model, model);
}
assert.ok(page.indexOf('id="timedScreenshotSettings"') > page.indexOf('id="screenshotDensity"'));
nodes.get('timedScreenshots').checked = true;
set('screenshotInterval', '7');
set('screenshotDensity', 'none');
set('sourceType', 'url');
run('toggleScreenshotSettings()');
assert.equal(nodes.get('screenshotIntervalField').hidden, false);
assert.equal(nodes.get('screenshotDensity').disabled, true);
assert.equal(nodes.get('screenshotVideoSettings').hidden, false);
const timed = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
assert.equal(timed.timed_screenshots, true);
assert.equal(timed.screenshot_interval, 7);
assert.equal(timed.include_screenshots, true);
assert.equal(timed.screenshot_density, 'none');
nodes.get('timedScreenshots').checked = false;
run('toggleScreenshotSettings()');
assert.equal(nodes.get('screenshotIntervalField').hidden, true);
assert.equal(nodes.get('screenshotVideoSettings').hidden, true);
assert.ok(!page.includes('id="includeScreenshots"'));
for (const style of ['faithful', 'concise', 'learning', 'tutorial', 'meeting']) {
    assert.ok(page.includes(`value="${style}"`));
    set('summaryStyle', style);
    for (const format of ['markdown', 'docx', 'obsidian']) {
        set('formatSelect', format);
        const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
        assert.equal(config.summary_style, style);
        assert.equal(Boolean(config.obsidian), format === 'obsidian');
    }
}
// One checkbox selects automatic acceleration; old manual preferences cannot revive the selector.
assert.ok(!page.includes('id="gpuBackend"'));
nodes.get('whisperModel').options = ['base', 'paraformer-zh', 'belle-turbo-zh'].map(value => ({value, dataset:{baseLabel:value}, textContent:value}));
nodes.get('useGpu').checked = true;
for (const model of ['paraformer-zh','belle-turbo-zh']) {
    set('whisperModel', model); run('updateGpuSettings()');
    assert.equal(nodes.get('whisperModel').value, model);
    assert.equal(nodes.get('whisperModel').options.find(option=>option.value===model).disabled, false);
}
assert.equal(run('defaultPrefs().global.gpu_backend'), 'auto');
for (const backend of ['auto', 'vulkan', 'cuda', 'amd', 'intel']) {
    run(`prefs=mergePrefs({global:{gpu_backend:'${backend}',use_gpu:true}})`);
    assert.equal(run('prefs.global.gpu_backend'),'auto');
    const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
    assert.equal(config.gpu_backend, 'auto');
    assert.equal(config.use_gpu, true);
}
nodes.get('useGpu').checked = false; run('updateGpuSettings()');
assert.equal(nodes.get('whisperModel').options[1].disabled, false);
assert.equal(run("buildSummarizeConfig('https://example.com/video', null).use_gpu"),false);
run(`currentMarkdown = '# test'; currentHtml = '';
     downloadMarkdownFile = async (format = 'markdown') => { globalThis.downloadedFormat = format; };
     exportSummaryImage = async () => 'png';
     convertToHtml = () => '<h1>test</h1>'; convertToJson = () => '{}'; stripMarkdown = () => 'test';
     triggerBlobDownload = (_blob, extension) => { globalThis.downloadedExtension = extension; };
     showToast = () => {};`);
(async () => {
    nodes.get('useGpu').checked = true;
    run('updateGpuSettings()');
    sandbox.fetch = async () => ({ok:true,json:async()=>({directory:'D:/models',default_directory:'D:/models',models:[
        {id:'base',status:'missing',managed_paths:[]}, {id:'small',status:'missing',managed_paths:[]},
        {id:'paraformer-zh',status:'unsupported',managed_paths:[]}, {id:'belle-turbo-zh',status:'unsupported',managed_paths:[]}
    ]})});
    await run('refreshWhisperModelHints()');
    assert.ok(nodes.get('whisperModel').options[0].textContent.includes('未安装'));
    assert.ok(nodes.get('whisperModel').options[1].textContent.includes('关闭 GPU'));
    assert.equal(nodes.get('modelStatusNotice').hidden, true);
    sandbox.fetch = async () => {throw new Error('offline');};
    await run('refreshWhisperModelHints()');
    assert.equal(nodes.get('modelStatusNotice').hidden, false);
    assert.ok(nodes.get('modelStatusHint').textContent.includes('读取失败'));
    assert.ok(!nodes.get('whisperModel').options[0].textContent.includes('状态待确认'));
    assert.ok(nodes.get('whisperModel').options[1].textContent.includes('关闭 GPU'));
    sandbox.fetch = async () => ({ok:true,json:async()=>({detected:true,name:'NVIDIA Test GPU'})});
    await run('refreshGpuDetected()');
    assert.equal(nodes.get('gpuDetected').textContent, '已检测到显卡：NVIDIA Test GPU');
    assert.equal(nodes.get('gpuDetected').dataset.state, 'detected');
    sandbox.fetch = async () => ({ok:true,json:async()=>({detected:true,name:'NVIDIA Test GPU',backend:'cuda'})});
    await run('refreshGpuDetected()');
    assert.ok(nodes.get('gpuDetected').textContent.includes('优先 CUDA'));
    set('whisperModel', 'base');
    const missingCuda = {detected:true,name:'NVIDIA Test GPU',backend:'cuda',runtime:{engine:'cuda',status:'missing',missing:['cublas64_12.dll'],expected_paths:['C:/Program Files/NVIDIA/CUDNN/v9.27/bin']}};
    sandbox.fetch = async () => ({ok:true,json:async()=>missingCuda});
    await run('refreshGpuDetected()');
    assert.equal(runtimeDialog.open, true);
    assert.equal(nodes.get('gpuRuntimeCudaSteps').hidden, false);
    assert.equal(nodes.get('gpuRuntimeVulkanSteps').hidden, true);
    const previousOpens = gpuRuntimeDialogOpens;
    run('closeGpuRuntimeDialog()');
    assert.equal(nodes.get('useGpu').checked, true, 'Dismissing installation help must preserve GPU preference');
    await run('refreshGpuDetected()');
    assert.equal(runtimeDialog.open, false, 'The same missing dependencies must not repeatedly reopen the dialog');
    assert.equal(gpuRuntimeDialogOpens, previousOpens);
    nodes.get('useGpu').checked = false;
    await run('refreshGpuDetected()');
    nodes.get('useGpu').checked = true;
    await run('refreshGpuDetected()');
    assert.equal(runtimeDialog.open, true, 'Explicitly enabling GPU again can show missing-dependency help');
    sandbox.fetch = async () => { throw new Error('offline'); };
    await run('recheckGpuRuntime()');
    assert.ok(nodes.get('gpuRuntimeDialogStatus').textContent.includes('检查失败'));
    assert.equal(nodes.get('gpuRuntimeRecheckBtn').disabled, false);
    sandbox.fetch = async () => ({ok:true,json:async()=>({detected:true,name:'NVIDIA Test GPU',backend:'cuda',runtime:{engine:'cuda',status:'present'}})});
    await run('recheckGpuRuntime()');
    assert.equal(runtimeDialog.open, false);
    assert.equal(nodes.get('useGpu').checked, true);
    sandbox.fetch = async () => ({ok:true,json:async()=>({detected:true,name:'Intel Test GPU',backend:'vulkan',runtime:{engine:'vulkan',status:'download_on_first_use'}})});
    await run('refreshGpuDetected()');
    assert.equal(runtimeDialog.open, true);
    assert.equal(nodes.get('gpuRuntimeCudaSteps').hidden, true);
    assert.equal(nodes.get('gpuRuntimeVulkanSteps').hidden, false);
    set('whisperModel', 'paraformer-zh');
    await run('refreshGpuDetected()');
    assert.equal(runtimeDialog.open, false, 'CPU-only models do not require GPU dependency help');
    set('whisperModel', 'base');
    let finishGpuFetch;
    sandbox.fetch = () => new Promise(resolve => { finishGpuFetch = resolve; });
    const pendingGpuFetch = run('refreshGpuDetected()');
    nodes.get('useGpu').checked = false;
    await run('refreshGpuDetected()');
    finishGpuFetch({ok:true,json:async()=>missingCuda});
    await pendingGpuFetch;
    assert.equal(runtimeDialog.open, false, 'A stale response cannot open help after GPU is disabled');
    assert.equal(nodes.get('gpuDetected').hidden, true);
    assert.ok(!nodes.has('gpuRuntimePaths') && !nodes.has('gpuRuntimeStatus'), 'Runtime paths and installation notes are absent from the sidebar');
    for (const [format, extension] of [['json', '.json'], ['txt', '.txt']]) {
        set('formatSelect', format);
        await run('downloadSummary()');
        assert.equal(sandbox.downloadedExtension, extension);
    }
    for(const format of ['docx','pdf','enex','html']) {
        set('formatSelect',format);
        await run('downloadSummary()');
        assert.equal(sandbox.downloadedFormat,format);
    }
    // Obsidian and ordinary Markdown both use the existing Markdown/attachment download route.
    for (const format of ['markdown', 'obsidian', 'png']) {
        set('formatSelect', format);
        await run('downloadSummary()');
    }

run("renderTaskAdvisory('details', 'Video download failed')");
assert.equal(nodes.get('taskScreenshotWarning').hidden, false);
assert.equal(nodes.get('taskScreenshotWarning').textContent, 'Video download failed');
run("renderTaskAdvisory('')");
assert.equal(nodes.get('taskScreenshotWarning').hidden, true);

for (const mode of ['none', 'heading', 'paragraph_start', 'paragraph_end', 'all']) {
    set('timelineMode', mode);
    for (const detail of ['brief', 'standard', 'detailed']) {
        set('textDetail', detail);
        const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
        assert.equal(config.timeline_mode, mode);
        assert.equal(config.text_detail, detail);
    }
}
set('sourceType', 'audio');
nodes.get('timedScreenshots').checked = true;
set('screenshotDensity', 'many');
const audioConfig = JSON.parse(run("JSON.stringify(buildSummarizeConfig('', 'audio-upload'))"));
assert.equal(audioConfig.include_screenshots, false);
assert.equal(audioConfig.timed_screenshots, false);
assert.equal(audioConfig.screenshot_density, 'none');
sandbox.fetch = async () => ({ok: true, json: async () => ({segments: [{start: 1.25, end: 62.5, text: 'Subtitle text'}]})});
let subtitleBlob;
sandbox.triggerBlobDownload = (blob, extension) => { subtitleBlob = blob; assert.equal(extension, '.srt'); };
sandbox.showToast = () => {};
run("currentTaskId = 'subtitle-export'; currentDownloadName = 'Video';");
    await run('exportSubtitle()');
    assert.equal(await subtitleBlob.text(), '1\n00:00:01,250 --> 00:01:02,500\nSubtitle text\n');
    assert.equal(nodes.get('exportSubtitleBtn').disabled, false);
    console.log('Timeline, audio input and SRT export checks passed');


    console.log('Frontend settings and preselected-format download checks passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });

for (const format of ['markdown', 'obsidian', 'docx', 'html', 'json', 'txt', 'png', 'pdf', 'enex']) {
    set('formatSelect', format);
    const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
    assert.equal(config.note_format, format);
}

assert.equal(run('defaultPrefs().global.obsidian_tags'), false);
assert.equal(run('defaultPrefs().global.obsidian_callouts'), false);
for (const tags of [false, true]) {
    for (const callouts of [false, true]) {
        nodes.get('obsidianTags').checked = tags;
        nodes.get('obsidianCallouts').checked = callouts;
        for (const format of ['obsidian', 'docx']) {
            set('formatSelect', format);
            const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
            assert.equal(config.obsidian_tags, format === 'obsidian' && tags);
            assert.equal(config.obsidian_callouts, format === 'obsidian' && callouts);
        }
    }
}

// Reopening restores the saved timeline/detail rather than their defaults.
const preferenceStorage = new Map();
sandbox.localStorage = {
    getItem: key => preferenceStorage.get(key) ?? null,
    setItem: (key, value) => preferenceStorage.set(key, value),
};
for (const mode of ['none', 'heading', 'paragraph_start', 'paragraph_end', 'all']) {
    for (const detail of ['brief', 'standard', 'detailed']) {
        run(`prefs = defaultPrefs(); prefs.global.timeline_mode = '${mode}';
             prefs.global.text_detail = '${detail}'; persistPrefs();`);
        assert.equal(run('mergePrefs(readStoredPrefs()).global.timeline_mode'), mode);
        assert.equal(run('mergePrefs(readStoredPrefs()).global.text_detail'), detail);
    }
}
console.log('Saved timeline and text detail survive preference reload');
run('prefs.global.auto_crop_screenshots=true; persistPrefs()');
assert.equal(run('mergePrefs(readStoredPrefs()).global.auto_crop_screenshots'),true);
console.log('Conservative crop opt-in survives preference reload');

// Every global option must survive the same save/reopen path.
run(`prefs = defaultPrefs();
    Object.keys(prefs.global).forEach(key => {
        const value = prefs.global[key];
        prefs.global[key] = typeof value === 'boolean' ? !value : 'saved-' + key;
    });
    prefs.global.text_detail = 'standard';
    prefs.ui.mcp_transport = 'sse'; prefs.update.ignored_version = 'v1.2.3';
    persistPrefs();`);
const beforeReload = JSON.parse(run('JSON.stringify(prefs.global)'));
beforeReload.gpu_backend = 'auto';
assert.deepEqual(JSON.parse(run('JSON.stringify(mergePrefs(readStoredPrefs()).global)')), beforeReload);
assert.equal(run('mergePrefs(readStoredPrefs()).ui.mcp_transport'), 'sse');
assert.equal(run('mergePrefs(readStoredPrefs()).update.ignored_version'), 'v1.2.3');
console.log('All global options, MCP transport and ignored update survive reload');

// Agent jobs preserve all note options and never transmit a model API key.
set('generationMode', 'agent'); set('formatSelect', 'docx'); set('sourceType', 'url');
set('timelineMode', 'none'); set('textDetail', 'brief'); set('summaryStyle', 'learning');
run("typedApiKey = () => 'must-not-send';");
const agentConfig = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
assert.equal(agentConfig.generation_mode, 'agent');
assert.deepEqual(agentConfig.llm_config, {});
assert.equal(agentConfig.note_format, 'docx');
assert.equal(agentConfig.timeline_mode, 'none');
assert.equal(agentConfig.text_detail, 'brief');
assert.equal(agentConfig.summary_style, 'learning');
console.log('Agent submission preserves note settings without API credentials');

set('generationMode','agent'); set('agentExecution','auto'); set('agentProvider','claude');
let autoConfig = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
assert.equal(autoConfig.agent_execution,'auto'); assert.equal(autoConfig.agent_provider,'claude');
set('agentProvider','custom'); set('agentCommand','["python", "my_agent.py", "{output_file}"]');
autoConfig = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
assert.deepEqual(autoConfig.agent_command,['python','my_agent.py','{output_file}']);
const subtitleAgent = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null, null, false, true))"));
assert.equal(subtitleAgent.output,'transcript');
assert.equal(subtitleAgent.generation_mode,'agent');
assert.equal(subtitleAgent.agent_execution,'auto');
assert.deepEqual(subtitleAgent.agent_command,['python','my_agent.py','{output_file}']);
assert.deepEqual(subtitleAgent.llm_config,{});
console.log('Automatic Claude and custom CLI options reach backend without API credentials');

// Commentary placement reaches both generation routes and keeps old checkbox preferences.
assert.ok(page.indexOf('id="timelineModeField"') < page.indexOf('id="aiCommentaryField"'));
assert.ok(page.indexOf('id="aiCommentaryField"') < page.indexOf('id="sourceLinkField"'));
assert.equal(run("normalizeAiCommentaryPosition(null, true)"), 'end');
assert.equal(run("normalizeAiCommentaryPosition(null, false)"), 'none');
assert.equal(run("normalizeAiCommentaryPosition('none', true)"), 'none');
for (const position of ['none', 'start', 'end']) {
    set('aiCommentary', position);
    assert.equal(run('selectedAiCommentaryPosition()'), position);
    for (const generation of ['api', 'agent']) {
        set('generationMode', generation);
        const config = JSON.parse(run("JSON.stringify(buildSummarizeConfig('https://example.com/video', null))"));
        assert.equal(config.ai_commentary_position, position);
        assert.equal(config.ai_commentary, position !== 'none');
    }
    run(`prefs = defaultPrefs(); prefs.global.ai_commentary_position = '${position}'; persistPrefs();`);
    assert.equal(run('mergePrefs(readStoredPrefs()).global.ai_commentary_position'), position);
}
console.log('Commentary placement, preference migration and both submission routes passed');

const modelTestIds = ['agentExecution','agentProvider','agentModel','agentCustomModel','agentReasoningEffort'];
const modelTestValues = modelTestIds.map(id=>nodes.get(id).value);
const modelTestPrefs = run('JSON.stringify(prefs)');
set('agentExecution','auto'); set('agentProvider','codex');
set('agentModel','__custom__'); set('agentCustomModel','gpt-test'); set('agentReasoningEffort','high');
let modelRequest=JSON.parse(run('JSON.stringify(agentExecutionConfig())'));
assert.equal(modelRequest.agent_model,'gpt-test');
assert.equal(modelRequest.agent_reasoning_effort,'high');
set('agentProvider','claude');
modelRequest=JSON.parse(run('JSON.stringify(agentExecutionConfig())'));
assert.equal(modelRequest.agent_model,'gpt-test');assert.equal(modelRequest.agent_reasoning_effort,'default');
set('agentProvider','codex');set('agentCustomModel','');
assert.throws(()=>run('agentExecutionConfig()'),/模型 ID/);
run("prefs.global.agent_model='gpt-test';prefs.global.agent_reasoning_effort='high';prefs=mergePrefs(prefs)");
assert.equal(run('prefs.global.agent_model'),'gpt-test');assert.equal(run('prefs.global.agent_reasoning_effort'),'high');
console.log('Codex model and effort submission, provider isolation and preference reload passed');

modelTestIds.forEach((id,i)=>set(id,modelTestValues[i]));
run('prefs=' + modelTestPrefs);

// Each provider restores its own model rather than carrying a Codex ID into Claude.
const providerPrefs = run('JSON.stringify(prefs)');
const providerValues = modelTestIds.map(id=>nodes.get(id).value);
run("prefs.global.agent_models={}; activeAgentModelProvider='codex'; agentModelCatalogs={codex:[{id:'gpt-test'}],claude:[{id:'claude-test'}]}");
set('agentProvider','codex');set('agentModel','gpt-test');set('agentReasoningEffort','high');
set('agentProvider','claude');run('switchAgentModelProvider()');
assert.equal(run('selectedAgentModel()'),'');
set('agentModel','claude-test');set('agentProvider','codex');run('switchAgentModelProvider()');
assert.equal(run('selectedAgentModel()'),'gpt-test');
set('agentProvider','claude');run('switchAgentModelProvider()');
assert.equal(run('selectedAgentModel()'),'claude-test');
modelTestIds.forEach((id,i)=>set(id,providerValues[i]));run('prefs='+providerPrefs);
console.log('Agent-specific model selections restore correctly');

const processingBefore = nodes.get('processingMode').value;
for (const mode of ['reuse','restart']) {
    set('processingMode',mode);
    assert.equal(run('selectedProcessingMode()'),mode);
    run('persistGlobals()');
    assert.equal(run('mergePrefs(readStoredPrefs()).global.processing_mode'),mode);
}
set('processingMode',processingBefore);
console.log('Sidebar subtitle-processing selection and preference persistence passed');

assert.equal(run('defaultPrefs().global.use_gpu'),false,'Fresh settings default to CPU');
assert.equal(run('mergePrefs({global:{use_gpu:true}}).global.use_gpu'),true,'An explicit saved GPU preference is preserved');
assert.ok(!/<input[^>]*id="useGpu"[^>]*checked/.test(page),'Initial HTML does not preselect GPU');
assert.equal(run('mergePrefs({global:{use_gpu:false}}).global.use_gpu'),false,'An explicit saved CPU preference is preserved');
console.log('GPU default and explicit saved preference checks passed');

set('noteSaveDirectory','D:\\Notes\\Video');
run('persistGlobals()');
assert.equal(run('mergePrefs(readStoredPrefs()).global.note_save_directory'),'D:\\Notes\\Video');
assert.equal(run("buildSummarizeConfig('https://example.com/video',null).note_save_directory"),'D:\\Notes\\Video');
console.log('Note save-directory preference and submission checks passed');

outputMode = 'note';
set('generationMode','api'); set('agentExecution','manual');
run('updateGenerationSettings()');
assert.equal(nodes.get('agentExecutionSettings').hidden,true);
assert.equal(nodes.get('agentChatConnectionSection').hidden,true);
set('generationMode','agent'); run('updateGenerationSettings()');
assert.equal(nodes.get('agentExecutionSettings').hidden,false);
assert.equal(nodes.get('agentChatConnectionSection').hidden,false);
set('agentExecution','auto'); run('updateGenerationSettings()');
assert.equal(nodes.get('agentExecutionSettings').hidden,false);
assert.equal(nodes.get('agentChatConnectionSection').hidden,true);
console.log('Agent access visibility follows API, manual and automatic modes');

// AI access stays available in both output modes; note-only controls still hide.
for (const mode of ['note', 'transcript']) {
    outputMode = mode;
    for (const channel of ['api', 'agent']) {
        set('generationMode', channel);
        run('applyOutputMode()');
        assert.notEqual(nodes.get('aiAccessSettings').inert, true);
        assert.equal(nodes.get('sidebarNoteSettings').inert, mode === 'transcript');
        assert.equal(nodes.get('apiModelSettings').hidden, channel === 'agent');
        assert.equal(nodes.get('apiConnectionSettings').hidden, channel === 'agent');
        assert.equal(nodes.get('agentExecutionSettings').hidden, channel !== 'agent');
    }
}
assert.ok(!page.includes('configureTranscriptAiBtn'));
assert.ok(nodes.get('outputModeHint').textContent.includes('AI接入设置'));
console.log('Shared AI settings remain usable in note and transcript modes');

// Rapid output-mode reversals must retain child layout and saved note settings.
set('sourceType','url'); set('formatSelect','obsidian');
set('screenshotDensity','medium'); nodes.get('timedScreenshots').checked=false;
for (const mode of ['note','transcript','note','transcript','note']) {
    outputMode=mode; run('applyOutputMode()');
    assert.equal(nodes.get('sidebarScreenshotSettings').inert,mode==='transcript');
    assert.equal(nodes.get('transcriptModeHintRegion').inert,mode!=='transcript');
    for (const id of ['screenshotDensityField','timedScreenshotSettings','visionScreenshotSettings','autoCropScreenshotSettings','screenshotVideoSettings','obsidianSettings']) {
        assert.equal(nodes.get(id).hidden,false,`${id} keeps its natural height inside the collapsing region`);
    }
    assert.ok(nodes.get('outputModeHint').textContent.includes('AI接入设置'),'Hint content survives reverse transitions');
    assert.equal(nodes.get('formatSelect').value,'obsidian');
    assert.equal(nodes.get('screenshotDensity').value,'medium');
}
console.log('Rapid output-mode reversals preserve measured content and note preferences');
