"""Project dependency diagnostics and a fixed, local repair operation."""
from __future__ import annotations
import hashlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile


def node_path(root):
    local = Path(root) / '.runtime' / 'node' / 'node.exe'
    candidates = [str(local)] if local.is_file() else []
    system = shutil.which('node')
    if system:
        candidates.append(system)
    for candidate in candidates:
        try:
            flags = {'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
            version = subprocess.check_output([candidate,'--version'],text=True,timeout=5,**flags).strip()
            if int(version.lstrip('v').split('.')[0]) >= 20:
                return candidate
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def ensure_node(root):
    if node_path(root):
        return
    if sys.platform != 'win32':
        raise RuntimeError('请安装 Node.js 20 或更新版本，以支持 YouTube 下载')
    print('正在安装 YouTube 下载所需的运行组件…', flush=True)
    name = 'node-v24.18.0-win-x64.zip'
    base = 'https://nodejs.org/dist/v24.18.0/'
    with urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60) as response:
        lines = response.read().decode().splitlines()
    digest = next(line.split()[0] for line in lines if line.split()[-1] == name)
    folder = Path(root) / '.runtime' / 'node'
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (name + '.part')
    with urllib.request.urlopen(base + name, timeout=120) as response, temporary.open('wb') as output:
        shutil.copyfileobj(response, output)
    with temporary.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
            raise RuntimeError('Node.js 下载校验失败，请重试')
    # Only the executable is needed by yt-dlp; no system installation or PATH mutation.
    with zipfile.ZipFile(temporary) as archive:
        data = archive.read('node-v24.18.0-win-x64/node.exe')
        (folder / 'LICENSE').write_bytes(archive.read('node-v24.18.0-win-x64/LICENSE'))
    staged = folder / 'node.exe.part'
    staged.write_bytes(data)
    staged.replace(folder / 'node.exe')
    temporary.unlink()


def diagnose(root):
    packages = {'fastapi':'网页服务','uvicorn':'启动服务','yt-dlp':'视频下载',
                'yt-dlp-ejs':'YouTube 支持','faster-whisper':'语音识别',
                'sherpa-onnx':'中文专用语音识别','av':'音视频处理','python-docx':'Word 笔记','reportlab':'PDF 导出'}
    rows = []
    for package, label in packages.items():
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = None
        rows.append({'name':label,'ok':bool(version),'detail':version or '尚未安装'})
    node = node_path(root)
    rows.append({'name':'YouTube 运行组件','ok':bool(node),'detail':node or '尚未安装'})
    folder = Path(root) / 'workspace'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.TemporaryFile(dir=folder):
            pass
        writable = True
    except OSError:
        writable = False
    rows.append({'name':'工作文件夹','ok':writable,'detail':str(folder)})
    return {'checks':rows,'ok':all(row['ok'] for row in rows),
            'python_executable':str(sys.executable),'can_repair':not getattr(sys,'frozen',False),
            'driver_note':'显卡驱动由 Windows 管理；自动修复不会安装或替换显卡驱动。'}


class EnvironmentRepair:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {'status':'idle','log':[]}
    def snapshot(self):
        with self.lock:
            return {**self.state,'log':list(self.state['log'])}
    def start(self, root):
        with self.lock:
            if self.state['status'] == 'running':
                raise ValueError('正在修复，请等待完成')
            if getattr(sys,'frozen',False):
                raise ValueError('打包版本的依赖随软件提供，请重新解压完整的软件包')
            self.state = {'status':'running','log':['正在检查并修复环境…']}
        threading.Thread(target=self._run,args=(Path(root),),daemon=True).start()
        return self.snapshot()
    def _run(self, root):
        try:
            options = {'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
            environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            with subprocess.Popen([sys.executable,str(root/'scripts'/'bootstrap.py')],
                    cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    text=True,encoding='utf-8',errors='replace',env=environment,**options) as process:
                for line in process.stdout:
                    with self.lock:
                        self.state['log'] = (self.state['log'] + [line.rstrip()])[-60:]
                code = process.wait()
            with self.lock:
                self.state['status'] = 'completed' if code == 0 else 'failed'
        except Exception as exc:
            with self.lock:
                self.state['status'] = 'failed'
                self.state['log'].append(str(exc))
