"""Workspace-owned media indexes, including roots outside the workspace."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from filelock import FileLock


def index_path(root: Path, kind: str, workspace: Path | None = None) -> Path:
    if kind not in {'video', 'audio'}:
        raise ValueError('Unsupported media index')
    workspace = Path(workspace).resolve() if workspace is not None else Path(root).resolve().parent
    return workspace / f'{kind}_index.json'


def _identity(root: Path, workspace: Path) -> str:
    root = Path(root).resolve()
    try:
        location = 'relative:' + root.relative_to(workspace.resolve()).as_posix()
    except ValueError:
        location = 'absolute:' + str(root)
    return hashlib.sha256(os.path.normcase(location).encode('utf-8')).hexdigest()


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('version') == 1 and isinstance(data.get('directories'), dict):
            return data
    except (OSError, ValueError):
        pass
    return {'version': 1, 'directories': {}}


def _write(path: Path, data: dict) -> None:
    temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _entries(data: dict, root: Path, workspace: Path) -> dict:
    group = data['directories'].get(_identity(root, workspace))
    if not isinstance(group, dict) or not isinstance(group.get('entries'), dict):
        return {}
    return group['entries']


def _merge(path: Path, root: Path, entries: dict, *, overwrite: bool) -> None:
    data = _load(path)
    current = dict(_entries(data, root, path.parent))
    if overwrite:
        current.update(entries)
    else:
        current = {**entries, **current}
    group = {'path': str(root.resolve()), 'entries': current}
    try:
        group['relative_path'] = root.resolve().relative_to(path.parent.resolve()).as_posix()
    except ValueError:
        pass
    data['directories'][_identity(root, path.parent)] = group
    _write(path, data)


def _migrate(root: Path, workspace: Path) -> None:
    legacy = root / 'index.json'
    try:
        snapshot = legacy.read_bytes()
        entries = json.loads(snapshot)
        if not isinstance(entries, dict):
            return
    except (OSError, ValueError):
        return
    groups = {'video': {}, 'audio': {}}
    for key, entry in entries.items():
        if not re.fullmatch(r'[a-f0-9]{32}', key) or not isinstance(entry, dict) or not isinstance(entry.get('filename'), str):
            return  # An unrecognized index is preserved rather than discarded.
        kind = 'video' if 'resolution' in entry or 'platform' in entry else 'audio'
        groups[kind][key] = entry
    for kind, group in groups.items():
        if group:
            path = index_path(root, kind, workspace)
            _merge(path, root, group, overwrite=False)
            if not all(key in _entries(_load(path), root, path.parent) for key in group):
                raise OSError('Media index migration verification failed')
    # Remove only the unchanged source, after the new indexes have been committed.
    try:
        if legacy.read_bytes() == snapshot:
            legacy.unlink()
    except OSError:
        pass


def read_entries(root: Path, kind: str, workspace: Path | None = None) -> dict:
    root = Path(root).resolve()
    path = index_path(root, kind, workspace)
    if not path.exists() and not (root / 'index.json').exists():
        return {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.parent / '.media_index.lock')):
        _migrate(root, path.parent)
        return dict(_entries(_load(path), root, path.parent))


def update_entries(root: Path, kind: str, entries: dict, workspace: Path | None = None) -> None:
    root = Path(root).resolve()
    path = index_path(root, kind, workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.parent / '.media_index.lock')):
        _migrate(root, path.parent)
        _merge(path, root, entries, overwrite=True)


def _group_root(group: dict, workspace: Path) -> Path | None:
    try:
        relative = group.get('relative_path')
        if isinstance(relative, str):
            root = (workspace / relative).resolve()
            return root if root.is_relative_to(workspace) else None
        path = group.get('path')
        return Path(path).resolve() if isinstance(path, str) and Path(path).is_absolute() else None
    except (OSError, ValueError):
        return None


def indexed_directories(workspace: Path, kind: str) -> set[Path]:
    workspace = Path(workspace).resolve()
    data = _load(workspace / f'{kind}_index.json')
    roots = set()
    for key, group in data['directories'].items():
        if not isinstance(group, dict):
            continue
        root = _group_root(group, workspace)
        if root is not None and _identity(root, workspace) == key:
            roots.add(root)
    return roots


def remove_file_entries(workspace: Path, kind: str, paths: set[Path]) -> None:
    """Prune only entries for the exact files successfully removed by the caller."""
    if not paths:
        return
    workspace = Path(workspace).resolve()
    path = workspace / f'{kind}_index.json'
    if not path.is_file():
        return
    targets = {item.resolve() for item in paths}
    with FileLock(str(workspace / '.media_index.lock')):
        data = _load(path)
        changed = False
        for group_key, group in data['directories'].items():
            if not isinstance(group, dict) or not isinstance(group.get('entries'), dict):
                continue
            root = _group_root(group, workspace)
            if root is None or _identity(root, workspace) != group_key:
                continue
            for key, entry in list(group['entries'].items()):
                filename = entry.get('filename') if isinstance(entry, dict) else None
                if not isinstance(filename, str) or Path(filename).name != filename:
                    continue
                candidate = root / filename
                if candidate.resolve().parent == root and candidate.resolve() in targets:
                    del group['entries'][key]
                    changed = True
        if changed:
            _write(path, data)


def migrate_workspace(workspace: Path) -> None:
    """Find the app's known old cache roots on startup; task.json stays untouched."""
    from . import task_storage
    workspace = Path(workspace).resolve()
    roots = {workspace / 'video', workspace / 'audio'}
    for directory in task_storage.directories(workspace):
        try:
            manifest = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
            custom = manifest.get('video_download_directory') if isinstance(manifest, dict) else None
            if isinstance(custom, str) and Path(custom).is_absolute():
                roots.add(Path(custom).resolve())
        except (OSError, ValueError):
            continue
    for root in roots:
        if (root / 'index.json').is_file():
            read_entries(root, 'video', workspace)
