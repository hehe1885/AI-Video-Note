"""Stage dependency metadata without installer provenance or Python caches."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import tempfile


def stage_metadata(package, output):
    source = Path(importlib.metadata.distribution(package)._path)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    container = Path(tempfile.mkdtemp(prefix='metadata-', dir=root))
    target = container / source.name
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'direct_url.json'))
    return {'path': str(target), 'name': source.name}


if __name__ == '__main__':
    print(json.dumps(stage_metadata(sys.argv[1], sys.argv[2])))
