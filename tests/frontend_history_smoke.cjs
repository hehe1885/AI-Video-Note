const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
    constructor(tag='div') {
        this.tag=tag; this.children=[]; this.dataset={}; this.checked=false; this.hidden=false; this.disabled=false; this.textContent=''; this.className='';
        const props = new Map();
        this.style = {
            setProperty: (k, v) => props.set(k, String(v)),
            getPropertyValue: k => props.get(k) || '',
            removeProperty: k => props.delete(k)
        };
    }
    getBoundingClientRect() { return { height: this.className.includes('recent-task-row') ? 68.25 : 0 }; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children=items; }
    setAttribute() {}
}
const frontend=path.join(__dirname,'..','frontend');
const page=fs.readFileSync(path.join(frontend,'index.html'),'utf8');
const nodes=new Map([...page.matchAll(/id="([^"]+)"/g)].map(([,id])=>[id,new Element()]));
const descendants = node => node.children.flatMap(child => child instanceof Element ? [child,...descendants(child)] : []);
const confirmations = [];
const sandbox={console,URL,document:{addEventListener(){},getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),
    querySelectorAll:()=>descendants(nodes.get('recentTaskList')).filter(node=>node.dataset.selectTaskId)},window:{addEventListener(){},confirm:message=>{confirmations.push(message);return true;}}};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js'].map(name => fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
run(`armRecentTasksElapsedTicker=()=>{}; recentTaskMetaText=()=>''; showToast=(message)=>{globalThis.lastToast=message;};
     loadRecentTasks=async()=>{}; resetTaskView=()=>{globalThis.didReset=true;};`);
run(`renderRecentTasks([{task_id:'done',title:'Finished',status:'completed'}, {task_id:'failed',title:'Failed',status:'failed'}, {task_id:'active',title:'Active',status:'processing'}])`);
const rows=nodes.get('recentTaskList').children;
assert.equal(rows[0].children[0].disabled,false);
assert.equal(rows[0].children[1].className,'recent-task-item');
assert.ok(descendants(rows[0].children[1]).some(node=>node.textContent==='已完成'));
assert.equal(rows[2].children[0].disabled,true);
assert.equal(rows[2].children[1].className,'recent-task-item');
assert.equal(rows[0].children.at(-1).dataset.deleteTaskId,'done');
assert.equal(rows[2].children.some(node=>node.dataset.deleteTaskId),false);
assert.equal(rows[2].children.at(-1).dataset.cancelTaskId,'active');
run(`selectedRecentTaskIds.add('done'); selectedRecentTaskIds.add('failed'); syncRecentSelection();`);
assert.equal(nodes.get('deleteSelectedTasksBtn').textContent,'删除所选（2）');
assert.equal(nodes.get('selectAllRecentTasks').checked,true);
run(`currentTaskId='done';`);
sandbox.fetch=async(url, options)=>{
    assert.equal(url.endsWith('/tasks/delete'),true);
    assert.deepEqual(JSON.parse(options.body).task_ids,['done','failed']);
    return {ok:true,json:async()=>({deleted:['done'],failed:[{task_id:'failed',error:'File locked'}]})};
};
(async()=>{
    await run(`deleteRecentTaskIds([...selectedRecentTaskIds])`);
    assert.equal(confirmations.length,1);
    assert.ok(confirmations[0].includes('关联的下载视频'));
    assert.ok(confirmations[0].includes('共享视频也会删除'));
    assert.equal(run(`selectedRecentTaskIds.has('done')`),false);
    assert.equal(run(`selectedRecentTaskIds.has('failed')`),true);
    assert.equal(nodes.get('resultArea').hidden,true);
    assert.ok(sandbox.lastToast.includes('File locked'));
    assert.equal(run('historyDeletionBusy'),false);
    // History list keeps a 3-row viewport height (none for <=3 rows, exact 3-row sum for >3 rows).
    assert.equal(nodes.get('recentTaskList').style.getPropertyValue('--recent-task-list-max-height'), 'none');
    run(`renderRecentTasks([
        {task_id:'t1',title:'One',status:'completed'},
        {task_id:'t2',title:'Two',status:'completed'},
        {task_id:'t3',title:'Three',status:'completed'},
        {task_id:'t4',title:'Four',status:'completed'}
    ])`);
    assert.equal(nodes.get('recentTaskList').style.getPropertyValue('--recent-task-list-max-height'), '206px');
    const styleCss = fs.readFileSync(path.join(frontend, 'style.css'), 'utf8');
    assert.match(styleCss, /\.recent-task-list\s*\{[^}]*max-height:\s*var\(--recent-task-list-max-height,\s*calc\(/);
    console.log('Completed-task selection, batch deletion and partial failure checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
