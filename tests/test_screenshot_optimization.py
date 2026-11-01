import asyncio
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from backend import vision_screenshots as vision
from backend.ai_screenshots import ScreenshotPoint


def image_frames(tmp_path, times):
    result = {}
    for stamp in times:
        path = tmp_path / f'{stamp}.png'
        Image.new('RGB', (96, 64), (round(stamp*7)%255, round(stamp*17)%255, 100)).save(path)
        result[stamp] = path
    return result


@pytest.mark.asyncio
async def test_retry_can_reconsider_original_frame_lost_by_old_wider_grid(tmp_path):
    point = ScreenshotPoint(10, 'formula', 'source')
    frames = image_frames(tmp_path, vision.candidate_times([point], 30))
    original = frames[11.5]
    calls = []
    async def review(prompt, paths):
        calls.append(paths)
        if len(calls) == 1:
            return '{"choices":[{"id":0,"candidate":null,"reason":"unfinished_operation"}]}'
        assert original in paths
        assert len(paths) == 9
        assert '11.500000' in prompt
        return json.dumps({'choices':[{'id':0,'candidate':paths.index(original),'caption':'Formula'}]})
    async def capture(times):
        assert 11.5 not in times
        return image_frames(tmp_path, times)
    accepted, selected = await vision.verify_screenshots(SimpleNamespace(), [point], frames, 30,
        review=review, capture=capture)
    assert [p.time for p in accepted] == [11.5]
    assert selected == {11.5:original}


@pytest.mark.parametrize('center,duration', [(0,30),(1,2),(10,30),(29,30),(30,30)])
def test_merged_retry_retains_initial_evidence_and_max_ten(center, duration):
    point = ScreenshotPoint(center,'','')
    window = vision.retry_windows([point], [])
    initial = vision.candidate_times([point], duration)
    merged = vision.review_times(point, duration, window)
    assert set(initial) <= set(merged)
    assert len(merged) <= 10
    assert 0 <= min(merged) <= max(merged) < duration


@pytest.mark.asyncio
async def test_pixel_identical_selection_removed_across_batches_but_small_changes_survive(tmp_path):
    points = [ScreenshotPoint(t,'setting','parameter') for t in (5,15,25,35)]
    frames = image_frames(tmp_path, vision.candidate_times(points, 50))
    base = Image.new('RGB',(96,64),'white')
    for t in (5,15,35):
        base.save(frames[t])
    changed = base.copy()
    changed.putpixel((48,32),(0,0,0))
    changed.save(frames[25])
    messages = []
    async def report(message): messages.append(message)
    async def review(prompt, paths):
        count = len(paths)//5
        return json.dumps({'choices':[{'id':i,'candidate':2} for i in range(count)]})
    accepted, selected = await vision.verify_screenshots(SimpleNamespace(), points, frames, 50,
        review=review, report=report)
    assert [p.time for p in accepted] == [5,25]
    assert set(selected) == {5,25}
    assert any('2 张像素完全相同' in m for m in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['zh-CN','en'])
async def test_context_and_image_identity_survive_both_rounds(tmp_path, language):
    point = ScreenshotPoint(10,'parameter','anchor')
    frames = image_frames(tmp_path, vision.candidate_times([point], 30))
    calls = []
    async def review(prompt, paths):
        calls.append(prompt)
        assert 'nearby setting detail' in prompt
        assert 'distant unrelated' not in prompt
        assert f'image=1, id=0, candidate=0,' in prompt
        assert f'file={paths[0].name}' in prompt
        if len(calls)==1:
            return '{"choices":[{"id":0,"candidate":null,"reason":"transition"}]}'
        return '{"choices":[{"id":0,"candidate":0}]}'
    async def capture(times): return image_frames(tmp_path,times)
    await vision.verify_screenshots(SimpleNamespace(_note_language=language), [point], frames,30,
        review=review,capture=capture,segments=[{'start':9,'end':11,'text':'nearby setting detail'},
            {'start':100,'end':101,'text':'distant unrelated'}])
    assert len(calls)==2


@pytest.mark.asyncio
async def test_api_labels_are_adjacent_to_corresponding_images():
    class Model:
        async def _complete(self, content, **kwargs):
            assert content[1:] == [
                {'type':'text','text':'id=0, candidate=0'},
                {'type':'image_url','image_url':{'url':'first'}},
                {'type':'text','text':'id=0, candidate=1'},
                {'type':'image_url','image_url':{'url':'second'}}]
            return 'ok'
    assert await vision.vision_complete(Model(),'review',['first','second'],
        labels=['id=0, candidate=0','id=0, candidate=1']) == 'ok'
    with pytest.raises(ValueError):
        await vision.vision_complete(Model(),'review',['first'],labels=[])


@pytest.mark.asyncio
async def test_retry_uses_one_point_per_request_and_never_exceeds_ten_images(tmp_path):
    points = [ScreenshotPoint(t,'step','source') for t in (10,30,50)]
    frames = image_frames(tmp_path, vision.candidate_times(points,70))
    calls = []
    async def review(prompt, paths):
        calls.append(len(paths))
        if len(calls)==1:
            assert len(paths)==15
            return json.dumps({'choices':[{'id':i,'candidate':None,'reason':'transition'} for i in range(3)]})
        assert len(paths)<=10
        return '{"choices":[{"id":0,"candidate":0}]}'
    async def capture(times): return image_frames(tmp_path,times)
    accepted, selected = await vision.verify_screenshots(SimpleNamespace(),points,frames,70,
        review=review,capture=capture)
    assert len(calls)==4
    assert len(accepted)==3
