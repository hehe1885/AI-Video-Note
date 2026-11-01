"""Restart must not turn pre-export bodies into successful task results."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.config_store import ConfigStore
from backend.video_processor import VideoProcessor


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'video_processor', VideoProcessor(tmp_path))
    monkeypatch.setattr(main, 'config_store', ConfigStore(tmp_path))
    monkeypatch.setattr(main, 'notify_task', lambda *args: None)
    return tmp_path, TestClient(main.app)


def saved_task(task_id='recovery', status='failed', output='note', artifact='body',
               agent=False, review=False, error=None):
    directory = main.task_directory(task_id)
    directory.mkdir(parents=True)
    runtime = {'logs': ['Body saved before export'], 'elapsed_seconds': 17,
               'finished_at': '2026-10-20T10:01:00+00:00', 'error': error}
    if status is not None:
        runtime['status'] = status
    manifest = {'task_id': task_id, 'origin': 'web', 'title': 'Recovery task',
                'output': output, 'runtime': runtime}
    (directory / 'task.json').write_text(json.dumps(manifest), encoding='utf-8')
    if artifact != 'missing':
        if output == 'transcript':
            (directory / 'transcript.json').write_text('' if artifact == 'empty' else json.dumps({
                'language': 'en', 'source': 'platform_subtitle',
                'segments': [{'start': 0, 'end': 10, 'text': 'Original transcript'}],
            }), encoding='utf-8')
            (directory / 'transcript.md').write_text('# Original transcript', encoding='utf-8')
        else:
            (directory / 'notes.md').write_text('' if artifact == 'empty' else '# Saved body', encoding='utf-8')
    if agent:
        (directory / 'agent_job.json').write_text('{}', encoding='utf-8')
    if review:
        (directory / 'transcript_review.json').write_text('{"status":"pending"}', encoding='utf-8')
    return directory


@pytest.mark.parametrize('status,output,artifact,agent,review,error,expected', [
    ('failed', 'note', 'body', False, False, 'PDF export failed', 'failed'),
    ('cancelled', 'note', 'body', False, False, None, 'cancelled'),
    ('pending', 'note', 'body', False, False, None, 'failed'),
    ('queued', 'note', 'body', False, False, None, 'failed'),
    ('processing', 'note', 'body', False, False, None, 'failed'),
    ('cancelling', 'note', 'body', False, False, None, 'failed'),
    ('uploaded', 'note', 'body', False, False, None, 'uploaded'),
    ('awaiting_agent', 'note', 'body', True, False, None, 'awaiting_agent'),
    ('awaiting_agent', 'note', 'body', False, False, None, 'failed'),
    ('completed', 'note', 'body', False, False, None, 'completed'),
    ('completed', 'note', 'empty', False, False, None, 'failed'),
    ('completed', 'note', 'missing', False, False, None, 'failed'),
    ('completed', 'note', 'body', False, False, 'Interrupted by previous restart', 'failed'),
    (None, 'note', 'body', False, False, None, 'completed'),
    (None, 'note', 'body', False, False, 'Legacy export error', 'failed'),
    (None, 'note', 'empty', False, False, None, 'failed'),
    (None, 'note', 'body', False, True, None, 'failed'),
    ('unknown', 'note', 'body', False, False, None, 'failed'),
    ('failed', 'transcript', 'body', False, False, 'Review failed', 'failed'),
    ('cancelled', 'transcript', 'body', False, False, None, 'cancelled'),
    ('processing', 'transcript', 'body', False, True, None, 'failed'),
    ('awaiting_agent', 'transcript', 'body', True, True, None, 'failed'),
    ('completed', 'transcript', 'body', False, False, None, 'completed'),
    ('completed', 'transcript', 'empty', False, False, None, 'failed'),
    ('completed', 'transcript', 'missing', False, False, None, 'failed'),
    (None, 'transcript', 'body', False, False, None, 'completed'),
    (None, 'transcript', 'body', False, True, None, 'failed'),
])
def test_history_startup_and_lazy_open_use_the_same_status(
        app_data, status, output, artifact, agent, review, error, expected):
    _, client = app_data
    directory = saved_task(status=status, output=output, artifact=artifact,
                           agent=agent, review=review, error=error)
    before = {path.name: path.read_bytes() for path in directory.iterdir() if path.name != 'task.json'}
    # History must classify tasks correctly even when they are not hydrated.
    assert client.get('/api/tasks').json()['tasks'][0]['status'] == expected
    assert not main.tasks
    main.restore_tasks_from_workspace()
    restored = main.tasks['recovery']
    assert restored['status'] == expected
    if status in {'failed', 'cancelled'} or error:
        assert restored['error'] == error
        assert restored['logs'] == ['Body saved before export']
    if status in {'pending', 'queued', 'processing', 'cancelling'} or (output == 'transcript' and status == 'awaiting_agent'):
        assert '服务重启' in restored['error']
        assert main.read_task_manifest('recovery')['runtime']['status'] == 'failed'
    assert client.get('/api/tasks').json()['tasks'][0]['status'] == expected
    main.tasks.clear()
    opened = client.get('/api/task/recovery')
    assert opened.status_code == 200
    assert opened.json()['status'] == expected
    if expected == 'failed':
        assert client.get('/api/download/recovery').status_code == 409
    assert {path.name: path.read_bytes() for path in directory.iterdir()
            if path.name not in {'task.json', '.task.lock'}} == before


def test_missing_completed_body_does_not_revert_to_uploaded(app_data):
    _, client = app_data
    directory = saved_task(status='completed', artifact='missing')
    (directory / 'input.wav').write_bytes(b'original media')
    main.restore_tasks_from_workspace()
    task = main.tasks['recovery']
    assert task['status'] == 'failed'
    assert '缺失或为空' in task['error']
    assert client.get('/api/tasks').json()['tasks'][0]['status'] == 'failed'
    assert (directory / 'input.wav').read_bytes() == b'original media'


@pytest.mark.asyncio
async def test_actual_export_failure_preserves_completed_note_after_restart(app_data, monkeypatch):
    _, client = app_data
    old = saved_task('source', status='completed', output='transcript')
    calls = []

    class Model:
        warnings = []

        def __init__(self, **kwargs):
            pass

        def describe_effort(self, *args):
            return 'test'

        async def generate_summary(self, *args, **kwargs):
            calls.append('generate')
            return '# Body generated before PDF export'

    def fail_export(*args, **kwargs):
        calls.append('export')
        raise OSError('PDF destination is unavailable')

    monkeypatch.setattr(main, 'LLMSummarizer', Model)
    monkeypatch.setattr(main, 'resolve_llm_credentials', lambda **kwargs: ('fake', 'test'))
    monkeypatch.setattr(main, 'archive_note', fail_export)
    task = main.new_task(task_id='export-failed')
    task['resume_task_id'] = 'source'
    main.tasks['export-failed'] = task
    directory = main.task_directory('export-failed')
    directory.mkdir(parents=True)
    request = main.SummarizeRequest(video_url='https://youtu.be/recovery-test',
                                    note_format='pdf', llm_config={})
    main.write_task_manifest('export-failed', request, 'source', 'web')
    await main.process_video_task('export-failed', request)
    assert task['status'] == 'completed', task.get('error')
    assert task['error'] is None
    assert 'PDF destination is unavailable' in task['result']['export_warning']
    body = (directory / 'notes.md').read_bytes()
    assert b'Body generated before PDF export' in body
    warning, logs = task['result']['export_warning'], list(task['logs'])
    main.tasks.clear()
    assert {row['task_id']: row['status'] for row in client.get('/api/tasks').json()['tasks']}['export-failed'] == 'completed'
    main.restore_tasks_from_workspace()
    restored = main.tasks['export-failed']
    assert restored['status'] == 'completed'
    assert restored['error'] is None and restored['logs'] == logs
    assert restored['result']['export_warning'] == warning
    assert (directory / 'notes.md').read_bytes() == body
    assert (old / 'transcript.json').is_file()
    assert client.get('/api/download/export-failed').status_code == 200
    assert calls == ['generate', 'export'], 'Restart must never call the model or export automatically'
