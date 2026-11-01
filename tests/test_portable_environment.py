import asyncio
import http.server
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend import main, model_storage
from backend.download import download_to_file, report_downloads
from backend.model_downloads import ModelDownloads
from backend.environment import EnvironmentRepair, node_path


def test_download_pause_and_resume_keeps_partial_and_reports_progress(tmp_path):
    payload = b'video-model' * 50000
    ranges = []
    cancel = threading.Event()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            start = int(self.headers.get('Range', 'bytes=0-').split('=')[1].split('-')[0])
            ranges.append(start)
            self.send_response(206 if start else 200)
            self.send_header('Content-Length', str(len(payload)-start))
            if start:
                self.send_header('Content-Range', f'bytes {start}-{len(payload)-1}/{len(payload)}')
            self.end_headers()
            try:
                for offset in range(start, len(payload), 8192):
                    self.wfile.write(payload[offset:offset+8192])
                    self.wfile.flush()
                    time.sleep(.001)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    target = tmp_path/'model.bin'
    progress = []
    def callback(path, received, total):
        progress.append((received,total))
        if received > 100000:
            cancel.set()
    url = f'http://127.0.0.1:{server.server_port}/model'
    try:
        with report_downloads(callback), pytest.raises(asyncio.CancelledError):
            download_to_file(url,target,{},cancel)
        assert not target.exists()
        partial = target.with_suffix('.bin.part').stat().st_size
        assert 0 < partial < len(payload)
        cancel.clear()
        resumed = []
        with report_downloads(lambda p,r,t:resumed.append((r,t))):
            assert download_to_file(url,target,{},cancel)[0]
        assert target.read_bytes() == payload
        assert ranges[-1] == partial
        assert resumed[0] == (partial,len(payload))
        assert resumed[-1] == (len(payload),len(payload))
    finally:
        server.shutdown()
        server.server_close()


def test_model_job_pause_retry_and_error(tmp_path):
    manager = ModelDownloads()
    entered = threading.Event()
    def download(model, cancel):
        entered.set()
        cancel.wait(2)
        raise asyncio.CancelledError()
    engine = SimpleNamespace(_cached_model_path=lambda _:None,_download_model_files=download)
    manager.start('tiny','cuda',tmp_path,engine,None)
    assert entered.wait(1)
    with pytest.raises(ValueError): manager.start('base','cuda',tmp_path,engine,None)
    manager.pause()
    manager.thread.join(2)
    assert manager.snapshot()['status'] == 'paused'
    engine._download_model_files = lambda model,cancel: tmp_path
    manager.start('tiny','cuda',tmp_path,engine,None)
    manager.thread.join(2)
    assert manager.snapshot()['status'] == 'completed'
    engine._download_model_files = lambda model,cancel: None
    engine._last_download_error = 'network unavailable'
    manager.start('tiny','cuda',tmp_path,engine,None)
    manager.thread.join(2)
    assert manager.snapshot()['status'] == 'failed'
    assert 'network unavailable' in manager.snapshot()['error']


def test_download_and_repair_guard_model_mutations_and_tasks(monkeypatch):
    manager = ModelDownloads()
    manager.job = {'status':'downloading'}
    monkeypatch.setattr(main,'model_downloads',manager)
    client = TestClient(main.app)
    assert client.delete('/api/whisper-models/tiny').status_code == 409
    assert client.put('/api/whisper-models/directory',json={'directory':''}).status_code == 409
    assert client.post('/api/environment/repair').status_code == 409
    assert client.post('/api/summarize',json={'video_url':'https://youtu.be/jNQXAC9IVRw','llm_config':{}}).status_code == 409


def test_download_rejects_unknown_and_incompatible_models(monkeypatch):
    monkeypatch.setattr(main,'model_downloads',ModelDownloads())
    monkeypatch.setattr(main,'tasks',{})
    monkeypatch.setattr(main,'running_jobs',{})
    client = TestClient(main.app)
    assert client.post('/api/model-download',json={'model':'../../escape'}).status_code == 422
    assert client.post('/api/model-download',json={'model':'paraformer-zh','gpu_backend':'vulkan'}).status_code == 422


