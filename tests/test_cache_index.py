import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import audio_cache, cache_index, video_cache
from tests.media_fixture import make_audio, make_av_video

URL = 'https://youtu.be/index-migration'


def old_video(root):
    root.mkdir(parents=True)
    media = root / 'retained.mp4'
    make_av_video(media)
    key = video_cache.cache_directory(root, URL, 720).name
    entry = {'source_url': URL, 'filename': media.name, 'resolution': 720, 'audio_state': 'present', 'platform': 'youtube'}
    (root / 'index.json').write_text(json.dumps({key: entry}), encoding='utf-8')
    return media


def test_video_index_migrates_to_workspace_without_moving_media(tmp_path):
    root = tmp_path / 'video'
    media = old_video(root)
    original = media.read_bytes()
    directory = video_cache.cache_directory(root, URL, 720)
    assert video_cache.find_cached(directory, tmp_path) == media
    assert (tmp_path / 'video_index.json').is_file()
    assert not (root / 'index.json').exists()
    assert media.read_bytes() == original
    assert video_cache.find_cached(directory, tmp_path) == media


def test_audio_index_migrates_to_workspace_without_moving_media(tmp_path):
    root = tmp_path / 'audio'
    root.mkdir()
    media = root / 'retained.m4a'
    make_audio(media)
    (root / 'index.json').write_text(json.dumps({audio_cache.source_key(URL): {'source_url': URL, 'filename': media.name}}))
    original = media.read_bytes()
    assert audio_cache.find_cached(root, URL) == media
    assert (tmp_path / 'audio_index.json').is_file()
    assert not (root / 'index.json').exists()
    assert media.read_bytes() == original


def test_custom_video_roots_share_workspace_index_but_keep_separate_entries(tmp_path):
    workspace = tmp_path / 'workspace'
    roots = [tmp_path / 'videos-one', tmp_path / 'videos-two']
    saved = []
    for root in roots:
        directory = video_cache.cache_directory(root, URL, 720)
        directory.mkdir(parents=True)
        media = directory / 'preview.mp4'
        make_av_video(media)
        saved.append(video_cache.record_cache(directory, media, True, source_url=URL, height=720, workspace=workspace))
    data = json.loads((workspace / 'video_index.json').read_text())
    assert len(data['directories']) == 2
    for root, media in zip(roots, saved):
        assert video_cache.find_cached(video_cache.cache_directory(root, URL, 720), workspace) == media
        assert not (root / 'index.json').exists()
        assert not (root.parent / 'video_index.json').exists()


def test_startup_migrates_known_custom_index_and_leaves_task_json_in_place(tmp_path):
    workspace = tmp_path / 'workspace'
    task = workspace / 'tasks/older-task'
    task.mkdir(parents=True)
    custom = tmp_path / 'custom-videos'
    media = old_video(custom)
    manifest = task / 'task.json'
    manifest.write_text(json.dumps({'video_download_directory': str(custom), 'source_url': URL}), encoding='utf-8')
    original = manifest.read_bytes()
    cache_index.migrate_workspace(workspace)
    assert (workspace / 'video_index.json').is_file()
    assert not (custom / 'index.json').exists()
    assert manifest.read_bytes() == original
    assert media.is_file()


def test_mixed_legacy_index_preserves_both_media_types(tmp_path):
    root = tmp_path / 'shared'
    root.mkdir()
    video_key = video_cache.cache_directory(root, URL, 720).name
    audio_key = audio_cache.source_key(URL)
    entries = {
        video_key: {'filename': 'clip.mp4', 'source_url': URL, 'resolution': 720},
        audio_key: {'filename': 'clip.m4a', 'source_url': URL},
    }
    (root / 'index.json').write_text(json.dumps(entries))
    assert cache_index.read_entries(root, 'video', tmp_path) == {video_key: entries[video_key]}
    assert cache_index.read_entries(root, 'audio', tmp_path) == {audio_key: entries[audio_key]}
    assert not (root / 'index.json').exists()


def test_indexes_keep_working_when_portable_workspace_is_copied(tmp_path):
    workspace = tmp_path / 'original'
    video_root = workspace / 'video'
    old_video(video_root)
    media = video_cache.find_cached(video_cache.cache_directory(video_root, URL, 720), workspace)
    audio_root = workspace / 'audio'
    audio_root.mkdir()
    audio = audio_root / 'source.m4a'
    make_audio(audio)
    audio_cache.publish(audio_root, URL, audio)
    relocated = tmp_path / 'relocated'
    shutil.copytree(workspace, relocated)
    restored_video = video_cache.find_cached(video_cache.cache_directory(relocated / 'video', URL, 720), relocated)
    restored_audio = audio_cache.find_cached(relocated / 'audio', URL)
    assert restored_video == relocated / 'video' / media.name
    assert restored_audio is not None and restored_audio.parent == relocated / 'audio'
    assert audio_cache.has_audio(restored_video) and audio_cache.has_audio(restored_audio)


def test_concurrent_updates_do_not_lose_entries(tmp_path):
    root = tmp_path / 'video'
    def write(index):
        cache_index.update_entries(root, 'video', {f'{index:032x}': {'filename': f'{index}.mp4'}}, tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(24)))
    assert len(cache_index.read_entries(root, 'video', tmp_path)) == 24
    assert not list(tmp_path.glob('*.tmp'))


def test_failed_migration_preserves_original_index(tmp_path, monkeypatch):
    root = tmp_path / 'video'
    old_video(root)
    original = (root / 'index.json').read_bytes()
    def fail(*args): raise OSError('test write failure')
    monkeypatch.setattr(cache_index, '_write', fail)
    with pytest.raises(OSError):
        cache_index.read_entries(root, 'video', tmp_path)
    assert (root / 'index.json').read_bytes() == original


@pytest.mark.parametrize('contents', ['broken JSON', '{"unrecognized":42}'])
def test_unknown_or_damaged_legacy_index_is_preserved(tmp_path, contents):
    root = tmp_path / 'video'
    root.mkdir()
    (root / 'index.json').write_text(contents)
    assert cache_index.read_entries(root, 'video', tmp_path) == {}
    assert (root / 'index.json').read_text() == contents
