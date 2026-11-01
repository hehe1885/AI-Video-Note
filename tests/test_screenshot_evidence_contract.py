import json
from types import SimpleNamespace
import pytest
from PIL import Image
from backend.ai_screenshots import ScreenshotPoint
from backend.vision_screenshots import verify_screenshots,candidate_times

@pytest.mark.asyncio
@pytest.mark.parametrize('language',['zh-CN','en'])
@pytest.mark.parametrize('reason',['transition','unfinished_operation','visual_gap','no_value','unreadable','irrelevant'])
async def test_negative_reason_cannot_retain_candidate_or_request_more_frames(tmp_path,language,reason):
    point=ScreenshotPoint(20,'dated tooltip','2026年8月19日多空爆仓金额')
    frames={}
    for index,stamp in enumerate(candidate_times([point],60)):
        path=tmp_path/f'{index}.png';Image.new('RGB',(24,24),(index,20,40)).save(path);frames[stamp]=path
    async def review(prompt,paths):
        return json.dumps({'choices':[{'id':0,'candidate':2,'reason':reason,
            'reference_candidate':2,'search_quote':point.anchor}]})
    async def capture(times):
        pytest.fail('Conflicting response must be rejected without extra recall')
    audit=[]
    accepted,selected=await verify_screenshots(SimpleNamespace(_note_language=language),[point],frames,60,
        review=review,capture=capture,audit=audit)
    assert accepted==[] and selected=={}
    assert audit[0]['candidate'] is None and audit[0]['selected_time'] is None
    assert audit[0]['selection_conflict'] is True and audit[0]['returned_candidate']==2

@pytest.mark.asyncio
@pytest.mark.parametrize('reason',[None,'selected'])
async def test_legacy_and_positive_selection_still_work(tmp_path,reason):
    point=ScreenshotPoint(20,'actual chart','source');path=tmp_path/'frame.png'
    Image.new('RGB',(24,24),'blue').save(path)
    async def review(prompt,paths):
        choice={'id':0,'candidate':0,'caption':'visible chart'}
        if reason:choice['reason']=reason
        return json.dumps({'choices':[choice]})
    accepted,selected=await verify_screenshots(SimpleNamespace(),[point],{20:path},60,review=review)
    assert len(accepted)==1 and selected=={20:path}

@pytest.mark.asyncio
async def test_rejection_does_not_discard_valid_neighbor(tmp_path):
    points=[ScreenshotPoint(10,'a','source a'),ScreenshotPoint(40,'b','source b')]
    frames={}
    for i,t in enumerate(candidate_times(points,60)):
        path=tmp_path/f'{i}.png';Image.new('RGB',(24,24),(i,20,60)).save(path);frames[t]=path
    async def review(prompt,paths):
        return json.dumps({'choices':[{'id':0,'candidate':2,'reason':'selected'},
            {'id':1,'candidate':2,'reason':'unreadable'}]})
    accepted,selected=await verify_screenshots(SimpleNamespace(),points,frames,60,review=review)
    assert [p.time for p in accepted]==[10] and list(selected)==[10]

@pytest.mark.asyncio
@pytest.mark.parametrize('candidate',[True,-1,99])
async def test_bad_index_is_not_hidden_by_negative_reason(tmp_path,candidate):
    path=tmp_path/'frame.png';Image.new('RGB',(24,24),'red').save(path)
    async def review(prompt,paths):
        return json.dumps({'choices':[{'id':0,'candidate':candidate,'reason':'irrelevant'}]})
    with pytest.raises(ValueError):
        await verify_screenshots(SimpleNamespace(),[ScreenshotPoint(20,'a','b')],{20:path},60,review=review)
