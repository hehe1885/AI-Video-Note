"""Identify the actual running code, including development edits without version bumps."""
import hashlib
import sys
from pathlib import Path

def code_revision():
    digest=hashlib.sha256()
    if getattr(sys,'frozen',False):
        stat=Path(sys.executable).stat()
        digest.update(f'{stat.st_size}:{stat.st_mtime_ns}'.encode())
    else:
        root=Path(__file__).resolve().parent.parent
        files=list((root/'backend').rglob('*.py'))+[root/'launcher.py']
        files += [root/'frontend'/name for name in ('index.html','style.css')]
        files += list((root/'frontend').glob('*.js'))
        files += list((root/'frontend'/'locales').glob('*.js'))
        for path in sorted(files):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:20]

CODE_REVISION=code_revision()
