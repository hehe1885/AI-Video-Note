"""Manual settings file export/import; never reads credential files or creates backups."""
from datetime import datetime, timezone
import hashlib
import json
from filelock import FileLock
from .preferences_store import PreferencesStore, PreferencesError, SCHEMA, MAX_BYTES, _schema, _validate, _migrate, _clean, _paths, _encode, _atomic

def _hash(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None

def _decode(raw):
    if len(raw) > MAX_BYTES: raise PreferencesError('invalid_backup', '备份文件过大', 413)
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict): raise ValueError()
        if doc.get('format') not in {None, 'ai-video-note-settings'}: raise ValueError()
        version = _schema(doc)
        prefs = doc.get('preferences', doc)
        _validate(prefs)
        if not {'global','ui','templates','customs','by_provider','confirmations','update','provider','theme'}.intersection(prefs): raise ValueError()
        if _schema(prefs) > version: raise ValueError()
        return version, _migrate(prefs)
    except PreferencesError: raise
    except (TypeError, ValueError, RecursionError) as error:
        raise PreferencesError('invalid_backup', '备份文件无效，未修改设置', 422) from error

def _source(document):
    if not isinstance(document, dict):
        raise PreferencesError('invalid_backup', '备份文件无效，未修改设置', 422)
    raw = _encode(document)
    schema, prefs = _decode(raw)
    return raw, schema, prefs, document.get('exported_at')

def _current(store):
    try: return store.path.read_bytes()
    except FileNotFoundError: return None

def export_settings(workspace):
    store = PreferencesStore(workspace)
    packet = store.load()
    if not packet['exists']: raise PreferencesError('missing_preferences', '尚无已保存的设置可导出', 404)
    return {'format': 'ai-video-note-settings', 'schema': SCHEMA,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'preferences': _paths(_clean(packet['preferences']), store.workspace.parent, expand=False)}

def preview_restore(workspace, document):
    store = PreferencesStore(workspace)
    with FileLock(str(store.workspace / '.preferences.lock'), timeout=10):
        raw, schema, prefs, created = _source(document)
        return {'backup_revision': _hash(raw), 'current_revision': _hash(_current(store)),
            'created_at': created, 'schema': schema, 'target_schema': SCHEMA,
            'templates': len(prefs.get('templates', [])), 'profiles': len(prefs.get('customs', [])),
            'sections': [key for key in ('global', 'ui', 'customs', 'by_provider', 'templates', 'update', 'confirmations') if key in prefs]}

def restore_settings(workspace, document, backup_revision, current_revision):
    store = PreferencesStore(workspace)
    with FileLock(str(store.workspace / '.preferences.lock'), timeout=10):
        raw, _, prefs, _ = _source(document)
        current = _current(store)
        if _hash(raw) != backup_revision or _hash(current) != current_revision:
            raise PreferencesError('restore_changed', '备份或当前设置已变化，请重新选择文件导入')
        prepared = _paths(_clean(prefs), store.workspace.parent, expand=False)
        data = _encode({'schema': SCHEMA, 'preferences': prepared})
        _atomic(store.path, data)
        return store._packet(data, prepared)
