import hashlib
import struct
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from backend import asr_policy,whisper_vulkan as v,vulkan_quantization as q,gpu_devices,main

@pytest.mark.parametrize('model,total,free,wanted',[
 ('large-v3',4, None,'int8'),('large-v3',8,None,'float16'),
 ('large-v3',8,2,'int8'),('turbo',2,None,'int8'),('small',2,None,'float16'),
 ('medium',4,None,'float16'),('base',None,None,'float16'),
 ('custom/model',1,None,'float16'),('base',8,0,'int8')])
def test_memory_policy_counts_buffers_and_available_vram(model,total,free,wanted):
    gib=1024**3
    assert asr_policy.select_weight_precision(model,None if total is None else total*gib,
        None if free is None else free*gib)==wanted


def write_model(path,ftype):
    path.parent.mkdir(parents=True,exist_ok=True)
    header=bytearray(48);header[:4]=b'lmgg';struct.pack_into('<i',header,44,ftype)
    with path.open('wb') as out:
        out.write(header);out.truncate(31*1024**2)


def test_precision_files_coexist_and_quantized_type_is_checked(tmp_path):
    fp=v.model_path(tmp_path,'base');quant=v.model_path(tmp_path,'base','q8_0')
    write_model(fp,1);write_model(quant,1)
    assert v.cache_status(tmp_path,'base','float16')=='cached'
    assert v.cache_status(tmp_path,'base','q8_0')=='incomplete'
    write_model(quant,2007)
    assert v.cache_status(tmp_path,'base','q8_0')=='cached'
    assert fp!=quant and fp.exists()


def test_quantization_uses_same_model_and_preserves_fp16(tmp_path,monkeypatch):
    fp=v.model_path(tmp_path,'belle-turbo-zh');write_model(fp,1);before=fp.stat().st_size
    calls=[]
    def convert(root,source,target,download,cancel):
        calls.append((source,target));write_model(target,2007)
    monkeypatch.setattr(q,'quantize_model',convert)
    monkeypatch.setattr(v,'checked_download',lambda *args:pytest.fail('cached FP16 should not redownload'))
    result=v.ensure_model(tmp_path,'belle-turbo-zh',precision='q8_0')
    assert result.name=='ggml-model-q8_0.bin' and calls[0][0]==fp
    assert fp.stat().st_size==before and fp.read_bytes()[:4]==b'lmgg'
    assert v.ensure_model(tmp_path,'belle-turbo-zh',precision='q8_0')==result
    assert len(calls)==1


def test_device_memory_does_not_count_shared_host_heaps_twice():
    props=gpu_devices.MemoryProperties(memoryHeapCount=2)
    props.memoryHeaps[0].size=8*1024**3;props.memoryHeaps[0].flags=1
    props.memoryHeaps[1].size=32*1024**3;props.memoryHeaps[1].flags=0
    assert gpu_devices.device_local_memory(props)==8*1024**3


def test_quantizer_hash_is_checked_before_any_execution(tmp_path):
    folder=tmp_path/'vulkan'/'tools'/f'quantizer-{q.VERSION}';folder.mkdir(parents=True)
    (folder/'upstream.zip').write_bytes(b'untrusted')
    with pytest.raises(RuntimeError,match='校验失败'):
        q.ensure_quantizer(tmp_path,lambda *args:pytest.fail('no download expected'))
    assert not (folder/'whisper-quantize.exe').exists()


def test_verified_archive_rejects_traversal_before_extracting(tmp_path,monkeypatch):
    folder=tmp_path/'vulkan'/'tools'/f'quantizer-{q.VERSION}';folder.mkdir(parents=True)
    archive=folder/'upstream.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('Release/whisper-quantize.exe',b'tool');z.writestr('../escape.dll',b'bad')
    monkeypatch.setattr(q,'ARCHIVE_SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    with pytest.raises(RuntimeError,match='非法路径'):
        q.ensure_quantizer(tmp_path,lambda *args:None)
    assert not (folder/'whisper-quantize.exe').exists()


def test_cancelled_conversion_kills_child_and_preserves_original(tmp_path,monkeypatch):
    from backend.whisper_asr import TranscriptionCancelledError
    source=tmp_path/'source.bin';source.write_bytes(b'original');target=tmp_path/'q8.bin'
    monkeypatch.setattr(q,'ensure_quantizer',lambda *args:tmp_path/'tool.exe')
    event=threading.Event();events=[]
    class Process:
        returncode=None
        def poll(self):return None
        def kill(self):events.append('kill')
        def wait(self):events.append('wait')
    def start(args,**kwargs):
        (tmp_path/args[2]).write_bytes(b'partial');event.set();return Process()
    monkeypatch.setattr(q.subprocess,'Popen',start)
    with pytest.raises(TranscriptionCancelledError):
        q.quantize_model(tmp_path,source,target,None,event)
    assert events==['kill','wait'] and source.read_bytes()==b'original'
    assert not target.exists() and not list(tmp_path.glob('*.tmp'))


def test_download_endpoint_passes_selected_precision(monkeypatch):
    captured=[]
    async def select(model,backend):return 'q8_0'
    monkeypatch.setattr(main,'preferred_vulkan_precision',select)
    monkeypatch.setattr(main,'require_models_idle',lambda:None)
    monkeypatch.setattr(main.model_downloads,'start',lambda *args,**kwargs:captured.append(kwargs) or {'status':'downloading'})
    with TestClient(main.app) as client:
        response=client.post('/api/model-download',json={'model':'large-v3','gpu_backend':'amd'})
    assert response.status_code==200 and captured==[{'precision':'q8_0'}]


def test_status_reports_selected_precision_cache_independently(tmp_path,monkeypatch):
    async def select(model,backend):return 'q8_0'
    monkeypatch.setattr(main,'WHISPER_CACHE_DIR',tmp_path)
    monkeypatch.setattr(main,'preferred_vulkan_precision',select)
    write_model(v.model_path(tmp_path,'base','q8_0'),2007)
    with TestClient(main.app) as client:
        response=client.get('/api/whisper-models?gpu_backend=amd')
    assert response.status_code==200
    base=next(item for item in response.json()['models'] if item['id']=='base')
    assert base['compute_type']=='q8_0' and base['status']=='cached'
    assert base['vulkan_precisions']=={'float16':'missing','q8_0':'cached'}
