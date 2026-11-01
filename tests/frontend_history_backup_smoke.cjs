const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
class Element {
    constructor(){this.children=[];this.value='';this.hidden=false;this.disabled=false;this.dataset={};this.textContent='';this.files=[];}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=items;}
    setAttribute(){}
    showModal(){this.open=true;}
    close(){this.open=false;}
}
const frontend=path.join(__dirname,'../frontend');
const html=fs.readFileSync(path.join(frontend,'index.html'),'utf8');
const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(([,id])=>[id,new Element()]));
const sandbox={console,URL,Blob,setTimeout,clearTimeout,
    document:{addEventListener(){},getElementById:id=>nodes.get(id),createElement:()=>new Element(),querySelectorAll:()=>[]},
    window:{addEventListener(){},setTimeout,clearTimeout,location:{reload(){sandbox.reloaded=(sandbox.reloaded||0)+1;}}}};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name=>fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
const reply=(data,status=200)=>({ok:status===200,status,json:async()=>data});
run("armRecentTasksElapsedTicker=()=>{}; recentTaskMetaText=()=>''; showToast=()=>{}; flushPendingPrefs=()=>{}; workspacePreferences.mode='ready';");
const rows=(start,end)=>Array.from({length:end-start},(_,i)=>({task_id:'t'+(start+i),title:'Task '+(start+i),status:'completed'}));
(async()=>{
    const requests=[];
    sandbox.fetch=async(url)=>{
        const query=new URL(url,'http://localhost').searchParams;
        requests.push(query);
        const offset=Number(query.get('offset'));
        return reply({tasks:offset?rows(19,23):rows(0,20),total:23,has_more:!offset,next_offset:offset?23:20});
    };
    nodes.get('historySearch').value='课程 & 视频';
    nodes.get('historyStatus').value='completed';
    await run('loadRecentTasks()');
    assert.equal(requests[0].get('q'),'课程 & 视频');
    assert.equal(requests[0].get('status'),'completed');
    assert.equal(nodes.get('recentTaskList').children.length,20);
    assert.equal(nodes.get('loadMoreHistoryBtn').hidden,false);
    await run('loadRecentTasks(false,true)');
    assert.equal(requests[1].get('offset'),'20');
    assert.equal(nodes.get('recentTaskList').children.length,23,'Append deduplicates overlapping rows');
    assert.equal(nodes.get('loadMoreHistoryBtn').hidden,true);
    assert.match(nodes.get('historyCount').textContent,/23/);
    // New filters must win over a late response from the old query.
    let finish;
    sandbox.fetch=()=>new Promise(resolve=>finish=resolve);
    const old=run('loadRecentTasks()');
    sandbox.fetch=async()=>reply({tasks:[{task_id:'new',title:'New',status:'failed'}],total:1,has_more:false,next_offset:1});
    await run('changeHistoryFilters()');
    finish(reply({tasks:rows(0,20),total:20}));await old;
    assert.equal(run('historyItems[0].task_id'),'new');
    assert.equal(nodes.get('refreshRecentTasksBtn').disabled,false);
    sandbox.fetch=async()=>reply({tasks:[],total:0,has_more:false,next_offset:0});
    await run('loadRecentTasks()');
    assert.equal(nodes.get('recentTasksPanel').hidden,false,'Keep filters accessible for zero matches');
    assert.equal(nodes.get('historyEmpty').hidden,false);

    // The import button opens the native chooser directly; there is no restore modal.
    assert.equal(nodes.has('settingsBackupDialog'),false);
    assert.equal(nodes.has('settingsBackupSelect'),false);
    assert.match(html,/<input id="settingsBackupFile"[^>]* hidden>/);
    assert.match(html,/id="restoreSettingsBtn"[^>]*>导入设置</);
    const calls=[];
    const route=async(url,options={})=>{
        calls.push({url,options});
        if(url.endsWith('/export'))return reply({format:'ai-video-note-settings',schema:3,preferences:{ui:{theme:'dark'}}});
        if(url.endsWith('/preview'))return reply({backup_revision:'a'.repeat(64),current_revision:'b'.repeat(64)});
        if(url.endsWith('/restore'))return reply({schema:3,exists:true,revision:'restored',preferences:{ui:{theme:'dark'}}});
        throw Error(url);
    };
    sandbox.route=route;
    sandbox.download=(blob,extension,name)=>{sandbox.downloaded={blob,extension,name};};
    run('fetchWithTimeout=(url,options)=>route(url,options); triggerBlobDownload=(...args)=>download(...args);');
    await run('exportSettingsBackup()');
    assert.match(sandbox.downloaded.name,/AI-Video-Note-settings/);
    assert.equal(JSON.parse(await sandbox.downloaded.blob.text()).format,'ai-video-note-settings');
    const input=nodes.get('settingsBackupFile');
    input.click=()=>{input.clicked=(input.clicked||0)+1;};
    input.value='old-file';
    run('chooseSettingsBackupFile()');
    assert.equal(input.clicked,1);
    assert.equal(input.value,'');
    assert.equal(calls.length,1,'Choosing a file makes no API request');
    await run('readSettingsBackupFile()');
    assert.equal(sandbox.reloaded,undefined,'Cancelling the chooser leaves settings unchanged');

    const document={format:'ai-video-note-settings',schema:3,preferences:{ui:{theme:'dark'}}};
    const select=(text=JSON.stringify(document),size=text.length)=>{input.files=[{size,text:async()=>text}];};
    const reset=()=>{
        calls.length=0;sandbox.reloaded=0;sandbox.route=route;input.files=[];
        run("settingsRecoveryBusy=false; isTaskActive=false; isSubmitting=false; currentTaskStatus='completed'; workspacePreferences.mode='ready'; workspacePreferences.saving=false; workspacePreferences.pending=null;");
    };
    reset();select();
    run("workspacePreferences.pending={ui:{theme:'stale'}};");
    await run('readSettingsBackupFile()');
    assert.equal(calls.length,2,'Selecting a file automatically validates and imports');
    assert.match(calls[0].url,/\/preview$/);
    const restored=JSON.parse(calls[1].options.body);
    assert.deepEqual(restored.document,document);
    assert.equal(restored.backup_revision,'a'.repeat(64));assert.equal(restored.current_revision,'b'.repeat(64));
    assert.equal(sandbox.reloaded,1);
    assert.equal(run('workspacePreferences.mode'),'blocked','No stale autosave before reload');
    assert.equal(run('workspacePreferences.pending'),null);
    assert.equal(input.value,'');

    reset();run('isTaskActive=true;');
    run('chooseSettingsBackupFile()');assert.equal(input.clicked,1);
    select();await run('readSettingsBackupFile()');assert.equal(calls.length,0);
    assert.match(nodes.get('settingsBackupStatus').textContent,/当前任务/);
    for(const [text,size] of [['{bad',4],['[]',2],[JSON.stringify(document),3*1024*1024]]){
        reset();select(text,size);await run('readSettingsBackupFile()');
        assert.equal(calls.length,0);assert.equal(sandbox.reloaded,0);
        assert.equal(run('settingsRecoveryBusy'),false);assert.equal(input.disabled,false);
        assert.match(nodes.get('settingsBackupStatus').textContent,/无效或过大/);
    }
    for(const status of [409,422]){
        reset();select();
        sandbox.route=async(url,options)=>url.endsWith(status===409?'/restore':'/preview')
            ?reply({detail:{message:'import rejected'}},status):route(url,options);
        await run('readSettingsBackupFile()');
        assert.equal(sandbox.reloaded,0);assert.equal(run('workspacePreferences.mode'),'ready');
        assert.equal(nodes.get('settingsBackupStatus').textContent,'import rejected');
        assert.equal(input.disabled,false);assert.equal(nodes.get('restoreSettingsBtn').disabled,false);
        sandbox.route=route;select();await run('readSettingsBackupFile()');
        assert.equal(sandbox.reloaded,1,'A failed import can be retried with the same file');
    }
    // A task appearing during file reading or validation must prevent replacement.
    reset();input.files=[{size:10,text:()=>new Promise(resolve=>finish=resolve)}];
    let pending=run('readSettingsBackupFile()');
    run('isTaskActive=true;');finish(JSON.stringify(document));await pending;
    assert.equal(calls.length,0);assert.equal(sandbox.reloaded,0);
    reset();select();finish=null;
    sandbox.route=()=>new Promise(resolve=>finish=resolve);
    pending=run('readSettingsBackupFile()');
    while(!finish || !run('settingsRecoveryBusy'))await new Promise(resolve=>setTimeout(resolve,0));
    // Wait for the asynchronous file read and save wait to reach the preview request.
    await new Promise(resolve=>setTimeout(resolve,0));
    run('isSubmitting=true;');finish(reply({backup_revision:'a'.repeat(64),current_revision:'b'.repeat(64)}));
    await pending;assert.equal(sandbox.reloaded,0);assert.equal(run('settingsRecoveryBusy'),false);

    // In-flight ordinary saves finish before import obtains its revision snapshot.
    reset();select();run('workspacePreferences.saving=true;');
    pending=run('readSettingsBackupFile()');
    await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(calls.length,0);assert.equal(run('settingsRecoveryBusy'),true);
    const requestsBeforeSubmit=calls.length;
    await run('startSummary()');assert.equal(calls.length,requestsBeforeSubmit,'New task submission is paused during import');
    run('workspacePreferences.saving=false;');await pending;
    assert.equal(sandbox.reloaded,1);
    console.log('History filtering, native settings picker, automatic file import, revision conflicts and save/task races passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
