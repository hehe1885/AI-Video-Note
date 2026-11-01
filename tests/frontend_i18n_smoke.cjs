const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..', 'frontend');
class Element {
    constructor(tag, id = '', text = '') { this.nodeType = 1; this.tagName = tag; this.id = id; this.children = []; this.attrs = {}; this.value = ''; this.checked = false; this.disabled = false; this.listeners = {}; if (text) this.addText(text); }
    add(child) { child.parentElement = this; this.children.push(child); return child; }
    addText(text) { return this.add({nodeType: 3, nodeValue: text}); }
    closest(selector) {
        for (let node = this; node; node = node.parentElement) {
            if (selector.split(',').some(item => item.trim() === '#' + node.id || item.trim().toUpperCase() === node.tagName || item.trim() === '[data-no-i18n]' && node.hasAttribute('data-no-i18n'))) return node;
        }
        return null;
    }
    hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
    getAttribute(name) { return this.attrs[name]; }
    setAttribute(name, value) { this.attrs[name] = value; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    get textContent() { return this.children.map(n => n.nodeType === 3 ? n.nodeValue : n.textContent).join(''); }
    set textContent(value) { this.children = []; this.addText(value); }
    set title(value) { this.attrs.title = value; }
}
function boot(storedLanguage, systemLanguage = 'zh-CN', nativeFetch = null) {
    const body = new Element('BODY');
    const button = body.add(new Element('BUTTON', 'languageToggle', 'English'));
    button.setAttribute('data-no-i18n', '');
    const state = body.add(new Element('SPAN', 'serviceState', '本机服务 · 127.0.0.1:8000'));
    const input = body.add(new Element('INPUT', 'videoUrl')); input.value = '用户自己的视频标题'; input.setAttribute('placeholder', '视频链接');
    const note = body.add(new Element('ARTICLE', 'markdownContent', '# 视频笔记\n生成的原文'));
    const logs = body.add(new Element('DIV', 'logArea', '准备音频：原始诊断'));
    const pathLabel = body.add(new Element('SPAN', 'outputPath', 'D:\视频笔记\记录.md'));
    const check = body.add(new Element('INPUT', 'obsidianTags')); check.checked = true; check.disabled = true;
    const model = body.add(new Element('OPTION', 'modelOption', 'base（默认，下载较小）（未安装，约 142MB）')); model.value = 'base';
    const caption = body.add(new Element('SPAN', 'caption', '处理进度'));
    const store = new Map(storedLanguage ? [['vtn_ui_language', storedLanguage]] : []);
    const prompts = [];
    const listeners = {};
    const scheduled = [];
    const document = {body, title: '', documentElement: {}, addEventListener: (name, fn) => { (listeners[name] ||= []).push(fn); }, getElementById: id => body.children.find(n => n.id === id), createTreeWalker: element => {
        const nodes = []; const collect = e => { for (const child of e.children || []) { nodes.push(child); collect(child); } }; collect(element); let i = 0;
        return {nextNode: () => nodes[i++] || null};
    }};
    const sandbox = {navigator: {language: systemLanguage}, document, NodeFilter: {SHOW_ELEMENT:1, SHOW_TEXT:4}, localStorage:{getItem:key => store.get(key), setItem:(key,value) => store.set(key,value)}, queueMicrotask: fn => fn(), setTimeout: fn => scheduled.push(fn), MutationObserver: class {observe() {}}, console};
    sandbox.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
    sandbox.window = {dispatchEvent() {}, confirm: message => {prompts.push(message); return true;}};
    if (nativeFetch) sandbox.fetch=nativeFetch;
    vm.createContext(sandbox);
    for (const file of ['locales/en.js', 'i18n.js']) vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),sandbox);
    for (const fn of listeners.DOMContentLoaded || []) fn();
    // App initialization must capture Chinese source labels before the translation task runs.
    const capturedModelLabel = model.textContent;
    for (const fn of scheduled) fn();
    return {capturedModelLabel, i18n:sandbox.window.AIVideoNoteI18n, document, button, state, input, note, logs, pathLabel, check, model, caption, store, prompts, window:sandbox.window};
}
const ui = boot();
assert.equal(ui.i18n.language,'zh-CN');
ui.button.listeners.click();
assert.equal(ui.document.documentElement.lang,'en');
assert.equal(ui.document.title,'AI Video Note - Local Video Notes');
assert.equal(ui.button.textContent,'中文');
assert.equal(ui.state.textContent,'Local service · 127.0.0.1:8000');
assert.equal(ui.input.getAttribute('placeholder'),'Video URL');
assert.equal(ui.model.value,'base');
assert.ok(ui.model.textContent.includes('Not installed'));
assert.equal(ui.input.value,'用户自己的视频标题');
assert.equal(ui.note.textContent,'# 视频笔记\n生成的原文');
assert.equal(ui.logs.textContent,'准备音频：原始诊断');
assert.equal(ui.pathLabel.textContent,'D:\视频笔记\记录.md');
assert.equal(ui.check.checked,true); assert.equal(ui.check.disabled,true);
ui.caption.textContent = '已完成'; ui.i18n.apply(ui.caption);
assert.equal(ui.caption.textContent,'Completed');
assert.equal(ui.i18n.translate('已完成（保存失败）'), 'Completed (save failed)');
assert.equal(ui.i18n.translate('笔记自动保存失败：Disk full。正文已保留，可使用下载按钮重试导出。'),
    'Automatic note save failed: Disk full。The generated text is preserved. Use Download to retry the export.');
