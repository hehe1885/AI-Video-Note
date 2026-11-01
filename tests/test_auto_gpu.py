import asyncio
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend import gpu_devices, main
from backend.whisper_asr import CudaUnavailableError, WhisperTranscriber


@pytest.mark.parametrize('devices,expected',[
    ([{'name':'Intel Iris','integrated':True},{'name':'NVIDIA RTX','integrated':False}], 'NVIDIA RTX'),
    ([{'name':'NVIDIA integrated','integrated':True},{'name':'AMD Radeon','integrated':False}], 'AMD Radeon'),
    ([{'name':'AMD Radeon','integrated':False},{'name':'NVIDIA RTX','integrated':False}], 'NVIDIA RTX'),
    ([{'name':'Intel Iris','integrated':True}], 'Intel Iris'),
    ([],None),
])
def test_automatic_choice_prefers_discrete_then_cuda(devices,expected):
    selected = gpu_devices.automatic_device(devices)
    assert (selected['name'] if selected else None) == expected


@pytest.mark.asyncio
async def test_detect_auto_reports_cuda_without_requiring_vulkan(monkeypatch):
    async def probe(): return [{'name':'GeForce RTX','integrated':False,'cuda':True}]
    monkeypatch.setattr(gpu_devices,'probe_devices',probe)
    assert await gpu_devices.detect('auto') == {'detected':True,'name':'GeForce RTX','backend':'cuda'}


@pytest.mark.asyncio
@pytest.mark.parametrize('device,model,expected',[
    ('NVIDIA RTX','belle-turbo-zh','cuda'),('NVIDIA RTX','paraformer-zh','cpu'),
    ('AMD Radeon','belle-turbo-zh','vulkan'),('AMD Radeon','base','vulkan'),
    ('Intel Iris','base','vulkan'),(None,'base','cpu'),
])
async def test_auto_resolution_accounts_for_model(monkeypatch,device,model,expected):
    async def probe(): return [{'name':device,'integrated':False}] if device else []
    monkeypatch.setattr(gpu_devices,'probe_devices',probe)
    assert await gpu_devices.resolve_backend('auto',model) == expected


@pytest.mark.asyncio
async def test_auto_cuda_success_and_fallbacks(monkeypatch,tmp_path):
    async def resolve(backend,model=None): return 'cuda'
    monkeypatch.setattr(main,'resolve_backend',resolve)
    calls=[]
    async def cuda(media,model,use_gpu,**options):
        calls.append(('whisper',model,use_gpu,options))
        return {'device':'cuda'}
    async def vulkan(*args):
        calls.append(('vulkan',args[2],args[3]))
        return {'device':'Vulkan','fallback_note':None}
    monkeypatch.setattr(main.transcriber,'transcribe',cuda)
    monkeypatch.setattr(main.whisper_vulkan,'transcribe',vulkan)
    result=await main.run_transcription_local(tmp_path/'audio','belle-turbo-zh',True,None,None,gpu_backend='auto')
    assert result['device']=='cuda'
    assert calls[0][3]['allow_cpu_fallback'] is False
    assert len(calls)==1
    calls.clear()
    async def failing_cuda(media,model,use_gpu,**options):
        calls.append(('whisper',model,use_gpu))
        if use_gpu: raise CudaUnavailableError('cudnn unavailable')
        return {'device':'cpu'}
    monkeypatch.setattr(main.transcriber,'transcribe',failing_cuda)
    result=await main.run_transcription_local(tmp_path/'audio','base',True,None,None,gpu_backend='auto')
    assert result['device']=='Vulkan'
    assert calls==[('whisper','base',True),('vulkan','base','vulkan')]
    assert 'CUDA 不可用' in result['fallback_note']
    calls.clear()
    result=await main.run_transcription_local(tmp_path/'audio','belle-turbo-zh',True,None,None,gpu_backend='auto')
    assert result['device']=='Vulkan'
    assert calls==[('whisper','belle-turbo-zh',True),('vulkan','belle-turbo-zh','vulkan')]
    assert 'CUDA 不可用' in result['fallback_note']


@pytest.mark.asyncio
async def test_auto_paraformer_uses_cpu(monkeypatch,tmp_path):
    async def probe(): return [{'name':'NVIDIA RTX','integrated':False}]
    monkeypatch.setattr(gpu_devices,'probe_devices',probe)
    calls=[]
    async def paraformer(media,model,use_gpu,**options):
        calls.append(use_gpu)
        return {'device':'cpu'}
    monkeypatch.setattr(main.paraformer_transcriber,'transcribe',paraformer)
    result=await main.run_transcription_local(tmp_path/'audio','paraformer-zh',True,None,None,gpu_backend='auto')
    assert result['device']=='cpu'
    assert calls==[False]


@pytest.mark.parametrize('vendor,belle_engine',[('NVIDIA RTX','cuda'),('AMD Radeon','vulkan')])
def test_auto_model_status_keeps_chinese_models_selectable(monkeypatch,vendor,belle_engine):
    async def probe(): return [{'name':vendor,'integrated':False}]
    monkeypatch.setattr(gpu_devices,'probe_devices',probe)
    with TestClient(main.app) as client:
        response=client.get('/api/whisper-models?gpu_backend=auto')
    assert response.status_code==200
    models={m['id']:m for m in response.json()['models']}
    assert models['belle-turbo-zh']['compatible'] is True
    assert models['belle-turbo-zh']['acceleration']==belle_engine
    assert models['paraformer-zh']['compatible'] is True
    assert models['paraformer-zh']['acceleration']=='cpu'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['load','generator'])
async def test_cuda_failover_happens_before_cpu_inference(monkeypatch,tmp_path,stage):
    constructed=[]
    class Model:
        def __init__(self,*args,**options):
            self.device=options['device']
            constructed.append(self.device)
            if stage=='load' and self.device=='cuda':
                raise RuntimeError('CUDA driver unavailable')
        def transcribe(self,*args,**kwargs):
            def segments():
                raise RuntimeError('Library cudnn64_9.dll is not found')
                yield
            return segments(),SimpleNamespace(duration=10)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model))
    transcriber=WhisperTranscriber(tmp_path)
    monkeypatch.setattr(transcriber,'_download_model_files',lambda *args:None)
    with pytest.raises(CudaUnavailableError):
        await transcriber.transcribe(tmp_path/'audio','base',True,allow_cpu_fallback=False)
    assert constructed==['cuda']
    assert ('base','cuda') not in transcriber._models


@pytest.mark.asyncio
async def test_lazy_cuda_failure_still_falls_back_in_manual_mode(monkeypatch,tmp_path):
    class Model:
        def __init__(self,*args,**options): self.device=options['device']
        def transcribe(self,*args,**kwargs):
            def segments():
                if self.device=='cuda': raise RuntimeError('CUDA failure in iterator')
                yield SimpleNamespace(start=0,end=1,text='hello')
            return segments(),SimpleNamespace(duration=1,duration_after_vad=1,language='en',language_probability=1)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model))
    transcriber=WhisperTranscriber(tmp_path)
    monkeypatch.setattr(transcriber,'_download_model_files',lambda *args:None)
    result=await transcriber.transcribe(tmp_path/'audio','base',True)
    assert result['device']=='cpu'
    assert result['text']=='hello'
