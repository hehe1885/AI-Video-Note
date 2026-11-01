import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.ai_screenshots import (
    ScreenshotPoint, _parse_points, plan_screenshots, screenshot_instructions, insert_screenshots,
)
from backend.transcript import TranscriptSegment
from backend.video_processor import VideoProcessor


@pytest.mark.asyncio
@pytest.mark.parametrize('density', ['few', 'medium', 'many'])
async def test_ai_decides_count_without_fixed_limit(density):
    calls = []
    class Model:
        async def _complete(self, prompt, **kwargs):
            import json
            calls.append(prompt)
            return json.dumps({'shots': [{'time': i*2, 'caption': f'topic {i}', 'anchor': 'demo'} for i in range(25)]})
    points = await plan_screenshots(Model(), [TranscriptSegment(0, 100, 'demo')], density)
    assert len(points) == 25
    assert len(calls) == 1
    assert '不要设置固定张数' in calls[0]


@pytest.mark.asyncio
async def test_no_images_skips_model_and_empty_ai_selection_is_valid():
    class Model:
        calls = 0
        async def _complete(self, *args, **kwargs):
            self.calls += 1
            return '{"shots":[]}'
    model = Model()
    assert await plan_screenshots(model, [TranscriptSegment(0, 30, 'talk')], 'none') == []
    assert model.calls == 0
    assert await plan_screenshots(model, [TranscriptSegment(0, 30, 'talk')], 'few') == []
    assert model.calls == 1


def test_invalid_times_and_duplicate_markers():
    points = _parse_points('{"shots":[{"time":-1},{"time":999},{"time":true},{"time":5,"caption":"valid"}]}', 0, 30)
    assert len(points) == 1
    assert points[0].time == 5
    note = insert_screenshots('# Title\n\nContent\n\n[[VIDEO_SHOT_0]]\n\n[[VIDEO_SHOT_0]]\n\n[[VIDEO_SHOT_99]]', points, {5: Path('shot.jpg')})
    assert note.count('./frames/shot.jpg') == 1
    assert 'VIDEO_SHOT' not in note
    assert '[[VIDEO_SHOT_0]]' in screenshot_instructions(points)


def test_omitted_markers_insert_near_related_content_and_preserve_code():
    points = [ScreenshotPoint(90, 'demo', 'operation example')]
    note = '# Title\n\n[00:10] Introduction\n\n[01:30] operation example\n\n## End\n\nClosing\n\n```python\n[[VIDEO_SHOT_0]]\n```'
    result = insert_screenshots(note, points, {90: Path('shot.jpg')})
    assert result.index('./frames/shot.jpg') < result.index('## End')
    assert result.count('./frames/shot.jpg') == 1
    assert 'VIDEO_SHOT' not in result
    assert '## 视频截图' not in result


@pytest.mark.asyncio
async def test_chunking_covers_long_video_and_cancellation():
    times = []
    class Model:
        async def _complete(self, prompt, **kwargs):
            import re
            timestamp = float(re.search(r'\[(\d+\.\d+)-', prompt)[1])
            times.append(timestamp)
            return '{"shots":[{"time":' + str(timestamp) + '}]}'
    segments = [TranscriptSegment(i*60, (i+1)*60, 'a'*6000) for i in range(4)]
    result = await plan_screenshots(Model(), segments, 'many')
    assert [p.time for p in result] == [0, 60, 120, 180]
    with pytest.raises(asyncio.CancelledError):
        await plan_screenshots(Model(), segments, 'many', lambda: True)


def test_extractor_decodes_past_keyframe(tmp_path, monkeypatch):
    import sys
    saved = []
    class Frame:
        def __init__(self, time): self.time = time
        def to_image(self): return self
        def save(self, path, **kwargs): saved.append(path)
    class Container:
        streams = SimpleNamespace(video=[True])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def seek(self, offset, backward): assert backward
        def decode(self, **kwargs): return iter([Frame(2), Frame(4), Frame(5.5)])
    monkeypatch.setitem(sys.modules, 'av', SimpleNamespace(open=lambda _: Container(), time_base=1000000))
    processor = VideoProcessor(tmp_path)
    result = processor._extract_frames_at_sync(Path('video.mp4'), 'test', [5.1])
    assert list(result) == [5.1]
    assert len(saved) == 1
    assert '5100ms' in saved[0].name


@pytest.mark.asyncio
async def test_invalid_json_retried_and_invalid_times_reported():
    class Model:
        calls = 0
        async def _complete(self, *args, **kwargs):
            self.calls += 1
            return 'invalid JSON' if self.calls == 1 else '{"shots":[{"time":4}]}'
    model = Model()
    points = await plan_screenshots(model, [TranscriptSegment(0, 10, 'demo')], 'medium')
    assert points[0].time == 4
    assert model.calls == 2
    with pytest.raises(ValueError):
        _parse_points('{"shots":[{"time":9999}]}', 0, 10)


@pytest.mark.asyncio
async def test_very_long_single_segment_does_not_drop_tail():
    prompts = []
    class Model:
        async def _complete(self, prompt, **kwargs):
            prompts.append(prompt)
            return '{"shots":[]}'
    await plan_screenshots(Model(), [TranscriptSegment(0, 500, 'a'*17000 + 'TAIL_CONTENT')], 'many')
    assert len(prompts) == 2
    assert 'TAIL_CONTENT' in prompts[-1]


def test_interval_screenshots_cover_duration_and_anchor_without_count_cap():
    from backend.ai_screenshots import interval_screenshots
    segments = [TranscriptSegment(0, 12, 'Introduction'), TranscriptSegment(12, 25, 'Example')]
    points = interval_screenshots(segments, 25, 7)
    assert [p.time for p in points] == [0, 7, 14, 21]
    assert points[2].anchor == 'Example'
    assert [p.time for p in interval_screenshots(segments, 0, 10)] == [0, 10, 20]
    assert len(interval_screenshots([], 3600, 1)) == 3600
    assert interval_screenshots([], 0, 10) == []
    for invalid in [0, -1, 1.5, True]:
        with pytest.raises(ValueError):
            interval_screenshots(segments, 25, invalid)


def test_real_interval_frames_are_inserted_in_note(tmp_path):
    from backend.ai_screenshots import interval_screenshots
    from tests.test_video_download_settings import make_video
    path = tmp_path / 'video.mp4'
    make_video(path, width=64, height=48)
    points = interval_screenshots([TranscriptSegment(0, 2, 'Example')], 2, 1)
    frames = VideoProcessor(tmp_path / 'workspace')._extract_frames_at_sync(path, 'task', [p.time for p in points])
    assert set(frames) == {0, 1}
    note = insert_screenshots('# Title\n\nExample', points, frames)
    assert note.count('./frames/') == 2
