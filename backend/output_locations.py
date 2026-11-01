"""Resolve task-owned results; browser requests never supply shell paths."""
from pathlib import Path

FORMATS = {'.md', '.docx', '.pdf', '.png', '.html', '.json', '.txt', '.enex'}


def locations(runtime, task_id):
    runtime.ensure_task_loaded(task_id)
    task = runtime.tasks.get(task_id)
    if not task or task.get('_deleting'):
        raise runtime.HTTPException(404, '任务不存在')
    directory = runtime.task_directory(task_id)
    manifest = runtime.read_task_manifest(task_id)
    result = task.get('result') or (manifest.get('runtime') or {}).get('result') or {}
    if not isinstance(result, dict): result = {}
    from .task_cleanup import exported_files
    owned = exported_files(runtime.WORKSPACE_DIR, task_id, manifest, result)
    candidates = [result.get('obsidian_path'), result.get('archived_path')]
    for value in candidates:
        if not isinstance(value, str) or not value: continue
        path = Path(value)
        if path.suffix.lower() in FORMATS and path.resolve() in owned and path.is_file() and not path.is_symlink():
            return {'file_path': str(path.resolve()), 'directory': str(path.resolve().parent),
                    'task_directory': str(directory)}
    for name in ('notes.md', 'transcript.md'):
        path = directory / name
        if path.is_file() and not path.is_symlink() and path.resolve().parent == directory.resolve():
            return {'file_path': str(path.resolve()), 'directory': str(directory),
                    'task_directory': str(directory)}
    raise runtime.HTTPException(404, '输出文件不存在，请重新生成或检查保存位置')
