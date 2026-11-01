import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from backend import main, obsidian_export
from backend.video_processor import VideoProcessor
from backend import audio_cache, video_cache
from tests.media_fixture import make_audio, make_av_video


def setup(root, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', root / 'workspace')
    main.WORKSPACE_DIR.mkdir()
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'video_processor', VideoProcessor(main.WORKSPACE_DIR))


def task(task_id, status='completed', result=None):
    directory = main.WORKSPACE_DIR / task_id
    directory.mkdir()
    (directory / 'task.json').write_text(json.dumps({'task_id': task_id}))
    main.tasks[task_id] = main.new_task(status, task_id)
    main.tasks[task_id]['result'] = result or {}
    return directory


@pytest.mark.asyncio
@pytest.mark.parametrize('tracked', [False, True])
async def test_delete_completed_cleans_exports_without_touching_user_files(tmp_path, monkeypatch, tracked):
    setup(tmp_path, monkeypatch)
    directory = task('finished')
    image = directory / 'shot.jpg'
    image.write_bytes(b'image')
    notes = main.WORKSPACE_DIR / 'notes'
    notes.mkdir()
    archive = notes / 'note.md'
    archive.write_text('note')
    vault = tmp_path / 'vault'
    vault.mkdir()
    owned = []
    note = obsidian_export.export_note('![shot](./frames/shot.jpg)', 'note', [image], vault=str(vault), created_files=owned)
    keep = vault / 'keep.jpg'
    keep.write_bytes(b'user image')
    with note.open('a') as file: file.write('\n![keep](keep.jpg)')
    download = tmp_path / 'downloads' / 'finished'
    download.mkdir(parents=True)
    video = download / 'preview.mp4'
    video.write_bytes(b'video')
    user_file = download / 'keep.txt'
    user_file.write_text('user')
    main.tasks['finished']['result'] = dict(archived_path=str(archive), obsidian_path=str(note), video_path=str(video))
    if tracked:
        main.tasks['finished']['result']['obsidian_files'] = list(map(str, owned))
        # Explicit ownership tracking still cleans images after a user edits the note.
        note.write_text('edited')
    assert await main.delete_task('finished') == {'deleted': True}
    assert 'finished' not in main.tasks
    assert not directory.exists()
    assert not archive.exists() and not note.exists() and not video.exists()
    assert all(not path.exists() for path in owned)
    assert keep.exists() and user_file.exists() and vault.exists()


