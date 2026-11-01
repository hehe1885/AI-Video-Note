"""Read-only size inventory and expiring, revalidated cache cleanup plans."""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time

from . import cache_index, model_storage, task_storage
from .video_cache import cache_lock, cache_directory

AUDIO = {'.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.opus', '.wma'}
VIDEO = {'.mp4', '.mkv', '.webm', '.m4s', '.mov', '.avi'}
IMAGES = {'.jpg', '.jpeg', '.png', '.webp'}


class StorageError(ValueError):
    pass


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def signature(path):
    if linked(path) or not path.is_file(): raise StorageError('缓存文件已变化，请重新预览')
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def indexed_files(workspace, kind, onerror=None):
    """Only exact app-named files in validated index groups are deletable."""
    roots = cache_index.indexed_directories(workspace, kind)
    roots.add(workspace / ('audio' if kind == 'audio' else 'video'))
    for root in sorted(roots):
        try:
            if not root.is_dir() or linked(root): continue
            for key, entry in cache_index.read_entries(root, kind, workspace).items():
                if not re.fullmatch(r'[a-f0-9]{32}', key) or not isinstance(entry, dict): continue
                filename, source = entry.get('filename'), entry.get('source_url')
                if not isinstance(filename, str) or Path(filename).name != filename: continue
                if not isinstance(source, str) or not source.startswith(('https://', 'http://')): continue
                path = root / filename
                if kind == 'audio':
                    from .audio_cache import source_key
                    if key != source_key(source): continue
                    if root != workspace / 'audio' or path.stem != key or path.suffix.lower() not in AUDIO | VIDEO: continue
                    lock_path = root / key
                else:
                    height = entry.get('resolution')
                    if type(height) is not int or height <= 0: continue
                    if key not in {cache_directory(root, source, height, mode).name for mode in ('compatibility', 'size')}: continue
                    owner = entry.get('download_task_id') or key
                    if not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner): continue
                    if path.stem not in {owner, owner + '-' + key[:8]} or path.suffix.lower() not in VIDEO: continue
                    lock_path = root / '_cache' / key
                if path.resolve().parent != root.resolve() or not path.is_file() or linked(path): continue
                yield {'path': str(path), 'kind': kind, 'lock': str(lock_path),
                       'signature': signature(path), 'links': path.stat().st_nlink}
        except (OSError, ValueError):
            if onerror: onerror()
            continue


def walk_files(root, onerror=None):
    """Never traverse directory links or junctions, including on Python 3.11."""
    try:
        if not root.is_dir() or linked(root): return
        for current, directories, files in os.walk(root, followlinks=False, onerror=lambda _error: onerror() if onerror else None):
            safe_dirs = []
            for name in directories:
                try:
                    if not linked(Path(current) / name): safe_dirs.append(name)
                except OSError:
                    if onerror: onerror()
            directories[:] = safe_dirs
            for name in files:
                path = Path(current) / name
                try:
                    if not linked(path): yield path
                except OSError:
                    if onerror: onerror()
    except OSError:
        if onerror: onerror()
        return


def inventory(runtime):
    workspace = Path(runtime.WORKSPACE_DIR).resolve()
    groups = {kind: {} for kind in ('audio', 'video', 'screenshots', 'models')}
    unreadable = 0
    all_files = {}
    def mark_unreadable():
        nonlocal unreadable
        unreadable += 1
    def add(path, kind):
        nonlocal unreadable
        try:
            info = path.stat()
            identity = (info.st_dev, info.st_ino) if info.st_ino else str(path.resolve())
            groups[kind][identity] = info.st_size
            all_files[identity] = info.st_size
        except OSError: unreadable += 1
    for kind in ('audio', 'video'):
        for entry in indexed_files(workspace, kind, mark_unreadable): add(Path(entry['path']), kind)
        for path in walk_files(workspace / kind, mark_unreadable):
            if path.suffix.lower() in AUDIO | VIDEO: add(path, kind)
    try: directories = task_storage.directories(workspace)
    except OSError:
        mark_unreadable(); directories = []
    for directory in directories:
        try:
            if linked(directory): continue
        except OSError:
            mark_unreadable(); continue
        for path in walk_files(directory, mark_unreadable):
            relative = path.relative_to(directory)
            if relative.parts[0] == 'frames' and path.suffix.lower() in IMAGES: add(path, 'screenshots')
            elif len(relative.parts) == 1 and path.suffix.lower() in AUDIO | VIDEO:
                add(path, 'audio' if path.suffix.lower() in AUDIO or path.stem.startswith('audio') else 'video')
    for model in runtime.ASR_MODELS:
        for root in model_storage.model_directories(runtime.WHISPER_CACHE_DIR, model, runtime.WHISPER_MODEL_REPOS.get(model)):
            for path in walk_files(root, mark_unreadable): add(path, 'models')
    entries = [entry for kind in ('audio', 'video') for entry in indexed_files(workspace, kind)]
    counts = Counter(entry['path'] for entry in entries)
    candidates = {entry['path']: entry for entry in entries if counts[entry['path']] == 1}
    return {'categories': {kind: {'bytes': sum(files.values()), 'count': len(files)} for kind, files in groups.items()},
            'total_bytes': sum(all_files.values()), 'unreadable_count': unreadable}, list(candidates.values())


