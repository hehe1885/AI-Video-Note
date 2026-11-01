"""Dependency bootstrap for a project environment created from an installed Python."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / 'backend' / 'requirements.txt'


def ensure_pip():
    try:
        import pip
        return
    except ImportError:
        pass
    print('正在安装依赖管理工具…', flush=True)
    subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'], cwd=ROOT, check=False)
    import importlib
    importlib.invalidate_caches()
    try:
        import pip
        return
    except ImportError:
        pass
    with urllib.request.urlopen('https://pypi.org/pypi/pip/26.1.2/json', timeout=60) as response:
        info = json.load(response)
    wheel = next(item for item in info['urls'] if item['filename'].endswith('.whl'))
    with urllib.request.urlopen(wheel['url'], timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != wheel['digests']['sha256']:
        raise RuntimeError('依赖管理工具校验失败，请重试')
    import sysconfig
    site = Path(sysconfig.get_path('purelib'))
    site.mkdir(parents=True, exist_ok=True)
    # Wheel paths must stay inside site-packages.
    with zipfile.ZipFile(__import__('io').BytesIO(data)) as archive:
        for item in archive.infolist():
            target = (site / item.filename).resolve()
            if not target.is_relative_to(site.resolve()):
                raise RuntimeError('下载的安装包路径无效')
        archive.extractall(site)
    sys.path.insert(0, str(site))
    import importlib
    importlib.invalidate_caches()


def missing_requirements():
    from pip._vendor.packaging.requirements import Requirement
    def satisfied(req):
        try:
            return importlib.metadata.version(req.name) in req.specifier
        except importlib.metadata.PackageNotFoundError:
            return False
    missing = []
    for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        complete = satisfied(req)
        if complete and req.extras:
            for entry in importlib.metadata.requires(req.name) or []:
                dependency = Requirement(entry)
                enabled = not dependency.marker or any(dependency.marker.evaluate({'extra':extra}) for extra in req.extras)
                if enabled and not satisfied(dependency):
                    complete = False
                    break
        if not complete:
            missing.append(str(req))
    return missing


def prepare(repair=False):
    ensure_pip()
    missing = missing_requirements()
    dependency_check = subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=ROOT, capture_output=True, text=True)
    if repair or missing or dependency_check.returncode:
        print('正在安装或修复软件依赖，首次启动需要一些时间…', flush=True)
        command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check']
        if repair:
            command += ['--force-reinstall']
        subprocess.run(command + ['-r', str(REQUIREMENTS)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=ROOT, check=True)
    subprocess.run([sys.executable, '-c', 'import uvicorn,fastapi,av,ctranslate2,sherpa_onnx,docx,yt_dlp'], cwd=ROOT, check=True)
    from backend.environment import ensure_node
    ensure_node(ROOT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair', action='store_true')
    options = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    try:
        prepare(options.repair)
    except Exception as exc:
        print(f'环境准备失败：{exc}\n请检查网络后重新双击 start.bat；已经下载的文件会保留。', flush=True)
        sys.exit(1)
