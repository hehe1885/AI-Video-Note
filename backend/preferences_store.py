"""Workspace-owned preferences, with guarded schema upgrades and atomic saves."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import uuid

from filelock import FileLock

SCHEMA = 3
FILENAME = 'preferences.json'
MAX_BYTES = 2 * 1024 * 1024
SENSITIVE = re.compile(r'key|token|secret|sessdata|jct|buvid|authorization|cookie', re.I)
DIRECTORY_KEYS = {'video_download_directory', 'note_save_directory', 'obsidian_vault'}


class PreferencesError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def _clean(value, depth=0):
    if depth > 30:
        raise PreferencesError('invalid_preferences', '设置内容层级过深', 422)
    if isinstance(value, dict):
        return {key: _clean(item, depth + 1) for key, item in value.items()
                if not SENSITIVE.search(key) and key not in {'__proto__', 'prototype', 'constructor'}}
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise PreferencesError('invalid_preferences', '设置内容无效', 422)


def _schema(value):
    schema = value.get('schema', 1)
    if type(schema) is not int or schema < 1:
        raise PreferencesError('invalid_preferences', '设置版本无效', 422)
    if schema > SCHEMA:
        raise PreferencesError('newer_preferences', '设置来自更新版本，已停止写入以保护原文件')
    return schema


def _validate(value):
    if not isinstance(value, dict):
        raise PreferencesError('invalid_preferences', '设置内容无效', 422)
    _schema(value)
    for key in ('global', 'ui', 'by_provider', 'confirmations', 'update'):
        if key in value and not isinstance(value[key], dict):
            raise PreferencesError('invalid_preferences', '设置内容无效', 422)
    for key in ('customs', 'templates'):
        if key in value and not isinstance(value[key], list):
            raise PreferencesError('invalid_preferences', '设置内容无效', 422)


def _paths(value, root, *, expand):
    if isinstance(value, list):
        return [_paths(item, root, expand=expand) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in DIRECTORY_KEYS and isinstance(item, str) and item:
            path = Path(item).expanduser()
            if expand:
                item = str(path if path.is_absolute() else (root / path).resolve())
            elif path.is_absolute():
                try:
                    item = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    pass  # External user-selected locations retain their absolute path.
        result[key] = _paths(item, root, expand=expand)
    return result


def _migrate(value):
    _validate(value)
    result = _clean(copy.deepcopy(value))
    version = _schema(result)
    # Each step is explicit and repeatable; defaults are merged by the UI only
    # for missing fields, never by replacing a user's saved preference file.
    while version < SCHEMA:
        if version == 1:
            if 'theme' in result:
                result.setdefault('ui', {}).setdefault('theme', result.pop('theme'))
        elif version == 2:
            ui = result.get('ui', {})
            if ui.get('font_size') == 'small':
                ui['font_size'] = 'compact'
            global_settings = result.get('global', {})
            if 'gpu_backend' in global_settings:
                global_settings['gpu_backend'] = 'auto'
            if global_settings.get('reasoning_effort') == 'max':
                global_settings['reasoning_effort'] = 'high'
        version += 1
        result['schema'] = version
    result['schema'] = SCHEMA
    return result


def _merge(current, incoming):
    result = copy.deepcopy(current)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _encode(value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES:
        raise PreferencesError('preferences_too_large', '设置文件过大', 413)
    return data


def _atomic(path, data):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class PreferencesStore:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / FILENAME

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None, None
        try:
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError()
            old_schema = _schema(document)
            value = document.get('preferences', document)
            _validate(value)
            # Check both versions: a mismatched newer inner record is not safe
            # for an older executable to rewrite either.
            if _schema(value) > old_schema:
                raise ValueError()
            migrated = _paths(_migrate(value), self.workspace.parent, expand=False)
        except PreferencesError:
            raise
        except (ValueError, TypeError, RecursionError) as exc:
            raise PreferencesError('corrupt_preferences', '设置文件损坏，已保留原文件并停止自动写入') from exc
        if old_schema < SCHEMA:
            data = _encode({'schema': SCHEMA, 'preferences': migrated})
            _atomic(self.path, data)
            raw = data
        return raw, migrated

    def _packet(self, raw, preferences):
        return {'exists': raw is not None, 'schema': SCHEMA,
                'revision': hashlib.sha256(raw).hexdigest() if raw is not None else None,
                'preferences': _paths(preferences, self.workspace.parent, expand=True) if preferences is not None else None}

    def load(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.workspace / '.preferences.lock'), timeout=10):
            return self._packet(*self._read())

    def save(self, preferences, expected_revision, import_source=None):
        _validate(preferences)
        incoming = _paths(_migrate(preferences), self.workspace.parent, expand=False)
        self.workspace.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.workspace / '.preferences.lock'), timeout=10):
            raw, current = self._read()
            packet = self._packet(raw, current)
            if packet['revision'] != expected_revision:
                raise PreferencesError('preferences_changed', '设置已被另一页面修改，请重新读取后再保存')
            if import_source is not None:
                _validate(import_source)
            combined = _merge(current or {}, incoming)
            data = _encode({'schema': SCHEMA, 'preferences': combined})
            if raw != data:
                _atomic(self.path, data)
            return self._packet(data, combined)
