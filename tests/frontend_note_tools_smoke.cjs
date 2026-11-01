const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const dir = path.join(__dirname,'../frontend');
const callbacks=[];
const windowListeners={};
const nodes=new Map([...fs.readFileSync(path.join(dir,'index.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(([,id])=>[id,{
    value:'',checked:false,disabled:false,hidden:false,options:[],listeners:{},
    addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},replaceChildren(...items){this.options=items;},add(option){this.options.push(option);}
}]));
const storage = new Map();
const sandbox={console,URL,URLSearchParams, setTimeout,clearTimeout,
    location:{search:''},Option:function(text,value){this.text=text;this.value=value;this.setAttribute=()=>{};},
    document:{addEventListener:(name,fn)=>callbacks.push(fn),getElementById:id=>nodes.get(id),querySelector:()=>null},
    window:{addEventListener(name,fn){windowListeners[name]=fn;},setTimeout,clearTimeout},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,val)=>storage.set(key,val)}
};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(dir,name),'utf8')).join('\n'),sandbox);
vm.runInContext(fs.readFileSync(path.join(dir,'note-tools.js'),'utf8'),sandbox);
const run=source=>vm.runInContext(source,sandbox);
run(`persistGlobals=()=>{}; persistProfileForm=()=>{}; applyPreferencesToForm=()=>{}; updateGenerationSettings=()=>{}; showToast=()=>{};
prefs=defaultPrefs(); prefs.provider='custom'; prefs.customs=[{id:'c1',label:'Local model',base_url:'http://localhost/v1',model_id:'custom',manual_model:'test'}];prefs.custom_id='c1';
prefs.global.timeline_mode='heading';prefs.global.ai_commentary_position='start';prefs.global.screenshot_interval='25';prefs.global.agent_command='never store this command';`);
nodes.get('noteLanguage').value='en';nodes.get('customPrompt').value='添加复习题';nodes.get('apiKey').value='TOP-SECRET';
callbacks.at(-1)();
nodes.get('templateName').value='课程学习';nodes.get('saveTemplateBtn').listeners.click();
const template=JSON.parse(run('JSON.stringify(prefs.templates[0])'));
assert.equal(template.settings.custom_prompt,'添加复习题');
assert.equal(template.settings.timeline_mode,'heading');
assert.equal(template.settings.ai_commentary_position,'start');
assert.equal(template.settings.agent_command,undefined);
assert.ok(!JSON.stringify(template).includes('TOP-SECRET'));
nodes.get('noteTemplate').value=template.id;nodes.get('defaultTemplate').checked=true;nodes.get('defaultTemplate').listeners.change();
run(`prefs=mergePrefs(JSON.parse(localStorage.getItem(PREFS_KEY))); prefs.global.timeline_mode='none'; prefs.customs=[];`);
nodes.get('applyTemplateBtn').listeners.click();
assert.equal(run('prefs.global.timeline_mode'),'heading');
assert.equal(nodes.get('customPrompt').value,'添加复习题');
assert.equal(run('activeCustom().base_url'),'http://localhost/v1');
assert.equal(nodes.get('apiKey').value,'');
nodes.get('apiKey').value='SAME-ENDPOINT-KEY';
nodes.get('applyTemplateBtn').listeners.click();
assert.equal(nodes.get('apiKey').value,'SAME-ENDPOINT-KEY');
run(`prefs.provider='deepseek';`);
nodes.get('applyTemplateBtn').listeners.click();
assert.equal(nodes.get('apiKey').value,''); // Switching endpoints must not carry a typed credential.
nodes.get('templateName').value='新名称';nodes.get('renameTemplateBtn').listeners.click();
assert.equal(run('prefs.templates[0].name'),'新名称');
nodes.get('noteTemplate').value=template.id;nodes.get('deleteTemplateBtn').listeners.click();
assert.equal(run('prefs.templates.length'),0);assert.equal(run('prefs.default_template_id'),'');
assert.equal(sandbox.window.NoteTools.stamp(3661),'01:01:01');
assert.equal(sandbox.window.NoteTools.stamp(-3),'00:00');
console.log('Template snapshot, secret exclusion, reload, deleted model recovery, rename and default deletion passed');

// Default templates must wait for asynchronous workspace preferences to load.
run('window.__aiVideoNoteReady=false; prefs=defaultPrefs();');
callbacks.at(-1)();
assert.equal(typeof windowListeners['preferences-ready'],'function');
sandbox.savedTemplate=template;
run("prefs.templates=[savedTemplate]; prefs.default_template_id=savedTemplate.id; prefs.global.timeline_mode='none'; window.__aiVideoNoteReady=true;");
windowListeners['preferences-ready']();
assert.equal(run('prefs.global.timeline_mode'),'heading');
assert.equal(nodes.get('customPrompt').value,'添加复习题');
console.log('Default template restoration waits for workspace settings');
