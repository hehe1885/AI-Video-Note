"""Explicit GPU runtime installation jobs. Never run on startup or from a GET."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid

from filelock import FileLock
from . import gpu_runtime as runtime
from .download import download_to_file, report_downloads

CUDA_PACKAGES = [{'package': 'nvidia-cublas-cu12', 'version': '12.9.2.10', 'filename': 'nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl', 'url': 'https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl', 'sha256': '623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661', 'bytes': 553162896, 'component': 'cuda'}, {'package': 'nvidia-cudnn-cu12', 'version': '9.27.0.42', 'filename': 'nvidia_cudnn_cu12-9.27.0.42-py3-none-win_amd64.whl', 'url': 'https://files.pythonhosted.org/packages/aa/38/f856579877f7c1c5066e61182e7de7bc27bf35a78c8d1b0fa592e6985bc4/nvidia_cudnn_cu12-9.27.0.42-py3-none-win_amd64.whl', 'sha256': '06e9b0026f3bad97d2b58666330fabec04fe1672f776661ecb0ce0029c27f142', 'bytes': 743068852, 'component': 'cudnn'}]
CUDA_FILES = [{'component': 'cuda', 'entry': 'nvidia/cublas/bin/cublas64_12.dll', 'destination_relative': 'NVIDIA GPU Computing Toolkit/CUDA/v12.9/bin/cublas64_12.dll', 'sha256': '52ce5ba0ec5327d39be6021f0f9362d2ed4d64d7206f9b5afc06398d827b8e82'}, {'component': 'cuda', 'entry': 'nvidia/cublas/bin/cublasLt64_12.dll', 'destination_relative': 'NVIDIA GPU Computing Toolkit/CUDA/v12.9/bin/cublasLt64_12.dll', 'sha256': '71705a7cf0923f4ab034d0d2620bbfc989ff669ac3d79f868b7a7c597ca559c5'}, {'component': 'cuda', 'entry': 'nvidia/cublas/bin/nvblas64_12.dll', 'destination_relative': 'NVIDIA GPU Computing Toolkit/CUDA/v12.9/bin/nvblas64_12.dll', 'sha256': 'f54ac264becdb8ac866cbc0cce74f1dfe0e0c1f6e60a0629f222490d210302cd'}, {'component': 'cuda', 'entry': 'nvidia_cublas_cu12-12.9.2.10.dist-info/licenses/License.txt', 'destination_relative': 'NVIDIA GPU Computing Toolkit/CUDA/v12.9/License.txt', 'sha256': 'ad6f5853fba0ca0d159d0f58d49ae49830c2f8c93f7a92648b9ce90adb4c6ccd'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn64_9.dll', 'sha256': '7268ef53e32c2c574372326dfc9d94a26095f4ed9c89dba87cfb3ab1e40a6470'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_adv64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_adv64_9.dll', 'sha256': '712ace96559e60b5be81a1097acb7609f601ff4cd0cdef848b013d671753b4b4'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_cnn64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_cnn64_9.dll', 'sha256': 'dff4c446c855b3ab2d486aebf4c11388cf74da14d7bd0bfafb61c3a75481313d'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_engines_precompiled64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_engines_precompiled64_9.dll', 'sha256': '6b603ac45fc018a96f014d0c6cdbc8f480dce2dbe33b8ca38228a4e4c531f3fe'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_engines_runtime_compiled64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_engines_runtime_compiled64_9.dll', 'sha256': 'f92f4776261e4409aa9f84357c40bc3626700292a1d22025746388bea43ae11b'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_engines_tensor_ir64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_engines_tensor_ir64_9.dll', 'sha256': 'd7380a7c4ad90590968e97b4d8b43a616e7e0070bfdeeee04c4d0be85ad52394'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_ext64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_ext64_9.dll', 'sha256': 'f20888488b701e429830e19cf59022e90a0c5dd398723162ea9f320b3ca06636'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_graph64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_graph64_9.dll', 'sha256': '445e8c3e44a6a6035749380009ae35ae8c2bc246428229b79545a25238bb56e2'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_heuristic64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_heuristic64_9.dll', 'sha256': '0634af738266cd36c912e99cd3807ee452450e963bf885be204fdcfb322cfc70'}, {'component': 'cudnn', 'entry': 'nvidia/cudnn/bin/cudnn_ops64_9.dll', 'destination_relative': 'NVIDIA/CUDNN/v9.27/bin/cudnn_ops64_9.dll', 'sha256': '390bfe103a244c714fca5a1320950ba51a3173a7fbd6664ed535ce7367891ccd'}, {'component': 'cudnn', 'entry': 'nvidia_cudnn_cu12-9.27.0.42.dist-info/licenses/License.txt', 'destination_relative': 'NVIDIA/CUDNN/v9.27/License.txt', 'sha256': '49cf79bdb35734b52fe6203013b3bd759f81e998cd32aa2c65c51db9a88c61d2'}]
TERMINAL = {'completed', 'failed'}


def download_directory() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    return (Path(local) if local else Path.home() / 'AppData' / 'Local') / 'AI-Video-Note' / 'gpu-installs'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_download(path: Path, expected: str):
    if not path.is_file() or digest(path) != expected:
        raise RuntimeError(f'安装包校验失败，请重新下载：{path.name}')


def download_cuda(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    for package in CUDA_PACKAGES:
        target = root / package['filename']
        if target.is_file() and digest(target) != package['sha256']:
            target.unlink()
        if not target.is_file():
            ok, error = download_to_file(package['url'], target, {'User-Agent': 'AI Video Note'})
            if not ok:
                raise RuntimeError(f"下载失败：{package['filename']}：{error}")
        try:
            verify_download(target, package['sha256'])
        except RuntimeError:
            target.unlink(missing_ok=True)
            raise


def powershell() -> Path:
    return Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'


def write_admin_script(root: Path) -> tuple[Path, str]:
    from .gpu_runtime_install_script import SCRIPT
    text = SCRIPT.replace('__PACKAGES__', json.dumps(CUDA_PACKAGES)).replace('__FILES__', json.dumps(CUDA_FILES))
    path = root / 'install-nvidia-runtime.ps1'
    temporary = path.with_suffix('.ps1.tmp')
    temporary.write_bytes(text.encode('utf-8-sig'))
    os.replace(temporary, path)
    return path, hashlib.sha256(text.encode('utf-8-sig')).hexdigest()


@contextmanager
def locked_script(path: Path, expected: str):
    """Hold the verified helper read-only across UAC so it cannot be swapped."""
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [c.c_wchar_p, c.c_uint32, c.c_uint32, c.c_void_p,
                                  c.c_uint32, c.c_uint32, c.c_void_p]
    kernel.CreateFileW.restype = c.c_void_p
    kernel.CloseHandle.argtypes = [c.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == c.c_void_p(-1).value:
        raise OSError(c.get_last_error(), '无法锁定系统安装脚本')
    try:
        verify_download(path, expected)
        yield
    finally:
        kernel.CloseHandle(handle)


def run_admin_install(root: Path, job_id: str, progress):
    """Only the fixed helper writes Program Files, after verifying pinned archives again."""
    script, expected = write_admin_script(root)
    with locked_script(script, expected):
        _run_admin_helper(root, job_id, progress, script)


def _run_admin_helper(root: Path, job_id: str, progress, script: Path):
    result = root / f'status-{job_id}.json'
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    arguments = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '"' + str(script) + '"',
                 '-CacheDirectory', '"' + str(root) + '"', '-JobId', job_id]
    command = "$ErrorActionPreference='Stop'; try { $p=Start-Process -FilePath " + quote(powershell())
    command += ' -ArgumentList @(' + ','.join(quote(value) for value in arguments) + ')'
    command += ' -Verb RunAs -WindowStyle Hidden -PassThru -Wait; exit $p.ExitCode } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }'
    encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([str(powershell()), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    started = time.monotonic()
    last = {}
    while process.poll() is None:
        if result.is_file():
            try:
                last = json.loads(result.read_text(encoding='utf-8-sig'))
                if last.get('status') == 'installing':
                    progress('installing', installed_files=last.get('done', 0), total_files=last.get('total', 0))
            except (OSError, ValueError):
                pass
        if time.monotonic() - started > 1800:
            # Do not kill an elevated installer halfway through a file transaction.
            raise RuntimeError('管理员确认或安装等待超时，请等待系统窗口结束后重新检测')
        time.sleep(0.25)
    if result.is_file():
        last = json.loads(result.read_text(encoding='utf-8-sig'))
    if process.returncode != 0 or last.get('status') != 'completed':
        raise RuntimeError(last.get('error') or '管理员确认已取消，或系统安装未完成；可重试或查看手动安装说明')


def probe(engine: str) -> dict:
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--gpu-runtime-probe', engine]
        cwd = Path(sys.executable).resolve().parent
    else:
        command = [sys.executable, '-B', '-m', 'backend.gpu_runtime_install', '--probe', engine]
        cwd = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('组件已准备，但加速验证未完成，请检查驱动或稍后重试') from error
    if result.returncode != 0 or not data.get('ok'):
        raise RuntimeError('组件已准备，但加速验证失败：' + str(data.get('error') or result.returncode))
    return data


def probe_main(engine: str) -> int:
    from .asr_process import use_utf8_pipes
    use_utf8_pipes()
    try:
        if engine == 'cuda':
            from .gpu_devices import enumerate_cuda_devices
            devices = enumerate_cuda_devices()
            if not devices:
                raise RuntimeError('未检测到可用 NVIDIA CUDA 驱动或显卡')
            runtime.activate_cuda_libraries()
            dlls = {name: c.WinDLL(name) for name in ['cublas64_12.dll', 'cudnn64_9.dll']}
            for name, create, destroy in [('cublas64_12.dll', 'cublasCreate_v2', 'cublasDestroy_v2'),
                                           ('cudnn64_9.dll', 'cudnnCreate', 'cudnnDestroy')]:
                library = dlls[name]
                make = getattr(library, create); make.argtypes = [c.POINTER(c.c_void_p)]; make.restype = c.c_int
                release = getattr(library, destroy); release.argtypes = [c.c_void_p]; release.restype = c.c_int
                handle = c.c_void_p()
                code = make(c.byref(handle))
                if code:
                    raise RuntimeError(f'{create} 返回 {code}，请检查驱动及运行库兼容性')
                if handle: release(handle)
        elif engine == 'vulkan':
            from .whisper_vulkan import RUNTIME_VERSION
            from .whisper_vulkan_native import Native
            devices = Native(runtime.vulkan_package(RUNTIME_VERSION) / 'build' / 'win-x64').devices()
            if not devices:
                raise RuntimeError('未检测到可用 Vulkan 显卡，请更新显卡驱动')
        else:
            raise ValueError('不支持的运行库类型')
        print(json.dumps({'ok': True, 'engine': engine, 'devices': devices}, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), flush=True)
        return 1


class GpuRuntimeInstalls:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None
        self.thread = None

    def snapshot(self):
        with self.lock:
            return dict(self.job) if self.job else {'status': 'idle'}

    def start(self, engine: str):
        if os.name != 'nt' or platform.machine().lower() not in {'amd64', 'x86_64'}:
            raise ValueError('一键安装当前支持 Windows 64 位系统')
        if engine not in {'cuda', 'vulkan'}:
            raise ValueError('未检测到可用显卡，请先按手动说明准备显卡驱动')
        with self.lock:
            if self.job and self.job['status'] not in TERMINAL:
                if self.job['engine'] == engine:
                    return self.snapshot()
                raise ValueError('已有另一套组件正在安装，请等待完成')
            self.job = {'id': uuid.uuid4().hex, 'engine': engine, 'status': 'checking',
                        'received': 0, 'total': None, 'error': '', 'file': '', 'verified': False}
            self.thread = threading.Thread(target=self._run, args=(engine, self.job['id']), daemon=True)
            self.thread.start()
            return self.snapshot()

    def _update(self, status, **fields):
        with self.lock:
            self.job.update(status=status, **fields)

    def _run(self, engine, job_id):
        root = download_directory().resolve()
        def progress(path, received, total):
            self._update('downloading', file=Path(path).name, received=received, total=total)
        try:
            root.mkdir(parents=True, exist_ok=True)
            with FileLock(str(root / '.install.lock'), timeout=0), report_downloads(progress):
                if engine == 'cuda':
                    if runtime.cuda_status()['status'] != 'present':
                        self._update('downloading')
                        download_cuda(root)
                        self._update('awaiting_admin')
                        run_admin_install(root, job_id, self._update)
                else:
                    from .whisper_vulkan import ensure_runtime, RUNTIME_VERSION
                    offline = root / f'whisper.net.runtime.vulkan.{RUNTIME_VERSION}.nupkg'
                    self._update('installing')
                    ensure_runtime(root / 'unused-model-path', archive_source=offline if offline.is_file() else None)
                self._update('verifying')
                data = probe(engine)
                # These are application-owned download-cache files, never user-selected paths.
                names = [item['filename'] for item in CUDA_PACKAGES] if engine == 'cuda' else []
                for name in names:
                    try: (root / name).unlink(missing_ok=True)
                    except OSError: pass
            self._update('completed', verified=True, devices=data['devices'])
        except Exception as error:
            self._update('failed', error=str(error), verified=False)


if __name__ == '__main__':
    raise SystemExit(probe_main(sys.argv[-1]))
