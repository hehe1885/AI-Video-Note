"""Optional exports must preserve notes, responsiveness and cancellation ownership."""
import asyncio
from pathlib import Path
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import agent_queue, main, note_export
from backend.config_store import ConfigStore
from backend.transcript import TranscriptSegment
from backend.video_processor import SubtitleResult, VideoProcessor


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'config_store', ConfigStore(tmp_path))
    monkeypatch.setattr(main, 'notify_task', lambda *args: None)
    processor = VideoProcessor(tmp_path)
    calls = []

    async def info(*args, **kwargs):
        return {'title': 'Export fixture', 'source': 'youtube', 'duration': 30}

    async def subtitles(*args, **kwargs):
        return SubtitleResult([TranscriptSegment(0, 30, 'Original source material.')], 'en', 'platform_subtitle')

    class Summarizer:
        def __init__(self, **kwargs): pass
        def describe_effort(self, *args): return 'auto'
        async def generate_summary(self, *args, **kwargs):
            calls.append('generate')
            return '# Preserved note\n\nOriginal content.'

    monkeypatch.setattr(processor, 'get_video_info', info)
    monkeypatch.setattr(processor, 'fetch_subtitles', subtitles)
    monkeypatch.setattr(main, 'video_processor', processor)
    monkeypatch.setattr(main, 'LLMSummarizer', Summarizer)

    def prepare(mode='api', format='png'):
        task_id = 'export-' + mode
        main.tasks[task_id] = main.new_task(task_id=task_id)
        main.task_directory(task_id).mkdir(parents=True)
        request = main.SummarizeRequest(
            video_url='https://www.youtube.com/watch?v=fixture', generation_mode=mode,
            include_screenshots=False, screenshot_density='none', note_format=format,
            llm_config=main.LLMConfig(model_type='deepseek', api_key='test-key'),
        )
        main.write_task_manifest(task_id, request, None, 'web')
        return task_id, request

    async def finish_agent(task_id):
        main.tasks[task_id]['transcript_review'] = {'status': 'clear'}
        return await agent_queue.finish(task_id, agent_queue.Result(markdown='# Preserved note\n\nOriginal content.'), manual=True)

    return prepare, finish_agent, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['api', 'agent'])
@pytest.mark.parametrize('format', ['png', 'pdf', 'docx'])
async def test_export_failure_keeps_completed_note_and_download_retry(pipeline, monkeypatch, mode, format):
    prepare, finish_agent, calls = pipeline
    task_id, request = prepare(mode, format)

    def fail(*args, **kwargs):
        raise RuntimeError('Renderer unavailable')

    from backend import pdf_export, word_export
    module, name = {'png': (note_export, 'png_document'), 'pdf': (pdf_export, 'build_pdf_document'),
                    'docx': (word_export, 'build_word_document')}[format]
    original = getattr(module, name)
    monkeypatch.setattr(module, name, fail)
    await main.process_video_task(task_id, request)
    if mode == 'agent':
        await finish_agent(task_id)
    task = main.tasks[task_id]
    assert task['status'] == 'completed' and task['error'] is None
    assert 'Original content.' in task['result']['markdown']
    assert task['result']['export_status'] == 'failed'
    assert 'Renderer unavailable' in task['advisory']
    client = TestClient(main.app)
    assert client.get(f'/api/download/{task_id}').status_code == 200
    before = list(calls)
    # Retry a supported download after restoring the renderer, with no new AI call.
    monkeypatch.setattr(module, name, original)
    response = client.get(f'/api/download/{task_id}?format=docx')
    assert response.status_code == 200 and response.content.startswith(b'PK')
    assert calls == before
    main.tasks.clear()
    main.restore_tasks_from_workspace()
    assert main.tasks[task_id]['status'] == 'completed'
    assert main.tasks[task_id]['result']['export_status'] == 'failed'
    assert client.get(f'/api/download/{task_id}').status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['api', 'agent'])
