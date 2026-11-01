const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const frontend=path.join(__dirname,'../frontend');
const select={value:'compatibility',options:[{}, {disabled:true}]},status={},retry={};
const nodes={videoEncoding:select,videoCodecStatus:status,videoCodecRetry:retry};
let response={available:true,hevc_extension:false};
const sandbox={console,URL,setTimeout,clearTimeout,document:{addEventListener(){},getElementById:id=>nodes[id]},
 window:{addEventListener(){},setTimeout,clearTimeout},AbortController,
 fetch:async()=>({ok:true,json:async()=>response})};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name=>fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
run("prefs=defaultPrefs();prefs.global.video_encoding='size'");
(async()=>{
 await run('loadVideoCodecCapabilities()');
 assert.equal(select.value,'size');assert.equal(select.options[1].disabled,false);
 assert.ok(status.textContent.includes('未安装 HEVC 扩展'));
 assert.ok(status.textContent.includes('不依赖该扩展'));
 response={available:false,hevc_extension:true};
 await run('loadVideoCodecCapabilities(true)');
 assert.equal(select.value,'compatibility');assert.equal(select.options[1].disabled,true);
 assert.ok(status.textContent.includes('可能是硬件不支持或驱动不可用'));
 sandbox.fetch=async()=>{throw Error('offline')};
 await run('loadVideoCodecCapabilities(true)');
 assert.equal(select.options[1].disabled,true);assert.equal(retry.disabled,false);
 assert.equal(select.disabled,false);assert.ok(status.textContent.includes('检测失败'));
 console.log('HEVC hardware gate, extension disclosure, saved preference and retry checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
