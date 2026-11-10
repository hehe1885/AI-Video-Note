"""Private JSONL diagnostics; UI task logs retain their existing concise format."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import threading
import time
import traceback

_context = ContextVar('detailed_log_context', default={})
_lock = threading.RLock()
_started = set()


@contextmanager
def bind(task_id=None, workspace=None):
    context = dict(_context.get())
    if task_id is not None: context['task_id'] = str(task_id)
    if workspace is not None: context['workspace'] = str(workspace)
    token = _context.set(context)
    try: yield
    finally: _context.reset(token)


def task_id():
    return _context.get().get('task_id')


def scrub(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'key|token|cookie|authorization|password|secret', str(k), re.I) else scrub(v, secrets)) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [scrub(v, secrets) for v in value]
    if not isinstance(value, str): return value
    text = value
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 8: text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'sk-[A-Za-z0-9_-]{8,}', '[REDACTED]', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s,;\"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|authorization|cookie|sessdata|password)[\"\']?\s*[:=]\s*[\"\']?)[^\s,;\"\']+', r'\1[REDACTED]', text)
    return text.replace(str(Path.home()), '~')


def event(kind, *, workspace=None, error=None, secrets=(), **fields):
    context = _context.get()
    location = workspace or context.get('workspace') or os.getenv('AI_VIDEO_NOTE_WORKSPACE')
    if not location: return
    now = datetime.now(timezone.utc)
    record = {'timestamp': now.isoformat(timespec='milliseconds'), 'event': kind,
              'pid': os.getpid(), 'parent_pid': os.getppid(), 'thread_id': threading.get_ident(),
              'task_id': context.get('task_id'), **fields}
    if error is not None:
        record['error'] = {'type': type(error).__name__, 'message': str(error),
                           'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))}
    try:
        line = json.dumps(scrub(record, secrets), ensure_ascii=False, allow_nan=False) + '\n'
        folder = Path(location) / 'logs'
        with _lock:
            if folder.is_symlink(): return
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'detail-{datetime.now().astimezone().date().isoformat()}-{os.getpid()}.jsonl'
            if path.is_symlink(): return
            with path.open('a', encoding='utf-8') as output: output.write(line)
    except (OSError, ValueError, TypeError):
        # Diagnostics must never fail a video task, including disk-full errors.
        pass


def startup(workspace, version):
    key = (str(Path(workspace).resolve()), os.getpid())
    with _lock:
        if key in _started: return
        _started.add(key)
    versions = {}
    for name in ('faster-whisper', 'ctranslate2', 'sherpa-onnx', 'av', 'fastapi'):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name] = None
    event('runtime.start', workspace=workspace, app_version=version, python=platform.python_version(),
          platform=platform.platform(), packages=versions)
    from .log_retention import clean_archives, load_days
    try:
        clean_archives(Path(workspace), load_days(Path(workspace)))
    except OSError:
        pass


def progress(task, step, stage, percent):
    now = time.monotonic()
    previous = task.get('_detail_stage')
    if previous != stage:
        if previous is not None:
            event('stage.end', task_id=task.get('_task_id') or task_id(), stage=previous,
                  elapsed_seconds=round(now-task.get('_detail_stage_started', now), 3))
        task['_detail_stage'] = stage
        task['_detail_stage_started'] = now
        event('stage.start', task_id=task.get('_task_id') or task_id(), stage=stage, step=step, percent=percent)