ui.window.confirm('取消当前任务？已生成的音频和转录文件会保留。');
assert.equal(ui.prompts[0],'Cancel this task? Generated audio and transcripts will be retained.');
assert.equal(ui.i18n.translate('笔记保存位置：D:\\视频笔记\\学习讲义\n图片保存位置：D:\\视频笔记\\图片'), 'Note folder:D:\\视频笔记\\学习讲义\nImage folder:D:\\视频笔记\\图片');
ui.button.listeners.click();
assert.equal(ui.document.documentElement.lang,'zh-CN');
assert.equal(ui.model.textContent,'base（默认，下载较小）（未安装，约 142MB）');
assert.equal(ui.state.textContent,'本机服务 · 127.0.0.1:8000');
assert.equal(ui.caption.textContent,'已完成');
assert.equal(ui.input.getAttribute('placeholder'),'视频链接');
ui.i18n.setLanguage('en');
assert.equal(ui.store.get('vtn_ui_language'),'en');
for (const [source,target] of [
    ['连接中断，任务状态待确认','Connection lost; task status is unknown'],
    ['重新连接','Reconnect'],
    ['正在重新连接原任务','Reconnecting to the original task'],
]) {
    ui.caption.textContent = source; ui.i18n.apply(ui.caption);
    assert.equal(ui.caption.textContent,target);
    ui.i18n.setLanguage('zh-CN');
    assert.equal(ui.caption.textContent,source);
    ui.i18n.setLanguage('en');
}
assert.equal(ui.i18n.translate('模型调用：达到输出上限，正在续写（1/3）'), 'Model request：Output limit reached; continuing（1/3）');
assert.equal(ui.i18n.translate('画面处于过渡或操作尚未完成，正在扩大范围补找一次'), 'Retrying nearby frames once for transitions or unfinished operations');
assert.equal(ui.i18n.translate('截图补找未完成，保留首轮已核对的图片'), 'The nearby-frame retry failed; keeping images already verified');
assert.equal(ui.i18n.translate('缺少 CUDA 12 / cuDNN 9 运行库，请按官方说明安装后重启软件。'), 'CUDA 12 / cuDNN 9 libraries are missing. Follow the official guides, then restart the app.');
assert.equal(ui.i18n.translate('首次识别时将下载 Vulkan 组件到 NuGet 全局包目录。'), 'Vulkan components will be downloaded to the global NuGet package folder on first use.');
assert.equal(ui.i18n.translate('CUDA 12 官方安装说明'), 'Official CUDA 12 installation guide');
assert.equal(ui.i18n.translate('需要准备显卡加速组件'), 'GPU acceleration components are needed');
assert.equal(ui.i18n.translate('需要 CUDA 12.x 和 cuDNN 9（CUDA 12 版本）。'), 'CUDA 12.x and cuDNN 9 for CUDA 12 are required.');
const restored = boot(ui.store.get('vtn_ui_language'));
assert.equal(ui.i18n.translate('手动安装'), 'Manual installation');
assert.equal(ui.i18n.translate('安装完成，已通过加速检查。请从托盘重启软件。'), 'Installation complete and GPU checks passed. Restart the app from the system tray.');
assert.equal(restored.state.textContent,'Local service · 127.0.0.1:8000');
assert.equal(restored.capturedModelLabel,'base（默认，下载较小）（未安装，约 142MB）');
const page = fs.readFileSync(path.join(root,'index.html'),'utf8');
assert.ok(page.indexOf('id="languageToggle"') < page.indexOf('id="serviceState"'));
console.log('Language switching, persistence, live labels, dialogs and user-content preservation passed');

