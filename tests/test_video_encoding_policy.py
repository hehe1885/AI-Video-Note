from pathlib import Path
import av
import pytest
from yt_dlp import YoutubeDL
from backend import main, video_codecs, video_cache
from backend.video_processor import VideoProcessor
from tests.media_fixture import make_av_video


@pytest.mark.parametrize('policy,has_hevc,expected', [('compatibility',True,'h264'),('size',True,'hevc'),('size',False,'h264')])
def test_format_selection_excludes_av1_and_falls_back(policy,has_hevc,expected):
    formats=[{'format_id':codec,'vcodec':codec,'acodec':'none','height':height,'ext':'mp4','url':'https://example.com/'+codec}
             for codec,height in [('avc1',1080),('av01',1080),*([('hvc1',720)] if has_hevc else [])]]
    with YoutubeDL({'quiet':True,'no_warnings':True}) as ydl:
        selected=ydl._select_formats(formats,ydl.build_format_selector(VideoProcessor._preview_format(1080,policy)))
    assert selected[0]['vcodec']==('hvc1' if expected=='hevc' else 'avc1')


def test_codec_modes_have_distinct_cache_identity(tmp_path):
    assert video_cache.cache_directory(tmp_path,'https://youtu.be/example',720)!=video_cache.cache_directory(tmp_path,'https://youtu.be/example',720,'size')


@pytest.mark.asyncio
@pytest.mark.parametrize('hevc_download_fails',[False,True])
async def test_size_mode_h264_fallback_keeps_h264_and_logs(tmp_path,monkeypatch,hevc_download_fails):
    processor=VideoProcessor(tmp_path)
    async def hardware():return {'available':True,'encoder':'hevc_nvenc'}
    monkeypatch.setattr(video_codecs,'capabilities',hardware)
    class Ydl:
        def __init__(self,options):self.options=options
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,*args,**kwargs):
            if hevc_download_fails and 'h265' in self.options['format']:
                raise RuntimeError('HEVC stream unavailable')
            make_av_video(Path(self.options['outtmpl'].replace('%(ext)s','mp4')),width=64,height=48)
            return {'title':'fallback'}
    monkeypatch.setattr(processor,'_ydl',lambda *a,**kw:Ydl(kw))
    def no_hevc(*args):raise AssertionError('H.264 fallback must not transcode to HEVC')
    monkeypatch.setattr(processor,'_limit_hevc_resolution',no_hevc)
    logs=[]
    path=await processor.download_preview_video('https://example.com/watch','fallback',notes=logs,video_encoding='size')
    assert video_codecs.video_codec(path)==av.codec.Codec('h264','r').id
    assert any('回退到 H.264' in log for log in logs)
    reused=await processor.download_preview_video('https://example.com/watch','reuse',notes=logs,video_encoding='size')
    assert reused==path


@pytest.mark.asyncio
async def test_hardware_failure_blocks_backend_size_submission(tmp_path,monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    async def hardware():return {'available':False,'hevc_extension':True}
    monkeypatch.setattr(video_codecs,'capabilities',hardware)
    request=main.SummarizeRequest(video_url='https://youtu.be/example',llm_config={},video_encoding='size',timed_screenshots=True)
    with pytest.raises(HTTPException) as error:
        await main.submit_video_task(request,origin='web')
    assert error.value.status_code==400 and '硬件编解码' in error.value.detail


@pytest.mark.asyncio
async def test_missing_extension_does_not_disable_verified_hardware(monkeypatch):
    monkeypatch.setattr(video_codecs,'_cached',{'available':True,'encoder':'hevc_nvenc','decoder':'d3d11va','hevc_extension':False})
    import time
    monkeypatch.setattr(video_codecs,'_cached_at',time.monotonic())
    result=await main.video_codec_capabilities()
    assert result['available'] and result['hevc_extension'] is False


def test_hevc_scaling_preserves_codec_and_real_frames(tmp_path):
    import numpy as np
    path=tmp_path/'hevc.mp4'
    with av.open(str(path),'w') as output:
        stream=output.add_stream('libx265',rate=10);stream.width=384;stream.height=256;stream.pix_fmt='yuv420p'
        stream.options={'preset':'ultrafast','x265-params':'log-level=error:pools=1'}
        for index in range(20):
            frame=av.VideoFrame.from_ndarray(np.full((256,384,3),index*8,dtype=np.uint8),format='rgb24');frame.pts=index
            for packet in stream.encode(frame):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    scaled=VideoProcessor._limit_hevc_resolution(path,128,'libx265')
    with av.open(str(scaled)) as media:
        assert media.streams.video[0].height==128
        assert media.streams.video[0].codec_context.codec.id==av.codec.Codec('hevc','r').id
        assert len(list(media.decode(video=0)))==20
