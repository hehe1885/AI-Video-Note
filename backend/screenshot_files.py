"""Unique screenshot filenames with exclusive writes for CPU and GPU output."""
import math
from pathlib import Path
import re
import shutil
import uuid


def _write_unique(directory, timestamp, kind, task_id, writer):
    directory=Path(directory)
    stamp=float(timestamp)
    if not math.isfinite(stamp) or stamp < 0 or kind not in {'ai_frame','frame'}:
        raise ValueError('Invalid screenshot timestamp or kind')
    directory.mkdir(parents=True,exist_ok=True)
    identity=re.sub(r'[^A-Za-z0-9_-]','_',str(task_id or directory.parent.name))[:64] or 'task'
    for _ in range(100):
        path=directory/f'{kind}_{identity}_{round(stamp*1000)}ms_{uuid.uuid4().hex}.jpg'
        try:
            output=path.open('xb')
        except FileExistsError:
            continue
        try:
            with output: writer(output)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    raise FileExistsError('Unable to reserve a unique screenshot filename')


def save_frame(image,directory,timestamp,*,kind='ai_frame',task_id=None,quality=85):
    return _write_unique(directory,timestamp,kind,task_id,
                         lambda output:image.save(output,format='JPEG',quality=quality))


def publish_frame(source,directory,timestamp,*,kind='ai_frame',task_id=None):
    def copy(output):
        with Path(source).open('rb') as image: shutil.copyfileobj(image,output)
    return _write_unique(directory,timestamp,kind,task_id,copy)
