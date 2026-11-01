import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from backend import main, model_storage
from backend.preferences_store import PreferencesStore, PreferencesError, SCHEMA
import launcher


def test_save_merge_keeps_false_zero_empty_and_unknown_fields(tmp_path):
    store = PreferencesStore(tmp_path)
    assert store.load()['exists'] is False
    first = store.save({'schema': 3, 'global': {'use_gpu': False, 'future_option': 0, 'note_save_directory': ''},
                        'ui': {'theme': 'dark'}, 'future_section': {'mode': 'keep'}}, None)
    saved = store.save({'schema': 3, 'global': {'text_detail': 'brief'}, 'ui': {'font_size': 'large'}}, first['revision'])
    assert saved['preferences']['global'] == {'use_gpu': False, 'future_option': 0, 'note_save_directory': '', 'text_detail': 'brief'}
    assert saved['preferences']['ui'] == {'theme': 'dark', 'font_size': 'large'}
    assert saved['preferences']['future_section'] == {'mode': 'keep'}
    assert not (tmp_path / 'settings_backups').exists()


@pytest.mark.parametrize('version', [1, 2])
def test_schema_upgrade_is_idempotent_without_automatic_backups(tmp_path, version):
    old = {'schema': version, 'global': {'use_gpu': False, 'reasoning_effort': 'max'}, 'ui': {'font_size': 'small'}, 'customs': []}
    if version == 1:
        old['theme'] = 'dark'
    original = json.dumps(old, ensure_ascii=False).encode('utf-8')
    path = tmp_path / 'preferences.json'
    path.write_bytes(original)
    store = PreferencesStore(tmp_path)
    loaded = store.load()
    assert loaded['preferences']['schema'] == SCHEMA
    assert loaded['preferences']['ui']['font_size'] == 'compact'
    assert loaded['preferences']['global']['reasoning_effort'] == 'high'
    assert loaded['preferences']['global']['use_gpu'] is False
    assert not (tmp_path / 'settings_backups').exists()
    assert store.load() == loaded
    assert not (tmp_path / 'settings_backups').exists()


@pytest.mark.parametrize('raw', [b'{bad', b'[]', b'null', b'{"schema":999,"preferences":{}}', b'{"schema":3,"preferences":{"schema":999}}'])
def test_corrupt_or_future_file_is_never_overwritten(tmp_path, raw):
    path = tmp_path / 'preferences.json'
    path.write_bytes(raw)
    store = PreferencesStore(tmp_path)
    for operation in [store.load, lambda: store.save({'schema': 3, 'global': {}}, None)]:
        with pytest.raises(PreferencesError):
            operation()
        assert path.read_bytes() == raw
    assert not list(tmp_path.glob('*.tmp'))


def test_first_browser_import_excludes_credentials_without_automatic_backups(tmp_path):
    old = {'schema': 2, 'global': {'use_gpu': True}, 'api_key': 'do-not-copy', 'templates': []}
    store = PreferencesStore(tmp_path)
    saved = store.save(old, None, import_source=old)
    assert saved['preferences']['schema'] == 3
    assert 'api_key' not in saved['preferences']
    assert saved['preferences']['global']['use_gpu'] is True
    assert not (tmp_path / 'settings_backups').exists()


def test_stale_page_cannot_overwrite_another_page(tmp_path):
    store = PreferencesStore(tmp_path)
    initial = store.save({'schema': 3, 'ui': {'theme': 'light'}}, None)
    def save(theme):
        try:
            return PreferencesStore(tmp_path).save({'schema': 3, 'ui': {'theme': theme}}, initial['revision'])
        except PreferencesError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['dark', 'system']))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert 'preferences_changed' in results
    winner = next(item for item in results if isinstance(item, dict))
    assert store.load()['preferences']['ui']['theme'] == winner['preferences']['ui']['theme']


@pytest.mark.parametrize('migration', [False, True])
def test_replacement_failure_preserves_valid_original(tmp_path, monkeypatch, migration):
    path = tmp_path / 'preferences.json'
    store = PreferencesStore(tmp_path)
    if migration:
        path.write_text('{"schema":2,"global":{"use_gpu":false}}', encoding='utf-8')
        original = path.read_bytes()
        operation = store.load
    else:
        packet = store.save({'schema': 3, 'ui': {'theme': 'dark'}}, None)
        original = path.read_bytes()
        operation = lambda: store.save({'schema': 3, 'ui': {'theme': 'light'}}, packet['revision'])
    replace = Path.replace
    def broken(source, target):
        if Path(target) == path:
            raise OSError('simulated disk failure')
        return replace(source, target)
    monkeypatch.setattr(Path, 'replace', broken)
    with pytest.raises(OSError):
        operation()
    assert path.read_bytes() == original
    assert not list(tmp_path.rglob('*.tmp'))
    assert not (tmp_path / 'settings_backups').exists()


