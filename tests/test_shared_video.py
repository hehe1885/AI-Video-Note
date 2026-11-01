import json
from pathlib import Path
import av
import pytest
from backend.video_processor import VideoProcessor
from backend import video_cache
from backend.note_review import find_media
from tests.media_fixture import make_av_video,make_audio
from tests.test_video_download_settings import make_video

@pytest.mark.asyncio
async def test_download_video_audio_once_and_reuse_after_task_removed(tmp_path,monkeypatch):
    processor=VideoProcessor(tmp_path);calls=[]
    class Ydl:
        def __init__(self,options):self.options=options
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            calls.append(self.options['format'])
            target=Path(self.options['outtmpl'].replace('%(ext)s','m4a' if self.options['format']=='bestaudio/best' else 'mp4'))
            if target.suffix=='.m4a':make_audio(target)
            else:make_video(target,width=64,height=48)
            return {'title':'Demo title'}
    monkeypatch.setattr(processor,'_ydl',lambda *a,**kw:Ydl(kw))
    url='https://example.com/watch?id=123'
    first=await processor.download_preview_video(url,'first-task')
    second=await processor.download_preview_video(url,'second-task')
    assert first==second==tmp_path/'video/first-task.mp4'
    assert len(calls)==2  # Video and audio, once each.
    assert len(list((tmp_path/'video').glob('*.mp4')))==1
    assert not (tmp_path/'second-task').exists()
    entry=next(iter(video_cache.read_index(tmp_path/'video', tmp_path).values()))
    assert (tmp_path/'video_index.json').is_file()
    assert not (tmp_path/'video/index.json').exists()
    assert entry['platform']=='example.com' and entry['source_url']==url
    assert entry['title']=='Demo title' and entry['audio_state']=='present'
    with av.open(str(first)) as media:
        assert media.streams.audio and next(media.decode(audio=0)).samples>0
    manifest={'source_url':url,'video_resolution':720,'runtime':{'video_path':str(first)}}
    assert find_media('second-task',tmp_path,{},lambda task:manifest)==first
    first.unlink()
    third=await processor.download_preview_video(url,'third-task')
    assert third.name=='third-task.mp4' and len(calls)==4

def test_scaling_keeps_real_audio_and_video(tmp_path):
    source=tmp_path/'source.mp4';make_av_video(source,width=640,height=480)
    result=VideoProcessor._limit_preview_resolution(source,360)
    with av.open(str(result)) as media:
        assert media.streams.video[0].height==360
        assert media.streams.audio
        assert next(media.decode(audio=0)).samples>0
    with av.open(str(result)) as media:
        assert len(list(media.decode(video=0)))==20

def test_cache_keys_keep_bilibili_pages_separate(tmp_path):
    assert video_cache.cache_directory(tmp_path,'https://www.bilibili.com/video/BV123/?p=2',720)!=video_cache.cache_directory(tmp_path,'https://www.bilibili.com/video/BV123/?p=3',720)
    assert video_cache.cache_directory(tmp_path,'https://www.bilibili.com/video/BV123/?share_source=test',720)==video_cache.cache_directory(tmp_path,'https://www.bilibili.com/video/BV123/',720)
