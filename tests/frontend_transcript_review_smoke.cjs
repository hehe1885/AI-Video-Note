const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const frontend=path.join(__dirname,'..','frontend');
const ready=[]; let activeElement=null;
class Element {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.events={};this.hidden=false;this.disabled=false;this.dataset={};this.attributes={};this.value='';this.textContent='';this.paused=true;this.currentTime=0;this.readyState=0;this.duration=NaN;this.style={setProperty(){}};}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=[...items];}
 setAttribute(name,value){this.attributes[name]=value;}
 removeAttribute(name){delete this.attributes[name];}
 addEventListener(name,callback){this.events[name]=callback;}
 focus(){activeElement=this;}
 reportValidity(){return Boolean(this.value.trim());}
 pause(){if(!this.paused){this.paused=true;this.events.pause?.();}}
 play(){this.paused=false;this.readyState=4;this.events.play?.();return Promise.resolve();}
 load(){}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 querySelectorAll(selector){const all=[]; const walk=node=>{for(const child of node.children){if(child.tagName==='textarea' || (selector.includes('button')&&child.tagName==='button'&&!child.disabled))all.push(child);walk(child);}};walk(this);return all;}
}
const nodes=new Map([...fs.readFileSync(path.join(frontend,'index.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(([,id])=>[id,new Element()]));
nodes.get('transcriptReviewModal').hidden=true;
const calls=[],scheduled=[];
const sandbox={console,URL,Blob,setTimeout,clearTimeout,document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),addEventListener:(name,fn)=>{if(name==='DOMContentLoaded')ready.push(fn);},get activeElement(){return activeElement;}},window:{addEventListener(){},setTimeout,clearTimeout},record:(url,options)=>calls.push([url,options]),recordSchedule:id=>scheduled.push(id)};
vm.createContext(sandbox);
vm.runInContext(['api.js','tasks.js','settings.js','gpu-runtime.js','script.js','note-view.js','transcript-review.js'].map(name=>fs.readFileSync(path.join(frontend,name),'utf8')).join('\n'),sandbox);
const run=code=>vm.runInContext(code,sandbox);
ready.at(-1)();
run('currentTaskId="review";setTaskActive=()=>{};setTaskState=()=>{};schedulePoll=id=>recordSchedule(id);');
sandbox.response={status:'pending',revision:'r1',manual_check:false,items:[{id:'seg-0',segment_index:0,start:0,end:10,original:'数值为 0.01 <script>',reason:'请核对数值',before:'context',after:''}],next_offset:null};
run('fetchWithTimeout=async(url,options)=>{record(url,options);return response};readResponse=async value=>value;');
const task={status:'processing',transcript_review:{status:'pending',revision:'r1'}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const descendants=root=>root.children.flatMap(child=>[child,...descendants(child)]);
(async()=>{
 assert.equal(sandbox.window.syncTranscriptReview('review',task),true);
 await tick();
 assert.equal(nodes.get('transcriptReviewModal').hidden,false);
 let editor=nodes.get('transcriptReviewItems').querySelector('textarea');
 assert.equal(editor.value,'数值为 0.01 <script>');
 editor.value='数值为 0.001';
 sandbox.window.syncTranscriptReview('review',task); await tick();
 assert.equal(nodes.get('transcriptReviewItems').querySelector('textarea'),editor);
 assert.equal(editor.value,'数值为 0.001');
 assert.equal(descendants(nodes.get('transcriptReviewItems')).some(el=>el.textContent==='前文'||el.textContent==='后文'),false);
 let player=descendants(nodes.get('transcriptReviewItems')).find(el=>el.tagName==='audio');
 assert.equal(player.controls,false);
 assert.equal(player.hidden,true);
 assert.equal(player.preload,'none');
 assert.match(player.src,/transcript-review\/seg-0\/audio\?revision=r1$/);
 assert.equal(descendants(nodes.get('transcriptReviewItems')).some(el=>el.textContent==='播放音频'||el.textContent==='暂停音频'),false);
 let toggle=descendants(nodes.get('transcriptReviewItems')).find(el=>el.className==='transcript-audio-toggle');
 let seek=descendants(nodes.get('transcriptReviewItems')).find(el=>el.className==='transcript-audio-seek');
 let mute=descendants(nodes.get('transcriptReviewItems')).find(el=>el.className==='transcript-audio-mute');
 seek.value='3'; seek.events.input();
 player.duration=10; player.readyState=1; player.events.loadedmetadata();
 assert.equal(player.currentTime,3,'Seeking before playback applies after metadata is ready');
 toggle.events.click(); await tick();
 assert.equal(player.paused,false);
 assert.equal(toggle.attributes['aria-label'],'暂停音频');
 seek.value='6'; seek.events.input(); assert.equal(player.currentTime,6);
 mute.events.click(); assert.equal(player.muted,true); assert.equal(mute.attributes['aria-label'],'取消静音');
 const audioCalls=calls.length;
 toggle.events.click(); assert.equal(player.paused,true);
 toggle.events.click(); await tick();
 assert.equal(calls.length,audioCalls,'Native playback uses the media URL without a preparation request');
 nodes.get('transcriptReviewLaterBtn').events.click();
 assert.equal(player.paused,true,'Deferring review stops playback');
 assert.equal(nodes.get('transcriptReviewModal').hidden,true);
 sandbox.window.syncTranscriptReview('review',task);
 assert.equal(nodes.get('transcriptReviewModal').hidden,true);
 assert.equal(nodes.get('transcriptReviewNotice').hidden,false);
 nodes.get('openTranscriptReviewBtn').events.click();
 assert.equal(editor.value,'数值为 0.001');
 run('fetchWithTimeout=async()=>{throw Error("failed save")};');
 nodes.get('transcriptReviewApplyBtn').events.click(); await tick();
 assert.equal(nodes.get('transcriptReviewModal').hidden,false);
 assert.equal(nodes.get('transcriptReviewApplyBtn').disabled,false);
 assert.equal(editor.value,'数值为 0.001');
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return {confirmed:true}};');
 nodes.get('transcriptReviewApplyBtn').events.click(); await tick();
 const saved=JSON.parse(calls.at(-1)[1].body);
 assert.deepEqual(saved,{revision:'r1',keep_original:false,decisions:[{id:'seg-0',text:'数值为 0.001'}]});
 assert.equal(nodes.get('transcriptReviewModal').hidden,true);
 assert.deepEqual(scheduled,['review']);
 // An old asynchronous response must not reopen a modal after switching tasks.
 let finish;
 sandbox.pendingResponse=()=>new Promise(resolve=>finish=resolve);
 run('fetchWithTimeout=()=>pendingResponse();');
 sandbox.window.syncTranscriptReview('review',{status:'processing',transcript_review:{status:'pending',revision:'r2'}});
 sandbox.window.resetTranscriptReview(); run('currentTaskId="new-task"');
 finish({...sandbox.response,revision:'r2'}); await tick();
 assert.equal(nodes.get('transcriptReviewModal').hidden,true);
 // Native playback must remain stopped after closing or switching the popup.
 run('currentTaskId="review"');
 sandbox.window.syncTranscriptReview('review',{status:'processing',transcript_review:{status:'pending',revision:'r3'}});
 finish({...sandbox.response,revision:'r3',items:[...sandbox.response.items,{id:'seg-1',segment_index:1,start:10,end:20,original:'second'}]}); await tick();
 player=descendants(nodes.get('transcriptReviewItems')).find(el=>el.tagName==='audio');
 const secondPlayer=descendants(nodes.get('transcriptReviewItems')).filter(el=>el.tagName==='audio')[1];
 await player.play(); await secondPlayer.play();
 assert.equal(player.paused,true,'Only one excerpt plays at a time');
 assert.equal(secondPlayer.paused,false);
 nodes.get('transcriptReviewLaterBtn').events.click();
 assert.equal(secondPlayer.paused,true);
 await player.play();
 assert.equal(player.paused,true);
 nodes.get('openTranscriptReviewBtn').events.click();
 player.events.error();
 const retry=descendants(nodes.get('transcriptReviewItems')).find(el=>el.className==='button ghost transcript-review-retry');
 assert.equal(retry.hidden,false,'Playback failure exposes a retry');
 retry.events.click(); await tick();
 assert.equal(player.paused,false);
 assert.equal(retry.hidden,true);
 // Confidence is actionable, but only explicit confirmation commits changes.
 sandbox.window.resetTranscriptReview();
 sandbox.response={status:'pending',revision:'proofread',manual_check:false,items:[
  {id:'seg-0',segment_index:0,start:0,end:10,original:'梯度下将',suggestion:'梯度下降',confidence:.96,reason:'术语'},
  {id:'seg-1',segment_index:1,start:10,end:20,original:'张三',suggestion:'章三',confidence:.45,reason:'需试听'}],next_offset:null};
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return response};');
 sandbox.window.syncTranscriptReview('review',{status:'processing',transcript_review:{status:'pending',revision:'proofread'}}); await tick();
 let editors=nodes.get('transcriptReviewItems').querySelectorAll('textarea');
 assert.equal(editors[0].value,'梯度下降','High confidence preselects suggestion');
 assert.equal(editors[1].value,'张三','Low confidence keeps original');
 let rows=nodes.get('transcriptReviewItems').children;
 const buttons=row=>descendants(row).filter(el=>el.tagName==='button');
 buttons(rows[0]).find(el=>el.textContent==='保留原文').events.click();
 assert.equal(editors[0].value,'梯度下将');
 buttons(rows[1]).find(el=>el.textContent==='采用AI建议').events.click();
 assert.equal(editors[1].value,'章三');
 nodes.get('transcriptReviewHighBtn').events.click();
 assert.equal(editors[0].value,'梯度下降');
 assert.equal(editors[1].value,'章三');
 assert.equal(rows[0].hidden,true,'Bulk acceptance removes high-confidence cards from the pending list');
 assert.equal(rows[1].hidden,false,'Uncertain cards stay visible');
 assert.match(nodes.get('transcriptReviewStatus').textContent,/1/);
 nodes.get('transcriptReviewResolvedBtn').events.click();
 assert.equal(rows[0].hidden,false,'Accepted suggestions can be expanded and undone');
 nodes.get('transcriptReviewResolvedBtn').events.click();
 assert.equal(rows[0].hidden,true);
 editors[1].value='人工核实的人名';
 sandbox.response={...sandbox.response,revision:'proofread2'};
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return options?.method==="POST"?{queued:false,revision:"proofread2"}:response};');
 nodes.get('transcriptReviewAiBtn').events.click(); await tick(); await tick();
 editors=nodes.get('transcriptReviewItems').querySelectorAll('textarea');
 assert.equal(editors[1].value,'人工核实的人名','Retry preserves a custom manual correction');
 assert.equal(editors[1].readOnly,false);
 assert.equal(calls.filter(([url,opts])=>url.endsWith('/transcript-review')&&opts?.method==='POST').length,1,'AI retry must not confirm the transcript');
 // Manual Agent retry completes later via polling; custom drafts survive that revision too.
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return options?.method==="POST"?{queued:true,revision:"proofread2"}:response};');
 nodes.get('transcriptReviewAiBtn').events.click(); await tick();
 sandbox.response={...sandbox.response,revision:'proofread3'};
 sandbox.window.syncTranscriptReview('review',{status:'processing',transcript_review:{status:'pending',revision:'proofread3'}}); await tick();
 assert.equal(nodes.get('transcriptReviewItems').querySelectorAll('textarea')[1].value,'人工核实的人名');
 // Listening updates suggestions, never a manual edit or an already accepted decision.
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return {revision:response.revision,item:{...response.items[1],suggestion:"音频建议",confidence:.82,audio_review:{method:"local_asr_then_text_model",text:"独立音频识别"}}}};');
 nodes.get('transcriptReviewAudioBtn').events.click(); await tick(); await tick();
 const audioRequest=calls.at(-1);
 assert.match(audioRequest[0],/seg-1\/audio-review$/,'Only unresolved low-confidence items are checked');
 assert.equal(JSON.parse(audioRequest[1].body).revision,'proofread3');
 editors=nodes.get('transcriptReviewItems').querySelectorAll('textarea');
 assert.equal(editors[1].value,'人工核实的人名','Audio review preserves manual corrections');
 assert.equal(nodes.get('transcriptReviewItems').children[0].hidden,true,'Audio review preserves accepted state');
 assert.ok(descendants(nodes.get('transcriptReviewItems')).some(el=>el.textContent==='独立音频识别'));
 assert.equal(nodes.get('transcriptReviewAudioBtn').disabled,false);
 const audioCount=calls.length;
 nodes.get('transcriptReviewAudioBtn').events.click(); await tick();
 assert.equal(calls.length,audioCount,'Bulk audio check skips previously checked excerpts');
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return {confirmed:true}};');
 nodes.get('transcriptReviewApplyBtn').events.click(); await tick();
 assert.deepEqual(JSON.parse(calls.at(-1)[1].body).decisions,[
  {id:'seg-0',text:'梯度下降'}, {id:'seg-1',text:'人工核实的人名'}
 ],'Hidden accepted suggestions are included in the final commit');
 // A custom edit must never be overwritten or hidden by a bulk acceptance.
 sandbox.window.syncTranscriptReview('review',{status:'processing',transcript_review:{status:'pending',revision:'proofread3'}}); await tick();
 // Restore GET behavior before the next independent review load.
 sandbox.window.resetTranscriptReview();
 sandbox.response={...sandbox.response,revision:'manual-edit'};
 run('fetchWithTimeout=async(url,options)=>{record(url,options);return response};');
 sandbox.window.syncTranscriptReview('review',{status:'processing',transcript_review:{status:'pending',revision:'manual-edit'}}); await tick();
 editors=nodes.get('transcriptReviewItems').querySelectorAll('textarea');
 editors[0].value='用户手动修正';
 nodes.get('transcriptReviewHighBtn').events.click();
 assert.equal(editors[0].value,'用户手动修正');
 assert.equal(nodes.get('transcriptReviewItems').children[0].hidden,false);
 console.log('Accepted cards collapse, can be restored, survive audio review and commit; custom edits stay intact');
 console.log('High/low confidence defaults, individual accept/restore, high-only bulk action and AI retry draft protection passed');
 console.log('Transcript popup, editable text, polling preservation, deferred review, save failures and stale task responses passed');
 console.log('Styled audio controls, accessible playback, seeking, mute, one excerpt at a time, dismissal guard and retry passed');
})().catch(error=>{console.error(error);process.exitCode=1});
