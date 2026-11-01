"""Desktop actions and cleanup are scoped to task outputs and indexed media."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

import launcher
from backend import main, cache_index, storage_usage
from backend.video_cache import cache_lock


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'WHISPER_CACHE_DIR', tmp_path / 'models')
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, '_active_transcriptions', set())
    monkeypatch.setattr(main, '_storage_cleaning', False)
    monkeypatch.setattr(main, 'model_downloads', SimpleNamespace(active=lambda: False))
    monkeypatch.setattr(main, 'environment_repair', SimpleNamespace(snapshot=lambda: {'status':'idle'}))
    return tmp_path, TestClient(main.app)


def cached(workspace, kind, name='fixture', root=None):
    root = root or workspace / kind
    root.mkdir(parents=True, exist_ok=True)
    from backend.audio_cache import source_key
    from backend.video_cache import cache_directory
    source = 'https://youtu.be/' + name
    key = source_key(source) if kind == 'audio' else cache_directory(root, source, 720).name
    path = root / (key + '.m4a' if kind == 'audio' else name + '.mp4')
    path.write_bytes(b'media-' + name.encode())
    cache_index.update_entries(root, kind, {key: {'filename':path.name,
        'source_url':source, 'download_task_id':name, 'resolution':720}}, workspace)
    return path, key


def completed(task_id='note'):
    task_dir = main.task_directory(task_id)
    task_dir.mkdir(parents=True)
    (task_dir / 'notes.md').write_text('# Preserved note', encoding='utf-8')
    main.tasks[task_id] = main.new_task('completed', task_id)
    main.tasks[task_id]['result'] = {'title':'Fixture', 'markdown':'# Preserved note'}
    main.write_task_manifest(task_id, main.SummarizeRequest(video_url='https://youtu.be/fixture', llm_config={}), None, 'web')
    main.persist_task_runtime(task_id)
    return task_dir


def test_usage_preview_and_cleanup_preserve_history_and_unowned_files(app_data, tmp_path):
    workspace, client = app_data
    task = completed()
    (task / 'frames').mkdir()
    image = task / 'frames' / 'shot.jpg'; image.write_bytes(b'original screenshot')
    original = task / 'input.mp4'; original.write_bytes(b'original upload')
    audio, _ = cached(workspace, 'audio')
    external = tmp_path / 'custom-videos'
    video, _ = cached(workspace, 'video', root=external)
    unrelated = external / 'personal.mp4'; unrelated.write_bytes(b'personal video')
    model = workspace / 'models' / 'manual' / 'base'; model.mkdir(parents=True)
    (model / 'model.bin').write_bytes(b'model data')
    data = client.get('/api/storage').json()
    assert data['categories']['audio']['bytes'] == audio.stat().st_size
    assert data['categories']['video']['bytes'] == video.stat().st_size + original.stat().st_size
    assert data['categories']['screenshots']['bytes'] == image.stat().st_size
    assert data['categories']['models']['bytes'] == len(b'model data')
    preview = client.post('/api/storage/preview', json={'kinds':['audio','video']}).json()
    assert preview['count'] == 2 and preview['can_cleanup']
    assert {item['path'] for item in preview['files']} == {str(audio), str(video)}
    result = client.post('/api/storage/cleanup', json={'token':preview['token']})
    assert result.status_code == 200 and result.json()['removed_count'] == 2
    assert not audio.exists() and not video.exists()
    assert image.read_bytes() == b'original screenshot' and original.read_bytes() == b'original upload'
    assert unrelated.read_bytes() == b'personal video' and (model/'model.bin').is_file()
    assert client.get('/api/download/note').status_code == 200
    assert cache_index.read_entries(audio.parent, 'audio', workspace) == {}
    assert cache_index.read_entries(external, 'video', workspace) == {}
    assert client.post('/api/storage/cleanup', json={'token':preview['token']}).status_code == 409


@pytest.mark.parametrize('status', ['pending','queued','processing','cancelling','awaiting_agent'])
def test_running_tasks_block_cleanup(app_data, status):
    workspace, client = app_data
    audio, _ = cached(workspace, 'audio')
    preview = client.post('/api/storage/preview', json={'kinds':['audio']}).json()
    main.tasks['active'] = main.new_task(status, 'active')
    assert client.get('/api/storage').json()['can_cleanup'] is False
    assert client.post('/api/storage/cleanup', json={'token':preview['token']}).status_code == 409
    assert audio.exists()


@pytest.mark.parametrize('flag', ['_deleting','_exporting','_agent_finishing','_agent_auto_running','_agent_capture'])
def test_background_file_activity_blocks_cleanup(app_data, flag):
    workspace, client = app_data
    audio, _ = cached(workspace, 'audio')
    preview = client.post('/api/storage/preview', json={'kinds':['audio']}).json()
    main.tasks['busy'] = {**main.new_task('completed','busy'), flag:True}
    assert client.post('/api/storage/cleanup', json={'token':preview['token']}).status_code == 409
    assert audio.exists()


@pytest.mark.parametrize('change', ['rewrite','symlink','remove_index'])
def test_preview_revalidation_deletes_nothing_if_any_file_changed(app_data, change):
    workspace, client = app_data
    first, _ = cached(workspace, 'audio', 'one')
    other, _ = cached(workspace, 'audio', 'two')
    preview = client.post('/api/storage/preview', json={'kinds':['audio']}).json()
    outside = workspace / 'keep.m4a'; outside.write_bytes(b'keep')
    if change == 'rewrite': first.write_bytes(b'changed after preview')
    elif change == 'remove_index': cache_index.remove_file_entries(workspace, 'audio', {first})
    else:
        first.unlink()
        try: first.symlink_to(outside)
        except OSError: pytest.skip('Symlink creation unavailable')
    response = client.post('/api/storage/cleanup', json={'token':preview['token']})
    assert response.status_code == 409
    assert other.exists() and outside.read_bytes() == b'keep'


def test_hardlinked_cache_does_not_remove_history_or_overstate_reclaimed_space(app_data):
    workspace, client = app_data
    audio, _ = cached(workspace, 'audio')
    task = completed()
    historical = task / 'audio.m4a'
    try: historical.hardlink_to(audio)
    except OSError: pytest.skip('Hard links unavailable')
    usage = client.get('/api/storage').json()
    assert usage['categories']['audio']['bytes'] == audio.stat().st_size
    preview = client.post('/api/storage/preview', json={'kinds':['audio']}).json()
    assert preview['reclaimable_bytes'] == 0
    response = client.post('/api/storage/cleanup', json={'token':preview['token']}).json()
    assert response['reclaimed_bytes'] == 0 and historical.read_bytes().startswith(b'media-')


def test_late_download_worker_lock_blocks_cleanup(app_data):
    workspace, client = app_data
    audio, key = cached(workspace, 'audio')
    preview = client.post('/api/storage/preview', json={'kinds':['audio']}).json()
    with cache_lock(audio.parent / key):
        response = client.post('/api/storage/cleanup', json={'token':preview['token']})
    assert response.status_code == 409 and audio.exists()


def test_expired_or_other_workspace_plan_is_rejected(app_data, monkeypatch):
    workspace, _ = app_data
    audio, _ = cached(workspace, 'audio')
    plans = storage_usage.CleanupPlans()
    preview = plans.preview(main, {'audio'})
    plans.plans[preview['token']]['created'] -= 301
    with pytest.raises(storage_usage.StorageError): plans.execute(main, preview['token'])
    preview = plans.preview(main, {'audio'})
    monkeypatch.setattr(main, 'WORKSPACE_DIR', workspace / 'different')
    with pytest.raises(storage_usage.StorageError): plans.execute(main, preview['token'])
    assert audio.exists()


def test_unowned_index_entries_and_model_directory_links_are_preserved(app_data):
    workspace, client = app_data
    root = workspace / 'video'; root.mkdir()
    personal = root / 'personal.mp4'; personal.write_bytes(b'personal')
    cache_index.update_entries(root, 'video', {'a'*32:{'filename':'personal.mp4', 'download_task_id':'other', 'source_url':'https://youtu.be/1'},
        'b'*32:{'filename':'../personal.mp4','source_url':'https://youtu.be/1'}}, workspace)
    assert client.post('/api/storage/preview', json={'kinds':['video']}).json()['count'] == 0
    assert personal.exists()


@pytest.mark.parametrize('target', ['file','folder'])
def test_output_actions_use_owned_paths_and_fall_back_to_canonical_note(app_data, monkeypatch, target):
    workspace, client = app_data
    directory = completed()
    opened = []
    monkeypatch.setattr(launcher, 'open_in_shell', opened.append)
    assert client.post('/api/task/note/output/open', json={'target':target}).status_code == 200
    assert opened == [directory/'notes.md' if target == 'file' else directory]
    owned = []
    archived = main.archive_note('Title', '# Archived', 'note', created_files=owned)
    main.tasks['note']['result'].update(archived_path=str(archived), note_export_files=[str(p) for p in owned])
    assert client.get('/api/task/note/output-location').json()['file_path'] == str(archived)
    archived.unlink()
    assert client.get('/api/task/note/output-location').json()['file_path'] == str(directory/'notes.md')
    assert client.post('/api/task/note/output/open', json={'target':target,'path':'C:/Windows/system32/cmd.exe'}).status_code == 422
    assert client.post('/api/task/note/output/open', json={'target':'arbitrary'}).status_code == 422
    assert client.post('/api/task/missing/output/open', json={'target':target}).status_code == 404


def test_open_failure_returns_actionable_error(app_data, monkeypatch):
    _, client = app_data
    completed()
    def fail(_): raise OSError('No association')
    monkeypatch.setattr(launcher, 'open_in_shell', fail)
    assert client.post('/api/task/note/output/open', json={'target':'file'}).status_code == 503


def test_unowned_output_path_is_not_opened(app_data, monkeypatch):
    workspace, client = app_data
    directory = completed()
    unrelated = workspace / 'unrelated.md'; unrelated.write_text('private')
    main.tasks['note']['result']['archived_path'] = str(unrelated)
    opened = []
    monkeypatch.setattr(launcher, 'open_in_shell', opened.append)
    assert client.post('/api/task/note/output/open', json={'target':'file'}).status_code == 200
    assert opened == [directory / 'notes.md']


def test_partial_cleanup_failure_preserves_failed_file_index(app_data, monkeypatch):
    workspace, client = app_data
    locked, locked_key = cached(workspace, 'audio', 'locked')
    removable, _ = cached(workspace, 'audio', 'removable')
    preview = client.post('/api/storage/preview', json={'kinds':['audio']}).json()
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == locked: raise PermissionError('File in use')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    result = client.post('/api/storage/cleanup', json={'token':preview['token']}).json()
    assert result['removed_count'] == 1 and result['failed_count'] == 1
    assert locked.exists() and not removable.exists()
    assert set(cache_index.read_entries(locked.parent, 'audio', workspace)) == {locked_key}


@pytest.mark.asyncio
async def test_submission_rechecks_cleanup_after_async_capability_probe(app_data, monkeypatch):
    from backend import video_codecs
    async def capabilities():
        main._storage_cleaning = True
        return {'available': True}
    monkeypatch.setattr(video_codecs, 'capabilities', capabilities)
    request = main.SummarizeRequest(video_url='https://youtu.be/1', include_screenshots=True,
        screenshot_density='medium', video_encoding='size', llm_config={})
    with pytest.raises(main.HTTPException) as error: await main.submit_video_task(request, 'web')
    assert error.value.status_code == 409 and not main.tasks


def test_duplicate_index_ownership_is_not_cleanup_eligible(app_data):
    workspace, client = app_data
    video, _ = cached(workspace, 'video')
    from backend.video_cache import cache_directory
    alternate = cache_directory(video.parent, 'https://youtu.be/alternate', 720).name
    cache_index.update_entries(video.parent, 'video', {alternate: {'filename': video.name,
        'download_task_id':'fixture', 'resolution':720, 'source_url':'https://youtu.be/alternate'}}, workspace)
    assert client.post('/api/storage/preview', json={'kinds':['video']}).json()['count'] == 0
    assert video.exists()


@pytest.mark.asyncio
async def test_cleanup_guard_survives_request_cancellation(app_data, monkeypatch):
    _, _client = app_data
    started = asyncio.Event(); release = threading.Event()
    loop = asyncio.get_running_loop()
    def slow(*args):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return {'removed_count':0,'failed_count':0}
    monkeypatch.setattr(storage_usage.CleanupPlans, 'execute', slow)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1') as client:
        job = asyncio.create_task(client.post('/api/storage/cleanup', json={'token':'a'*24}))
        try:
            await asyncio.wait_for(started.wait(), 1)
            assert main._storage_cleaning
            request = main.SummarizeRequest(video_url='https://youtu.be/1', llm_config={})
            with pytest.raises(main.HTTPException) as error: await main.submit_video_task(request, 'web')
            assert error.value.status_code == 409
            assert (await client.post('/api/upload',files={'file':('test.mp4',b'data','video/mp4')})).status_code == 409
            job.cancel(); await asyncio.sleep(0)
            assert main._storage_cleaning
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 2)
        assert not main._storage_cleaning

