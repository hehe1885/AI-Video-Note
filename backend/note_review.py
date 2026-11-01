"""Read-only replay metadata and strictly scoped task media discovery."""
import math
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MEDIA_SUFFIXES = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.opus', '.wma'}
AUDIO_SUFFIXES = {'.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.opus', '.wma'}

def replay_link(source: str, seconds: float, pages: list[dict] | None = None) -> dict:
    """Map a combined Bilibili timeline back to its page-local timestamp."""
    try:
        parsed = urlsplit(source)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            return {'url': None, 'precise': False}
        host = parsed.hostname.lower()
        second = max(0, int(seconds)) if math.isfinite(float(seconds)) else 0
    except (TypeError, ValueError):
        return {'url': None, 'precise': False}
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if host == 'bilibili.com' or host.endswith('.bilibili.com'):
        pages = pages or []
        if len(pages) > 1:
            if any(not isinstance(p.get('duration'), (float, int)) or p['duration'] <= 0 for p in pages):
                return {'url': source, 'precise': False}
            offset = 0.0
            chosen = pages[-1]
            for page in pages:
                chosen = page
                if second < offset + page['duration']: break
                offset += page['duration']
            query['p'] = str(chosen['page'])
            second = max(0, int(second - min(offset, sum(p['duration'] for p in pages[:-1]))))
        elif pages:
            query['p'] = str(pages[0]['page'])
        query['t'] = str(second)
        query.pop('start_progress', None)
    elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
        query.pop('start', None)
        query['t'] = str(second)
    else:
        return {'url': source, 'precise': False}
    return {'url': urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), '')), 'precise': True}

def find_media(task_id, workspace, tasks, read_manifest):
    """Only serve input/preview artifacts owned by the task or its resume chain.

    An external download must be the recorded preview inside its task-id folder.
    Never accept a path from the HTTP caller or an unrelated manifest field.
    """
    root = Path(workspace).resolve()
    visited = set()
    current = task_id
    while current and current not in visited and len(visited) < 20:
        from .task_storage import directory as task_directory
        try: directory = task_directory(root, current)
        except ValueError: return None
        visited.add(current)
        manifest = read_manifest(current)
        task = tasks.get(current) or {}
        if task.get('_deleting'): return None
        runtime = manifest.get('runtime') or {}
        result = task.get('result') or runtime.get('result') or {}
        recorded = task.get('video_path') or result.get('video_path') or runtime.get('video_path')
        candidates = list(directory.glob('input.*')) + list(directory.glob('preview.*'))
        if recorded:
            path = Path(recorded).resolve()
            from .video_cache import is_cache_path
            if (path.parent.name == current and path.stem in {'preview', 'preview_scaled', 'preview_with_audio'}) or is_cache_path(path, root, manifest): candidates.insert(0, path)
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved != candidate.absolute(): continue  # No symlink escapes.
            if resolved.suffix.lower() in MEDIA_SUFFIXES and resolved.is_file() and resolved.stat().st_size:
                return resolved
        current = task.get('resume_task_id') or manifest.get('reused_task_id')
    return None
