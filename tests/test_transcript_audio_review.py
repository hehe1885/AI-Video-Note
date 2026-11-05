import asyncio
import json
import wave
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend import main, transcript_review as review, transcript_audio_review as listening
from backend.transcript import TranscriptSegment
from tests.test_transcript_review import source, question
from tests.test_transcript_audio import pcm_source


@pytest.fixture
def listening_review(source, monkeypatch):
    task_id, directory, segments = source
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    pcm_source(directory / 'input.wav', duration=20)
    monkeypatch.setattr(main.transcriber, '_cached_model_path', lambda model: directory if model == 'turbo' else None)
    return task_id, directory, segments, value


@pytest.mark.asyncio
async def test_real_clip_and_independent_asr_feed_model_without_committing(listening_review, monkeypatch):
    task_id, directory, segments, value = listening_review
    before = (directory / 'transcript.json').read_bytes()
    calls = []
    async def transcribe(path, model, gpu, title, cancel):
        with wave.open(str(path)) as audio:
            assert audio.getnframes() == 12 * 24000  # 10-second target plus context.
        assert model == 'turbo' and not gpu
        return {'segments':[TranscriptSegment(0,12,'学习率为零点零零一，随后执行。')], 'model':'turbo'}
    async def complete(prompt):
        calls.append(prompt)
        return json.dumps({'issues':[{**question(), 'replacement':'0.001', 'confidence':.86}]})
    monkeypatch.setattr(main, 'run_transcription', transcribe)
    main.tasks[task_id]['_transcript_review_complete'] = complete
    result = await review.revise_with_audio(task_id, 'seg-0', review.AIReviewRequest(revision=value['revision']))
    assert '零点零零一' in calls[0] and '没有直接听到原音频' in calls[0]
    assert result['item']['suggestion'] == '学习率为 0.001。'
    assert result['item']['audio_review']['method'] == 'local_asr_then_text_model'
    assert (await review.get_review(task_id))['items'][0]['audio_review']['text'].startswith('学习率')
    assert (directory / 'transcript.json').read_bytes() == before
    assert not main.tasks[task_id]['_transcript_review_event'].is_set()
    await review.confirm_review(task_id, review.Confirmation(revision=value['revision'],
        decisions=[review.Decision(id='seg-0', text=result['item']['suggestion'])]))
    assert main.load_transcript_result(directory)['segments'][0].text == '学习率为 0.001。'


@pytest.mark.asyncio
async def test_stale_and_missing_audio_checks_never_invoke_model(listening_review, monkeypatch):
    task_id, directory, segments, value = listening_review
    async def forbidden(*args, **kwargs):
        pytest.fail('Must reject before invoking ASR/model')
    monkeypatch.setattr(main, 'run_transcription', forbidden)
    main.tasks[task_id]['_transcript_review_complete'] = forbidden
    for item, revision, code in [('seg-0','stale',409),('seg-99',value['revision'],404)]:
        with pytest.raises(HTTPException) as error:
            await review.revise_with_audio(task_id,item,review.AIReviewRequest(revision=revision))
        assert error.value.status_code == code
    monkeypatch.setattr(main.transcriber,'_cached_model_path',lambda name:None)
    with pytest.raises(HTTPException) as error:
        await review.revise_with_audio(task_id,'seg-0',review.AIReviewRequest(revision=value['revision']))
    assert error.value.status_code == 409
    assert 'Whisper' in error.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['wrong_target', 'empty_audio', 'cancelled', 'source_changed'])
async def test_failed_audio_evidence_cannot_change_source_or_release_gate(listening_review, monkeypatch, failure):
    task_id, directory, segments, value = listening_review
    before = (directory / 'transcript.json').read_bytes()
    async def transcribe(*args):
        return {'segments': [] if failure=='empty_audio' else [TranscriptSegment(0,10,'独立语音识别结果')]}
    async def complete(prompt):
        if failure == 'cancelled':
            main.tasks[task_id]['_cancel_requested'] = True
        if failure == 'source_changed':
            main.tasks[task_id]['transcript_review']['status'] = 'confirmed'
        issue = question(1) if failure=='wrong_target' else question()
        return json.dumps({'issues':[{**issue,'replacement':'不同文字','confidence':.9}]})
    monkeypatch.setattr(main, 'run_transcription', transcribe)
    main.tasks[task_id]['_transcript_review_complete'] = complete
    with pytest.raises(HTTPException):
        await review.revise_with_audio(task_id,'seg-0',review.AIReviewRequest(revision=value['revision']))
    assert (directory / 'transcript.json').read_bytes() == before
    assert review.read_review(directory) == value
    assert not main.tasks[task_id]['_transcript_review_event'].is_set()
