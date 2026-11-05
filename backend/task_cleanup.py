"""Delete task-owned exports without recursively removing user-selected folders."""
import re
from pathlib import Path
from urllib.parse import unquote
from .note_export import assets_directory

def exported_files(workspace, task_id, manifest, result):
    workspace = Path(workspace).resolve()
    paths = set()
    archived = result.get('archived_path')
    if archived:
        path = Path(archived).resolve()
        if path.parent in {workspace / 'notes', workspace / 'transcripts'} and path.suffix in {'.md','.docx','.html','.json','.txt','.png','.pdf','.enex'}:
            paths.add(path)
    custom_notes = manifest.get("note_save_directory")
    archived_path = Path(archived).resolve() if archived else None
    token = re.sub(r"[^a-zA-Z0-9]", "", task_id)[:12]
    if archived_path and (not custom_notes or Path(custom_notes).is_absolute()):
        note_root = Path(custom_notes).resolve() if custom_notes else workspace / "notes"
        valid_name = archived_path.stem.endswith("-" + token)
        if archived_path.parent == note_root and valid_name and archived_path.suffix in {".md",".docx",".html",".json",".txt",".png",".pdf",".enex"}:
            paths.add(archived_path)
            assets = {assets_directory(archived_path), note_root / (archived_path.stem + "-assets")}
            for item in result.get("note_export_files") or []:
                path = Path(item).resolve()
                if path.parent in assets and path.suffix.lower() in {".jpg",".jpeg",".png",".webp"}:
                    paths.add(path)
    # Title-named Markdown folders own only the explicitly recorded files.
    if archived_path and archived_path.suffix == '.md' and archived_path.name == archived_path.parent.name + '.md':
        note_root = Path(custom_notes).resolve() if custom_notes and Path(custom_notes).is_absolute() else workspace / 'notes'
        recorded = {Path(item).resolve() for item in result.get('note_export_files') or []}
        if archived_path.parent.parent == note_root and archived_path in recorded:
            paths.add(archived_path)
            for path in recorded:
                if path.parent == archived_path.parent and path.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}:
                    paths.add(path)
    video = result.get('video_path')
    if video:
        path = Path(video).resolve()
        if path.parent.name == task_id and path.stem == 'preview' and path.suffix.lower() in {'.mp4', '.mkv', '.webm', '.m4s', '.mov', '.avi'}:
            paths.add(path)
    custom = manifest.get('video_download_directory')
    if custom and Path(custom).is_absolute():
        folder = Path(custom).resolve() / task_id
        if folder.is_dir() and folder.resolve().parent == Path(custom).resolve():
            for partial in folder.glob('preview.*'):
                if partial.is_file() and partial.resolve().parent == folder.resolve():
                    paths.add(partial.resolve())
    note = result.get('obsidian_path')
    if note:
        note = Path(note).resolve()
        token = re.search(r'-([0-9a-f]{12}|[0-9a-f]{32})\.md$', note.name)
        if token:
            paths.add(note)
            try:
                content = note.read_text(encoding='utf-8')
            except OSError:
                content = ''
            for link in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', content):
                path = (note.parent / unquote(link)).resolve()
                if re.fullmatch(r'video-' + token[1] + r'-\d+\.(?:jpg|jpeg|png|webp)', path.name, re.I):
                    paths.add(path)
    # New tasks also retain the exact successful export paths, so edited Markdown does not orphan images.
    for item in result.get('obsidian_files') or []:
        path = Path(item).resolve()
        note = result.get('obsidian_path')
        token = re.search(r'-([0-9a-f]{12}|[0-9a-f]{32})\.md$', Path(note).name) if note else None
        if token and re.fullmatch(r'video-' + token[1] + r'-\d+\.(?:jpg|jpeg|png|webp)', path.name, re.I):
            paths.add(path)
    return paths


def unlink_exports(paths):
    note_folders = {path.parent for path in paths if path.suffix == '.md' and path.name == path.parent.name + '.md'}
    for path in paths:
        path.unlink(missing_ok=True)
        if path.parent.name.endswith("-assets"):
            try:
                path.parent.rmdir()
            except OSError:
                pass

    # Remove only empty export folders; never recurse into user-added files.
    for folder in note_folders:
        try:
            folder.rmdir()
        except OSError:
            pass
