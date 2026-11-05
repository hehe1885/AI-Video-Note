import asyncio
from contextlib import contextmanager
import pytest
from backend import main, agent_queue
from backend.video_processor import VideoProcessor


def test_preview_download_has_bounded_network_wait_and_reports_bytes(tmp_path, monkeypatch):
    processor = VideoProcessor(tmp_path)
    options = {}
    notes = []
    @contextmanager
    def fake_ydl(*args, **kwargs):
        options.update(kwargs)
        class Downloader:
            def extract_info(self, *args, **kwargs):
                for hook in options['progress_hooks']:
                    hook({'status':'downloading', 'downloaded_bytes':1048576, 'total_bytes':2097152})
                raise TimeoutError('stalled CDN')
        yield Downloader()
    monkeypatch.setattr(processor, '_ydl', fake_ydl)
    with pytest.raises(TimeoutError, match='stalled CDN'):
        asyncio.run(processor.download_preview_video('https://youtu.be/test', 'stall', notes=notes))
    assert options['socket_timeout'] == 20
    assert options['retries'] == options['fragment_retries'] == options['extractor_retries'] == 2
    assert any('50%' in line and '1.0 MiB' in line for line in notes)


@pytest.mark.parametrize('cancelled', [False, True])
def test_agent_preparation_reports_video_stage_and_handles_timeout(tmp_path, monkeypatch, cancelled):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    task_id = 'stalled-preview'
    request = main.SummarizeRequest(video_url='https://youtu.be/test', generation_mode='agent',
                                   agent_execution='manual', screenshot_density='few', llm_config={})
    main.tasks[task_id] = main.new_task(task_id=task_id, status='processing')
    main.task_directory(task_id).mkdir(parents=True)
    main.write_task_manifest(task_id, request, None, 'web')
    monkeypatch.setattr(main, 'reuse_preview_video', lambda *args: None)
    stages = []
    async def stalled(*args, **kwargs):
        stages.append((main.tasks[task_id]['step'], main.tasks[task_id]['step_name']))
        if cancelled:
            raise asyncio.CancelledError()
        raise TimeoutError('stalled CDN')
    monkeypatch.setattr(main.video_processor, 'download_preview_video', stalled)
    operation = agent_queue.prepare(task_id, request, 'Fixture', {}, [], source_url=request.video_url)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(operation)
        assert main.tasks[task_id]['status'] != 'awaiting_agent'
    else:
        asyncio.run(operation)
        assert main.tasks[task_id]['status'] == 'awaiting_agent'
        assert main.tasks[task_id]['screenshot_warning']
        assert agent_queue.read_job(task_id)['video_path'] is None
    assert stages == [(5, '准备配图视频')]