async def test_slow_export_keeps_requests_responsive_and_cancellation_safe(pipeline, monkeypatch, mode):
    prepare, finish_agent, _ = pipeline
    task_id, request = prepare(mode, 'markdown')
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    original = main.archive_note

    def slow_export(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), 'Test did not release the exporter'
        return original(*args, **kwargs)

    monkeypatch.setattr(main, 'archive_note', slow_export)
    if mode == 'agent':
        await main.process_video_task(task_id, request)
        claim = await agent_queue.claim_job(task_id, agent_queue.Claim())
        job = asyncio.create_task(finish_agent(task_id))
    else:
        job = asyncio.create_task(main.run_queued_video_task(task_id, request))
        main.running_jobs[task_id] = job
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not release.is_set() and not job.done()
        health = await asyncio.wait_for(main.health_check(), .5)
        assert health['status'] == 'ok'
        assert (await main.get_task_status(task_id))['step_name'] == '保存输出'
        if mode == 'agent':
            renewal = await agent_queue.renew_job(task_id, agent_queue.Renew(claim_token=claim['claim_token']))
            assert renewal['released'] is False and renewal['lease_seconds'] == agent_queue.LEASE_SECONDS
            with pytest.raises(HTTPException) as duplicate:
                await agent_queue.finish(task_id, agent_queue.Result(markdown='# Duplicate'), manual=True)
            assert duplicate.value.status_code == 409
        await main.cancel_task(task_id)
        await asyncio.sleep(0)
        with pytest.raises(HTTPException) as deletion:
            await main.delete_task(task_id)
        assert deletion.value.status_code == 409
        assert main.task_directory(task_id).is_dir()
    finally:
        release.set()
        if mode == 'agent':
            with pytest.raises(HTTPException) as cancelled:
                await asyncio.wait_for(job, 3)
            assert cancelled.value.status_code == 409
        else:
            await asyncio.wait_for(job, 3)
    task = main.tasks[task_id]
    assert task['status'] == 'cancelled'
    assert task['result']['note_export_files']
    owned = [Path(item) for item in task['result']['note_export_files']]
    assert all(path.exists() for path in owned)
    assert 'Original content.' in (main.task_directory(task_id) / 'notes.md').read_text(encoding='utf-8')
    assert 'Duplicate' not in (main.task_directory(task_id) / 'notes.md').read_text(encoding='utf-8')
    main.running_jobs.pop(task_id, None)
    assert await main.delete_task(task_id) == {'deleted': True}
    assert all(not path.exists() for path in owned)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['api', 'agent'])
async def test_obsidian_failure_preserves_note_and_other_export(pipeline, monkeypatch, tmp_path, mode):
    prepare, finish_agent, _ = pipeline
    task_id, request = prepare(mode, 'markdown')
    vault = tmp_path / 'vault'
    vault.mkdir()
    request.obsidian = main.ObsidianExportPayload(vault=str(vault))

    def fail(*args, **kwargs):
        raise OSError('Vault unavailable')

    monkeypatch.setattr(main.obsidian_export, 'export_note', fail)
    await main.process_video_task(task_id, request)
    if mode == 'agent':
        await finish_agent(task_id)
    task = main.tasks[task_id]
    assert task['status'] == 'completed'
    assert Path(task['result']['archived_path']).is_file()
    assert 'Vault unavailable' in task['advisory']
    assert task['result']['obsidian_path'] is None
    assert (main.task_directory(task_id) / 'notes.md').is_file()


@pytest.mark.asyncio
async def test_successful_agent_export_clears_waiting_advisory(pipeline):
    prepare, finish_agent, _ = pipeline
    task_id, request = prepare('agent', 'markdown')
    await main.process_video_task(task_id, request)
    main.tasks[task_id]['advisory'] = '正在使用本机 agent 自动生成笔记'
    await finish_agent(task_id)
    task = main.tasks[task_id]
    assert task['status'] == 'completed'
    assert task['advisory'] == ''
    assert task['result']['export_status'] == 'completed'
    assert task['result']['export_warning'] is None


def test_partial_export_cleans_created_files_and_keeps_user_files(tmp_path, monkeypatch):
    task = tmp_path / 'task'
    (task / 'frames').mkdir(parents=True)
    (task / 'frames' / 'shot.jpg').write_bytes(b'image')
    destination = tmp_path / 'exports'
    destination.mkdir()
    existing = destination / 'user.json'
    existing.write_text('keep')
    original = note_export.shutil.copyfileobj

    def fail_copy(source, output):
        output.write(b'partial')
        raise OSError('Disk full')

    monkeypatch.setattr(note_export.shutil, 'copyfileobj', fail_copy)
    with pytest.raises(OSError, match='Disk full'):
        note_export.save_note_export('Title', '![shot](frames/shot.jpg)', task, destination, 'json')
    assert list(destination.iterdir()) == [existing]
    assert existing.read_text() == 'keep'
    monkeypatch.setattr(note_export.shutil, 'copyfileobj', original)
    saved = note_export.save_note_export('Title', '![shot](frames/shot.jpg)', task, destination, 'json')
    assert saved.is_file()


def test_concurrent_default_archives_never_overwrite(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda number: main.archive_note('Same title', f'Body {number}', folder='transcripts'), range(8)))
    assert len(set(paths)) == 8
    assert {path.read_text(encoding='utf-8') for path in paths} == {f'Body {number}' for number in range(8)}
