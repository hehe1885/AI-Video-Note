from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError
from backend.main import SummarizeRequest
from backend.video_processor import VideoProcessor


def test_resolution_defaults_and_limit():
    request = SummarizeRequest(llm_config={})
    assert request.video_resolution == 720
    assert request.video_download_directory == ''
    for resolution in (1440, 2160, 0, 719):
        with pytest.raises(ValidationError):
            SummarizeRequest(video_resolution=resolution, llm_config={})


@pytest.mark.asyncio
@pytest.mark.parametrize('resolution', [360, 480, 720, 1080])
async def test_preview_download_uses_bounded_format_and_custom_folder(tmp_path, monkeypatch, resolution):
    from backend import video_cache
    monkeypatch.setattr(video_cache,'inspect_video',lambda path:True)
    processor = VideoProcessor(tmp_path / 'workspace')
    root = tmp_path / 'downloads'
    seen = {}
    class Ydl:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            target = Path(seen['outtmpl'].replace('%(ext)s', 'mp4'))
            make_video(target,width=64,height=48)
    def ydl(*args, **kwargs):
        seen.update(kwargs)
        return Ydl()
    def check(path, height, *args):
        assert height == resolution
        assert path.parent.parent == root / '_cache'
        return path
    monkeypatch.setattr(processor, '_ydl', ydl)
    monkeypatch.setattr(processor, '_limit_preview_resolution', check)
    result = await processor.download_preview_video('https://example.com/video', 'task', max_height=resolution, download_directory=str(root))
    assert result == root / 'task.mp4'
    assert seen['format'] == VideoProcessor._preview_format(resolution, 'compatibility')
    with pytest.raises(ValueError):
        await processor.download_preview_video('https://example.com/video', '../outside', download_directory=str(root))


def make_video(path, width=640, height=480):
    av = pytest.importorskip('av')
    import numpy as np
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=10)
        stream.options = {'preset':'ultrafast','crf':'20'}
        stream.width, stream.height = width, height
        stream.pix_fmt = 'yuv420p'
        stream.codec_context.gop_size = 10
        for index in range(20):
            pixels = np.zeros((height, width, 3), dtype=np.uint8)
            pixels[:, :, 0] = index * 10
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)


def test_real_video_resolution_and_precise_frame_selection(tmp_path):
    av = pytest.importorskip('av')
    from PIL import Image
    path = tmp_path / 'preview.mp4'
    make_video(path)
    limited = VideoProcessor._limit_preview_resolution(path, 360)
    with av.open(str(limited)) as video:
        assert video.streams.video[0].height == 360
        assert video.streams.video[0].width == 480
    processor = VideoProcessor(tmp_path / 'workspace')
    frames = processor._extract_frames_at_sync(limited, 'task', [0.35, 1.25, 5.0])
    assert set(frames) == {0.35, 1.25}
    # First decoded frames at/after the requested timestamps are 0.4s and 1.3s.
    for timestamp, expected in ((0.35, 40), (1.25, 130)):
        with Image.open(frames[timestamp]) as image:
            assert image.height == 360
            assert abs(image.getpixel((100, 100))[0] - expected) < 12
    assert not path.exists()


def test_direct_fallback_retains_lower_resolution(tmp_path):
    pytest.importorskip('av')
    path = tmp_path / 'preview.mp4'
    make_video(path, width=64, height=48)
    assert VideoProcessor._limit_preview_resolution(path, 720) == path
    assert path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('resolution,quality', [(360,16),(480,32),(720,64),(1080,80)])
async def test_bilibili_fallback_stays_within_selected_resolution(tmp_path, monkeypatch, resolution, quality):
    processor = VideoProcessor(tmp_path)
    processor._bili_view_cache['BVtest'] = {'bvid': 'BVtest', 'pages': [{'page': 1, 'cid': 1}]}
    monkeypatch.setattr(processor, '_bilibili_ids', lambda _: ('BVtest', None))
    seen = {}
    def api(url, params, headers):
        seen.update(params)
        return {'data': {'dash': {'video': [
            {'height': height, 'baseUrl': f'https://cdn.test/{height}.m4s'}
            for height in (240,360,480,720,1080,2160)
        ]}}}
    def download(url, target, *args, **kwargs):
        seen['url'] = url
        return target
    monkeypatch.setattr(processor, '_bilibili_get_json', api)
    monkeypatch.setattr(processor, '_download_direct_media', download)
    processor._download_bilibili_media_via_api('https://www.bilibili.com/video/BVtest', tmp_path/'preview.m4s', None, 'video', max_height=resolution)
    assert seen['url'] == f'https://cdn.test/{resolution}.m4s'
    assert seen['qn'] == quality


@pytest.mark.parametrize('heights,expected', [([240, 360], 360), ([240, 360, 480], 480), ([360, 480, 720, 1080], 720)])
@pytest.mark.parametrize('audio', ['none', 'aac'])
def test_default_download_selects_highest_available_below_720(heights, expected, audio):
    import yt_dlp
    formats = [dict(format_id=str(h), height=h, url=f'https://example.com/{h}',
                    ext='mp4', vcodec='h264', acodec=audio) for h in heights]
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        selector = ydl.build_format_selector('bestvideo[height<=720]/best[height<=720]')
        assert ydl._select_formats(formats, selector)[0]['height'] == expected


@pytest.mark.asyncio
async def test_bilibili_default_download_selects_highest_when_source_below_720(tmp_path, monkeypatch):
    processor = VideoProcessor(tmp_path)
    processor._bili_view_cache['BVtest'] = {'bvid': 'BVtest', 'pages': [{'page': 1, 'cid': 1}]}
    monkeypatch.setattr(processor, '_bilibili_ids', lambda _: ('BVtest', None))
    monkeypatch.setattr(processor, '_bilibili_get_json', lambda *args: {'data': {'dash': {'video': [
        {'height': h, 'baseUrl': f'https://cdn.test/{h}.m4s'} for h in (240, 360, 480)]}}})
    seen = []
    def download(url, target, *args, **kwargs):
        seen.append(url)
        return target
    monkeypatch.setattr(processor, '_download_direct_media', download)
    processor._download_bilibili_media_via_api('https://www.bilibili.com/video/BVtest', tmp_path/'preview.m4s', None, 'video')
    assert seen == ['https://cdn.test/480.m4s']
