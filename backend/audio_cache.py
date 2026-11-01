"""Reuse source media independently of transcript, ASR model and video quality."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import task_storage
from .video_cache import cache_directory, read_index as read_video_index, is_cache_path
from .cache_index import read_entries, update_entries

AUDIO_SUFFIXES = {'.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.opus', '.wma', '.mp4', '.mkv', '.webm', '.m4s', '.mov', '.avi'}


def source_key(url: str) -> str:
    # Keep Bilibili page identities; video height deliberately has no effect on audio.
    return cache_directory(Path('.'), url, 0).name


def has_audio(path: Path) -> bool:
    if path.suffix.lower() not in AUDIO_SUFFIXES or not path.is_file() or path.is_symlink():
        return False
    try:
        import av
        with av.open(str(path)) as media:
            if not media.streams.audio:
                return False
            return next(media.decode(audio=0), None) is not None
    except Exception:
        return False


def _indexed_file(root: Path, entry) -> Path | None:
    if not isinstance(entry, dict) or not isinstance(entry.get('filename'), str):
        return None
    path = root / entry['filename']
    if path.resolve().parent != root.resolve() or path.is_symlink():
        return None
    return path if has_audio(path) else None


def find_cached(root: Path, url: str) -> Path | None:
    return _indexed_file(root, read_entries(root, 'audio').get(source_key(url)))


def publish(root: Path, url: str, source: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    key = source_key(url)
    target = root / (key + source.suffix.lower())
    if source.resolve() != target.resolve():
        temporary = target.with_name(f'{target.name}.{uuid.uuid4().hex}.tmp')
        try:
            try:
                os.link(source, temporary)  # Same-volume files need no duplicate disk space.
            except OSError:
                shutil.copy2(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    update_entries(root, 'audio', {key: {'source_url': url, 'filename': target.name}})
    return target


def _history(workspace: Path):
    directories = task_storage.directories(workspace)
    def modified(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0
    for directory in sorted(directories, key=modified, reverse=True):
        try:
            manifest = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
            if isinstance(manifest, dict) and isinstance(manifest.get('source_url'), str):
                parsed = urlsplit(manifest['source_url'])
                if parsed.scheme in {'http', 'https'} and parsed.hostname:
                    yield directory, manifest
        except (OSError, ValueError):
            continue


def find_history_audio(workspace: Path, url: str) -> Path | None:
    from .video_processor import bilibili_page_url
    key = source_key(url)
    for directory, manifest in _history(workspace):
        source = manifest['source_url']
        for path in directory.glob('audio*.*'):
            if path.resolve().parent != directory.resolve() or path.is_symlink():
                continue
            match = re.fullmatch(r'audio_p([1-9]\d*)', path.stem)
            if match:
                source_url = bilibili_page_url(source, int(match.group(1)))
            elif path.stem == 'audio':
                pages = manifest.get('bilibili_pages') or []
                if not isinstance(pages, list) or any(not isinstance(page, int) or page < 1 for page in pages):
                    continue
                if len(pages) > 1:
                    continue  # A combined recording is not interchangeable with one page.
                source_url = bilibili_page_url(source, pages[0]) if pages else source
            else:
                continue
            if source_key(source_url) == key and has_audio(path):
                return path
    return None


def find_video_audio(workspace: Path, url: str, video_directory: str = '') -> Path | None:
    key = source_key(url)
    roots = {workspace / 'video'}
    if video_directory:
        directory = Path(video_directory).expanduser()
        if directory.is_absolute():
            roots.add(directory.resolve())
    history = list(_history(workspace))
    for directory, manifest in history:
        saved_root = manifest.get('video_download_directory')
        if isinstance(saved_root, str) and Path(saved_root).is_absolute():
            roots.add(Path(saved_root).resolve())
        if source_key(manifest['source_url']) != key:
            continue
        runtime = manifest.get('runtime') or {}
        runtime = runtime if isinstance(runtime, dict) else {}
        result = runtime.get('result') or {}
        result = result if isinstance(result, dict) else {}
        recorded = runtime.get('video_path') or result.get('video_path')
        candidates = list(directory.glob('preview.*'))
        if isinstance(recorded, str):
            path = Path(recorded)
            if path.parent.resolve() == directory.resolve() or is_cache_path(path, workspace, manifest):
                candidates.insert(0, path)
        for path in candidates:
            if has_audio(path):
                return path
    # A shared video remains usable even after its task has been deleted.
    for root in roots:
        for entry in read_video_index(root, workspace).values():
            if isinstance(entry, dict) and isinstance(entry.get('source_url'), str):
                try:
                    matches = source_key(entry['source_url']) == key
                except ValueError:
                    continue
                path = _indexed_file(root, entry) if matches else None
                if path:
                    return path
    return None
