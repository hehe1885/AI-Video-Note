"""Local uploads survive deleting an ancestor, including after restart/history eviction."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException
import pytest

from backend import main, task_dependencies
from backend.note_review import find_media
from backend.video_processor import VideoProcessor


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'video_processor', VideoProcessor(tmp_path))
    return tmp_path


def make_task(identifier, previous=None, status='completed'):
    directory = main.task_directory(identifier)
    directory.mkdir(parents=True)
    (directory / 'notes.md').write_text('# Saved note', encoding='utf-8')
    (directory / 'task.json').write_text(json.dumps({
        'task_id': identifier, 'source_url': None, 'uploaded_filename': 'sample.wav',
        'reused_task_id': previous, 'runtime': {'status': status, 'result': {'title': identifier}},
    }), encoding='utf-8')
    return directory


@pytest.mark.asyncio
@pytest.mark.parametrize('loaded', [False, True])
async def test_delete_after_restart_preserves_unloaded_and_transitive_inputs(workspace, loaded):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'synthetic source audio')
    make_task('middle', 'source')
    leaf = make_task('leaf', 'middle')
    if loaded:
        main.restore_tasks_from_workspace()
    await main.delete_task('source')
    assert not source.exists()
    assert (leaf / 'notes.md').read_text(encoding='utf-8') == '# Saved note'
    for identifier in ['middle', 'leaf']:
        media = find_media(identifier, workspace, main.tasks, main.read_task_manifest)
        assert media == main.task_directory(identifier) / 'input.wav'
        assert media.read_bytes() == b'synthetic source audio'
    # A second restart must discover the preserved files without memory pointers.
    main.tasks.clear()
    main.restore_tasks_from_workspace()
    assert find_media('leaf', workspace, main.tasks, main.read_task_manifest) == leaf / 'input.wav'
    await main.delete_task('middle')
    assert (leaf / 'input.wav').read_bytes() == b'synthetic source audio'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['pending', 'processing', 'awaiting_agent'])
async def test_live_transitive_dependency_blocks_deletion(workspace, status):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'audio')
    make_task('middle', 'source')
    make_task('active', 'middle')
    main.restore_tasks_from_workspace()
    main.tasks['active']['status'] = status
    with pytest.raises(HTTPException) as error:
        await main.delete_task('source')
    assert error.value.status_code == 409
    assert source.read_bytes() == b'audio'
    assert not (main.task_directory('middle') / 'input.wav').exists()


@pytest.mark.asyncio
async def test_unloaded_waiting_agent_blocks_source_deletion(workspace):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'audio')
    waiting = make_task('waiting', 'source', status='awaiting_agent')
    (waiting / 'agent_job.json').write_text('{}')
    with pytest.raises(HTTPException) as error:
        await main.delete_task('source')
    assert error.value.status_code == 409 and source.exists()


@pytest.mark.asyncio
async def test_interrupted_unloaded_dependency_can_keep_input_without_blocking(workspace):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'audio')
    other = make_task('interrupted', 'source', status='processing')
    await main.delete_task('source')
    assert (other / 'input.wav').read_bytes() == b'audio'


@pytest.mark.asyncio
async def test_cross_device_copy_fallback(workspace, monkeypatch):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'audio')
    other = make_task('other', 'source')
    def no_link(*args):
        raise OSError('hard links unavailable')
    monkeypatch.setattr(task_dependencies.os, 'link', no_link)
    await main.delete_task('source')
    assert (other / 'input.wav').read_bytes() == b'audio'


@pytest.mark.asyncio
async def test_failed_preservation_keeps_source_and_removes_partial_copy(workspace, monkeypatch):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'audio')
    other = make_task('other', 'source')
    def no_link(*args):
        raise OSError('hard links unavailable')
    def broken_copy(original, output):
        output.write(b'partial')
        raise OSError('disk full')
    monkeypatch.setattr(task_dependencies.os, 'link', no_link)
    monkeypatch.setattr(task_dependencies.shutil, 'copyfileobj', broken_copy)
    with pytest.raises(HTTPException) as error:
        await main.delete_task('source')
    assert error.value.status_code == 409
    assert source.read_bytes() == b'audio'
    assert not (other / 'input.wav').exists()
    assert main.task_directory('source').is_dir()


@pytest.mark.asyncio
async def test_independent_upload_is_never_overwritten(workspace):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'source audio')
    other = make_task('other', 'source') / 'input.wav'
    other.write_bytes(b'independent audio')
    await main.delete_task('source')
    assert other.read_bytes() == b'independent audio'


@pytest.mark.asyncio
@pytest.mark.parametrize('loaded', [False, True])
async def test_deleting_middle_task_keeps_descendant_media(workspace, loaded):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'original audio')
    make_task('middle', 'source')
    leaf = make_task('leaf', 'middle')
    if loaded:
        main.restore_tasks_from_workspace()
    await main.delete_task('middle')
    assert source.read_bytes() == b'original audio'
    assert find_media('leaf', workspace, main.tasks, main.read_task_manifest) == leaf / 'input.wav'
    assert (leaf / 'input.wav').read_bytes() == b'original audio'


@pytest.mark.asyncio
async def test_live_dependency_on_middle_task_blocks_deletion(workspace):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'audio')
    make_task('middle', 'source')
    make_task('leaf', 'middle')
    main.restore_tasks_from_workspace()
    main.tasks['leaf']['status'] = 'processing'
    with pytest.raises(HTTPException) as error:
        await main.delete_task('middle')
    assert error.value.status_code == 409
    assert source.exists() and main.task_directory('middle').is_dir()


@pytest.mark.asyncio
async def test_killed_copy_never_publishes_partial_input(workspace):
    source = make_task('source') / 'input.wav'
    original = b'complete media bytes'
    source.write_bytes(original)
    other = make_task('other', 'source')
    program = '''
import os, sys
from pathlib import Path
from backend import main, task_dependencies
main.WORKSPACE_DIR = Path(sys.argv[1])
main.tasks = {}; main.running_jobs = {}
def no_link(*args): raise OSError('hard links unavailable')
def die_during_copy(original, output):
    output.write(original.read(4)); output.flush(); os.fsync(output.fileno()); os._exit(17)
task_dependencies.os.link = no_link
task_dependencies.shutil.copyfileobj = die_during_copy
task_dependencies.preserve_reused_inputs(main, 'source')
'''
    environment = {**os.environ, 'AI_VIDEO_NOTE_WORKSPACE': str(workspace), 'PYTHONDONTWRITEBYTECODE': '1'}
    child = subprocess.run([sys.executable, '-c', program, str(workspace)],
                           cwd=Path(main.__file__).parents[1], env=environment, capture_output=True, timeout=30)
    assert child.returncode == 17, child.stderr.decode(errors='replace')
    assert not (other / 'input.wav').exists()
    assert source.read_bytes() == original
    await main.delete_task('source')
    assert (other / 'input.wav').read_bytes() == original


@pytest.mark.asyncio
async def test_silent_short_copy_is_rejected(workspace, monkeypatch):
    source = make_task('source') / 'input.wav'
    source.write_bytes(b'complete media bytes')
    other = make_task('other', 'source')
    def no_link(*args): raise OSError('hard links unavailable')
    def short_copy(original, output): output.write(original.read(4))
    monkeypatch.setattr(task_dependencies.os, 'link', no_link)
    monkeypatch.setattr(task_dependencies.shutil, 'copyfileobj', short_copy)
    with pytest.raises(HTTPException) as error:
        await main.delete_task('source')
    assert error.value.status_code == 409 and source.exists()
    assert not (other / 'input.wav').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('loaded', [False, True])
async def test_preserved_video_paths_and_http_playback_survive_restart(workspace, loaded):
    from fastapi.testclient import TestClient
    from tests.media_fixture import make_av_video
    source = make_task('source') / 'input.mp4'
    make_av_video(source)
    expected = source.read_bytes()
    other = make_task('other', 'source')
    main.task_storage.update_manifest(other, lambda payload: payload['runtime'].update(
        video_path=str(source), result={'title': 'other', 'video_path': str(source)}))
    if loaded:
        main.restore_tasks_from_workspace()
    await main.delete_task('source')
    target = other / 'input.mp4'
    saved = main.read_task_manifest('other')['runtime']
    assert saved['video_path'] == str(target) and saved['result']['video_path'] == str(target)
    main.tasks.clear()
    main.restore_tasks_from_workspace()
    assert main.tasks['other']['video_path'] == str(target)
    assert main.tasks['other']['result']['video_path'] == str(target)
    client = TestClient(main.app)
    response = client.get('/api/task/other/media', headers={'range': 'bytes=0-31'})
    assert response.status_code == 206 and response.content == expected[:32]
