"""Task directories grouped under tasks/, with compatibility for older workspaces."""
from pathlib import Path
from collections.abc import Callable
import json
import uuid

from filelock import FileLock


def read_manifest(directory: Path) -> dict:
    """Readers see either complete version thanks to atomic replacement."""
    try:
        payload = json.loads((Path(directory) / 'task.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def update_manifest(directory: Path, update: Callable[[dict], bool | None]) -> bool:
    """Merge against the latest record under a per-directory process/thread lock.

    The callback runs inside the lock: callers must not pass a stale full snapshot
    or recursively update the same file. Never recreate deleted task directories.
    This serializes file transactions, not multiple servers' in-memory task state.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        return False
    with FileLock(str(directory / '.task.lock'), timeout=10):
        if not directory.is_dir():
            return False
        payload = read_manifest(directory)
        if update(payload) is False:
            return False
        path = directory / 'task.json'
        temporary = directory / f'task.json.{uuid.uuid4().hex}.tmp'
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def directory(workspace,task_id):
    root=Path(workspace).resolve()
    if not task_id or Path(task_id).name!=task_id or task_id in {'.','..'}:raise ValueError('非法任务 ID')
    grouped=(root/'tasks'/task_id).resolve()
    if grouped.parent!=(root/'tasks').resolve() or grouped.parent.parent!=root:raise ValueError('非法任务目录')
    legacy=(root/task_id).resolve()
    if legacy.parent!=root:raise ValueError('非法任务目录')
    return grouped if grouped.exists() or not legacy.exists() else legacy

def directories(workspace):
    root=Path(workspace).resolve()
    if not root.exists():return []
    results=[p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and (p/'task.json').is_file()]
    tasks=root/'tasks'
    if tasks.is_dir() and not tasks.is_symlink():results.extend(p for p in tasks.iterdir() if p.is_dir() and not p.is_symlink())
    return results
