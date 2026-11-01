const assert=require('node:assert/strict');
const fs=require('node:fs'); const path=require('node:path'); const vm=require('node:vm');
const frontend=path.join(__dirname,'..','frontend');
const html=fs.readFileSync(path.join(frontend,'index.html'),'utf8');
assert.ok(html.indexOf('id="visionScreenshotSettings"') < html.indexOf('id="autoCropScreenshotSettings"'));
assert.ok(html.indexOf('id="autoCropScreenshotSettings"') < html.indexOf('id="timedScreenshotSettings"'));
const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(([,id])=>[id,{value:'',checked:false,disabled:false,hidden:false,textContent:'',dataset:{}}]));
let next=0; const timers=new Map();
const setTimer=(fn,ms)=>{const id=++next;timers.set(id,{fn,ms});return id;};
const clearTimer=id=>timers.delete(id);
const sandbox={console,URL,Blob,AbortController,setTimeout:setTimer,clearTimeout:clearTimer,
 document:{addEventListener(){},getElementById:id=>nodes.get(id),querySelector:()=>({value:'note'})},
 window:{addEventListener(){},setTimeout:setTimer,clearTimeout:clearTimer}};
vm.createContext(sandbox); vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
const set=(id,value)=>nodes.get(id).value=value;
run(`biliPagesPanelVisible=()=>false; checkedBiliPages=()=>[]; getSelectedModelConfig=()=>globalThis.testModel; typedApiKey=()=> 'fake'; hasReusableKey=()=>false;`);
sandbox.testModel={provider:'custom',baseUrl:'https://example.com/v1',model:'text'};
set('sourceType','url');set('screenshotDensity','medium');set('formatSelect','markdown');
async function flush() {const entry=[...timers].find(([,item])=>item.ms===800);assert.ok(entry);timers.delete(entry[0]);await entry[1].fn();}
(async()=>{
 sandbox.fetch=async()=>({ok:true,json:async()=>({state:'unsupported',supported:false,message:'所选模型不支持视觉识别'})});
 run('scheduleVisionCheck(); scheduleVisionCheck();'); // Debouncing must not discard the pending probe.
 assert.equal(nodes.get('visionScreenshots').disabled,true);
 await flush();
 assert.equal(nodes.get('visionScreenshots').disabled,true);
 assert.equal(nodes.get('visionCapabilityHint').textContent,'所选模型不支持视觉识别');
 sandbox.testModel.model='vision';
 sandbox.fetch=async()=>({ok:true,json:async()=>({state:'supported',supported:true,message:'Supported'})});
 run('scheduleVisionCheck()');await flush();
 assert.equal(nodes.get('visionScreenshots').disabled,false);
 assert.equal(nodes.get('visionScreenshots').checked,false); // Default is opt out.
 assert.equal(nodes.get('autoCropScreenshots').checked,false);
 assert.equal(nodes.get('autoCropScreenshots').disabled,true);
 run('visionPreference=true; updateVisionCheckbox()');
 assert.equal(nodes.get('autoCropScreenshots').disabled,false);
 run('cropPreference=true; updateVisionCheckbox()');
 assert.equal(nodes.get('autoCropScreenshots').checked,true);
 assert.equal(run("buildSummarizeConfig('https://example.com/video',null).auto_crop_screenshots"),true);
 run('visionPreference=false; updateVisionCheckbox()');
 assert.equal(nodes.get('autoCropScreenshots').disabled,true);
 assert.equal(run("buildSummarizeConfig('https://example.com/video',null).auto_crop_screenshots"),false);
 run('visionPreference=true; updateVisionCheckbox()');
 assert.equal(nodes.get('autoCropScreenshots').checked,true); // Restore remembered opt-in.

 assert.equal(run("buildSummarizeConfig('https://example.com/video',null).vision_screenshots"),true);
 // A stale result from the previous model cannot enable or disable the new model.
 let resolveOld;
 sandbox.testModel.model='old';sandbox.fetch=()=>new Promise(resolve=>{resolveOld=resolve;});
 run('scheduleVisionCheck()');const old=flush();
 sandbox.testModel.model='new';sandbox.fetch=async()=>({ok:true,json:async()=>({state:'supported',supported:true,message:'New supported'})});
 run('scheduleVisionCheck()');await flush();
 resolveOld({ok:true,json:async()=>({state:'unsupported',supported:false,message:'Old unsupported'})});await old;
 assert.equal(nodes.get('visionScreenshots').disabled,false);
 assert.equal(nodes.get('visionCapabilityHint').textContent,'New supported');
 nodes.get('timedScreenshots').checked=true;run('scheduleVisionCheck()');
 assert.equal(nodes.get('visionScreenshots').disabled,true);
 assert.equal(run("buildSummarizeConfig('https://example.com/video',null).vision_screenshots"),false);
 assert.equal(nodes.get('autoCropScreenshots').disabled,true);
 assert.equal(run("buildSummarizeConfig('https://example.com/video',null).auto_crop_screenshots"),false);
 set('sourceType','url');nodes.get('timedScreenshots').checked=false;
 set('generationMode','agent');run('scheduleVisionCheck()');
 assert.equal(nodes.get('retryVisionCheckBtn').hidden,true);
 assert.equal(nodes.get('visionScreenshots').disabled,false);
 assert.match(nodes.get('visionCapabilityHint').textContent,/支持图片的客户端模型/);
 assert.equal(run("buildSummarizeConfig('https://example.com/video',null).auto_crop_screenshots"),true);
 set('generationMode','api');run('scheduleVisionCheck()');
 assert.equal(nodes.get('retryVisionCheckBtn').hidden,false);
 set('sourceType','audio');run('updateVisionCheckbox()');assert.equal(nodes.get('visionScreenshotSettings').hidden,true);
 assert.equal(nodes.get('autoCropScreenshotSettings').hidden,true);
 console.log('Crop checkbox placement, default, dependent states, remembered opt-in and API/Agent payload passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
