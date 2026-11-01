import asyncio
import ctypes as c
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend import main, whisper_vulkan as v

@pytest.mark.parametrize('backend,name,index', [('intel','Intel Iris Xe',1), ('amd','AMD Radeon Graphics',2), ('vulkan','NVIDIA GeForce',0)])
def test_selects_requested_vendor_including_integrated(backend,name,index):
    devices=[{'name':'NVIDIA GeForce','index':0},{'name':'Intel Iris Xe','index':1},{'name':'AMD Radeon Graphics','index':2}]
    assert v.select_device(devices,backend) == {'name':name,'index':index}
    assert v.select_device([],backend) is None

@pytest.mark.asyncio
async def test_new_backend_is_always_isolated(monkeypatch,tmp_path):
    calls=[]
    async def child(*args,**kwargs):
        calls.append(kwargs)
        return {'device':'Vulkan'}
    monkeypatch.setattr(main.asr_process,'use_child_process',lambda:False)
    monkeypatch.setattr(main.asr_process,'run_in_child',child)
    assert await main.run_transcription(tmp_path/'audio.wav','base',True,None,None,gpu_backend='intel') == {'device':'Vulkan'}
    assert calls == [{'gpu_backend':'intel'}]

@pytest.mark.asyncio
async def test_local_worker_dispatches_vulkan(monkeypatch,tmp_path):
    calls=[]
    async def fake(*args):
        calls.append(args)
        return {'device':'Intel'}
    monkeypatch.setattr(v,'transcribe',fake)
    await main.run_transcription_local(tmp_path/'audio.wav','base',True,None,None,gpu_backend='intel')
    assert calls[0][3] == 'intel'

@pytest.mark.parametrize('backend',['amd','intel','vulkan'])
def test_api_rejects_incompatible_models(backend):
    with TestClient(main.app) as client:
        result=client.post('/api/transcribe',json={'video_url':'https://example.com/video','whisper_model':'paraformer-zh','use_gpu':True,'gpu_backend':backend})
        assert result.status_code == 422
        assert 'Whisper' in result.json()['detail']

def test_vulkan_status_is_distinct_from_ct2(monkeypatch,tmp_path):
    monkeypatch.setattr(main,'WHISPER_CACHE_DIR',tmp_path)
    monkeypatch.setattr(main.transcriber,'_model_cache_status',lambda model:'cached')
    with TestClient(main.app) as client:
        data=client.get('/api/whisper-models?gpu_backend=intel').json()
    models={m['id']:m['status'] for m in data['models']}
    assert models['base'] == 'missing'
    assert models['paraformer-zh'] == 'unsupported'
    assert models['belle-turbo-zh'] == 'missing'
    items={m['id']:m for m in data['models']}
    assert items['base']['installation_status'] == 'cached'
    assert items['base']['formats']['vulkan'] == 'missing'
    assert items['belle-turbo-zh']['installation_status'] == 'cached'
    assert items['belle-turbo-zh']['compatible'] is True

def test_manual_vulkan_import(monkeypatch,tmp_path):
    monkeypatch.setattr(main,'WHISPER_CACHE_DIR',tmp_path)
    with TestClient(main.app) as client:
        result=client.post('/api/whisper-models/manual-folder',json={'model':'base','gpu_backend':'intel','open':False})
    assert result.status_code == 200
    folder=result.json()['folders'][0]
    assert Path(folder['path']) == tmp_path/'vulkan'/'base'
    assert folder['files'] == ['ggml-base.bin']

def test_cancelled_before_install(tmp_path):
    event=threading.Event(); event.set()
    with pytest.raises(v.TranscriptionCancelledError):
        v._transcribe(tmp_path,tmp_path/'absent.wav','base','intel',None,event,None)

def test_corrupt_native_model_not_cached(tmp_path):
    path=v.model_path(tmp_path,'base');path.parent.mkdir(parents=True)
    path.write_bytes(b'not a model')
    assert v.cache_status(tmp_path,'base') == 'incomplete'

# ABI layout is pinned to the downloaded runtime/header, never a moving latest library.
def test_pinned_abi_layout():
    from backend.whisper_vulkan_native import Context, Params
    assert c.sizeof(Context) == 48
    assert c.sizeof(Params) == 304


@pytest.mark.parametrize('vendor', ['vulkan', 'intel', 'amd'])
def test_discrete_gpu_wins_even_if_integrated_is_listed_first(vendor):
    prefix={'vulkan':'NVIDIA', 'intel':'Intel', 'amd':'AMD'}[vendor]
    integrated={'name':prefix+' Integrated', 'index':0, 'integrated':True}
    discrete={'name':prefix+' Discrete', 'index':1, 'integrated':False}
    assert v.select_device([integrated,discrete],vendor) is discrete
    assert v.select_device([integrated],vendor) is integrated


def test_runtime_installer_verifies_archive_before_extracting(tmp_path,monkeypatch):
    import hashlib, zipfile
    monkeypatch.setenv('NUGET_PACKAGES', str(tmp_path/'packages'))
    archive=tmp_path/'runtime.nupkg'
    with zipfile.ZipFile(archive,'w') as z:
        for name in v.DLLS:
            z.writestr('build/win-x64/'+name,b'test runtime')
    monkeypatch.setattr(v,'RUNTIME_SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    def download(url,target,cancel):
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(archive.read_bytes())
    monkeypatch.setattr(v,'checked_download',download)
    directory=v.ensure_runtime(tmp_path/'cache')
    assert all((directory/name).read_bytes()==b'test runtime' for name in v.DLLS)
    assert directory == tmp_path/'packages'/'whisper.net.runtime.vulkan'/v.RUNTIME_VERSION/'build'/'win-x64'
    monkeypatch.setenv('NUGET_PACKAGES', str(tmp_path/'bad-packages'))
    monkeypatch.setattr(v,'RUNTIME_SHA256','invalid hash')
    with pytest.raises(RuntimeError,match='校验失败'):
        v.ensure_runtime(tmp_path/'bad-cache')
    assert not (tmp_path/'bad-cache'/'vulkan'/f'runtime-{v.RUNTIME_VERSION}'/'whisper.dll').exists()
