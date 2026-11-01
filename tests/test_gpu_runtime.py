import asyncio
import hashlib
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import gpu_runtime as r, main, whisper_vulkan as v


def fake_system(monkeypatch, tmp_path):
    monkeypatch.setattr(r, 'os', SimpleNamespace(**{**vars(os), 'name': 'nt'}))
    monkeypatch.setenv('ProgramW6432', str(tmp_path / 'Program Files'))
    monkeypatch.setenv('PATH', '')
    for key in list(r.os.environ):
        if key.startswith('CUDA_PATH'):
            monkeypatch.delenv(key)


def test_cuda_uses_matching_system_versions_and_reports_partial_install(monkeypatch, tmp_path):
    fake_system(monkeypatch, tmp_path)
    root = r.program_files()
    cuda = root / 'NVIDIA GPU Computing Toolkit' / 'CUDA' / 'v12.9' / 'bin'
    newer = root / 'NVIDIA GPU Computing Toolkit' / 'CUDA' / 'v13.1' / 'bin'
    cudnn = root / 'NVIDIA' / 'CUDNN' / 'v9.27' / 'bin'
    for folder in [cuda, cudnn, newer]: folder.mkdir(parents=True)
    (cuda / 'cublas64_12.dll').write_bytes(b'cublas')
    (newer / 'cublasLt64_12.dll').write_bytes(b'wrong directory')
    (cudnn / 'cudnn64_9.dll').write_bytes(b'cudnn')
    status = r.cuda_status()
    assert status['status'] == 'missing'
    assert status['missing'] == ['cublasLt64_12.dll']
    assert newer.resolve() not in r.cuda_directories()
    (cuda / 'cublasLt64_12.dll').write_bytes(b'cublasLt')
    assert r.cuda_status()['status'] == 'present'
    assert r.cuda_status()['inference_verified'] is False


def test_dll_handles_survive_lazy_inference_with_idempotent_process_path(monkeypatch, tmp_path):
    fake_system(monkeypatch, tmp_path)
    directory = tmp_path / 'system-bin'; directory.mkdir()
    handles = []
    monkeypatch.setattr(r, 'cuda_directories', lambda: [directory])
    monkeypatch.setattr(r, '_handles', {})
    monkeypatch.setattr(r.os, 'add_dll_directory', lambda path: handles.append(object()) or handles[-1], raising=False)
    monkeypatch.setenv('PATH', str(tmp_path / 'other-bin'))
    original = r.os.environ['PATH']
    r.activate_cuda_libraries(); r.activate_cuda_libraries()
    assert len(handles) == 1
    assert list(r._handles.values()) == handles
    assert r.os.environ['PATH'].split(r.os.pathsep) == [str(directory), original]


def test_nuget_override_is_respected_but_relative_path_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv('NUGET_PACKAGES', str(tmp_path / 'shared-packages'))
    assert r.vulkan_package('1.9.1') == tmp_path / 'shared-packages' / 'whisper.net.runtime.vulkan' / '1.9.1'
    monkeypatch.setenv('NUGET_PACKAGES', 'workspace/runtime')
    with pytest.raises(RuntimeError, match='绝对路径'):
        r.nuget_packages()


def make_package(tmp_path, monkeypatch, entries=None):
    monkeypatch.setattr(v, 'os', SimpleNamespace(**{**vars(os), 'name': 'nt'}))
    archive = tmp_path / 'fixture.nupkg'
    with zipfile.ZipFile(archive, 'w') as package:
        for name in v.DLLS: package.writestr('build/win-x64/' + name, b'official fixture')
        for name, data in (entries or {}).items(): package.writestr(name, data)
    monkeypatch.setenv('NUGET_PACKAGES', str(tmp_path / 'global-packages'))
    monkeypatch.setattr(v, 'RUNTIME_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
    calls = []
    def download(url, target, event):
        calls.append(url); target.write_bytes(archive.read_bytes())
    monkeypatch.setattr(v, 'checked_download', download)
    return calls


def test_vulkan_reuses_verified_global_package_and_keeps_model_folder_clean(tmp_path, monkeypatch):
    calls = make_package(tmp_path, monkeypatch)
    models = tmp_path / 'software' / 'models'
    directory = v.ensure_runtime(models)
    assert not models.exists()
    package = directory.parent.parent
    assert (package / '.nupkg.metadata').is_file()
    assert v.ensure_runtime(tmp_path / 'other-software') == directory
    assert len(calls) == 1
    (directory / v.DLLS[0]).write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='校验失败'):
        v.ensure_runtime(models)
    assert (directory / v.DLLS[0]).read_bytes() == b'tampered'  # Do not replace another package manager's files.


def test_vulkan_zip_cannot_escape_global_package(tmp_path, monkeypatch):
    make_package(tmp_path, monkeypatch, {'../../escape.dll': b'bad'})
    with pytest.raises(RuntimeError, match='非法路径'):
        v.ensure_runtime(tmp_path / 'models')
    assert not r.vulkan_package(v.RUNTIME_VERSION).exists()
    assert not (tmp_path / 'escape.dll').exists()


def test_cancelled_vulkan_install_does_not_create_package(tmp_path, monkeypatch):
    make_package(tmp_path, monkeypatch)
    event = threading.Event(); event.set()
    with pytest.raises(v.TranscriptionCancelledError):
        v.ensure_runtime(tmp_path / 'models', event)
    assert not r.vulkan_package(v.RUNTIME_VERSION).exists()


@pytest.mark.asyncio
async def test_auto_vulkan_failure_falls_back_to_cpu_but_cancellation_propagates(monkeypatch, tmp_path):
    async def resolve(*args): return 'vulkan'
    async def broken(*args): raise OSError('driver unavailable')
    calls = []
    async def cpu(*args, **kwargs):
        calls.append(args[2]); return {'device': 'cpu'}
    monkeypatch.setattr(main, 'resolve_backend', resolve)
    monkeypatch.setattr(v, 'transcribe', broken)
    monkeypatch.setattr(main.transcriber, 'transcribe', cpu)
    result = await main.run_transcription_local(tmp_path / 'audio', 'base', True, None, None, gpu_backend='auto')
    assert calls == [False]
    assert 'driver unavailable' in result['fallback_note']
    async def cancel(*args): raise asyncio.CancelledError()
    monkeypatch.setattr(v, 'transcribe', cancel)
    with pytest.raises(asyncio.CancelledError):
        await main.run_transcription_local(tmp_path / 'audio', 'base', True, None, None, gpu_backend='auto')
    assert calls == [False]


@pytest.mark.parametrize('backend', ['cuda', 'vulkan', 'cpu'])
def test_gpu_api_checks_dependencies_without_installing(monkeypatch, tmp_path, backend):
    fake_system(monkeypatch, tmp_path)
    monkeypatch.setenv('NUGET_PACKAGES', str(tmp_path / 'packages'))
    async def detect(_backend): return {'detected': backend != 'cpu', 'name': 'Test GPU', 'backend': backend}
    from backend import gpu_devices
    monkeypatch.setattr(gpu_devices, 'detect', detect)
    with TestClient(main.app) as client:
        response = client.get('/api/gpu-device?backend=auto')
    assert response.status_code == 200
    assert response.json()['runtime']['status'] == {'cuda': 'missing', 'vulkan': 'download_on_first_use', 'cpu': 'not_required'}[backend]
    assert not (tmp_path / 'packages').exists()
