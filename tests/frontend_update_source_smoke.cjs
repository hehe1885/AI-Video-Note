const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend=path.join(__dirname,'..','frontend');
const nodes=new Map(['updateBody','updateTitle','updateIgnoreBtn','updateCancelBtn','updateModal','updateGithubLink'].map(id=>[id,{textContent:'',hidden:true}]));
nodes.get('updateGithubLink').href='https://github.com/hehe1885/AI-Video-Note/releases/latest';
const requests=[];const opened=[];const listeners=[];
const source={isConnected:true,focus(){sandbox.document.activeElement=this;}};
nodes.get('updateIgnoreBtn').hidden=false;nodes.get('updateCancelBtn').hidden=false;
const modal=nodes.get('updateModal');
modal.dataset={};modal.open=false;modal.events={};
modal.addEventListener=(name,fn)=>modal.events[name]=fn;
modal.showModal=()=>{modal.open=true;};
modal.close=()=>{modal.open=false;modal.events.close?.();};
for(const node of nodes.values()){node.tabIndex=0;node.getClientRects=()=>node.hidden?[]:[{}];node.focus=()=>{sandbox.document.activeElement=node;};}
modal.querySelectorAll=()=>['updateGithubLink','updateIgnoreBtn','updateCancelBtn'].map(id=>nodes.get(id));
const sandbox={console,URL,setTimeout,clearTimeout,
    document:{activeElement:source,documentElement:{lang:'zh-CN'},addEventListener(){},getElementById:id=>nodes.get(id)},
    window:{addEventListener:(name,fn)=>listeners.push({name,fn}),AIVideoNoteI18n:{language:'zh-CN'},open:url=>opened.push(url)}
};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
sandbox.response={ok:true,update_available:true,current_version:'1.0.0',latest_version:'v2.0.0',source:'gitee',source_name:'Gitee',release_url:'https://gitee.com/hehe1885/AI-Video-Note/releases/latest',notes:'Test notes'};
sandbox.record=url=>requests.push(url);
run('fetchWithTimeout=async url=>{record(url);return response};readResponse=async response=>response;renderNoteSaveLocation=()=>{};schedulePersist=()=>{};');
function language(value){sandbox.window.AIVideoNoteI18n.language=value;sandbox.document.documentElement.lang=value;for(const item of listeners)if(item.name==='ui-language-changed')item.fn({detail:{}});}
(async()=>{
    await run('runStartupUpdateCheck()');
    assert.match(requests.at(-1),/language=zh-CN$/);
    assert.match(nodes.get('updateBody').textContent,/更新来源：Gitee/);
    assert.equal(nodes.get('updateGithubLink').hidden,false);
    const html=fs.readFileSync(path.join(frontend,'index.html'),'utf8');
    for (const brand of ['Github']) {
        const anchor = html.match(new RegExp('<a id="update'+brand+'Link"[^>]*>([\\s\\S]*?)</a>'));
        assert.ok(anchor);
        assert.match(anchor[0], /class="update-site-button"/);
        assert.match(anchor[0], /target="_blank"/);
        assert.match(anchor[1], /<svg[^>]*aria-hidden="true"/);
        assert.ok(anchor[1].includes('<span>GitHub</span>'));
    }
    assert.ok(!html.includes('copyUpdateGithubBtn') && !html.includes('copyUpdateGiteeBtn'));
    assert.ok(!html.includes('id="updateAcceptBtn"'));
    assert.ok(!html.includes('updateGiteeLink') && !html.includes('updateGiteeRow'));
    assert.ok(!html.includes('gitee.com'));

    assert.match(html,/id="updateGithubLink" href="https:\/\/github.com\/hehe1885\/AI-Video-Note\/releases\/latest"/);
    sandbox.window.AIVideoNoteI18n.language='en';
    sandbox.response={...sandbox.response,source:'github',source_name:'GitHub',release_url:'https://github.com/hehe1885/AI-Video-Note/releases/latest'};
    await run('runStartupUpdateCheck()');
    assert.match(requests.at(-1),/language=en$/);
    assert.match(nodes.get('updateBody').textContent,/Update source: GitHub/);
    assert.equal(nodes.get('updateGithubLink').hidden,false);
    sandbox.window.AIVideoNoteI18n.language='zh-CN';
    run('renderUpdateReleaseLinks()');
    assert.equal(nodes.get('updateGithubLink').hidden,false);
    sandbox.window.AIVideoNoteI18n.language='en';
    run('renderUpdateReleaseLinks()');
    assert.equal(nodes.get('updateGithubLink').hidden,false);
    assert.ok(modal.open);
    nodes.get('updateGithubLink').focus();
    let prevented=false;
    modal.events.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented=true;}});
    assert.equal(prevented,true);
    assert.equal(sandbox.document.activeElement,nodes.get('updateCancelBtn'));
    modal.events.keydown({key:'Tab',shiftKey:false,preventDefault(){}});
    assert.equal(sandbox.document.activeElement,nodes.get('updateGithubLink'));
    language('zh-CN');
    assert.match(nodes.get('updateBody').textContent,/发现新版本/);
    assert.equal(nodes.get('updateTitle').textContent,'发现新版本');
    nodes.get('updateGithubLink').focus();
    language('en');
    assert.match(nodes.get('updateBody').textContent,/New version/);
    assert.equal(nodes.get('updateTitle').textContent,'New version available');
    assert.equal(sandbox.document.activeElement,nodes.get('updateGithubLink'));
    run('dismissUpdateOffer()');
    assert.equal(modal.open,false);
    assert.equal(sandbox.document.activeElement,source);
    let resolve;
    sandbox.delayed=new Promise(r=>resolve=r);
    run('fetchWithTimeout=async()=>await delayed');
    language('zh-CN');
    const pending=run('runStartupUpdateCheck()');
    language('en');
    resolve({...sandbox.response,notes:'原始发布说明，不应被翻译'});
    await pending;
    assert.match(nodes.get('updateBody').textContent,/New version/);
    assert.ok(nodes.get('updateBody').textContent.includes('原始发布说明，不应被翻译'));
    assert.match(html,/<dialog id="updateModal"[^>]*aria-describedby="updateBody"/);
    assert.match(html,/id="updateBody"[^>]*data-no-i18n/);
    run('ignoreUpdateOffer()');
    assert.equal(modal.open,false);
    assert.equal(run('prefs.update.ignored_version'),sandbox.response.latest_version);
    sandbox.response={ok:false};
    run('fetchWithTimeout=async()=>response');
    nodes.get('updateModal').hidden=true;
    await run('runStartupUpdateCheck()');
    assert.equal(nodes.get('updateModal').hidden,true);
    console.log('GitHub-only release button, language changes, focus, late responses and unavailable-source checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});

