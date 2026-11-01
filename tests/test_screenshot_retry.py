import asyncio
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from backend import vision_screenshots as vision, agent_auto, agent_queue, main
from backend.ai_screenshots import ScreenshotPoint
from backend.video_processor import VideoProcessor
from tests.test_agent_auto import job
from tests.test_preview_reuse import cached_video, URL


def make_frames(directory, times):
    directory.mkdir(exist_ok=True)
    result = {}
    for stamp in times:
        path = directory / f'{stamp}.jpg'
        image = Image.new('RGB', (30, 20), 'white')
        ImageDraw.Draw(image).text((0, 0), str(stamp), fill='black')
        image.save(path)
        result[stamp] = path
    return result


@pytest.mark.asyncio
async def test_retry_only_rejected_transition_and_keep_first_round_selection(tmp_path):
    points = [ScreenshotPoint(10, 'A', 'A'), ScreenshotPoint(30, 'B', 'B')]
    frames = make_frames(tmp_path, vision.candidate_times(points, 60))
    calls, captured = [], []
    async def review(prompt, paths):
        calls.append(paths)
        if len(calls) == 1:
            return json.dumps({'choices': [
                {'id': 0, 'candidate': 2, 'caption': 'First'},
                {'id': 1, 'candidate': None, 'reason': 'transition'}]})
        assert len(paths) == 9
        return '{"choices":[{"id":0,"candidate":8,"caption":"Result","reason":"selected"}]}'
    async def capture(times):
        captured.append(times)
        return make_frames(tmp_path, times)
    accepted, selected = await vision.verify_screenshots(SimpleNamespace(), points, frames, 60,
        review=review, capture=capture)
    assert [point.time for point in accepted] == [10, 38]
    assert list(selected) == [10, 38]
    assert len(calls) == 2 and len(captured) == 1
    assert captured[0] == [22, 26, 34, 38]  # The center frame is reused.


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', [None, 'no_value', 'unreadable', 'irrelevant'])
async def test_legacy_or_non_timing_rejection_never_triggers_extra_capture(tmp_path, reason):
    points = [ScreenshotPoint(10, 'Topic', 'Source')]
    frames = make_frames(tmp_path, vision.candidate_times(points, 30))
    async def review(prompt, paths):
        return json.dumps({'choices': [{'id': 0, 'candidate': None, 'reason': reason}]})
    async def capture(times): raise AssertionError('Unnecessary capture')
    assert await vision.verify_screenshots(SimpleNamespace(), points, frames, 30,
        review=review, capture=capture) == ([], {})


@pytest.mark.asyncio
async def test_retry_is_bounded_even_when_second_round_still_shows_transition(tmp_path):
    points = [ScreenshotPoint(10, 'Topic', 'Source')]
    frames = make_frames(tmp_path, vision.candidate_times(points, 30))
    calls = []
    async def review(prompt, paths):
        calls.append(1)
        return '{"choices":[{"id":0,"candidate":null,"reason":"unfinished_operation"}]}'
    async def capture(times): return make_frames(tmp_path, times)
    assert await vision.verify_screenshots(SimpleNamespace(), points, frames, 30,
        review=review, capture=capture) == ([], {})
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['capture', 'review', 'cancel'])
async def test_retry_failure_preserves_verified_images_and_cancellation_propagates(tmp_path, failure):
    points = [ScreenshotPoint(10, 'A', 'A'), ScreenshotPoint(30, 'B', 'B')]
    frames = make_frames(tmp_path, vision.candidate_times(points, 60))
    calls = []
    async def review(prompt, paths):
        calls.append(1)
        if len(calls) == 1:
            return '{"choices":[{"id":0,"candidate":2},{"id":1,"candidate":null,"reason":"transition"}]}'
        raise ValueError('Invalid response')
    async def capture(times):
        if failure == 'cancel': raise asyncio.CancelledError()
        if failure == 'capture': raise RuntimeError('Decode failed')
        return make_frames(tmp_path, times)
    operation = vision.verify_screenshots(SimpleNamespace(), points, frames, 60,
        review=review, capture=capture)
    if failure == 'cancel':
        with pytest.raises(asyncio.CancelledError): await operation
    else:
        accepted, selected = await operation
        assert [point.time for point in accepted] == [10]
        assert list(selected) == [10]


