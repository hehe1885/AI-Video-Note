const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const dir=path.join(__dirname,'../frontend');
const code=fs.readFileSync(path.join(dir,'note-view.js'),'utf8');
const sandbox={API_BASE:'/api'};vm.createContext(sandbox);vm.runInContext(code,sandbox);
const cases=[
    {text:'普通正文需要缩进。',expected:true},
    {text:'加粗开头的正文。',expected:true},
    {text:'视频链接：https://example.com',expected:false},
    {text:'Source: Watch',links:['Watch'],outside:'Source: ',expected:false},
    {text:'https://example.com',expected:false},
    {text:'观看视频',links:['观看视频'],outside:'',expected:false},
    {text:'正文带参考链接仍然缩进。',links:['参考链接'],outside:'正文带仍然缩进。',expected:true},
    {text:'图注：独立的图片备注。',expected:false},
    {text:'Caption: Figure text.',expected:false},
    {text:'时长：04:17',expected:false},
    {text:'列表段落',container:true,expected:false},
    {text:'表格段落',container:true,expected:false},
    {text:'',image:true,expected:false},
    {text:'公式独立段落',image:true,expected:false},
];
const nodes=cases.map(test=>({textContent:test.text,
    querySelectorAll:()=>test.links||[],
    querySelector:()=>test.image?{}:null,
    closest:()=>test.container?{}:null,
    cloneNode:()=>({textContent:test.outside,querySelectorAll:()=>[]}),
    classList:{toggle(name,on){if(name==='note-body-paragraph')test.actual=on;else assert.equal(name,'note-image-caption');}}
}));
sandbox.root={querySelectorAll:()=>nodes};
vm.runInContext('applyImageExportParagraphIndent(root)',sandbox);
for(const test of cases)assert.equal(test.actual,test.expected,test.text);
assert.match(code,/sheet\.innerHTML = renderMarkdown\([\s\S]*?applyImageExportParagraphIndent\(sheet\)/);
const css=fs.readFileSync(path.join(dir,'style.css'),'utf8');
assert.match(css,/\.image-export-sheet p\.note-body-paragraph\s*\{\s*text-indent:\s*2em/);
console.log('Browser PNG export paragraph indentation and heading/link/caption/container exclusions passed');
