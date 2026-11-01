import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient
from backend import gpu_runtime_install as i, main, gpu_devices, whisper_vulkan as v


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(i, 'download_directory', lambda: tmp_path / 'download-cache')


def finish(manager):
    manager.thread.join(5)
    assert not manager.thread.is_alive()
    return manager.snapshot()


def test_healthy_cuda_is_verified_without_downloading_or_elevation(monkeypatch):
    monkeypatch.setattr(i.runtime, 'cuda_status', lambda: {'status': 'present'})
    monkeypatch.setattr(i, 'download_cuda', lambda *_: pytest.fail('unnecessary download'))
    monkeypatch.setattr(i, 'run_admin_install', lambda *_: pytest.fail('unnecessary UAC'))
    monkeypatch.setattr(i, 'probe', lambda engine: {'ok': True, 'devices': [{'name': 'Test GPU'}]})
    manager = i.GpuRuntimeInstalls(); manager.start('cuda')
    assert finish(manager)['verified'] is True
    assert manager.snapshot()['status'] == 'completed'


def test_missing_cuda_downloads_installs_then_probes(monkeypatch):
    calls = []
    monkeypatch.setattr(i.runtime, 'cuda_status', lambda: {'status': 'missing'})
    monkeypatch.setattr(i, 'download_cuda', lambda root: calls.append('download'))
    def install(root, job, progress):
        calls.append('install'); progress('installing', installed_files=2, total_files=15)
    monkeypatch.setattr(i, 'run_admin_install', install)
    def probe(engine): calls.append('probe'); return {'devices': []}
    monkeypatch.setattr(i, 'probe', probe)
    manager = i.GpuRuntimeInstalls(); manager.start('cuda')
    assert finish(manager)['status'] == 'completed'
    assert calls == ['download', 'install', 'probe']


@pytest.mark.parametrize('failure_stage', ['download', 'uac', 'probe'])
def test_failure_never_claims_installation_success(monkeypatch, failure_stage):
    calls = []
    monkeypatch.setattr(i.runtime, 'cuda_status', lambda: {'status': 'missing'})
    def action(stage):
        calls.append(stage)
        if stage == failure_stage: raise RuntimeError('simulated ' + stage + ' failure')
    monkeypatch.setattr(i, 'download_cuda', lambda root: action('download'))
    monkeypatch.setattr(i, 'run_admin_install', lambda *args: action('uac'))
    monkeypatch.setattr(i, 'probe', lambda engine: action('probe') or {'devices': []})
    manager = i.GpuRuntimeInstalls(); manager.start('cuda')
    state = finish(manager)
    assert state['status'] == 'failed' and state['verified'] is False
    assert failure_stage in state['error']
    assert calls[-1] == failure_stage


def test_duplicate_clicks_share_job_and_other_engine_cannot_race(monkeypatch):
    waiting = threading.Event(); release = threading.Event()
    monkeypatch.setattr(i.runtime, 'cuda_status', lambda: {'status': 'present'})
    def probe(engine):
        waiting.set(); assert release.wait(4); return {'devices': []}
    monkeypatch.setattr(i, 'probe', probe)
    manager = i.GpuRuntimeInstalls(); first = manager.start('cuda')
    try:
        assert waiting.wait(3)
        assert manager.start('cuda')['id'] == first['id']
        with pytest.raises(ValueError, match='另一套'):
            manager.start('vulkan')
    finally: release.set()
    assert finish(manager)['status'] == 'completed'


def test_download_checks_hash_and_reuses_valid_local_package(monkeypatch, tmp_path):
    payload = b'official release fixture'
    monkeypatch.setattr(i, 'CUDA_PACKAGES', [{'filename': 'fixture.whl', 'url': 'https://example.test/file', 'sha256': hashlib.sha256(payload).hexdigest()}])
    root = tmp_path / 'cache'; root.mkdir(); (root / 'fixture.whl').write_bytes(payload)
    monkeypatch.setattr(i, 'download_to_file', lambda *_: pytest.fail('valid offline package should be reused'))
    i.download_cuda(root)
    (root / 'fixture.whl').write_bytes(b'damaged')
    def corrupt(url, path, headers): path.write_bytes(b'corrupt download'); return True, None
    monkeypatch.setattr(i, 'download_to_file', corrupt)
    with pytest.raises(RuntimeError, match='校验失败'):
        i.download_cuda(root)
    assert not (root / 'fixture.whl').exists()


def test_vulkan_one_click_does_not_download_models_or_use_uac(monkeypatch):
    calls = []
    monkeypatch.setattr(v, 'ensure_runtime', lambda root, **kwargs: calls.append('runtime'))
    monkeypatch.setattr(v, 'ensure_model', lambda *_: pytest.fail('installation must not download an ASR model'))
    monkeypatch.setattr(i, 'run_admin_install', lambda *_: pytest.fail('Vulkan does not need UAC'))
    monkeypatch.setattr(i, 'probe', lambda engine: {'devices': [{'name': 'Intel Test'}]})
    manager = i.GpuRuntimeInstalls(); manager.start('vulkan')
    assert finish(manager)['status'] == 'completed'
    assert calls == ['runtime']


