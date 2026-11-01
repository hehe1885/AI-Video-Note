import asyncio,json,hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from backend import screenshot_crop as crop,agent_queue,agent_auto,main
from backend.ai_screenshots import ScreenshotPoint
from tests.test_agent_auto import job

@pytest.mark.parametrize('box',[None,[],[0,0,1],[-.1,0,1,1],[0,0,1.1,1],[.8,0,.2,1],
    [True,0,1,1],[0,0,float('nan'),1],[0,0,float('inf'),1],[0,0,1,1],
    ])
def test_uncertain_or_unsafe_geometry_keeps_full_frame(box):
    assert crop.padded_box(box,(1000,600)) is None

def test_padding_is_outward_and_keeps_useful_large_region():
    assert crop.padded_box([.2,.1,.8,.9],(1000,600))==(160,36,841,564)
    assert crop.padded_box([.1,.1,.8,.8],(320,180)) is not None


@pytest.mark.parametrize('box', [[.45,.1,.55,.9], [.2,.45,.8,.55], [.2,.2,.7,.7],
    [.372,0,.628,1], [.03,.33,.97,.62], [.383,.086,.617,.736], [.37,0,.63,.62]])
def test_content_can_justify_narrow_or_small_regions_without_size_limits(box):
    assert crop.padded_box(box,(1280,720)) is not None

def source(tmp_path):
    image=tmp_path/'frame.jpg';Image.new('RGB',(1000,600),'steelblue').save(image)
    return image,ScreenshotPoint(10,'设置面板','原文设置面板')

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['null','ambiguous','guard','reject','exception','badjson','notbool'])
async def test_every_uncertain_step_falls_back_without_modifying_original(tmp_path,failure):
    image,point=source(tmp_path);before=image.read_bytes();calls=[]
    async def review(prompt,paths,schema):
        calls.append(paths)
        if failure=='exception':raise TimeoutError()
        if failure=='badjson':return 'invalid'
        if len(calls)==1:
            return json.dumps({'box':None if failure=='null' else [0,0,1,1] if failure=='guard' else [.2,.1,.8,.9],
                'context_complete':False if failure=='ambiguous' else 'true' if failure=='notbool' else True,'clearly_better':True})
        return json.dumps({'approved':False,'context_complete':True,'clearly_better':True})
    audit=[];points,frames=await crop.crop_screenshots(SimpleNamespace(),[point],{10:image},review=review,audit=audit)
    assert frames=={10:image} and image.read_bytes()==before
    assert not list(tmp_path.glob('*-crop-*.jpg')) and audit[0]['accepted'] is False
    assert len(calls)==(2 if failure=='reject' else 1)

@pytest.mark.asyncio
async def test_accepted_crop_preserves_original_and_compares_both_images(tmp_path):
    image,point=source(tmp_path);before=image.read_bytes();calls=[]
    async def review(prompt,paths,schema):
        calls.append(paths)
        if len(paths)==1:return json.dumps({'box':[.2,.1,.8,.9],'context_complete':True,'clearly_better':True})
        assert paths[0]==image and paths[1]!=image and paths[1].is_file()
        return json.dumps({'approved':True,'context_complete':True,'clearly_better':True})
    audit=[];points,frames=await crop.crop_screenshots(SimpleNamespace(),[point],{10:image},review=review,audit=audit)
    assert points==[point] and frames[10]!=image and len(calls)==2
    assert (tmp_path/'originals/frame.jpg').read_bytes()==before==image.read_bytes()
    assert audit[0]['accepted'] is True
    with Image.open(frames[10]) as im:assert im.size==(681,528)

@pytest.mark.asyncio
async def test_cancellation_propagates_and_removes_unapproved_preview(tmp_path):
    image,point=source(tmp_path);calls=[]
    async def review(prompt,paths,schema):
        calls.append(paths)
        if len(calls)==1:return json.dumps({'box':[.2,.1,.8,.9],'context_complete':True,'clearly_better':True})
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await crop.crop_screenshots(SimpleNamespace(),[point],{10:image},review=review)
    assert image.is_file() and not list(tmp_path.glob('*-crop-*.jpg'))

