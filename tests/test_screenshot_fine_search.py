import asyncio
import json
from types import SimpleNamespace
import pytest
from backend import vision_screenshots as vision
from backend.ai_screenshots import ScreenshotPoint
from tests.test_screenshot_cross_time import frames


@pytest.mark.asyncio
async def test_visible_partial_cross_time_frame_receives_one_final_refinement(tmp_path):
    point=ScreenshotPoint(100,'diagram','The diagram shows the result')
    images=frames(tmp_path,vision.candidate_times([point],500))
    counts=[];audit=[]
    async def capture(times):return frames(tmp_path,times)
    async def review(prompt,paths):
        counts.append(len(paths))
        if len(counts)==1:
            return '{"choices":[{"id":0,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
        if len(counts)==2:
            index=next(i for i,p in enumerate(paths) if p.name=='130.000000.png')
            return json.dumps({'choices':[{'id':0,'candidate':None,'reason':'unfinished_operation','reference_candidate':index}]})
        assert '最后一次' in prompt
        index=next(i for i,p in enumerate(paths) if p.name=='135.000000.png')
        return json.dumps({'choices':[{'id':0,'candidate':index,'caption':'Complete diagram'}]})
    accepted,selected=await vision.verify_screenshots(SimpleNamespace(),[point],images,500,
        review=review,capture=capture,audit=audit)
    assert counts==[5,15,7]
    assert [(p.time,p.speech_time) for p in accepted]==[(135,100)]
    assert [row['stage'] for row in audit]==['local','cross_time','fine']


@pytest.mark.asyncio
@pytest.mark.parametrize('reference',[None,-1,999,True,'2'])
async def test_invalid_or_legacy_reference_never_opens_another_search(tmp_path,reference):
    point=ScreenshotPoint(100,'diagram','The diagram shows the result')
    images=frames(tmp_path,vision.candidate_times([point],500))
    calls=[]
    async def capture(times):return frames(tmp_path,times)
    async def review(prompt,paths):
        calls.append(1)
        if len(calls)==1:
            return '{"choices":[{"id":0,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
        return json.dumps({'choices':[{'id':0,'candidate':None,'reason':'unfinished_operation','reference_candidate':reference}]})
    assert await vision.verify_screenshots(SimpleNamespace(),[point],images,500,review=review,capture=capture)==([],{})
    assert len(calls)==2


@pytest.mark.asyncio
async def test_final_refinement_cannot_repeat_itself(tmp_path):
    point=ScreenshotPoint(100,'diagram','The diagram shows the result')
    images=frames(tmp_path,vision.candidate_times([point],500))
    calls=[]
    async def capture(times):return frames(tmp_path,times)
    async def review(prompt,paths):
        calls.append(1)
        if len(calls)==1:
            return '{"choices":[{"id":0,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
        return '{"choices":[{"id":0,"candidate":null,"reason":"unfinished_operation","reference_candidate":0}]}'
    assert await vision.verify_screenshots(SimpleNamespace(),[point],images,500,review=review,capture=capture)==([],{})
    assert len(calls)==3


@pytest.mark.parametrize('seed,duration,next_probe',[(0,20,None),(19.9,20,None),(100,500,130),(625.5,709.7875625,709.287563)])
def test_refinement_is_bounded_and_can_cover_long_coarse_gaps(seed,duration,next_probe):
    times=vision.refinement_candidate_times(seed,duration,next_probe)
    assert 1<=len(times)<=7
    assert 0<=min(times)<=max(times)<duration
    if seed==625.5:
        assert 704<max(times)<706
        assert seed in times


def test_cross_time_tail_probe_avoids_tiny_audio_video_duration_difference():
    point=ScreenshotPoint(565.5,'','')
    times=vision.semantic_candidate_times(point,709.7875625)
    assert max(times)<=709.3


def test_wider_source_context_can_ground_a_preceding_visual_description():
    point=ScreenshotPoint(100,'HDR','brightness principle')
    segments=[{'start':76,'end':80,'text':'the HDR histogram has four brightness levels'},
              {'start':20,'end':25,'text':'unrelated old example'}]
    assert vision.grounded_search_quote(point,segments,'HDR histogram has four brightness levels')
    assert not vision.grounded_search_quote(point,segments,'unrelated old example')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['decode','cancel'])
async def test_refinement_failure_preserves_verified_images_and_cancel_propagates(tmp_path,failure):
    points=[ScreenshotPoint(10,'first','first'),ScreenshotPoint(100,'diagram','diagram shows the result')]
    images=frames(tmp_path,vision.candidate_times(points,500))
    calls=[];captures=[]
    async def capture(times):
        captures.append(1)
        if len(captures)==2:
            if failure=='cancel':raise asyncio.CancelledError()
            raise OSError('decode failed')
        return frames(tmp_path,times)
    async def review(prompt,paths):
        calls.append(1)
        if len(calls)==1:
            return '{"choices":[{"id":0,"candidate":2},{"id":1,"candidate":null,"reason":"visual_gap","search_quote":"diagram shows the result"}]}'
        index=next(i for i,p in enumerate(paths) if p.name=='130.000000.png')
        return json.dumps({'choices':[{'id':0,'candidate':None,'reason':'unfinished_operation','reference_candidate':index}]})
    operation=vision.verify_screenshots(SimpleNamespace(),points,images,500,review=review,capture=capture)
    if failure=='cancel':
        with pytest.raises(asyncio.CancelledError):await operation
    else:
        accepted,selected=await operation
        assert [p.time for p in accepted]==[10]
        assert set(selected)=={10}
