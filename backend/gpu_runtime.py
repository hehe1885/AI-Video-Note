"""Locate external GPU dependencies without changing persistent system settings."""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading

CUDA_GUIDE = 'https://docs.nvidia.com/cuda/archive/12.9.1/cuda-installation-guide-microsoft-windows/index.html'
CUDNN_GUIDE = 'https://docs.nvidia.com/deeplearning/cudnn/installation/latest/windows.html'
VULKAN_GUIDE = 'https://www.nuget.org/packages/Whisper.net.Runtime.Vulkan/1.9.1'
_handles = {}
_lock = threading.Lock()


def program_files() -> Path:
    return Path(os.environ.get('ProgramW6432') or os.environ.get('ProgramFiles') or r'C:\Program Files')


def _version(path):
    return tuple(int(value) for value in re.findall(r'\d+', path.name))


def cuda_directories() -> list[Path]:
    """Prefer CUDA 12 / cuDNN 9 system installs; also honor explicit CUDA paths."""
    cuda = program_files() / 'NVIDIA GPU Computing Toolkit' / 'CUDA'
    cudnn = program_files() / 'NVIDIA' / 'CUDNN'
    candidates = []
    for key, value in os.environ.items():
        if key == 'CUDA_PATH' or key.startswith('CUDA_PATH_V12_'):
            root = Path(value)
            if key != 'CUDA_PATH' or not root.name.lower().startswith('v13'):
                candidates.append(root / 'bin')
    for root in sorted(cuda.glob('v12.*'), key=_version, reverse=True):
        candidates.append(root / 'bin')
    for root in sorted(cudnn.glob('v9.*'), key=_version, reverse=True):
        candidates.append(root / 'bin')
        candidates.extend(sorted((root / 'bin').glob('12*'), key=_version, reverse=True))
    return list(dict.fromkeys(path.resolve() for path in candidates if path.is_dir()))


def activate_cuda_libraries() -> None:
    """Keep DLL search handles alive for lazy CTranslate2 inference in this process."""
    if os.name != 'nt':
        return
    with _lock:
        directories = cuda_directories()
        for directory in directories:
            name = str(directory)
            if name not in _handles:
                _handles[name] = os.add_dll_directory(name)
        # CTranslate2 also uses legacy LoadLibrary calls, which do not consult
        # Python's DLL search handles. Change this process only, never the registry.
        existing = [value for value in os.environ.get('PATH', '').split(os.pathsep) if value]
        leading = [str(path) for path in directories]
        known = {value.casefold() for value in leading}
        os.environ['PATH'] = os.pathsep.join(leading + [value for value in existing if value.casefold() not in known])


def cuda_status() -> dict:
    directories = cuda_directories()
    # Existing PATH installations are usable too; do not change the user's PATH.
    search = directories + [Path(value) for value in os.environ.get('PATH', '').split(os.pathsep) if value]
    required = ('cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn64_9.dll')
    found = {name: next((str(folder / name) for folder in search if (folder / name).is_file()), None)
             for name in required}
    return {'engine': 'cuda', 'status': 'present' if all(found.values()) else 'missing',
            'missing': [name for name, path in found.items() if path is None],
            'paths': list(dict.fromkeys(str(Path(path).parent) for path in found.values() if path)),
            'expected_paths': [str(program_files() / 'NVIDIA GPU Computing Toolkit' / 'CUDA' / 'v12.9' / 'bin'),
                               str(program_files() / 'NVIDIA' / 'CUDNN' / 'v9.27' / 'bin')],
            'guides': [CUDA_GUIDE, CUDNN_GUIDE], 'inference_verified': False}


def nuget_packages() -> Path:
    override = os.environ.get('NUGET_PACKAGES')
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise RuntimeError('NUGET_PACKAGES 必须是绝对路径，运行库不能写入软件目录')
        return path
    return Path.home() / '.nuget' / 'packages'


def vulkan_package(version: str) -> Path:
    return nuget_packages() / 'whisper.net.runtime.vulkan' / version


def dependency_status(backend: str) -> dict:
    if backend == 'cuda':
        return cuda_status()
    if backend == 'vulkan':
        from .whisper_vulkan import DLLS, RUNTIME_VERSION
        path = vulkan_package(RUNTIME_VERSION) / 'build' / 'win-x64'
        return {'engine': 'vulkan',
                'status': 'present' if all((path / name).is_file() for name in DLLS) else 'download_on_first_use',
                'paths': [str(path)], 'guides': [VULKAN_GUIDE], 'inference_verified': False}
    return {'engine': 'cpu', 'status': 'not_required', 'paths': [], 'guides': [], 'inference_verified': False}
