from datetime import date, timedelta
import os
import pytest
from fastapi.testclient import TestClient
from backend import log_retention, main


def test_default_and_invalid_settings(tmp_path):
    assert log_retention.load_days(tmp_path) == 2
    for data in ('bad json', '{"days":true}', '{"days":0}', '{"days":366}'):
        (tmp_path / '_log_settings.json').write_text(data)
        assert log_retention.load_days(tmp_path) == 2


def test_cleanup_boundary_and_preserves_unrelated_files(tmp_path):
    folder = tmp_path / 'logs'
    folder.mkdir()
    today = date(2026, 10, 14)
    for offset in range(7):
        (folder / f'app-{today - timedelta(days=offset)}.log').write_text(str(offset))
    other = folder / 'user-notes.log'
    other.write_text('keep')
    task = tmp_path / 'tasks' / 'abc' / 'task.json'
    task.parent.mkdir(parents=True)
    task.write_text('task history')
    log_retention.clean_archives(tmp_path, 5, today)
    assert len(list(folder.glob('app-*.log'))) == 5
    assert (folder / 'app-2026-10-10.log').exists()
    assert not (folder / 'app-2026-10-09.log').exists()
    assert task.read_text() == 'task history' and other.read_text() == 'keep'


def test_daily_rotation_restart_and_live_setting_cleanup(tmp_path, monkeypatch):
    today = date.today()
    old = tmp_path / '_app.log'
    old.write_text('yesterday\n')
    yesterday = today - timedelta(days=1)
    stamp = __import__('datetime').datetime.combine(yesterday, __import__('datetime').time(12)).timestamp()
    os.utime(old, (stamp, stamp))
    stream = log_retention.open_log_stream(tmp_path)
    try:
        stream.write('today\n')
        stream.flush()
        archive = tmp_path / 'logs' / f'app-{yesterday}.log'
        assert archive.read_text() == 'yesterday\n'
        assert old.read_text() == 'today\n'
        log_retention.save_days(tmp_path, 1)
        assert not archive.exists()
        assert log_retention.load_days(tmp_path) == 1
    finally:
        stream.close()
    stream = log_retention.open_log_stream(tmp_path)
    try:
        stream.write('restart\n'); stream.flush()
        assert old.read_text() == 'today\nrestart\n'
        class Tomorrow(date):
            @classmethod
            def today(cls): return today + timedelta(days=1)
        monkeypatch.setattr(log_retention, 'date', Tomorrow)
        stream.write('next day\n'); stream.flush()
        assert old.read_text() == 'next day\n'
        assert not (tmp_path / 'logs' / f'app-{today}.log').exists()
    finally:
        stream.close()


def test_settings_api_persisted_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    client = TestClient(main.app)
    assert client.get('/api/log-settings').json() == {'days': 2}
    assert client.put('/api/log-settings', json={'days': 7}).json() == {'days': 7}
    assert client.get('/api/log-settings').json() == {'days': 7}
    for value in (0, 366, 1.5, True, '5'):
        assert client.put('/api/log-settings', json={'days': value}).status_code == 422
    assert log_retention.load_days(tmp_path) == 7


def test_locked_log_rotation_keeps_writing_and_retries(tmp_path, monkeypatch):
    from pathlib import Path
    today = date.today()
    stream = log_retention.open_log_stream(tmp_path)
    try:
        stream.write('first\n'); stream.flush()
        class Tomorrow(date):
            @classmethod
            def today(cls): return today + timedelta(days=1)
        monkeypatch.setattr(log_retention, 'date', Tomorrow)
        rename = Path.rename
        def blocked(*args): raise PermissionError('native process holds log')
        monkeypatch.setattr(Path, 'rename', blocked)
        stream.write('while locked\n'); stream.flush()
        assert (tmp_path / '_app.log').read_text() == 'first\nwhile locked\n'
        monkeypatch.setattr(Path, 'rename', rename)
        stream.write('after unlock\n'); stream.flush()
        assert (tmp_path / '_app.log').read_text() == 'after unlock\n'
        assert (tmp_path / 'logs' / f'app-{today}.log').read_text() == 'first\nwhile locked\n'
    finally:
        stream.close()
