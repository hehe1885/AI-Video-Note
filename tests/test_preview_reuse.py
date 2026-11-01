import json
from pathlib import Path
import pytest
from backend import main
from backend.video_processor import VideoProcessor
from tests.media_fixture import make_av_video as make_video

URL = 'https://youtube.com/watch?v=cache-test'

def cached_video(root, external=None):
    directory = root / 'older-success'
    directory.mkdir()
    video = (external or directory) / 'preview.mp4'
    video.parent.mkdir(parents=True, exist_ok=True)
    make_video(video, width=64, height=48)
    (directory / 'task.json').write_text(json.dumps({'source_url': URL,
        'runtime': {'result': {'video_path': str(video), 'video_resolution': 720}}}), encoding='utf-8')
    return video


def test_cache_scans_older_tasks_and_honors_quality_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    source = cached_video(tmp_path, tmp_path / 'external')
    failed = tmp_path / 'newer-failure'
    failed.mkdir()
    (failed / 'task.json').write_text(json.dumps({'source_url': URL, 'runtime': {'result': {'video_resolution': 720}}}))
    request = main.SummarizeRequest(llm_config={}, video_download_directory=str(tmp_path / 'new-location'))
    reused = main.reuse_preview_video(request, 'new-task', URL)
    assert reused == tmp_path / 'new-location' / 'older-success.mp4'
    assert reused.read_bytes() == source.read_bytes()
    source.unlink()
    assert reused.stat().st_size > 0
    request.video_resolution = 1080
    assert main.reuse_preview_video(request, 'other-task', URL) is None
    assert main.reuse_preview_video(request, 'other-task', URL + '-different') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('cached', [True, False])
async def test_pipeline_reuses_video_or_persists_visible_download_warning(tmp_path, monkeypatch, cached):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    if cached:
        cached_video(tmp_path)
    processor = VideoProcessor(tmp_path)
    async def info(*args, **kwargs):
        return {'title': 'Cache regression', 'source': 'youtube', 'duration': 2,
                'subtitles': {'en': [{'ext': 'vtt', 'data': 'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nExample\n'}]}}
    calls = []
    async def download(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('HTTP 403 Forbidden')
    class Model:
        warnings = []
        def __init__(self, **kwargs): pass
        def describe_effort(self, *args): return 'test'
        async def generate_summary(self, *args, **kwargs): return '# Title\n\nExample'
    monkeypatch.setattr(processor, 'get_video_info', info)
    monkeypatch.setattr(processor, 'download_preview_video', download)
    monkeypatch.setattr(main, 'video_processor', processor)
    monkeypatch.setattr(main, 'LLMSummarizer', Model)
    monkeypatch.setattr(main, 'resolve_llm_credentials', lambda **kwargs: ('fake', 'test'))
    monkeypatch.setattr(main, 'archive_note', lambda *args, **kwargs: None)
    task_id = 'current'
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task(task_id=task_id)
    request = main.SummarizeRequest(video_url=URL, llm_config={}, timed_screenshots=True, screenshot_interval=1)
    await main.process_video_task(task_id, request)
    task = main.tasks[task_id]
    assert task['status'] == 'completed', task.get('error')
    assert task['result']['screenshot_count'] == (2 if cached else 0)
    assert len(calls) == (0 if cached else 1)
    if not cached:
        assert task['screenshot_warning']
        assert task['result']['screenshot_warning'] == task['screenshot_warning']
        main.tasks.clear()
        main.restore_tasks_from_workspace()
        assert main.tasks[task_id]['screenshot_warning']
        assert main.tasks[task_id]['advisory']
