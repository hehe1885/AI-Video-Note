import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from backend import vision_screenshots as vision
from backend.ai_screenshots import ScreenshotPoint, insert_screenshots, screenshot_instructions


def frames(tmp_path, times):
    result={}
    for i,stamp in enumerate(times):
        path=tmp_path/f'{stamp:.6f}.png'
        Image.new('RGB',(80,50),(int(stamp*11)%255,int(stamp*17)%255,int(stamp*29)%255)).save(path)
        result[stamp]=path
    return result


@pytest.mark.parametrize('center,duration',[(0,100),(1,2),(100,500),(499,500),(565.5,710)])
def test_cross_time_candidates_preserve_local_detail_and_bounds(center,duration):
    point=ScreenshotPoint(center,'','')
    times=vision.semantic_candidate_times(point,duration)
    assert set(vision.candidate_times([point],duration))<=set(times)
    assert len(times)<=15
    assert 0<=min(times)<=max(times)<duration
    assert max(abs(t-center) for t in times)<=180.000001


@pytest.mark.parametrize('duration',[None,0,float('inf')])
def test_unknown_video_bound_does_not_trigger_unbounded_search(duration):
    point=ScreenshotPoint(10,'','')
    assert vision.semantic_candidate_times(point,duration)==vision.candidate_times([point],duration)


@pytest.mark.parametrize('quote,expected',[
    ('opened a chat window',True),('Opened A Chat Window',True),
    ('open an unrelated settings page',False),('chat',True),('xx',False),(None,False),({'quote':'chat'},False)])
def test_wider_search_requires_a_real_source_quote(quote,expected):
    point=ScreenshotPoint(100,'Chat','And then he opened a chat window with one of them')
    assert vision.grounded_search_quote(point,[],quote) is expected


@pytest.mark.asyncio
async def test_delayed_visual_search_preserves_narration_time_and_trace(tmp_path):
    point=ScreenshotPoint(100,'Chat comparison','He opened a chat window')
    images=frames(tmp_path,vision.candidate_times([point],500))
    calls=[];audit=[];captured=[]
    async def capture(times):
        captured.extend(times)
        return frames(tmp_path,times)
    async def review(prompt,paths):
        calls.append(paths)
        assert 'visual_gap' in prompt
        if len(calls)==1:
            return '{"choices":[{"id":0,"candidate":null,"reason":"visual_gap","search_quote":"opened a chat window"}]}'
        assert len(paths)<=15
        chosen=next(i for i,p in enumerate(paths) if p.name.startswith('130.000000'))
        return json.dumps({'choices':[{'id':0,'candidate':chosen,'caption':'Two visible chat windows','reason':'selected'}]})
    accepted,selected=await vision.verify_screenshots(SimpleNamespace(),[point],images,500,
        review=review,capture=capture,audit=audit)
    assert len(calls)==2
    assert accepted[0].time==130
    assert accepted[0].speech_time==100
    assert accepted[0].source_time==100
    assert len(set(captured)&set(vision.candidate_times([point],500)))==0
    assert audit[0]['stage']=='local' and audit[1]['stage']=='cross_time'
    assert audit[1]['selected_time']==130 and audit[1]['source_time']==100
    assert vision.save_review_trace(tmp_path,audit)
    stored=json.loads((tmp_path/'vision_review.json').read_text())
    assert stored['decisions'][1]['selected_time']==130


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,quote',[
    ('visual_gap',None),('visual_gap','invented visual source'),('visual_gap','xx'),
    ('no_value','opened a chat window'),('irrelevant','opened a chat window'),
    ('unreadable','opened a chat window'),(None,'opened a chat window')])
async def test_abstract_or_unanchored_rejection_never_widens(tmp_path,reason,quote):
    point=ScreenshotPoint(100,'topic','He opened a chat window')
    images=frames(tmp_path,vision.candidate_times([point],500))
    async def review(prompt,paths):return json.dumps({'choices':[{'id':0,'candidate':None,'reason':reason,'search_quote':quote}]})
    async def capture(times):raise AssertionError('Unjustified cross-time search')
    assert await vision.verify_screenshots(SimpleNamespace(),[point],images,500,review=review,capture=capture)==([],{})


@pytest.mark.asyncio
async def test_cross_time_search_has_only_one_round_even_if_still_missing(tmp_path):
    point=ScreenshotPoint(100,'chart','The diagram shows the result')
    images=frames(tmp_path,vision.candidate_times([point],500))
    calls=[]
    async def review(prompt,paths):
        calls.append(1)
        return '{"choices":[{"id":0,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
    async def capture(times):return frames(tmp_path,times)
    assert await vision.verify_screenshots(SimpleNamespace(),[point],images,500,review=review,capture=capture)==([],{})
    assert len(calls)==2


def test_note_placement_uses_narration_time_but_image_caption_uses_frame_time():
    point=ScreenshotPoint(200,'Result','zzzz',source_time=10)
    body='[00:10] alpha paragraph\n\n[03:20] beta paragraph'
    result=insert_screenshots(body,[point],{200:Path('actual-frame.jpg')})
    assert result.index('actual-frame.jpg')<result.index('beta paragraph')
    assert '03:20' in result
    assert '讲解 00:10，画面 03:20' in screenshot_instructions([point])
    english=screenshot_instructions([point],'en')
    assert 'narration 00:10, frame 03:20' in english
    assert ScreenshotPoint(200,'','',source_time=0).speech_time==0


@pytest.mark.asyncio
async def test_failed_cross_time_capture_keeps_already_verified_image(tmp_path):
    points=[ScreenshotPoint(10,'A','A'),ScreenshotPoint(100,'chart','diagram shows the result')]
    images=frames(tmp_path,vision.candidate_times(points,500))
    async def review(prompt,paths):
        return '{"choices":[{"id":0,"candidate":2},{"id":1,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
    async def capture(times):raise RuntimeError('Decoder failed')
    accepted,selected=await vision.verify_screenshots(SimpleNamespace(),points,images,500,review=review,capture=capture)
    assert [p.time for p in accepted]==[10]
    assert list(selected)==[10]


@pytest.mark.asyncio
async def test_cancel_during_cross_time_capture_propagates(tmp_path):
    point=ScreenshotPoint(100,'chart','diagram shows the result')
    images=frames(tmp_path,vision.candidate_times([point],500))
    async def review(prompt,paths):return '{"choices":[{"id":0,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
    async def capture(times):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await vision.verify_screenshots(SimpleNamespace(),[point],images,500,review=review,capture=capture)
