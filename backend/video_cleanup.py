"""Identify task-associated downloaded videos without deleting unrelated media."""
from pathlib import Path
import re

from .cache_index import indexed_directories
from .video_cache import read_index

VIDEO_SUFFIXES = {'.mp4', '.mkv', '.webm', '.m4s', '.mov', '.avi'}


def downloaded_videos(workspace, task_id, manifest, result):
    workspace = Path(workspace).resolve()
    roots = {workspace / 'video'} | indexed_directories(workspace, 'video')
    custom = manifest.get('video_download_directory')
    if isinstance(custom, str) and Path(custom).is_absolute():
        roots.add(Path(custom).resolve())
    recorded = result.get('video_path')
    recorded = Path(recorded) if isinstance(recorded, str) else None
    recorded = recorded.resolve() if recorded is not None and not recorded.is_symlink() else None
    paths = set()
    for root in roots:
        for entry in read_index(root, workspace).values():
            if not isinstance(entry, dict):
                continue
            filename = entry.get('filename')
            if not isinstance(filename, str) or Path(filename).name != filename:
                continue
            path = root / filename
            if path.is_symlink() or path.resolve().parent != root.resolve() or path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if entry.get('download_task_id') == task_id or path.resolve() == recorded:
                paths.add(path.resolve())
    if recorded is not None and recorded.suffix.lower() in VIDEO_SUFFIXES:
        if recorded.parent.name == task_id and recorded.stem == 'preview':
            paths.add(recorded)
        elif recorded.parent in roots:
            # Recover exact task-owned cache files even if their index was lost.
            stem = recorded.stem
            uuid_name = re.fullmatch(r'(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-[0-9a-f]{8})?', stem, re.I)
            if stem == task_id or stem.startswith(task_id + '-') or uuid_name:
                paths.add(recorded)
    return paths
