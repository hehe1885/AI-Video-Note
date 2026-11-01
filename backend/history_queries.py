"""Compact history from all manifests without loading note bodies."""
from datetime import date, datetime, timezone, timedelta
from . import task_storage
from .task_recovery import recovered_status, safe_number

ACTIVE = {'pending', 'queued', 'processing', 'cancelling', 'awaiting_agent'}

def _number(value):
    return safe_number(value)

def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError): return datetime.min.replace(tzinfo=timezone.utc)

def collect_history(workspace, memory, q, status, output, start_date, end_date, utc_offset):
    records = {}
    try: directories = task_storage.directories(workspace)
    except OSError: directories = []
    for directory in directories:
        manifest = task_storage.read_manifest(directory)
        if not manifest: continue
        saved = manifest.get('runtime') if isinstance(manifest.get('runtime'), dict) else {}
        result = saved.get('result') if isinstance(saved.get('result'), dict) else {}
        records[directory.name] = {
            'task_id': directory.name, 'status': recovered_status(directory, manifest),
            'step_name': str(saved.get('step_name') or ''), 'progress': int(safe_number(saved.get('progress'))),
            'output': manifest.get('output') or 'note',
            'title': result.get('title') or manifest.get('title') or manifest.get('uploaded_filename') or manifest.get('source_url') or '本地媒体任务',
            'created_at': saved.get('created_at') or manifest.get('created_at'),
            'finished_at': saved.get('finished_at'), 'elapsed_seconds': _number(saved.get('elapsed_seconds')),
        }
    for task in memory:
        if task.pop('_deleting', False):
            records.pop(task['task_id'], None)
            continue
        previous = records.get(task['task_id'], {})
        records[task['task_id']] = {'output': 'note', **previous, **{k:v for k,v in task.items() if v is not None}}
        if not task.get('title'): records[task['task_id']]['title'] = previous.get('title') or '本地媒体任务'
    local_zone = timezone(timedelta(minutes=utc_offset))
    def matches(row):
        if q and q.casefold() not in str(row['title']).casefold(): return False
        if status and (row['status'] not in ACTIVE if status == 'active' else row['status'] != status): return False
        if output and row.get('output') != output: return False
        if not start_date and not end_date: return True
        created = _stamp(row.get('created_at'))
        if created.year == 1: return False
        try: local_date = created.astimezone(local_zone).date()
        except (OverflowError, ValueError): return False
        return (not start_date or local_date >= start_date) and (not end_date or local_date <= end_date)
    return sorted((r for r in records.values() if matches(r)),
                  key=lambda r: (_stamp(r.get('created_at')), r['task_id']), reverse=True)

async def query_history(runtime, q='', status='', output='', start_date='', end_date='', utc_offset=0, offset=0, limit=20):
    if len(q) > 200 or status not in {'', 'active', 'completed', 'failed', 'cancelled', 'uploaded'} or output not in {'', 'note', 'transcript'}:
        raise runtime.HTTPException(400, '历史筛选条件无效')
    if not 1 <= limit <= 100 or offset < 0 or not -840 <= utc_offset <= 840:
        raise runtime.HTTPException(400, '历史分页参数无效')
    try:
        first = date.fromisoformat(start_date) if start_date else None
        last = date.fromisoformat(end_date) if end_date else None
        if first and last and first > last: raise ValueError()
    except ValueError: raise runtime.HTTPException(400, '历史日期范围无效') from None
    memory = []
    for task_id, task in list(runtime.tasks.items()):
        result = task.get('result') if isinstance(task.get('result'), dict) else {}
        memory.append({'task_id': task_id, 'status': task.get('status'), 'step_name': task.get('step_name'),
            'progress': task.get('progress', 0), 'title': result.get('title'), 'output': task.get('output'),
            'created_at': task.get('created_at'), 'finished_at': task.get('finished_at'),
            'elapsed_seconds': runtime.task_elapsed_seconds(task), '_deleting': task.get('_deleting', False)})
    rows = await runtime.asyncio.to_thread(collect_history, runtime.WORKSPACE_DIR, memory,
        q.strip(), status, output, first, last, utc_offset)
    page = rows[offset:offset + limit]
    return {'tasks': page, 'total': len(rows), 'offset': offset, 'limit': limit,
            'has_more': offset + len(page) < len(rows), 'next_offset': offset + len(page)}