def reclaimed_bytes(files):
    counts = Counter(entry['signature'][:2] for entry in files)
    seen, amount = set(), 0
    for entry in files:
        identity = entry['signature'][:2]
        if identity not in seen and entry['links'] <= counts[identity]: amount += entry['signature'][2]
        seen.add(identity)
    return amount


class CleanupPlans:
    def __init__(self):
        self.plans = {}
        self.guard = threading.Lock()

    def preview(self, runtime, kinds):
        usage, entries = inventory(runtime)
        files = [entry for entry in entries if entry['kind'] in kinds]
        now = time.monotonic()
        token = secrets.token_urlsafe(24)
        with self.guard:
            self.plans = {key: value for key, value in self.plans.items() if now - value['created'] < 300}
            while len(self.plans) >= 8: self.plans.pop(next(iter(self.plans)))
            self.plans[token] = {'created': now, 'workspace': str(Path(runtime.WORKSPACE_DIR).resolve()), 'files': files}
        return {**usage, 'token': token, 'count': len(files),
                'bytes': sum(entry['signature'][2] for entry in files), 'reclaimable_bytes': reclaimed_bytes(files),
                'files': [{'path': item['path'], 'kind': item['kind'], 'bytes': item['signature'][2]} for item in files[:50]],
                'truncated': len(files) > 50}

    def execute(self, runtime, token):
        with self.guard: plan = self.plans.pop(token, None)
        workspace = Path(runtime.WORKSPACE_DIR).resolve()
        if not plan or time.monotonic() - plan['created'] >= 300 or plan['workspace'] != str(workspace):
            raise StorageError('清理预览已过期，请重新预览')
        files = plan['files']
        removed, failed = [], []
        with ExitStack() as stack:
            # Use the same per-source locks as download/reuse workers, including
            # workers still winding down after their asyncio task was cancelled.
            for key in sorted({entry['lock'] for entry in files}):
                lock = cache_lock(Path(key))
                if not lock.acquire(blocking=False): raise StorageError('素材正在使用，请稍后重试')
                stack.callback(lock.release)
            entries = [entry for kind in ('audio', 'video') for entry in indexed_files(workspace, kind)]
            counts = Counter(entry['path'] for entry in entries)
            current = {entry['path']: entry for entry in entries if counts[entry['path']] == 1}
            for entry in files:
                fresh = current.get(entry['path'])
                if fresh is None or fresh['kind'] != entry['kind'] or fresh['lock'] != entry['lock'] or fresh['signature'] != entry['signature']:
                    raise StorageError('缓存文件已变化，请重新预览')
            for entry in files:
                path = Path(entry['path'])
                try:
                    if signature(path)[:4] != entry['signature'][:4]: raise StorageError('缓存文件已变化，请重新预览')
                    path.unlink()
                    removed.append(entry)
                except (OSError, StorageError): failed.append(entry['path'])
            index_error = False
            for kind in ('audio', 'video'):
                try: cache_index.remove_file_entries(workspace, kind, {Path(entry['path']) for entry in removed if entry['kind'] == kind})
                except OSError: index_error = True
        return {'removed_count': len(removed), 'failed_count': len(failed), 'failed_paths': failed[:50],
                'removed_bytes': sum(entry['signature'][2] for entry in removed),
                'reclaimed_bytes': reclaimed_bytes(removed), 'index_error': index_error}
