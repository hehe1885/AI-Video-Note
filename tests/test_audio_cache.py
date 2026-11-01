import asyncio
import json
import time
from pathlib import Path

import pytest

from backend import audio_cache, main, video_cache
from backend.transcript import TranscriptSegment
from backend.video_processor import VideoProcessor
from tests.media_fixture import make_audio, make_av_video

URL = 'https://youtu.be/audio-reuse'


def downloader(processor, monkeypatch, calls, delay=0):
    class Ydl:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=True):
            calls.append(url)
            if delay:
                time.sleep(delay)
            make_audio(Path(self.options['outtmpl'].replace('%(ext)s', 'm4a')))
            return {}
    monkeypatch.setattr(processor, '_ydl', lambda *args, **kwargs: Ydl(kwargs))


@pytest.mark.asyncio
async def test_repeated_audio_requests_download_only_once(tmp_path, monkeypatch):
    processor = VideoProcessor(tmp_path)
    calls, notes = [], []
    downloader(processor, monkeypatch, calls)
    first = await processor.download_audio(URL, 'first')
    second = await processor.download_audio(URL, 'second', notes=notes)
    assert calls == [URL]
    assert first.name == 'audio.m4a'
    assert audio_cache.has_audio(second)
    assert first.read_bytes() == second.read_bytes()
    assert any('无需重新下载' in note for note in notes)


@pytest.mark.asyncio
async def test_simultaneous_requests_share_one_download(tmp_path, monkeypatch):
    processor = VideoProcessor(tmp_path)
    calls = []
    downloader(processor, monkeypatch, calls, delay=.05)
    results = await asyncio.gather(processor.download_audio(URL, 'one'), processor.download_audio(URL, 'two'))
    assert len(calls) == 1
    assert all(audio_cache.has_audio(path) for path in results)


@pytest.mark.asyncio
async def test_historical_audio_remains_reusable_after_original_task_file_removed(tmp_path, monkeypatch):
    old = tmp_path / 'old'
    old.mkdir()
    media = old / 'audio.m4a'
    make_audio(media)
    (old / 'task.json').write_text(json.dumps({'source_url': URL}))
    processor = VideoProcessor(tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError('must not download')
    monkeypatch.setattr(processor, '_ydl', forbidden)
    shared = await processor.download_audio(URL, 'new')
    assert shared.parent == tmp_path / 'audio'
    media.unlink()
    (old / 'task.json').unlink()
    assert await processor.download_audio(URL, 'third') == shared
    assert audio_cache.has_audio(shared)


@pytest.mark.asyncio
@pytest.mark.parametrize('custom', [False, True])
async def test_cached_video_audio_is_used_without_download_at_any_resolution(tmp_path, monkeypatch, custom):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    root = tmp_path / 'custom-videos' if custom else workspace / 'video'
    directory = video_cache.cache_directory(root, URL, 360)
    directory.mkdir(parents=True)
    video = directory / 'preview.mp4'
    make_av_video(video)
    saved = video_cache.record_cache(directory, video, True, source_url=URL, height=360, workspace=workspace)
    processor = VideoProcessor(workspace)
    def forbidden(*args, **kwargs): raise AssertionError('must not download audio')
    monkeypatch.setattr(processor, '_ydl', forbidden)
    notes = []
    media = await processor.download_audio(URL, 'new', notes=notes, video_directory=str(root) if custom else '')
    assert media == saved
    assert any('视频音轨' in note for note in notes)
    assert audio_cache.has_audio(media)


@pytest.mark.asyncio
async def test_bilibili_pages_use_their_own_historical_audio(tmp_path, monkeypatch):
    base = 'https://www.bilibili.com/video/BV1test/'
    old = tmp_path / 'old'
    old.mkdir()
    (old / 'task.json').write_text(json.dumps({'source_url': base, 'bilibili_pages': [2, 3]}))
    make_audio(old / 'audio_p2.m4a')
    make_audio(old / 'audio_p3.m4a')
    processor = VideoProcessor(tmp_path)
    calls = []
    downloader(processor, monkeypatch, calls)
    second = await processor.download_audio(base + '?p=2', 'new', media_name='audio_p2')
    third = await processor.download_audio(base + '?p=3', 'new', media_name='audio_p3')
    assert second != third and calls == []
    await processor.download_audio(base + '?p=4', 'new', media_name='audio_p4')
    assert calls == [base + '?p=4']


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
async def test_unusable_audio_is_downloaded_again(tmp_path, monkeypatch, damage):
    processor = VideoProcessor(tmp_path)
    calls = []
    downloader(processor, monkeypatch, calls)
    await processor.download_audio(URL, 'first')
    cached = audio_cache.find_cached(tmp_path / 'audio', URL)
    if damage == 'missing': cached.unlink()
    else: cached.write_bytes(b'broken media')
    fresh = await processor.download_audio(URL, 'second')
    assert len(calls) == 2
    assert audio_cache.has_audio(fresh)


@pytest.mark.asyncio
async def test_cancellation_is_checked_before_cached_audio_is_returned(tmp_path, monkeypatch):
    processor = VideoProcessor(tmp_path)
    calls = []
    downloader(processor, monkeypatch, calls)
    await processor.download_audio(URL, 'first')
    with pytest.raises(asyncio.CancelledError):
        await processor.download_audio(URL, 'second', should_abort=lambda: True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_restart_retranscribes_with_new_model_without_redownloading(tmp_path, monkeypatch):
    from backend import transcript_review
    async def checked(runtime, task_id, request, segments, *args): return segments
    monkeypatch.setattr(transcript_review, 'review_transcript_output', checked)
    processor = VideoProcessor(tmp_path)
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'video_processor', processor)
    downloads, transcriptions = [], []
    downloader(processor, monkeypatch, downloads)
    async def info(*args, **kwargs): return {'title': '音频复用测试', 'source': 'youtube', 'duration': 2}
    async def transcribe(path, model, *args, **kwargs):
        transcriptions.append((path, model))
        assert audio_cache.has_audio(path)
        return {'segments': [TranscriptSegment(0, 2, '重新识别出的字幕')], 'language': 'zh', 'duration': 2, 'device': 'cpu', 'model': model, 'requested_model': model}
    monkeypatch.setattr(processor, 'get_video_info', info)
    monkeypatch.setattr(main, 'run_transcription', transcribe)
    for identifier, model in [('first', 'base'), ('second', 'small')]:
        request = main.SummarizeRequest(video_url=URL, processing_mode='restart', prefer_subtitles=False, output='transcript', whisper_model=model, llm_config={})
        main.task_directory(identifier).mkdir(parents=True)
        main.tasks[identifier] = main.new_task(task_id=identifier)
        main.write_task_manifest(identifier, request, None, 'web')
        await main.process_video_task(identifier, request)
        assert main.tasks[identifier]['status'] == 'completed', main.tasks[identifier].get('error')
    assert downloads == [URL]
    assert [model for path, model in transcriptions] == ['base', 'small']
    assert any('已复用下载的音频' in note for note in main.tasks['second']['logs'])


def test_partial_and_wrong_source_history_are_not_reused(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    make_audio(old / 'audio.m4a')
    (old / 'task.json').write_text(json.dumps({'source_url': URL + '-different'}))
    assert audio_cache.find_history_audio(tmp_path, URL) is None
    (old / 'task.json').write_text(json.dumps({'source_url': URL}))
    (old / 'audio.m4a').rename(old / 'audio.m4a.part')
    assert audio_cache.find_history_audio(tmp_path, URL) is None
