"""Real HEVC/AV1 packets must survive remuxing and produce screenshots."""
from pathlib import Path

import av
import numpy as np
import pytest

from backend.video_processor import VideoProcessor
from tests.media_fixture import make_audio


@pytest.mark.parametrize('codec,encoder', [('hevc','libx265'),('av1','libsvtav1')])
@pytest.mark.parametrize('pixel_format', ['yuv420p','yuv420p10le'])
def test_modern_video_screenshots_and_audio_remux(tmp_path, codec, encoder, pixel_format):
    try:
        av.codec.Codec(encoder, 'w')
    except av.codec.codec.UnknownCodecError:
        pytest.skip(f'{encoder} is not included in this PyAV build')
    source = tmp_path / 'source.mp4'
    with av.open(str(source), 'w') as output:
        stream = output.add_stream(encoder, rate=10)
        stream.width = stream.height = 128
        stream.pix_fmt = pixel_format
        stream.options = {'crf':'30','preset':'ultrafast','x265-params':'log-level=error:pools=1'} if codec=='hevc' else {'crf':'35','preset':'10','svtav1-params':'lp=2'}
        for index in range(20):
            pixels = np.full((128,128,3), 30+index*8, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    audio = tmp_path / 'audio.m4a'
    make_audio(audio)
    processor = VideoProcessor(tmp_path)
    (tmp_path / 'tasks' / 'modern').mkdir(parents=True)
    precise = processor._extract_frames_at_sync(source,'modern',[0,.5,1.5])
    periodic = processor._extract_frames_sync(source,'modern',1)
    assert len(precise)==3 and len(periodic)>=2
    assert all(path.is_file() for path in [*precise.values(),*periodic])
    merged = processor._merge_preview_audio(source,audio)
    with av.open(str(merged)) as media:
        assert media.streams.video[0].codec_context.codec.id == av.codec.Codec(codec,'r').id
        assert media.streams.video[0].codec_context.format.name == pixel_format
        assert len(list(media.decode(video=0)))==20
    with av.open(str(merged)) as media:
        assert next(media.decode(audio=0)).samples > 0
    extracted = processor.extract_audio_track(merged, tmp_path/'extracted.m4a')
    assert extracted is not None
    with av.open(str(extracted)) as media:
        assert next(media.decode(audio=0)).samples > 0
