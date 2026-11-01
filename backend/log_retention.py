"""Daily application logs with a workspace-wide retention preference."""
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import os
import tempfile
import threading

DEFAULT_DAYS = 3
_streams = {}
_settings_lock = threading.Lock()


def load_days(workspace: Path) -> int:
    try:
        value = json.loads((workspace / '_log_settings.json').read_text(encoding='utf-8'))['days']
        return value if type(value) is int and 1 <= value <= 365 else DEFAULT_DAYS
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_DAYS


def clean_archives(workspace: Path, days: int, today: date | None = None) -> None:
    cutoff = (today or date.today()) - timedelta(days=days - 1)
    folder = workspace / 'logs'
    if folder.is_symlink():
        return
    for path in folder.glob('app-????-??-??.log'):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            stamp = date.fromisoformat(path.stem[4:])
            if stamp < cutoff:
                path.unlink()
        except (ValueError, OSError):
            # A locked file can be retried at the next cleanup.
            continue


def save_days(workspace: Path, days: int) -> None:
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('日志保留天数应为 1–365')
    workspace.mkdir(parents=True, exist_ok=True)
    with _settings_lock:
        descriptor, name = tempfile.mkstemp(prefix='_log_settings-', suffix='.tmp', dir=workspace)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                json.dump({'days': days}, file)
            os.replace(name, workspace / '_log_settings.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)
    stream = _streams.get(workspace.resolve())
    if stream:
        stream.maintain()
    else:
        clean_archives(workspace, days)


class DailyLogStream:
    encoding = 'utf-8'

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.path = self.workspace / '_app.log'
        self.lock = threading.RLock()
        self.file = None
        self.day = datetime.fromtimestamp(self.path.stat().st_mtime).date() if self.path.exists() else date.today()
        self.stopped = threading.Event()
        self.maintain()
        _streams[self.workspace] = self
        self.worker = threading.Thread(target=self._periodic_cleanup, name='log-retention', daemon=True)
        self.worker.start()

    def maintain(self):
        with self.lock:
            today = date.today()
            if self.day != today:
                if self.file:
                    self.file.close()
                    self.file = None
                if self.path.exists():
                    archive = self.workspace / 'logs' / f'app-{self.day.isoformat()}.log'
                    try:
                        if archive.parent.is_symlink() or archive.is_symlink():
                            raise OSError('Application log archive must remain inside workspace')
                        archive.parent.mkdir(parents=True, exist_ok=True)
                        # Append when a clock change produced this archive already.
                        if archive.exists():
                            with archive.open('ab') as output, self.path.open('rb') as source:
                                import shutil
                                shutil.copyfileobj(source, output)
                            self.path.unlink()
                        else:
                            self.path.rename(archive)
                    except OSError:
                        # Native subprocesses can hold the file on Windows; keep logging and retry.
                        self.file = self.path.open('a', encoding='utf-8', buffering=1)
                        return
                self.day = today
            if self.file is None:
                self.file = self.path.open('a', encoding='utf-8', buffering=1)
            clean_archives(self.workspace, load_days(self.workspace), today)

    def _periodic_cleanup(self):
        while not self.stopped.wait(3600):
            try:
                self.maintain()
            except OSError:
                pass

    def write(self, text):
        with self.lock:
            if self.day != date.today():
                self.maintain()
            return self.file.write(text)

    def flush(self):
        with self.lock:
            self.file.flush()

    def fileno(self):
        return self.file.fileno()

    def isatty(self):
        return False

    def close(self):
        self.stopped.set()
        with self.lock:
            if _streams.get(self.workspace) is self:
                _streams.pop(self.workspace, None)
            self.file.close()


def open_log_stream(workspace: Path) -> DailyLogStream:
    return DailyLogStream(workspace)
