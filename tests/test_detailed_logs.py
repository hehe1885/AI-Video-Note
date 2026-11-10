import asyncio
from datetime import date, timedelta
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend import detailed_logs as detail, log_retention


def records(root):
    return [json.loads(line) for path in (root/'logs').glob('detail-*.jsonl') for line in path.read_text(encoding='utf-8').splitlines()]


def test_jsonl_contains_correlation_fields_and_full_redacted_traceback(tmp_path):
    secret = 'arbitrary-private-value'
    with detail.bind('task-a', tmp_path):
        try:
            raise RuntimeError('Authorization: Bearer abc123secret api_key='+secret)
        except RuntimeError as error:
            detail.event('task.error', stage='ASR', error=error, secrets=(secret,), requested={'api_key':secret,'cookie':'session-cookie','model':'turbo'})
    row, = records(tmp_path)
    assert row['task_id'] == 'task-a'
    assert row['pid'] > 0 and row['parent_pid'] > 0 and row['thread_id']
    assert row['timestamp'].endswith('+00:00')
    assert row['requested']['model'] == 'turbo'
    assert row['requested']['api_key'] == '[REDACTED]'
    assert row['error']['type'] == 'RuntimeError'
    assert 'raise RuntimeError' in row['error']['traceback']
    text = json.dumps(row)
    for value in [secret, 'abc123secret', 'session-cookie']:
        assert value not in text


@pytest.mark.asyncio
async def test_task_context_crosses_threads_without_leaking_between_tasks(tmp_path):
    async def task(task_id):
        with detail.bind(task_id, tmp_path):
            await asyncio.to_thread(detail.event, 'thread.event', model=task_id)
    await asyncio.gather(task('a'), task('b'))
    assert sorted((r['task_id'],r['model']) for r in records(tmp_path)) == [('a','a'),('b','b')]


def test_concurrent_writes_are_complete_json_records(tmp_path):
    def write(index): detail.event('parallel', workspace=tmp_path, index=index)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(100)))
    assert sorted(r['index'] for r in records(tmp_path)) == list(range(100))


def test_logging_failure_never_breaks_task(tmp_path, monkeypatch):
    def fail(*args, **kwargs): raise OSError('disk full')
    monkeypatch.setattr(Path, 'open', fail)
    detail.event('test', workspace=tmp_path)


def test_detail_logs_follow_existing_retention_but_unrelated_files_remain(tmp_path):
    folder = tmp_path/'logs'; folder.mkdir()
    today = date.today()
    old = folder/f'detail-{today-timedelta(days=4)}-123.jsonl'
    current = folder/f'detail-{today}-123.jsonl'
    personal = folder/'personal.jsonl'
    for path in (old,current,personal): path.write_text('keep')
    log_retention.clean_archives(tmp_path,3)
    assert not old.exists()
    assert current.exists() and personal.exists()


def test_environment_snapshot_is_written_once_per_process(tmp_path):
    detail.startup(tmp_path, '1.0.2')
    detail.startup(tmp_path, '1.0.2')
    row, = records(tmp_path)
    assert row['event'] == 'runtime.start'
    assert row['app_version'] == '1.0.2' and row['python']
    assert 'ctranslate2' in row['packages']


def test_stage_timing_only_records_transitions(tmp_path):
    task = {'_task_id':'a'}
    with detail.bind('a',tmp_path):
        detail.progress(task,3,'audio',25)
        detail.progress(task,3,'audio',26)
        detail.progress(task,4,'ASR',38)
    rows = records(tmp_path)
    assert [r['event'] for r in rows] == ['stage.start','stage.end','stage.start']
    assert rows[1]['elapsed_seconds'] >= 0


def test_retention_failure_does_not_prevent_startup(tmp_path, monkeypatch):
    def fail(*args): raise PermissionError('log folder locked')
    monkeypatch.setattr(log_retention, 'clean_archives', fail)
    detail.startup(tmp_path, '1.0.2')
    assert records(tmp_path)[0]['event'] == 'runtime.start'