def test_expanded_window_respects_neighbor_points_pauses_and_video_end():
    points = [ScreenshotPoint(10, '', ''), ScreenshotPoint(20, '', '')]
    segments = [{'start': 0, 'end': 4}, {'start': 7, 'end': 30}]
    windows = vision.retry_windows(points, segments)
    assert windows[10] == (7, 15)
    assert windows[20] == (15, 28)
    assert vision.candidate_times(points[:1], 30, radius=8, windows=windows) == [7, 9, 11, 13, 15]
    edge = vision.candidate_times([ScreenshotPoint(29, '', '')], 30, radius=8)
    assert len(edge) == 5 and min(edge) >= 0 and max(edge) < 30


@pytest.mark.asyncio
async def test_api_retry_uses_real_verifier_and_cleans_all_unselected_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    cached_video(tmp_path)
    processor = VideoProcessor(tmp_path)
    async def info(*args, **kwargs):
        return {'title': 'Retry fixture', 'source': 'youtube', 'duration': 30,
                'subtitles': {'en': [{'ext': 'vtt', 'data': 'WEBVTT\n\n00:00:00.000 --> 00:00:30.000\nExample\n'}]}}
    captured = []
    async def extract(video, task_id, times, cancel):
        captured.append(times)
        return make_frames(tmp_path / task_id / 'frames', times)
    monkeypatch.setattr(processor, 'get_video_info', info)
    monkeypatch.setattr(processor, 'extract_frames_at', extract)
    monkeypatch.setattr(main, 'video_processor', processor)
    class Model:
        warnings = []
        def __init__(self, **kwargs): self.calls = 0
        def describe_effort(self, *args): return 'test'
        async def _complete(self, prompt, **kwargs):
            if isinstance(prompt, str):
                return '{"shots":[{"time":10,"caption":"Example","anchor":"Example"}]}'
            self.calls += 1
            return ('{"choices":[{"id":0,"candidate":null,"reason":"transition"}]}' if self.calls == 1 else
                    '{"choices":[{"id":0,"candidate":8,"caption":"Verified result"}]}')
        async def generate_summary(self, *args, **kwargs): return '# Notes\n\nExample'
    monkeypatch.setattr(main, 'LLMSummarizer', Model)
    monkeypatch.setattr(main, 'resolve_llm_credentials', lambda **kwargs: ('fake', 'test'))
    monkeypatch.setattr(main, 'archive_note', lambda *args, **kwargs: None)
    task_id = 'api-retry'
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task(task_id=task_id)
    await main.process_video_task(task_id, main.SummarizeRequest(video_url=URL, llm_config={},
        screenshot_density='medium', vision_screenshots=True))
    task = main.tasks[task_id]
    assert task['status'] == 'completed', task.get('error')
    assert len(captured) == 2
    assert task['result']['screenshot_count'] == 1
    assert 'Verified result' in task['result']['markdown']
    assert [path.name for path in (tmp_path / task_id / 'frames').glob('*.jpg')] == ['18.0.jpg']


def test_auto_agent_retry_retains_selected_frame_in_note_and_job(job, monkeypatch):
    task_id, directory = job
    data = agent_queue.read_job(task_id)
    data['settings']['vision_screenshots'] = True
    agent_queue.write_job(task_id, data)
    reviews = []
    async def extract(video, task_id, times, cancel):
        return make_frames(directory / 'frames', times)
    async def cli(task_id, prompt, schema=None, images=()):
        if schema and 'times' in schema['properties']: return '{"times":[3]}'
        if schema:
            reviews.append(images)
            return ('{"choices":[{"id":0,"candidate":null,"reason":"transition"}]}' if len(reviews) == 1 else
                    '{"choices":[{"id":0,"candidate":8,"caption":"Result","reason":"selected"}]}')
        current = agent_queue.read_job(task_id)
        assert len(images) == 1
        return '# Notes\n\n![Result](' + current['screenshots'][0]['markdown_ref'] + ')'
    monkeypatch.setattr(main.video_processor, 'extract_frames_at', extract)
    monkeypatch.setattr(agent_auto, 'run_cli', cli)
    asyncio.run(agent_auto.work(task_id))
    assert len(reviews) == 2
    assert main.tasks[task_id]['status'] == 'completed'
    latest = agent_queue.read_job(task_id)
    assert latest['screenshots'][0]['time'] == 9.95
    assert (directory / 'frames' / latest['screenshots'][0]['filename']).is_file()
    assert len(list((directory / 'frames').glob('*.jpg'))) == 1
