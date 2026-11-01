import asyncio
import io
import json
import wave
import numpy as np
import httpx
import pytest
from fastapi import HTTPException
from backend import main, transcript_audio as audio, transcript_review as review
from backend.transcript import TranscriptSegment
from tests.test_transcript_review import source, question


def pcm_source(path, duration=3, rate=48000):
    samples = np.arange(round(duration * rate))
    # The level changes at one second, allowing an assertion against the actual offset.
    level = np.where(samples < rate, 3000, 9000)
    data = (level * np.sin(2 * np.pi * 440 * samples / rate)).astype('<i2')
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(data.tobytes())


@pytest.fixture
def clip_review(source):
    task_id, directory, _ = source
    segments = [TranscriptSegment(1.1, 1.85, '学习率为 0.01。')]
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    pcm_source(directory / 'input.wav')
    return task_id, directory, value


@pytest.mark.asyncio
async def test_exact_excerpt_playback_and_cache_reuse(clip_review, monkeypatch):
    task_id, directory, value = clip_review
    url = f'/api/task/{task_id}/transcript-review/seg-0/audio?revision={value["revision"]}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://localhost') as client:
        prepared = await client.post(url)
        assert prepared.status_code == 200 and prepared.json() == {'ready': True}
        response = await client.get(url)
        assert response.status_code == 200 and response.headers['content-type'] == 'audio/wav'
        with wave.open(io.BytesIO(response.content)) as clip:
            assert clip.getnframes() == round(.75 * 24000)
            assert clip.getnchannels() == 1 and clip.getsampwidth() == 2
            signal = np.frombuffer(clip.readframes(clip.getnframes()), dtype='<i2')
            assert np.sqrt(np.mean(signal.astype(float) ** 2)) > 5000
        def no_decode(*args): raise AssertionError('Repeated playback must reuse the excerpt')
        monkeypatch.setattr(audio, 'write_clip', no_decode)
        again = await client.get(url, headers={'Range': 'bytes=0-43'})
        assert again.status_code == 206 and again.content == response.content[:44]
        assert (await client.get(url.replace(value['revision'], 'old'))).status_code == 409
        assert (await client.get(url.replace('seg-0', 'seg-99'))).status_code == 404
        main.tasks[task_id]['_cancel_requested'] = True
        assert (await client.get(url)).status_code == 409
    assert (directory / 'input.wav').is_file()


def test_page_mapping_and_cross_page_clip(tmp_path):
    manifest = {'bili_pages': [{'page': 2, 'duration': 3}, {'page': 4, 'duration': 3}]}
    assert audio.page_ranges(manifest, 2.5, 3.5) == [(2, 2.5, 3.0), (4, 0.0, .5)]
    one, two = tmp_path / 'one.wav', tmp_path / 'two.wav'
    pcm_source(one); pcm_source(two)
    target = tmp_path / 'clip.wav'
    audio.write_clip([(one, 2.5, 3), (two, 0, .5)], target)
    with wave.open(str(target)) as clip:
        assert clip.getnframes() == 24000
    with pytest.raises(HTTPException): audio.page_ranges({'bili_pages': [{'page': 1}, {'page': 2}]}, 2, 3)


@pytest.mark.asyncio
async def test_platform_prepares_matching_page_and_passes_cache_options(source, monkeypatch):
    task_id, directory, _ = source
    manifest = main.read_task_manifest(task_id)
    manifest.update(source_url='https://www.bilibili.com/video/BVtest', video_download_directory=str(directory / 'custom'),
                    bili_pages=[{'page': 2, 'duration': 60}, {'page': 4, 'duration': 60}])
    (directory / 'task.json').write_text(json.dumps(manifest), encoding='utf-8')
    main.tasks[task_id]['_review_audio_options'] = {'cookie': {'SESSDATA': 'fictional'}}
    calls = []
    async def download(url, current_id, cookie, **kwargs):
        calls.append((url, current_id, cookie, kwargs))
        return directory / 'fixture.wav'
    monkeypatch.setattr(main.video_processor, 'download_audio', download)
    parts = await audio.source_parts(main, task_id, 75, 80, lambda: False)
    assert parts == [(directory / 'fixture.wav', 15, 20)]
    assert 'p=4' in calls[0][0] and calls[0][1] == task_id
    assert calls[0][2] == {'SESSDATA': 'fictional'}
    assert calls[0][3]['video_directory'] == str(directory / 'custom')


@pytest.mark.asyncio
async def test_missing_audio_is_visible_and_does_not_confirm(source):
    task_id, _, segments = source
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    with pytest.raises(HTTPException) as error:
        await review.get_review_audio(task_id, 'seg-0', value['revision'])
    assert error.value.status_code == 404
    assert main.tasks[task_id]['transcript_review']['status'] == 'pending'
    assert not main.tasks[task_id]['_transcript_review_event'].is_set()


def test_cancelled_decode_does_not_publish_partial_audio(tmp_path):
    source = tmp_path / 'input.wav'; pcm_source(source)
    target = tmp_path / 'clip.wav'
    with pytest.raises(asyncio.CancelledError): audio.write_clip([(source, 0, 2)], target, lambda: True)
    assert not target.exists() and not target.with_suffix('.tmp.wav').exists()


def test_compressed_audio_seek_and_sample_trim(tmp_path):
    from tests.media_fixture import make_audio
    source = tmp_path / 'input.m4a'; make_audio(source)
    target = tmp_path / 'clip.wav'
    audio.write_clip([(source, .5, 1.25)], target)
    with wave.open(str(target)) as clip:
        assert abs(clip.getnframes() - round(.75 * 24000)) <= 2
        assert clip.getframerate() == 24000