def test_project_local_model_location_survives_move(tmp_path):
    original = tmp_path/'original'
    workspace = original/'workspace'
    workspace.mkdir(parents=True)
    model_storage.save_directory(workspace,str(original/'my models'))
    moved = tmp_path/'moved folder'
    original.rename(moved)
    assert model_storage.load_directory(moved/'workspace',moved/'default') == moved/'my models'


def test_frozen_repair_cannot_launch_exe_as_python(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    with pytest.raises(ValueError,match='打包版本'):
        EnvironmentRepair().start(tmp_path)


def test_local_node_is_preferred_and_old_node_rejected(tmp_path, monkeypatch):
    from backend import environment
    local = tmp_path/'.runtime'/'node'/'node.exe'
    local.parent.mkdir(parents=True)
    local.write_bytes(b'fake')
    monkeypatch.setattr(environment.shutil,'which',lambda _: 'system-node')
    monkeypatch.setattr(environment.subprocess,'check_output',lambda args,**kw: 'v24.18.0' if str(args[0]) == str(local) else 'v18.0.0')
    assert node_path(tmp_path) == str(local)
    local.unlink()
    assert node_path(tmp_path) is None

def test_bootstrap_detects_missing_optional_runtime_dependency(tmp_path, monkeypatch):
    from scripts import bootstrap
    requirements = tmp_path/'requirements.txt'
    requirements.write_text('yt-dlp[default]>=2026.7.4\n',encoding='utf-8')
    monkeypatch.setattr(bootstrap,'REQUIREMENTS',requirements)
    monkeypatch.setattr(bootstrap.importlib.metadata,'requires',lambda name:['yt-dlp-ejs==0.8.0; extra == "default"'])
    def version(name):
        if name == 'yt-dlp':
            return '2026.8.19'
        raise bootstrap.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(bootstrap.importlib.metadata,'version',version)
    assert bootstrap.missing_requirements() == ['yt-dlp[default]>=2026.7.4']

def test_node_component_install_verifies_archive_and_keeps_system_path(tmp_path, monkeypatch):
    import hashlib
    import io
    import os
    import zipfile
    from backend import environment
    archive = io.BytesIO()
    with zipfile.ZipFile(archive,'w') as package:
        package.writestr('node-v24.18.0-win-x64/node.exe',b'node executable')
        package.writestr('node-v24.18.0-win-x64/LICENSE',b'license')
    data = archive.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(environment,'node_path',lambda _:None)
    monkeypatch.setattr(environment.sys,'platform','win32')
    def open_url(url, **kwargs):
        return io.BytesIO(f'{digest}  node-v24.18.0-win-x64.zip\n'.encode() if url.endswith('.txt') else data)
    monkeypatch.setattr(environment.urllib.request,'urlopen',open_url)
    before = os.environ.get('PATH')
    environment.ensure_node(tmp_path)
    assert (tmp_path/'.runtime/node/node.exe').read_bytes() == b'node executable'
    assert (tmp_path/'.runtime/node/LICENSE').read_bytes() == b'license'
    assert os.environ.get('PATH') == before
    assert not list((tmp_path/'.runtime/node').glob('*.part'))


def test_model_download_api_uses_selected_directory(tmp_path, monkeypatch):
    manager = ModelDownloads()
    calls = []
    def download(model, cancel):
        calls.append(model)
        return tmp_path
    engine = SimpleNamespace(_cached_model_path=lambda _:None,_download_model_files=download)
    monkeypatch.setattr(main,'model_downloads',manager)
    monkeypatch.setattr(main,'transcriber',engine)
    monkeypatch.setattr(main,'WHISPER_CACHE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{})
    monkeypatch.setattr(main,'running_jobs',{})
    client = TestClient(main.app)
    response = client.post('/api/model-download',json={'model':'tiny','gpu_backend':'cuda'})
    assert response.status_code == 200
    manager.thread.join(2)
    state = client.get('/api/model-download').json()
    assert state['status'] == 'completed'
    assert state['model'] == 'tiny'
    assert calls == ['tiny']