def test_offline_vulkan_archive_is_verified_before_install(tmp_path, monkeypatch):
    root = tmp_path / 'packages'; monkeypatch.setenv('NUGET_PACKAGES', str(root))
    archive = tmp_path / 'offline.nupkg'
    with zipfile.ZipFile(archive, 'w') as package:
        for name in v.DLLS: package.writestr('build/win-x64/' + name, b'pinned runtime')
    monkeypatch.setattr(v, 'RUNTIME_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(v, 'checked_download', lambda *_: pytest.fail('offline install should not use network'))
    folder = v.ensure_runtime(tmp_path / 'models', archive_source=archive)
    assert (folder / 'whisper.dll').read_bytes() == b'pinned runtime'
    monkeypatch.setenv('NUGET_PACKAGES', str(tmp_path / 'bad-packages'))
    archive.write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='校验失败'):
        v.ensure_runtime(tmp_path / 'models', archive_source=archive)
    assert not i.runtime.vulkan_package(v.RUNTIME_VERSION).exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows file-sharing rules')
def test_admin_helper_cannot_be_modified_while_uac_is_pending(tmp_path):
    path, expected = i.write_admin_script(tmp_path)
    assert i.digest(path) == expected
    with i.locked_script(path, expected):
        with pytest.raises(PermissionError): path.write_bytes(b'replaced')
        with pytest.raises(PermissionError): path.unlink()
    path.write_bytes(b'replaced')
    with pytest.raises(RuntimeError, match='校验失败'):
        with i.locked_script(path, expected): pytest.fail('tampered helper must not run')
    path.unlink()  # The error path must release the file handle.


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell validation')
def test_real_powershell_validates_pinned_archive_without_system_writes(tmp_path, monkeypatch):
    archive = tmp_path / 'fixture.whl'
    with zipfile.ZipFile(archive, 'w') as package: package.writestr('bin/fake.dll', b'test')
    monkeypatch.setattr(i, 'CUDA_PACKAGES', [{'component': 'cuda', 'filename': archive.name, 'sha256': i.digest(archive)}])
    monkeypatch.setattr(i, 'CUDA_FILES', [{'component': 'cuda', 'entry': 'bin/fake.dll', 'sha256': hashlib.sha256(b'test').hexdigest(),
        'destination_relative': 'NVIDIA GPU Computing Toolkit/CUDA/v12.9/bin/fake-test-runtime.dll'}])
    script, _ = i.write_admin_script(tmp_path)
    job_id = 'a' * 32
    command = [str(i.powershell()), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
               '-CacheDirectory', str(tmp_path), '-JobId', job_id, '-ValidateOnly']
    result = subprocess.run(command, capture_output=True, timeout=20)
    state = json.loads((tmp_path / ('status-' + job_id + '.json')).read_text(encoding='utf-8-sig'))
    assert result.returncode == 0 and state['status'] == 'validated', (result.stderr, state)
    archive.write_bytes(b'tampered')
    result = subprocess.run(command, capture_output=True, timeout=20)
    state = json.loads((tmp_path / ('status-' + job_id + '.json')).read_text(encoding='utf-8-sig'))
    assert result.returncode != 0 and state['status'] == 'failed'
    assert 'checksum' in state['error'].lower()


def test_status_get_does_not_install_and_cross_origin_posts_are_rejected(monkeypatch):
    manager = i.GpuRuntimeInstalls()
    monkeypatch.setattr(main, 'gpu_runtime_installs', manager)
    monkeypatch.setattr(manager, 'start', lambda engine: pytest.fail('no installation authorized'))
    with TestClient(main.app) as client:
        assert client.get('/api/gpu-runtime-install').json()['status'] == 'idle'
        assert client.post('/api/gpu-runtime-install', json={'backend': 'auto'}, headers={'origin': 'https://other.example'}).status_code == 403
        assert client.post('/api/gpu-runtime-install', json={'backend': 'cuda', 'url': 'https://other.example'}).status_code == 422


def test_api_auto_selects_vulkan_on_amd_without_installing_cuda(monkeypatch):
    calls = []
    async def detect(backend): return {'backend': 'vulkan', 'detected': True, 'name': 'AMD Radeon'}
    monkeypatch.setattr(gpu_devices, 'detect', detect)
    manager = i.GpuRuntimeInstalls()
    monkeypatch.setattr(main, 'gpu_runtime_installs', manager)
    monkeypatch.setattr(manager, 'start', lambda engine: calls.append(engine) or {'id': 'test', 'engine': engine, 'status': 'checking'})
    with TestClient(main.app) as client:
        assert client.post('/api/gpu-runtime-install', json={'backend': 'auto'}).status_code == 200
        assert client.post('/api/gpu-runtime-install', json={'backend': 'intel'}).status_code == 422
    assert calls == ['vulkan']


def test_probe_flag_bypasses_tray_and_launcher_startup(monkeypatch):
    import launcher
    calls = []
    monkeypatch.setattr(sys, 'argv', ['AI-Video-Note.exe', '--gpu-runtime-probe', 'cuda'])
    monkeypatch.setattr(i, 'probe_main', lambda engine: calls.append(engine) or 0)
    monkeypatch.setattr(launcher, 'configure_runtime_dirs', lambda: pytest.fail('probe must not start the app'))
    assert launcher.main() == 0
    assert calls == ['cuda']