def test_moving_whole_app_keeps_internal_paths_and_model_files(tmp_path):
    old = tmp_path / 'old-app'
    workspace = old / 'workspace'
    workspace.mkdir(parents=True)
    internal = old / 'my-notes'
    internal.mkdir()
    external = tmp_path / 'external-models'
    external.mkdir()
    model = workspace / '_model_cache/manual/base/model.bin'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'preserve model')
    (workspace / '.profile_entropy.bin').write_bytes(b'preserve entropy')
    (workspace / 'llm_keys.json').write_bytes(b'preserve credentials')
    model_storage.save_directory(workspace, str(workspace / '_model_cache'))
    PreferencesStore(workspace).save({'schema': 3, 'global': {'note_save_directory': str(internal), 'video_download_directory': str(external)},
        'templates': [{'id': 't', 'name': 'template', 'settings': {'note_save_directory': str(internal)}}]}, None)
    raw = json.loads((workspace / 'preferences.json').read_text(encoding='utf-8'))
    assert raw['preferences']['global']['note_save_directory'] == 'my-notes'
    new = tmp_path / 'moved-app'
    shutil.copytree(old, new)
    new_workspace = new / 'workspace'
    loaded = PreferencesStore(new_workspace).load()['preferences']
    assert loaded['global']['note_save_directory'] == str(new / 'my-notes')
    assert loaded['templates'][0]['settings']['note_save_directory'] == str(new / 'my-notes')
    assert loaded['global']['video_download_directory'] == str(external)
    assert model_storage.load_directory(new_workspace, new_workspace / '_model_cache') == new_workspace / '_model_cache'
    assert (new_workspace / '_model_cache/manual/base/model.bin').read_bytes() == b'preserve model'
    assert (new_workspace / '.profile_entropy.bin').read_bytes() == b'preserve entropy'
    assert (new_workspace / 'llm_keys.json').read_bytes() == b'preserve credentials'


def test_api_initial_import_schema_and_conflict_handling(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    client = TestClient(main.app)
    assert client.get('/api/preferences').json()['exists'] is False
    assert not (tmp_path / 'preferences.json').exists()
    payload = {'client_schema': 3, 'expected_revision': None,
        'preferences': {'schema': 2, 'global': {'use_gpu': False}, 'ui': {'theme': 'dark'}}}
    response = client.put('/api/preferences', json=payload)
    assert response.status_code == 200
    packet = response.json()
    assert client.put('/api/preferences', json=payload).status_code == 409
    assert client.get('/api/preferences').json() == packet
    payload['client_schema'] = 2
    assert client.put('/api/preferences', json=payload).status_code == 422
    raw = (tmp_path / 'preferences.json').read_bytes()
    (tmp_path / 'preferences.json').write_bytes(b'{"schema":999}')
    assert client.get('/api/preferences').json()['detail']['code'] == 'newer_preferences'
    assert client.put('/api/preferences', json={**payload, 'client_schema': 3}).status_code == 409
    assert (tmp_path / 'preferences.json').read_bytes() == b'{"schema":999}'
    assert raw


def test_frozen_workspace_stays_beside_exe_and_does_not_overwrite_probe(tmp_path, monkeypatch):
    monkeypatch.delenv('AI_VIDEO_NOTE_WORKSPACE', raising=False)
    monkeypatch.setattr(launcher, 'is_frozen', lambda: True)
    monkeypatch.setattr(launcher, 'base_dir', lambda: tmp_path)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    probe = workspace / '.write_test'
    probe.write_bytes(b'user-owned file')
    launcher.configure_runtime_dirs()
    assert Path(launcher.os.environ['AI_VIDEO_NOTE_WORKSPACE']) == workspace
    assert probe.read_bytes() == b'user-owned file'


def test_readonly_location_does_not_silently_switch_to_appdata(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.delenv('AI_VIDEO_NOTE_WORKSPACE', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'other-data'))
    monkeypatch.setattr(launcher, 'base_dir', lambda: tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(tempfile, 'TemporaryFile', denied)
    with pytest.raises(RuntimeError, match='数据目录无法写入'):
        launcher.configure_runtime_dirs()
    assert not (tmp_path / 'other-data').exists()


def test_newer_model_location_is_not_reset(tmp_path):
    file = tmp_path / 'model_storage.json'
    original = b'{"schema":999,"directory":"custom-models","extra":"keep"}'
    file.write_bytes(original)
    with pytest.raises(ValueError):
        model_storage.save_directory(tmp_path, '')
    assert file.read_bytes() == original
