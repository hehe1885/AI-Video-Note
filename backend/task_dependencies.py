"""Keep local uploads available to surviving tasks, including unloaded history."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid

from filelock import FileLock

from . import task_storage
from .note_review import MEDIA_SUFFIXES
from .task_recovery import recovered_status


def _valid_input(path):
    return (path.stem == 'input' and path.suffix.lower() in MEDIA_SUFFIXES
            and path.resolve() == path.absolute() and path.is_file() and path.stat().st_size > 0)


def _input_source(runtime, task_id, manifest):
    """Return the actual upload and every task needed to reach it, without a depth limit."""
    visited, legacy = set(), []
    current = task_id
    while current and current not in visited:
        try:
            directory = runtime.task_directory(current)
        except ValueError:
            break
        visited.add(current)
        task = runtime.tasks.get(current, {})
        for candidate in sorted(directory.glob('input.*')):
            if _valid_input(candidate):
                return candidate, visited
        if task.get('uploaded_file_path'):
            legacy.append(Path(task['uploaded_file_path']))
        current = task.get('resume_task_id') or manifest(current).get('reused_task_id')
    for candidate in legacy:
        if _valid_input(candidate):
            return candidate, visited
    return None, visited


def _same_path(value, path):
    return isinstance(value, str) and Path(value).resolve() == path.resolve()


def _record_input(runtime, task_id, source, target):
    def repoint(record):
        if _same_path(record.get('video_path'), source):
            record['video_path'] = str(target)
        result = record.get('result')
        if isinstance(result, dict) and _same_path(result.get('video_path'), source):
            result['video_path'] = str(target)

    def update(payload):
        saved = payload.get('runtime')
        if isinstance(saved, dict):
            repoint(saved)
    if not task_storage.update_manifest(runtime.task_directory(task_id), update):
        raise OSError('复用任务已不存在')
    if task_id in runtime.tasks:
        task = runtime.tasks[task_id]
        task['uploaded_file_path'] = str(target)
        repoint(task)


def _preserve(source, target):
    """Only publish a complete input.*; a killed copier leaves an ignored temporary file."""
    with FileLock(str(target.parent / '.input-preserve.lock'), timeout=10):
        if target.exists() or target.is_symlink():
            raise OSError('复用任务已有同名素材，未覆盖文件')
        try:
            os.link(source, target)
            return
        except FileExistsError:
            raise OSError('复用任务已有同名素材，未覆盖文件') from None
        except OSError:
            pass
        temporary = target.parent / ('.media-preserve-' + uuid.uuid4().hex + '.tmp')
        try:
            with source.open('rb') as original, temporary.open('xb') as output:
                before = os.fstat(original.fileno())
                shutil.copyfileobj(original, output)
                output.flush()
                os.fsync(output.fileno())
                after = os.fstat(original.fileno())
            if (temporary.stat().st_size != before.st_size
                    or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                raise OSError('复用素材复制不完整')
            if target.exists() or target.is_symlink():
                raise OSError('复用任务已有同名素材，未覆盖文件')
            temporary.rename(target)
        finally:
            temporary.unlink(missing_ok=True)


def preserve_reused_inputs(runtime, task_id: str) -> None:
    directory = runtime.task_directory(task_id)
    directories = {path.name: path for path in task_storage.directories(runtime.WORKSPACE_DIR)}
    manifests = {key: task_storage.read_manifest(path) for key, path in directories.items()}

    def manifest(key):
        return manifests.get(key, {})

    dependencies, relocated = [], []
    for other_id in sorted(set(directories) | set(runtime.tasks)):
        if other_id == task_id:
            continue
        other = runtime.tasks.get(other_id, {})
        source, chain = _input_source(runtime, other_id, manifest)
        references_input = bool(source and (source.resolve().parent == directory or task_id in chain))
        job = runtime.running_jobs.get(other_id)
        status = other.get('status')
        if not status and other_id in directories:
            status = recovered_status(directories[other_id], manifest(other_id))
        active = (status in {'pending', 'queued', 'processing', 'cancelling', 'awaiting_agent'}
                  or other.get('_agent_auto_running') or other.get('_agent_capture')
                  or (job and not job.done()))
        if active and (references_input or task_id in chain):
            raise runtime.HTTPException(409, '该任务的文件正在被另一任务复用，请等待其完成后删除')
        if references_input:
            dependencies.append((other_id, source))
        elif source and source.parent == runtime.task_directory(other_id):
            # Retry after a metadata-write failure: the complete copy may already exist.
            saved = manifest(other_id).get('runtime') or {}
            for record in (other, saved):
                if not isinstance(record, dict):
                    continue
                result = record.get('result') or {}
                for value in (record.get('video_path'), result.get('video_path') if isinstance(result, dict) else None):
                    if isinstance(value, str) and Path(value).resolve().parent == directory and Path(value).stem == 'input':
                        relocated.append((other_id, Path(value), source))

    # Check every active dependency before writing any copies. Each surviving task
    # gets its own input.* so existing playback and restart discovery keep working.
    for other_id, source in dependencies:
        target = runtime.task_directory(other_id) / ('input' + source.suffix)
        if target.resolve() != target.absolute():
            raise OSError('复用任务的素材路径无效')
        _preserve(source, target)
        _record_input(runtime, other_id, source, target)
    for other_id, old_source, target in relocated:
        _record_input(runtime, other_id, old_source, target)


def restart_input(runtime, task_id):
    """Resolve a historical upload without reusing transcripts or generation state."""
    try:
        runtime.task_directory(task_id)  # Reject malformed or traversing IDs.
        source, chain = _input_source(runtime, task_id, runtime.read_task_manifest)
    except ValueError as error:
        raise runtime.HTTPException(400, '历史任务标识无效') from error
    if any(runtime.tasks.get(identifier, {}).get('_deleting') for identifier in chain):
        raise runtime.HTTPException(409, '原任务正在删除，请稍后重试')
    if source is None:
        raise runtime.HTTPException(404, '历史任务的本地素材已不存在，请重新选择视频或音频文件')
    return source


def preserve_input(source, target):
    """Create an independent input for a fresh task, preferring a hard link."""
    _preserve(source, target)