@pytest.mark.asyncio
async def test_bulk_reports_running_and_missing_without_hiding_success(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('done')
    task('active', 'processing')
    result = await main.delete_tasks(main.DeleteTasksRequest(task_ids=['done', 'active', 'missing', 'done']))
    assert result['deleted'] == ['done']
    assert [item['task_id'] for item in result['failed']] == ['active', 'missing']
    assert (main.WORKSPACE_DIR / 'active').exists()


@pytest.mark.asyncio
async def test_active_resume_protects_source_then_stopped_input_is_preserved(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    source_dir = task('source')
    source = source_dir / 'input.wav'
    source.write_bytes(b'audio')
    other_dir = task('other', 'processing')
    main.tasks['other'].update(resume_task_id='source', uploaded_file_path=str(source))
    with pytest.raises(HTTPException) as error: await main.delete_task('source')
    assert error.value.status_code == 409
    assert source.exists()
    main.tasks['other']['status'] = 'completed'
    await main.delete_task('source')
    assert (other_dir / 'input.wav').read_bytes() == b'audio'
    assert main.tasks['other']['uploaded_file_path'] == str(other_dir / 'input.wav')


@pytest.mark.asyncio
async def test_shared_export_and_unrelated_manifest_path_are_preserved(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    notes = main.WORKSPACE_DIR / 'notes'
    notes.mkdir()
    shared = notes / 'shared.md'
    shared.write_text('shared')
    task('one', result={'archived_path': str(shared)})
    task('two', result={'archived_path': str(shared)})
    await main.delete_task('one')
    assert shared.exists()
    outside = tmp_path / 'important.md'
    outside.write_text('important')
    main.tasks['two']['result'].update(archived_path=str(outside), obsidian_path=str(outside), video_path=str(outside))
    await main.delete_task('two')
    assert outside.read_text() == 'important'


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_task_for_retry(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('locked')
    def locked(*args): raise PermissionError('file in use')
    monkeypatch.setattr(main, 'unlink_exports', locked)
    with pytest.raises(HTTPException) as error: await main.delete_task('locked')
    assert error.value.status_code == 409
    assert 'locked' in main.tasks
    assert not main.tasks['locked'].get('_deleting')


@pytest.mark.asyncio
async def test_failed_custom_download_parts_are_removed(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    directory = task('download-failed', 'failed')
    custom = tmp_path / 'custom'
    folder = custom / 'download-failed'
    folder.mkdir(parents=True)
    partial = folder / 'preview.mp4.part'
    partial.write_bytes(b'partial download')
    (directory / 'task.json').write_text(json.dumps({'task_id': 'download-failed', 'video_download_directory': str(custom)}))
    await main.delete_task('download-failed')
    assert not partial.exists() and not folder.exists()
    assert custom.exists()


@pytest.mark.asyncio
async def test_partial_folder_cleanup_failure_restores_manifest(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    directory = task('locked-folder')
    (directory / 'task.json').write_text(json.dumps({'task_id': 'locked-folder', 'title': 'Retained title'}))
    async def partial(*args):
        (directory / 'task.json').unlink()
        raise PermissionError('in use')
    monkeypatch.setattr(main.video_processor, 'cleanup', partial)
    with pytest.raises(HTTPException): await main.delete_task('locked-folder')
    saved = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
    assert saved['title'] == 'Retained title'
    assert saved['runtime']['status'] == 'completed'


def cached_video(task_id, root=None):
    root = root or main.WORKSPACE_DIR / 'video'
    url = 'https://youtu.be/delete-associated-video'
    directory = video_cache.cache_directory(root, url, 720)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'preview.mp4'
    make_av_video(source)
    video = video_cache.record_cache(directory, source, True, source_url=url, storage_id=task_id, workspace=main.WORKSPACE_DIR)
    return video, directory


@pytest.mark.asyncio
@pytest.mark.parametrize('custom', [False, True])
async def test_delete_history_removes_cached_video_and_index_but_preserves_user_files(tmp_path, monkeypatch, custom):
    setup(tmp_path, monkeypatch)
    directory = task('owner')
    root = tmp_path / 'custom-videos' if custom else main.WORKSPACE_DIR / 'video'
    video, cache_dir = cached_video('owner', root)
    unrelated = root / 'personal-video.mp4'
    unrelated.write_bytes(b'user-owned video')
    main.tasks['owner']['result']['video_path'] = str(video)
    if custom:
        (directory / 'task.json').write_text(json.dumps({'task_id': 'owner', 'video_download_directory': str(root)}))
    audio_root = main.WORKSPACE_DIR / 'audio'
    audio_root.mkdir()
    audio = audio_root / 'source.m4a'
    make_audio(audio)
    shared_audio = audio_cache.publish(audio_root, 'https://youtu.be/delete-associated-video', audio)
    await main.delete_task('owner')
    assert not video.exists()
    assert cache_dir.name not in video_cache.read_index(root, main.WORKSPACE_DIR)
    assert unrelated.read_bytes() == b'user-owned video'
    assert root.is_dir()
    assert shared_audio.is_file()


@pytest.mark.asyncio
async def test_deleting_reuse_history_deletes_video_even_when_other_finished_task_uses_it(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('owner')
    other = task('reuse')
    video, cache_dir = cached_video('owner')
    main.tasks['owner']['result']['video_path'] = str(video)
    main.tasks['reuse']['result']['video_path'] = str(video)
    (other / 'notes.md').write_text('Retained only until this history is deleted')
    await main.delete_task('reuse')
    assert not video.exists()
    assert 'owner' in main.tasks and 'reuse' not in main.tasks
    assert main.task_directory('owner').is_dir()
    assert video_cache.find_cached(cache_dir, main.WORKSPACE_DIR) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['processing', 'queued', 'awaiting_agent'])
async def test_active_video_reuse_blocks_delete_until_other_task_stops(tmp_path, monkeypatch, status):
    setup(tmp_path, monkeypatch)
    task('owner')
    task('active', status=status)
    video, cache_dir = cached_video('owner')
    main.tasks['owner']['result']['video_path'] = str(video)
    main.tasks['active']['video_path'] = str(video)
    with pytest.raises(HTTPException) as error:
        await main.delete_task('owner')
    assert error.value.status_code == 409
    assert video.is_file()
    assert cache_dir.name in video_cache.read_index(video.parent, main.WORKSPACE_DIR)
    main.tasks['active']['status'] = 'cancelled'
    await main.delete_task('owner')
    assert not video.exists()


@pytest.mark.asyncio
async def test_pending_same_source_blocks_deletion_before_video_path_is_attached(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    source = task('owner')
    active = task('pending', status='pending')
    video, _ = cached_video('owner')
    main.tasks['owner']['result']['video_path'] = str(video)
    for directory in [source, active]:
        (directory / 'task.json').write_text(json.dumps({'source_url': 'https://youtu.be/delete-associated-video'}))
    with pytest.raises(HTTPException) as error:
        await main.delete_task('owner')
    assert error.value.status_code == 409 and video.exists()


@pytest.mark.asyncio
async def test_failed_task_without_result_path_cleans_video_it_downloaded(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('owner', status='failed')
    video, cache_dir = cached_video('owner')
    await main.delete_task('owner')
    assert not video.exists()
    assert video_cache.find_cached(cache_dir, main.WORKSPACE_DIR) is None


@pytest.mark.asyncio
async def test_missing_index_still_allows_exact_task_owned_video_cleanup(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('owner')
    video, _ = cached_video('owner')
    main.tasks['owner']['result']['video_path'] = str(video)
    (main.WORKSPACE_DIR / 'video_index.json').unlink()
    await main.delete_task('owner')
    assert not video.exists()


@pytest.mark.asyncio
async def test_unindexed_unrelated_video_in_custom_directory_is_not_deleted(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    directory = task('owner')
    root = tmp_path / 'custom'
    root.mkdir()
    video = root / 'personal-video.mp4'
    video.write_bytes(b'personal')
    (directory / 'task.json').write_text(json.dumps({'video_download_directory': str(root)}))
    main.tasks['owner']['result']['video_path'] = str(video)
    await main.delete_task('owner')
    assert video.read_bytes() == b'personal'


@pytest.mark.asyncio
async def test_video_unlink_failure_keeps_history_and_cache_entry_for_retry(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('owner')
    video, cache_dir = cached_video('owner')
    main.tasks['owner']['result']['video_path'] = str(video)
    original = Path.unlink
    def locked(path, *args, **kwargs):
        if path == video:
            raise PermissionError('video is open')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    with pytest.raises(HTTPException) as error:
        await main.delete_task('owner')
    assert error.value.status_code == 409
    assert 'owner' in main.tasks and not main.tasks['owner'].get('_deleting')
    assert video.is_file()
    assert cache_dir.name in video_cache.read_index(video.parent, main.WORKSPACE_DIR)


@pytest.mark.asyncio
async def test_batch_delete_shared_video_is_idempotent(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    task('owner')
    task('reuse')
    video, _ = cached_video('owner')
    for identifier in ['owner', 'reuse']:
        main.tasks[identifier]['result']['video_path'] = str(video)
    result = await main.delete_tasks(main.DeleteTasksRequest(task_ids=['owner', 'reuse']))
    assert result['deleted'] == ['owner', 'reuse'] and result['failed'] == []
    assert not video.exists()
