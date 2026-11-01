"""Real task-file contention, interrupted saves and late worker callbacks."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from backend import main, task_runtime, task_storage


def test_threads_merge_metadata_and_runtime_without_lost_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    task = main.new_task('processing', 'merge')
    task['uploaded_filename'] = 'input.mp4'
    task['result'] = {'title': 'result', 'markdown': 'large private body'}
    task['logs'] = [str(i) for i in range(250)]
    monkeypatch.setattr(main, 'tasks', {'merge': task})
    directory = main.task_directory('merge')
    directory.mkdir(parents=True)
    start = threading.Barrier(4)

    def save(kind):
        start.wait(timeout=5)
        for _ in range(12):
            if kind == 'runtime':
                main.persist_task_runtime('merge')
            elif kind == 'metadata':
                main.update_task_manifest('merge', {'title': 'video', 'bili_pages': [{'page': 2}]})
            elif kind == 'request':
                main.write_task_manifest('merge', main.SummarizeRequest(video_url='https://youtu.be/test', llm_config=main.LLMConfig()), None, 'web')
            else:
                main.write_upload_manifest('merge', 'input.mp4')

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, ['runtime', 'metadata', 'request', 'upload']))
    manifest = main.read_task_manifest('merge')
    assert manifest['title'] == 'video'
    assert manifest['uploaded_filename'] == 'input.mp4'
    assert manifest['processing_mode']
    assert manifest['bili_pages'] == [{'page': 2}]
    assert manifest['runtime']['status'] == 'processing'
    assert manifest['runtime']['result'] == {'title': 'result'}
    assert manifest['runtime']['logs'] == [str(i) for i in range(50, 250)]
    assert not list(directory.glob('task.json.*.tmp'))


def test_processes_serialize_whole_read_modify_write_transaction(tmp_path):
    source = '''
from pathlib import Path
import sys
from backend.task_storage import update_manifest
def increment(payload):
    payload['count'] = payload.get('count', 0) + 1
for _ in range(20):
    update_manifest(Path(sys.argv[1]), increment)
'''
    processes = [subprocess.Popen([sys.executable, '-c', source, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(3)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, (stdout, stderr)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert task_storage.read_manifest(tmp_path)['count'] == 60


@pytest.mark.parametrize('remove', [False, True])
def test_waiting_runtime_save_uses_current_task_after_lock(tmp_path, remove):
    task = task_runtime.new_task('processing', 'waiting')
    tasks = {'waiting': task}
    entered = threading.Event()
    release = threading.Event()
    saving = threading.Event()

    def hold(payload):
        payload['title'] = 'keep metadata'
        entered.set()
        assert release.wait(timeout=5)

    def save():
        saving.set()
        task_runtime.persist_task_runtime('waiting', tasks, lambda _: tmp_path, lambda _: 0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(task_storage.update_manifest, tmp_path, hold)
        assert entered.wait(timeout=5)
        waiter = executor.submit(save)
        try:
            assert saving.wait(timeout=5)
            if remove:
                tasks.clear()
            else:
                task.update(status='cancelled', step_name='已取消', progress=61)
        finally:
            release.set()
        holder.result(timeout=5)
        waiter.result(timeout=5)
    manifest = task_storage.read_manifest(tmp_path)
    assert manifest['title'] == 'keep metadata'
    if remove:
        assert 'runtime' not in manifest
    else:
        assert manifest['runtime']['status'] == 'cancelled'
        assert manifest['runtime']['step_name'] == '已取消'


@pytest.mark.parametrize('failure', ['replace', 'serialize'])
def test_failed_save_keeps_old_record_and_cleans_temporary(tmp_path, monkeypatch, failure):
    task_storage.update_manifest(tmp_path, lambda p: p.update(title='old', runtime={'status': 'completed'}))
    before = (tmp_path / 'task.json').read_bytes()
    if failure == 'replace':
        original = Path.replace
        def fail_replace(path, target):
            if path.name.startswith('task.json.') and path.suffix == '.tmp':
                raise OSError('simulated replacement failure')
            return original(path, target)
        monkeypatch.setattr(Path, 'replace', fail_replace)
        expected = OSError
        update = lambda p: p.update(title='new')
    else:
        expected = TypeError
        update = lambda p: p.update(title=object())
    with pytest.raises(expected):
        task_storage.update_manifest(tmp_path, update)
    assert (tmp_path / 'task.json').read_bytes() == before
    assert not list(tmp_path.glob('task.json.*.tmp'))


def test_missing_or_deleted_task_is_not_recreated(tmp_path):
    missing = tmp_path / 'deleted'
    assert task_storage.update_manifest(missing, lambda p: pytest.fail('missing callback')) is False
    assert not missing.exists()
    task_runtime.persist_task_runtime('deleted', {}, lambda _: missing, lambda _: 0)
    assert not missing.exists()


@pytest.mark.parametrize('contents', ['[]', 'null', '{broken'])
def test_legacy_invalid_manifest_can_be_repaired_atomically(tmp_path, contents):
    (tmp_path / 'task.json').write_text(contents, encoding='utf-8')
    task_storage.update_manifest(tmp_path, lambda p: p.update(task_id='legacy'))
    assert task_storage.read_manifest(tmp_path) == {'task_id': 'legacy'}


@pytest.mark.asyncio
@pytest.mark.parametrize('status,flag', [
    ('cancelled', None), ('failed', None), ('completed', None),
    ('cancelling', None), ('processing', '_cancel_requested'), ('processing', '_deleting'),
])
async def test_queued_worker_report_cannot_change_stopped_task(tmp_path, monkeypatch, status, flag):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    task = main.new_task('processing', 'late')
    monkeypatch.setattr(main, 'tasks', {'late': task})
    directory = main.task_directory('late')
    directory.mkdir(parents=True)
    reporter = main.transcribe_progress_reporter(task, 'base')
    reporter(10, 30)  # Queued before cancellation but delivered afterwards.
    task.update(status=status, step=7, step_name='final', progress=100)
    if flag:
        task[flag] = True
    main.persist_task_runtime('late')
    before = dict(task)
    before['logs'] = list(task['logs'])
    stored = task_storage.read_manifest(directory)
    await asyncio.sleep(0)
    assert task == before
    assert task_storage.read_manifest(directory) == stored


def test_active_progress_still_updates_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    task = main.new_task('processing', 'active')
    monkeypatch.setattr(main, 'tasks', {'active': task})
    main.task_directory('active').mkdir(parents=True)
    main.set_progress(task, 4, '语音转写', 38, '正常进度')
    saved = main.read_task_manifest('active')['runtime']
    assert saved['step'] == 4 and saved['progress'] == 38
    assert saved['logs'] == ['正常进度']