def prepare_manual(job):
    task_id,directory=job;folder=directory/'frames';folder.mkdir(exist_ok=True)
    image=folder/'full.jpg';Image.new('RGB',(1000,600),'navy').save(image)
    data=agent_queue.read_job(task_id);data['settings'].update(vision_screenshots=True,auto_crop_screenshots=True,screenshot_density='medium')
    data['screenshots']=[{'time':3,'filename':image.name,'markdown_ref':'frames/full.jpg'}]
    agent_queue.write_job(task_id,data)
    claim=asyncio.run(agent_queue.claim_job(task_id,agent_queue.Claim()))
    return task_id,directory,claim['claim_token']

@pytest.mark.parametrize('confirm',[False,True])
@pytest.mark.parametrize('style',['ordinary','title','reference'])
def test_manual_preview_must_be_confirmed_before_export(job,confirm,style):
    task_id,directory,token=prepare_manual(job)
    prepared=asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='prepare',filename='full.jpg',box=[.2,.1,.8,.9])))
    preview=prepared['preview'];ref=preview['markdown_ref']
    if confirm:
        result=asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='confirm',filename=preview['filename'],approved=True,context_complete=True,clearly_better=True)))
        assert result['accepted']
        ref='frames/full.jpg' # Server also swaps an original reference to the confirmed crop.
    image='![image]('+ref+')' if style=='ordinary' else '![image](<'+ref+'> "title")' if style=='title' else '![image][plot]\n\n[plot]: '+ref
    asyncio.run(agent_queue.finish(task_id,agent_queue.Result(claim_token=token,markdown='# Note\n\n'+image)))
    note=(directory/'notes.md').read_text(encoding='utf-8')
    assert (preview['markdown_ref'] in note)==confirm
    assert main.tasks[task_id]['result']['screenshot_count']==1
    if confirm:assert (directory/'frames/originals/full.jpg').is_file()

@pytest.mark.parametrize('override',[{'auto_crop_screenshots':False},{'vision_screenshots':False},{'timed_screenshots':True},{'screenshot_density':'none'},{'output':'transcript'}])
def test_manual_crop_cannot_bypass_opt_out_or_mode(job,override):
    task_id,directory,token=prepare_manual(job);data=agent_queue.read_job(task_id);data['settings'].update(override);agent_queue.write_job(task_id,data)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='prepare',filename='full.jpg',box=[.2,.1,.8,.9])))
    assert exc.value.status_code==409

def test_manual_crop_claim_and_source_membership_are_checked(job):
    task_id,directory,token=prepare_manual(job)
    from fastapi import HTTPException
    for filename,claim in [('full.jpg','wrong'),('../full.jpg',token),('other.jpg',token)]:
        with pytest.raises(HTTPException):asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=claim,action='prepare',filename=filename,box=[.2,.1,.8,.9])))

def test_crop_option_is_explicitly_opt_in():
    assert main.SummarizeRequest(video_url='https://example.org',llm_config={}).auto_crop_screenshots is False

def test_auto_agent_crop_is_sent_to_notes_and_original_survives(job,monkeypatch):
    task_id,directory=job;data=agent_queue.read_job(task_id);data['settings'].update(vision_screenshots=True,auto_crop_screenshots=True)
    agent_queue.write_job(task_id,data);stages=[]
    async def extract(video,task_id,times,cancel):
        folder=directory/'frames';folder.mkdir(exist_ok=True);frames={}
        for i,t in enumerate(times):
            path=folder/f'f{i}.jpg';Image.new('RGB',(1000,600),(20+i,40,60)).save(path);frames[t]=path
        return frames
    async def cli(task_id,prompt,schema=None,images=()):
        props=schema.get('properties',{}) if schema else {}
        if 'times' in props:return '{"times":[3]}'
        if 'choices' in props:return '{"choices":[{"id":0,"candidate":2,"caption":"settings","reason":"selected"}]}'
        if 'box' in props:stages.append('propose');return '{"box":[0.2,0.1,0.8,0.9],"context_complete":true,"clearly_better":true,"reason":"background"}'
        if 'approved' in props:stages.append('compare');assert len(images)==2;return '{"approved":true,"context_complete":true,"clearly_better":true,"reason":"complete"}'
        shots=agent_queue.read_job(task_id)['screenshots'];assert '-crop-' in shots[0]['filename'];return '# Note\n\n![image]('+shots[0]['markdown_ref']+')'
    monkeypatch.setattr(main.video_processor,'extract_frames_at',extract);monkeypatch.setattr(agent_auto,'run_cli',cli)
    asyncio.run(agent_auto.work(task_id))
    assert main.tasks[task_id]['status']=='completed' and stages==['propose','compare']
    assert len(list((directory/'frames/originals').glob('*.jpg')))==1

