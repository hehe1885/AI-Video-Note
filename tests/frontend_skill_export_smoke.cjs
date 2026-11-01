const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const front = path.join(__dirname, '../frontend');
const html = fs.readFileSync(path.join(front, 'index.html'), 'utf8');
const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {disabled:false,textContent:''}]));
let next = {cancelled:false,path:'D:/Export/AI-Video-Note-Skill.zip'}, failure = false;
const sandbox = {console,window:{},byId:id=>nodes.get(id),API_BASE:'/api',readResponse:async r=>r.json(),fetch:async(url, options)=>{
    assert.equal(url,'/api/skill/export');assert.equal(options.method,'POST');
    assert.equal(nodes.get('exportSkillBtn').disabled,true);
    if (failure) throw new Error('offline');
    return {json:async()=>next};
}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(front,'settings.js'),'utf8'),sandbox);
(async()=>{
    await vm.runInContext('exportAgentSkill()',sandbox);
    assert.ok(nodes.get('skillExportStatus').textContent.includes(next.path));
    assert.equal(nodes.get('exportSkillBtn').disabled,false);
    next={cancelled:true,path:''};await vm.runInContext('exportAgentSkill()',sandbox);
    assert.equal(nodes.get('skillExportStatus').textContent,'已取消导出');
    failure=true;await vm.runInContext('exportAgentSkill()',sandbox);
    assert.equal(nodes.get('skillExportStatus').textContent,'offline');
    assert.equal(nodes.get('exportSkillBtn').disabled,false);
    assert.ok(html.includes('不要只复制 SKILL.md'));
    console.log('Skill export UI selection, cancellation and failure checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
