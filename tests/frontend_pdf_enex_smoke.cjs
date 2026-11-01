const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const front=path.join(__dirname,'../frontend');
const html=fs.readFileSync(path.join(front,'index.html'),'utf8');
for(const format of ['pdf','enex','html'])assert.ok(html.includes(`option value="${format}"`));
const nodes={formatSelect:{value:''}};
const events=[];
const sandbox={console,Blob,URL,API_BASE:"/api",window:{},document:{addEventListener(){},getElementById:id=>nodes[id]}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(front,'note-view.js'),'utf8'),sandbox);
vm.runInContext(`var isDownloading=false,currentMarkdown='# 笔记',currentTaskId='task/123',currentDownloadName='笔记',taskViewSequence=0,API_BASE='/api';
    function byId(id){return document.getElementById(id)}
    function setDownloading(value){isDownloading=value}
    function showToast(){}
    function serverFilename(response){return response.filename||''}
    function noteFileName(extension){return '笔记'+extension}
    function validationError(message){throw Error(message)}
    async function extractErrorMessage(){return '导出失败'}`,sandbox);
sandbox.triggerBlobDownload=(blob,extension,filename)=>events.push({extension,filename,size:blob.size});
const run=code=>vm.runInContext(code,sandbox);
(async()=>{
    for(const format of ['pdf','enex','html']){
        let url;
        sandbox.fetch=async address=>{url=address;return {ok:true,filename:'服务端笔记.'+format,blob:async()=>new Blob(['payload'])}};
        nodes.formatSelect.value=format;
        await run('downloadSummary()');
        assert.equal(url,'/api/download/task%2F123?format='+format);
        assert.deepEqual(events.at(-1),{extension:'.'+format,filename:'服务端笔记.'+format,size:7});
        assert.equal(run('isDownloading'),false);
        sandbox.fetch=async()=>({ok:true,blob:async()=>new Blob(['payload'])});
        await run('downloadSummary()');
        assert.equal(events.at(-1).filename,'笔记.'+format,'Fallback extension follows selected format');
        const count=events.length;
        sandbox.fetch=async()=>({ok:false});await run('downloadSummary()');
        assert.equal(events.length,count,'Failed responses must not download broken files');
        assert.equal(run('isDownloading'),false);
    }
    console.log('PDF/ENEX server downloads, filenames, fallback extensions and failure recovery passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
