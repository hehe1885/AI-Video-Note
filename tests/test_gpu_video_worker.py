import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import av
import pytest

from backend import gpu_devices, gpu_video_worker, video_codecs
from backend.video_processor import VideoProcessor
from tests.media_fixture import make_av_video


@pytest.mark.parametrize('devices,expected',[
    ([{'name':'Intel Iris','integrated':True},{'name':'NVIDIA RTX','integrated':False,'cuda_index':1}],'nvenc'),
    ([{'name':'AMD Radeon','integrated':False},{'name':'NVIDIA integrated','integrated':True}],'amf'),
    ([{'name':'Intel Arc','integrated':False},{'name':'Intel Iris','integrated':True}],'qsv'),
    ([{'name':'Intel Iris','integrated':True}],'qsv'),
    ([{'name':'AMD Radeon integrated','integrated':True}],'amf'),
])
def test_shared_gpu_inventory_orders_vendor_codecs(devices,expected):
    candidates=video_codecs.encoder_candidates(devices,'h264')
    assert candidates[0]['encoder']=='h264_'+expected
    assert len(candidates)==3
    if expected=='nvenc':assert candidates[0]['options']=={'gpu':'1'}


def test_probe_tries_integrated_gpu_when_discrete_backend_fails(monkeypatch):
    calls=[]
    def run(command,**kwargs):
        candidate=json.loads(command[-1]);calls.append(candidate['encoder'])
        success=candidate['encoder']=='hevc_qsv'
        return SimpleNamespace(returncode=0,stdout=json.dumps({'available':success,'encoding_available':success,
            'encoder':candidate['encoder'] if success else None,'decoder':'d3d11va' if success else None}).encode())
    monkeypatch.setattr(video_codecs.subprocess,'run',run)
    monkeypatch.setattr(video_codecs,'IS_WINDOWS',True)
    result=video_codecs.probe([{'name':'NVIDIA RTX','integrated':False},{'name':'Intel Iris','integrated':True}],check_extension=False)
    assert calls==['hevc_nvenc','hevc_qsv'] and result['available']


@pytest.mark.asyncio
async def test_video_capabilities_reuse_asr_gpu_probe(monkeypatch):
    inventory=[{'name':'AMD Radeon','integrated':False}]
    async def devices():return inventory
    calls=[]
    def probe(values):calls.append(values);return {'available':False}
    monkeypatch.setattr(gpu_devices,'probe_devices',devices)
    monkeypatch.setattr(video_codecs,'probe',probe)
    monkeypatch.setattr(video_codecs,'_cached',None)
    monkeypatch.setattr(video_codecs,'_processing_cache',{})
    result=await video_codecs.capabilities()
    assert result['available'] is False and calls==[inventory]


def test_worker_failure_preserves_input_and_cpu_scaling_succeeds(tmp_path,monkeypatch):
    source=tmp_path/'input.mp4';make_av_video(source,width=640,height=480)
    original=source.read_bytes()
    monkeypatch.setattr(video_codecs,'processing_hardware',lambda codec:{'encoder':'h264_nvenc','decoder':'cuda'})
    def crash(*args):raise RuntimeError('native driver crashed')
    monkeypatch.setattr(gpu_video_worker,'_run_process',crash)
    notes=[]
    assert gpu_video_worker.try_operation('scale',source,max_height=360,codec='h264',notes=notes) is None
    assert source.read_bytes()==original
    assert not list(tmp_path.glob('_video_gpu_*'))
    result=VideoProcessor._limit_preview_resolution(source,360,notes=notes)
    with av.open(str(result)) as media:
        assert media.streams.video[0].height==360 and media.streams.audio
        assert media.streams.video[0].codec_context.codec.id==av.codec.Codec('h264','r').id
    assert any('回退 CPU' in note for note in notes)


def test_gpu_screenshot_failure_retries_cpu_at_requested_time(tmp_path,monkeypatch):
    source=tmp_path/'input.mp4';make_av_video(source,width=64,height=48)
    monkeypatch.setattr(video_codecs,'processing_hardware',lambda codec:{'encoder':'h264_qsv','decoder':'d3d11va'})
    monkeypatch.setattr(gpu_video_worker,'_run_process',lambda *a: (_ for _ in ()).throw(RuntimeError('driver error')))
    processor=VideoProcessor(tmp_path/'workspace')
    result=processor._extract_frames_at_sync(source,'task',[.35,1.25])
    assert list(result)==[.35,1.25] and all(path.is_file() for path in result.values())
    assert source.is_file()
    assert any('回退 CPU' in note for note in processor.take_gpu_notes('task'))


def test_decode_only_gpu_is_usable_for_screenshots(tmp_path,monkeypatch):
    source=tmp_path/'input.mp4';make_av_video(source,width=64,height=48)
    monkeypatch.setattr(video_codecs,'processing_hardware',lambda codec:{'encoder':None,'decoder':'d3d11va'})
    def success(job_file,*args):
        job=json.loads(Path(job_file).read_text(encoding='utf-8'))
        from PIL import Image
        Image.new('RGB',(64,48)).save(Path(job['destination'])/'frame.jpg')
        return {'ok':True,'images':[{'time':.5,'file':'frame.jpg'}]}
    monkeypatch.setattr(gpu_video_worker,'_run_process',success)
    result=gpu_video_worker.try_operation('screenshots',source,frames_directory=tmp_path/'frames',timestamps=[.5])
    assert result[.5].is_file()


def test_cancellation_does_not_fall_back_or_remove_source(tmp_path,monkeypatch):
    source=tmp_path/'input.mp4';make_av_video(source,width=64,height=48)
    original=source.read_bytes()
    monkeypatch.setattr(video_codecs,'processing_hardware',lambda codec:{'encoder':'h264_amf','decoder':'d3d11va'})
    def cancelled(*args):raise asyncio.CancelledError()
    monkeypatch.setattr(gpu_video_worker,'_run_process',cancelled)
    with pytest.raises(asyncio.CancelledError):
        gpu_video_worker.try_operation('screenshots',source,frames_directory=tmp_path/'frames',timestamps=[.5])
    assert source.read_bytes()==original and not list((tmp_path/'frames').glob('_video_gpu_*'))


def test_worker_is_killed_on_cancellation(monkeypatch,tmp_path):
    class Process:
        stopped=False
        def poll(self):return 1 if self.stopped else None
        def kill(self):self.stopped=True
        def communicate(self,**kwargs):return b'',b''
    process=Process()
    monkeypatch.setattr(gpu_video_worker.subprocess,'Popen',lambda *a,**kw:process)
    with pytest.raises(asyncio.CancelledError):
        gpu_video_worker._run_process(tmp_path/'job.json',lambda:True,10)
    assert process.stopped


def test_hevc_hardware_failure_keeps_hevc_with_cpu_fallback(tmp_path,monkeypatch):
    source=tmp_path/'input.mp4';make_av_video(source,width=640,height=480)
    monkeypatch.setattr(gpu_video_worker,'try_operation',lambda *a,**kw:None)
    result=VideoProcessor._limit_hevc_resolution(source,360,'hevc_amf')
    with av.open(str(result)) as media:
        assert media.streams.video[0].height==360
        assert media.streams.video[0].codec_context.codec.id==av.codec.Codec('hevc','r').id
        assert next(media.decode(video=0)) is not None
