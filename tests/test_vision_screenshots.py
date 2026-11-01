import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from backend import vision_screenshots as vision, main
from backend.ai_screenshots import ScreenshotPoint
from backend.llm_summarizer import LLMSummarizer
from tests.test_preview_reuse import cached_video, URL
from backend.video_processor import VideoProcessor


@pytest.fixture(autouse=True)
def clear_cache():
    vision._CACHE.clear()
    vision._LOCKS.clear()


@pytest.mark.asyncio
async def test_capability_probe_sends_only_synthetic_image_and_caches(monkeypatch):
    monkeypatch.setattr(vision, 'probe_image', lambda: ('123456', 'data:image/png;base64,test'))
    calls = []
    class Model:
        model_type='custom'; model='vision'; base_url='https://example.com'
        async def _complete(self, content, **kwargs):
            assert content[1]['type'] == 'image_url'
            assert '123456' not in content[0]['text']
            calls.append(content)
            return '123456'
    assert (await vision.probe_vision(Model(), 'secret'))['supported']
    assert (await vision.probe_vision(Model(), 'secret'))['supported']
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [(400,'unsupported'), (401,'error'), (429,'error')])
async def test_unsupported_is_distinct_from_auth_and_network_errors(status, expected):
    class Error(Exception): status_code=status
    class Model:
        model_type='custom'; model='text'; base_url='https://example.com'
        calls=0
        async def _complete(self, *args, **kwargs):
            self.calls += 1
            raise Error('image inputs are unsupported')
    model = Model()
    first = await vision.probe_vision(model, 'secret')
    assert first['state'] == expected
    assert not first['supported']
    await vision.probe_vision(model, 'secret')
    assert model.calls == (1 if expected == 'unsupported' else 2)


def frames(tmp_path, times):
    result = {}
    for stamp in times:
        path = tmp_path / f'{stamp}.jpg'
        Image.new('RGB', (30,20), 'white').save(path)
        result[stamp] = path
    return result


@pytest.mark.asyncio
async def test_visual_review_selects_neighbor_and_rejects_unrelated(tmp_path):
    points = [ScreenshotPoint(5, 'Chart', 'chart explanation'), ScreenshotPoint(15, 'Other', 'other topic')]
    images = frames(tmp_path, vision.candidate_times(points, 30))
    class Model:
        async def _complete(self, content, **kwargs):
            assert len([item for item in content if item['type']=='image_url']) == 10
            assert content[1] == {'type':'text','text':'id=0, candidate=0, time=2.000000s'}
            assert content[2]['image_url']['url'].startswith('data:image/jpeg;base64,')
            return json.dumps({'choices': [{'id':0, 'candidate':4, 'caption':'Visible chart'}, {'id':1,'candidate':None}]})
    accepted, selected = await vision.verify_screenshots(Model(), points, images, 30)
    assert [point.time for point in accepted] == [8]
    assert accepted[0].caption == 'Visible chart'
    assert selected == {8: images[8]}


@pytest.mark.asyncio
async def test_invalid_visual_selection_and_cancellation_are_not_accepted(tmp_path):
    points = [ScreenshotPoint(5, 'Topic', 'text')]
    images = frames(tmp_path, vision.candidate_times(points,30))
    class Model:
        async def _complete(self, *args, **kwargs): return '{"choices":[{"id":0,"candidate":99}]}'
    with pytest.raises(ValueError): await vision.verify_screenshots(Model(), points, images, 30)
    with pytest.raises(asyncio.CancelledError): await vision.verify_screenshots(Model(), points, images, 30, lambda: True)


@pytest.mark.asyncio
async def test_real_completion_passes_multimodal_content_to_api():
    captured = []
    async def create(**kwargs):
        captured.append(kwargs)
        async def stream():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='123456'),finish_reason='stop')])
        return stream()
    model = LLMSummarizer('custom', 'fake', base_url='https://example.com/v1', model='vision-model')
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert await vision.vision_complete(model, 'Read image', ['data:image/png;base64,test']) == '123456'
    content = captured[0]['messages'][1]['content']
    assert content[0] == {'type':'text','text':'Read image'}
    assert content[1]['type'] == 'image_url'


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,fails', [(False,False), (True,False), (True,True)])
async def test_opt_in_pipeline_uses_visual_review_and_excludes_unverified_frames(tmp_path, monkeypatch, enabled, fails):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    cached_video(tmp_path)
    processor = VideoProcessor(tmp_path)
    async def info(*args, **kwargs):
        return {'title':'Vision example','source':'youtube','duration':2,'subtitles':{'en':[{'ext':'vtt','data':'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nExample\n'}]}}
    monkeypatch.setattr(processor, 'get_video_info', info)
    monkeypatch.setattr(main, 'video_processor', processor)
    calls = []
    class Model:
        warnings = []
        def __init__(self, **kwargs): pass
        def describe_effort(self, *args): return 'test'
        async def _complete(self, content, **kwargs):
            if isinstance(content, str): return '{"shots":[{"time":0.5,"caption":"Example","anchor":"Example"}]}'
            calls.append(content)
            if fails: raise RuntimeError('image unsupported')
            return '{"choices":[{"id":0,"candidate":1,"caption":"Verified image"}]}'
        async def generate_summary(self, *args, **kwargs): return '# Title\n\nExample'
    monkeypatch.setattr(main, 'LLMSummarizer', Model)
    monkeypatch.setattr(main, 'resolve_llm_credentials', lambda **kwargs: ('fake','test'))
    monkeypatch.setattr(main, 'archive_note', lambda *args, **kwargs: None)
    task_id = 'vision-task'
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task(task_id=task_id)
    request = main.SummarizeRequest(video_url=URL,llm_config={},screenshot_density='medium',vision_screenshots=enabled)
    await main.process_video_task(task_id, request)
    task = main.tasks[task_id]
    assert task['status'] == 'completed', task.get('error')
    assert len(calls) == (1 if enabled else 0)
    assert task['result']['screenshot_count'] == (0 if fails else 1)
    if fails:
        assert task['screenshot_warning']
        assert not list((tmp_path / task_id / 'frames').glob('*.jpg'))
    elif enabled: assert 'Verified image' in task['result']['markdown']


@pytest.mark.asyncio
async def test_capability_manual_refresh_bypasses_cache(monkeypatch):
    monkeypatch.setattr(vision, 'probe_image', lambda: ('123456', 'data:image/png;base64,test'))
    class Model:
        model_type='custom'; model='vision'; base_url='https://example.com'
        calls=0
        async def _complete(self, *args, **kwargs):
            self.calls += 1
            return '123456'
    model = Model()
    await vision.probe_vision(model, 'secret')
    await vision.probe_vision(model, 'secret', refresh=True)
    assert model.calls == 2

@pytest.mark.parametrize("center,duration",[(10,30),(0,30),(1,30),(29,30),(30,30),(1,2),(3,None)])
def test_five_candidates_are_even_and_within_bounds(center,duration):
    times=vision.candidate_times([ScreenshotPoint(center,"Topic","text")],duration)
    assert len(times)==5
    deltas=[b-a for a,b in zip(times,times[1:])]
    assert max(deltas)-min(deltas)<0.000002
    assert min(times)>=max(0,center-3)
    assert max(times)<=center+3
    if duration: assert max(times)<duration
    if center==10: assert times==[7,8.5,10,11.5,13]
