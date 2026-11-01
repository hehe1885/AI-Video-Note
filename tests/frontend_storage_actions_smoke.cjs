const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function element() {
    return {disabled:false,hidden:false,checked:true,open:false,textContent:'',children:[],listeners:{},
        addEventListener(name,fn){this.listeners[name]=fn;},
        append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
        showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},focus(){}};
}
const ids = ['openOutputFileBtn','openOutputFolderBtn','copyOutputPathBtn','storageManageBtn','storageDialog',
    'storageRefreshBtn','storageAudio','storageVideo','storageCleanBtn','storageCloseBtn',
    'storageSizes','storagePreview','storagePreviewFiles','storagePreviewSummary','storageStatus'];
const nodes = Object.fromEntries(ids.map(id=>[id,element()]));
const events=[], copies=[], messages=[], requests=[];
const sandbox={console,API_BASE:'/api',currentTaskId:'old',taskViewSequence:0,
    document:{getElementById:id=>nodes[id],createElement:element,addEventListener:(name,fn)=>events.push(fn)},
    window:{addEventListener(){}},
    readResponse:async response=>{if(response.error)throw Error(response.error);return response;},
    copyTextToClipboard:async text=>copies.push(text),showToast:text=>messages.push(text)};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/storage-tools.js'),'utf8'),sandbox);
events.forEach(fn=>fn());
const click = id=>nodes[id].listeners.click();
const data={categories:{audio:{bytes:1024},video:{bytes:2048},screenshots:{bytes:512},models:{bytes:4096}},total_bytes:7680,can_cleanup:true,
    count:1,bytes:1024,reclaimable_bytes:0,token:'opaque-preview-token',files:[{path:'C:/素材/音频.m4a',kind:'audio',bytes:1024}]};
(async()=>{
    let resolve;
    sandbox.fetchWithTimeout=(url,options)=>{requests.push({url,options});return new Promise(r=>resolve=r);};
    sandbox.window.setResultLocation('old',{output_directory:'C:/old'});
    const pending=click('copyOutputPathBtn');
    sandbox.currentTaskId='new';sandbox.taskViewSequence++;
    sandbox.window.setResultLocation('new',{output_directory:'C:/new'});
    resolve({file_path:'C:/old/note.md'});await pending;
    assert.equal(copies.length,0,'Late path response must not copy the old task path');
    assert.equal(nodes.copyOutputPathBtn.disabled,false);
    sandbox.fetchWithTimeout=async(url,options)=>{requests.push({url,options});return {file_path:'C:/new/note.md'};};
    await click('copyOutputPathBtn');assert.equal(copies.at(-1),'C:/new/note.md');
    await click('openOutputFileBtn');assert.equal(requests.at(-1).url,'/api/task/new/output/open');
    assert.deepEqual(JSON.parse(requests.at(-1).options.body),{target:'file'});
    await click('openOutputFolderBtn');assert.deepEqual(JSON.parse(requests.at(-1).options.body),{target:'folder'});
    sandbox.window.resetResultLocation();assert.equal(nodes.openOutputFileBtn.disabled,true);

    sandbox.fetchWithTimeout=async(url,options)=>{requests.push({url,options});return data;};
    await click('storageManageBtn');
    assert.equal(nodes.storageDialog.open,true);
    assert.equal(requests.at(-1).url,'/api/storage/preview','Opening the manager automatically previews the selected caches');
    assert.deepEqual(JSON.parse(requests.at(-1).options.body),{kinds:['audio','video']});
    assert.ok(!requests.some(item=>item.url.endsWith('/cleanup')));
    assert.equal(nodes.storageCleanBtn.disabled,false);
    assert.equal(nodes.storagePreviewFiles.children[0].textContent.includes('C:/素材/音频.m4a'),true);
    nodes.storageAudio.checked=false;
    const changed=nodes.storageAudio.listeners.change();
    assert.equal(nodes.storageCleanBtn.disabled,true,'Changing selected types disables cleanup until the new preview arrives');
    await changed;
    assert.deepEqual(JSON.parse(requests.at(-1).options.body),{kinds:['video']});
    assert.equal(nodes.storageCleanBtn.disabled,false);
    nodes.storageVideo.checked=false;await nodes.storageVideo.listeners.change();
    assert.equal(requests.at(-1).url,'/api/storage','Selecting no cache types only refreshes sizes');
    assert.equal(nodes.storagePreview.hidden,true);
    assert.equal(nodes.storageCleanBtn.disabled,true);
    nodes.storageAudio.checked=true;await nodes.storageAudio.listeners.change();
    sandbox.fetchWithTimeout=async(url,options)=>{
        requests.push({url,options});
        return url.endsWith('/cleanup')?{removed_count:1,failed_count:0,reclaimed_bytes:0}:data;
    };
    await click('storageCleanBtn');
    assert.equal(requests.filter(item=>item.url.endsWith('/cleanup')).length,1);
    assert.deepEqual(JSON.parse(requests.find(item=>item.url.endsWith('/cleanup')).options.body),{token:data.token});
    assert.match(nodes.storageStatus.textContent,/缓存清理完成/);
    assert.equal(nodes.storageCleanBtn.disabled,true,'A consumed preview must not be reused');
    sandbox.fetchWithTimeout=async()=>({...data,can_cleanup:false});
    await click('storageRefreshBtn');assert.equal(nodes.storageCleanBtn.disabled,true,'Running tasks block confirmation');
    click('storageCloseBtn');assert.equal(nodes.storageDialog.open,false);
    sandbox.fetchWithTimeout=(url,options)=>{requests.push({url,options});return new Promise(r=>resolve=r);};
    const opening=click('storageManageBtn');
    click('storageCloseBtn');resolve(data);await opening;
    assert.equal(nodes.storagePreview.hidden,true,'A preview arriving after close must be discarded');
    assert.equal(nodes.storageCleanBtn.disabled,true);
    console.log('Task output paths, late responses, automatic previews, changed and empty selections, consumed tokens and busy-task cleanup checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