for (const language of ['zh-CN', 'zh-TW', 'zh-HK', 'zh-SG']) assert.equal(boot(null, language).i18n.language, 'zh-CN');
for (const language of ['en-US', 'ja-JP', 'de-DE', 'fr-FR']) assert.equal(boot(null, language).i18n.language, 'en');
assert.equal(boot('en', 'zh-CN').i18n.language, 'en');
assert.equal(boot('zh-CN', 'en-US').i18n.language, 'zh-CN');
console.log('First-use language detection and saved preference priority passed');

const animated = boot('zh-CN');
const transitions = [];
let skipped = 0;
animated.document.startViewTransition = update => {
    transitions.push(update);
    return {skipTransition: () => skipped++, ready: Promise.resolve(), finished: Promise.resolve()};
};
animated.button.listeners.click();
assert.equal(animated.i18n.language, 'zh-CN', 'snapshot precedes translation');
animated.button.listeners.click();
animated.button.listeners.click();
transitions[0](); transitions[1]();
assert.equal(animated.i18n.language, 'zh-CN', 'stale snapshots must not apply');
transitions[2]();
assert.equal(animated.i18n.language, 'en');
assert.equal(animated.input.value, '用户自己的视频标题');
assert.equal(skipped, 2);
animated.window.matchMedia = () => ({matches:true});
animated.button.listeners.click();
assert.equal(animated.i18n.language, 'zh-CN');
assert.equal(transitions.length, 3, 'reduced motion skips snapshots');
console.log('Language transition, rapid switching and reduced motion passed');

(async()=>{
    // The workspace language overrides stale browser cache after the response.
    const remote=boot('zh-CN','zh-CN',async()=>({ok:true,json:async()=>({language:'en',saved:true})}));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(remote.i18n.language,'en');
    // A late disk response cannot undo a user's manual toggle.
    let resolveNative;
    const manual=boot('zh-CN','zh-CN',()=>new Promise(resolve=>resolveNative=resolve));
    manual.button.listeners.click();
    resolveNative({ok:true,json:async()=>({language:'zh-CN',saved:true})});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(manual.i18n.language,'en');
    for (const source of ['设置文件损坏，已保留原文件并停止自动写入','设置已被另一页面修改，请重新读取后再保存','重试保存设置','没有匹配的任务','导入设置','正在导入设置…','设置已导入，正在刷新…','请等待当前任务结束后再导入设置','备份或当前设置已变化，请重新选择文件导入']) {
        remote.caption.textContent=source;remote.i18n.apply(remote.caption);
        assert.notEqual(remote.caption.textContent,source);
        remote.i18n.setLanguage('zh-CN');assert.equal(remote.caption.textContent,source);
        remote.i18n.setLanguage('en');
    }
    console.log('Workspace language priority, manual-toggle races and settings-error translations passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
