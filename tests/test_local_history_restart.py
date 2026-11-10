import asyncio
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import main, task_dependencies


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    directory = main.task_directory('old')
    directory.mkdir(parents=True)
    (directory/'input.mp4').write_bytes(b'original local video')
    (directory/'transcript.json').write_text('{"old":true}')
    (directory/'notes.md').write_text('# Previous note')
    (directory/'task.json').write_text(json.dumps({'uploaded_filename':'original.mp4','runtime':{'status':'cancelled'}}))
    main.tasks['old'] = main.new_task(status='cancelled', task_id='old')
    async def queued(*args): pass
    monkeypatch.setattr(main, 'run_queued_video_task', queued)
    monkeypatch.setattr(main, 'find_reusable_task', lambda *args: pytest.fail('Restart must not search old results'))
    return directory


def payload(**changes):
    return {'source_task_id':'old', 'processing_mode':'restart', 'output':'transcript',
            'llm_config':{'model_type':'deepseek','api_key':'test-key'}, **changes}


def test_restart_owns_media_without_old_transcript_or_notes(history):
    response=TestClient(main.app).post('/api/summarize', json=payload())
    assert response.status_code == 200, response.text
    data=response.json()
    assert data['task_id'] != 'old' and data['reused_task_id'] is None
    task=main.tasks[data['task_id']]
    assert task['resume_task_id'] is None
    target=Path(task['uploaded_file_path'])
    assert target.parent == main.task_directory(data['task_id'])
    assert target.read_bytes() == b'original local video'
    assert task['uploaded_filename'] == 'original.mp4'
    assert not (target.parent/'transcript.json').exists()
    assert not (target.parent/'notes.md').exists()
    assert (history/'notes.md').read_text() == '# Previous note'
    (history/'input.mp4').unlink()
    assert target.read_bytes() == b'original local video'


def test_missing_historical_media_requests_reselection(history):
    (history/'input.mp4').unlink()
    response=TestClient(main.app).post('/api/summarize', json=payload())
    assert response.status_code == 404
    assert '重新选择' in response.json()['detail']


@pytest.mark.parametrize('changes,status', [({'source_task_id':'../old'},400), ({'processing_mode':'reuse'},422), ({'video_url':'https://youtu.be/abc'},422)])
def test_rejects_invalid_or_conflicting_source(history,changes,status):
    assert TestClient(main.app).post('/api/summarize',json=payload(**changes)).status_code == status


def test_deleting_source_is_protected(history):
    main.tasks['old']['_deleting']=True
    assert TestClient(main.app).post('/api/summarize',json=payload()).status_code == 409


def test_copy_failure_cleans_only_new_task(history,monkeypatch):
    def fail(*args): raise OSError('simulated copy failure')
    monkeypatch.setattr(task_dependencies, 'preserve_input', fail)
    response=TestClient(main.app,raise_server_exceptions=False).post('/api/summarize',json=payload())
    assert response.status_code == 500
    assert list(main.tasks) == ['old']
    assert list(history.parent.iterdir()) == [history]
    assert (history/'input.mp4').read_bytes() == b'original local video'
