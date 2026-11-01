from types import SimpleNamespace
import sys
import pytest
from fastapi.testclient import TestClient
from backend import main
import launcher


@pytest.fixture
def registry(monkeypatch, tmp_path):
    values, writes = {}, []
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def read(key, name):
        if name not in values: raise FileNotFoundError(name)
        return values[name], 1
    def write(key, name, reserved, kind, value):
        writes.append(name)
        values[name] = value
    def delete(key, name):
        if name not in values: raise FileNotFoundError(name)
        writes.append(name)
        values.pop(name)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(
        HKEY_CURRENT_USER=object(), REG_SZ=1, OpenKey=lambda *args: Key(),
        CreateKey=lambda *args: Key(), QueryValueEx=read, SetValueEx=write, DeleteValue=delete))
    monkeypatch.setattr(launcher.sys, 'platform', 'win32')
    monkeypatch.setattr(launcher, 'base_dir', lambda: tmp_path / 'application with spaces')
    monkeypatch.setattr(launcher, 'is_frozen', lambda: False)
    return values, writes


def test_startup_status_reads_existing_setting_without_changing_registry(registry):
    values, writes = registry
    client = TestClient(main.app)
    assert client.get('/api/autostart').json() == {'supported': True, 'enabled': False}
    values[launcher.autostart_name()] = launcher.subprocess.list2cmdline(launcher.launcher_command())
    assert client.get('/api/autostart').json() == {'supported': True, 'enabled': True}
    assert writes == []
    assert '--no-browser' not in values[launcher.autostart_name()]


def test_startup_toggle_sets_quiet_command_and_deletes_only_own_entry(registry):
    values, writes = registry
    values['Other app'] = 'unrelated.exe'
    client = TestClient(main.app)
    assert client.put('/api/autostart', json={'enabled': True}).json() == {'supported': True, 'enabled': True}
    assert '--no-browser' in values[launcher.autostart_name()]
    assert client.put('/api/autostart', json={'enabled': False}).json() == {'supported': True, 'enabled': False}
    assert values == {'Other app': 'unrelated.exe'}
    for value in ('true', 1, None):
        assert client.put('/api/autostart', json={'enabled': value}).status_code == 422
    assert writes == [launcher.autostart_name(), launcher.autostart_name()]


def test_startup_unavailable_on_other_systems(monkeypatch):
    monkeypatch.setattr(launcher, 'sys', SimpleNamespace(platform='linux'))
    client = TestClient(main.app)
    assert client.get('/api/autostart').json() == {'supported': False, 'enabled': False}
    assert client.put('/api/autostart', json={'enabled': True}).status_code == 400


def test_startup_registry_failures_return_readable_errors(registry, monkeypatch):
    def denied(*args): raise PermissionError('blocked')
    monkeypatch.setattr(sys.modules['winreg'], 'OpenKey', denied)
    assert TestClient(main.app).get('/api/autostart').status_code == 503
    monkeypatch.setattr(launcher, 'set_autostart', denied)
    assert TestClient(main.app).put('/api/autostart', json={'enabled': True}).status_code == 503


def test_log_icon_opens_only_current_workspace_log(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    opened = []
    monkeypatch.setattr(launcher, 'open_in_shell', opened.append)
    client = TestClient(main.app)
    assert client.post('/api/log/open').status_code == 404
    assert opened == []
    log = tmp_path / '_app.log'
    log.write_text('fixture runtime log')
    assert client.post('/api/log/open', json={'path': 'other-file.txt'}).json() == {'opened': True}
    assert opened == [log]
    def fail(path): raise OSError('file association unavailable')
    monkeypatch.setattr(launcher, 'open_in_shell', fail)
    assert client.post('/api/log/open').status_code == 503