def test_manual_changed_preview_and_rejected_proposal_cannot_be_confirmed(job):
    task_id,directory,token=prepare_manual(job)
    prepared=asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='prepare',filename='full.jpg',box=[.2,.1,.8,.9])))
    preview=prepared['preview'];(directory/'frames'/preview['filename']).write_bytes(b'changed')
    result=asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='confirm',filename=preview['filename'],approved=True,context_complete=True,clearly_better=True)))
    assert result['accepted'] is False and result['screenshot']['filename']=='full.jpg'
    from fastapi import HTTPException
    with pytest.raises(HTTPException):asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='confirm',filename=preview['filename'],approved=True,context_complete=True,clearly_better=True)))
    with pytest.raises(HTTPException):asyncio.run(agent_queue.crop_job(task_id,agent_queue.CropRequest(claim_token=token,action='prepare',filename='full.jpg',box=[.1,.1,.9,.9])))

@pytest.mark.asyncio
@pytest.mark.parametrize('visual,enabled,expected_calls',[(False,True,0),(True,False,1),(True,True,3)])
async def test_api_pipeline_respects_opt_in_and_uses_cropped_reference(tmp_path,monkeypatch,visual,enabled,expected_calls):
    from tests.test_preview_reuse import cached_video,URL
    from backend.video_processor import VideoProcessor
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path);monkeypatch.setattr(main,'tasks',{})
    cached_video(tmp_path);processor=VideoProcessor(tmp_path)
    async def info(*args,**kwargs):return {'title':'Crop fixture','source':'youtube','duration':2,'subtitles':{'en':[{'ext':'vtt','data':'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nExample\n'}]}}
    async def extract(video,task_id,times,should_abort):
        folder=tmp_path/task_id/'frames';folder.mkdir(exist_ok=True);frames={}
        for i,t in enumerate(times):
            path=folder/f'f{i}.jpg';Image.new('RGB',(1000,600),(20+i,40,60)).save(path);frames[t]=path
        return frames
    monkeypatch.setattr(processor,'get_video_info',info);monkeypatch.setattr(processor,'extract_frames_at',extract)
    monkeypatch.setattr(main,'video_processor',processor);calls=[]
    class Model:
        warnings=[]
        def __init__(self,**kwargs):pass
        def describe_effort(self,*args):return 'test'
        async def _complete(self,content,**kwargs):
            if isinstance(content,str):return '{"shots":[{"time":0.5,"caption":"Example","anchor":"Example"}]}'
            calls.append(content);text=content[0]['text']
            if '图片1是全图' in text:return '{"approved":true,"context_complete":true,"clearly_better":true}'
            if '判断这张已核对的全图' in text:return '{"box":[0.2,0.1,0.8,0.9],"context_complete":true,"clearly_better":true}'
            return '{"choices":[{"id":0,"candidate":1,"caption":"Settings","reason":"selected"}]}'
        async def generate_summary(self,*args,**kwargs):return '# Title\n\nExample\n\n[[VIDEO_SHOT_0]]'
    monkeypatch.setattr(main,'LLMSummarizer',Model);monkeypatch.setattr(main,'resolve_llm_credentials',lambda **kwargs:('fake','fixture'))
    monkeypatch.setattr(main,'archive_note',lambda *args,**kwargs:None)
    task_id='crop-api';(tmp_path/task_id).mkdir();main.tasks[task_id]=main.new_task(task_id=task_id)
    request=main.SummarizeRequest(video_url=URL,llm_config={},screenshot_density='medium',vision_screenshots=visual,auto_crop_screenshots=enabled)
    await main.process_video_task(task_id,request)
    assert main.tasks[task_id]['status']=='completed',main.tasks[task_id].get('error')
    assert len(calls)==expected_calls
    if visual and enabled:
        assert '-crop-' in main.tasks[task_id]['result']['markdown']
        assert len(list((tmp_path/task_id/'frames/originals').glob('*.jpg')))==1
