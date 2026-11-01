import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from backend import asr_recovery as recovery, main, transcript_audio, whisper_asr
from backend.transcript import TranscriptSegment


def row(text='循环测试内容' * 10, start=0., end=10., probability=-1., compression=4.):
    return dict(start=start, end=end, text=text, avg_logprob=probability, compression_ratio=compression)


def fake_clip(parts, target, should_abort):
    should_abort()
    target.write_bytes(b'audio')
    return target


@pytest.mark.parametrize('text', ['你好你好', '注意' * 8, 'PR剪辑软件可以编辑视频', '正常解释，下面继续操作'])
def test_short_repetitions_and_ordinary_speech_do_not_trigger(text):
    assert recovery.find_regions([row(text)]) == []


def test_confidence_and_compression_are_required_in_addition_to_text_repetition():
    assert recovery.find_regions([row()]) == [(0, 1)]
    assert recovery.find_regions([row(probability=-.2)]) == []
    assert recovery.find_regions([row(compression=1.5)]) == []
    assert recovery.find_regions([row(probability=None)]) == []
    assert recovery.find_regions([row(end=31)]) == []


def test_repeated_adjacent_lines_are_detected_but_separated_speech_is_not():
    records = [row('循环测试内容重复', start=i, end=i+1) for i in range(6)]
    assert recovery.find_regions(records) == [(0, 6)]
    records = [row('循环测试内容重复', start=i*3, end=i*3+1) for i in range(6)]
    assert recovery.find_regions(records) == []


@pytest.mark.parametrize('candidate,reason', [
    ([], 'empty_candidate'),
    ([row(probability=-.1)], 'repetition_remains'),
    ([row('已经恢复正常说明', probability=-.95)], 'insufficient_confidence_gain'),
    ([row('EMA120已经恢复正常说明', probability=-.1)], 'numbers_changed'),
    ([row('已经恢复正常说明', end=4, probability=-.1)], 'coverage_reduced'),
    ([row('已经恢复正常说明', probability=-.1)], 'accepted'),
])
def test_candidate_must_pass_each_conservative_guard(candidate, reason):
    assert recovery.candidate_decision([row()], candidate) == reason


def test_local_retry_preserves_neighbors_absolute_times_and_original_receipt(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    records = [row('前文', 0, 2, -.1), row(start=3, end=13), row('后文', 14, 16, -.1)]
    calls = []
    def decode(path):
        calls.append(path)
        return [SimpleNamespace(**row('已经恢复正常说明', 1, 11, -.1))], None
    segments, receipts = recovery.retry_regions(records, 16, Path('audio.wav'), decode, lambda: None)
    assert [(s.start, s.end, s.text) for s in segments] == [(0, 2, '前文'), (3, 13, '已经恢复正常说明'), (14, 16, '后文')]
    assert receipts[0]['original'] == [records[1]] and receipts[0]['decision'] == 'accepted'
    assert len(calls) == 1 and not calls[0].exists()  # Temporary audio cleaned up.


def test_retry_failures_keep_original_and_do_not_retry_whole_video(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    calls = []
    def decode(path):
        calls.append(path)
        raise RuntimeError('temporary inference failure')
    segments, receipts = recovery.retry_regions([row()], 10, Path('audio.wav'), decode, lambda: None)
    assert segments[0].text == row()['text']
    assert len(calls) == 1 and receipts[0]['decision'] == 'retry_failed'


def test_normal_transcript_does_not_decode_audio_again(monkeypatch):
    def forbidden(*args):
        raise AssertionError('normal transcript must not be retried')
    monkeypatch.setattr(transcript_audio, 'write_clip', forbidden)
    segments, receipts = recovery.retry_regions([row('正常内容')], 10, Path('audio.wav'), forbidden, lambda: None)
    assert segments[0].text == '正常内容' and receipts == []


def test_retry_has_a_three_region_budget(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    calls = []
    records = [row(start=i*30, end=i*30+25) for i in range(5)]
    def decode(path):
        calls.append(path)
        return [], None
    _, receipts = recovery.retry_regions(records, 150, Path('audio.wav'), decode, lambda: None)
    assert len(calls) == 3 and [r['decision'] for r in receipts][-2:] == ['budget_exhausted']*2


def test_chinese_numbers_cannot_be_changed_by_retry():
    assert recovery.candidate_decision([row('一百二十' + '循环测试内容'*10)],
        [row('一百三十，已经恢复正常说明', probability=-.1)]) == 'numbers_changed'


def test_confident_dense_title_loop_is_not_mistaken_for_reliable_speech():
    bad = row('【测试演讲标题】'*50, start=0, end=1.4, probability=-.05, compression=17.)
    assert recovery.find_regions([bad]) == [(0, 1)]
    # Corrected inference need not beat a hallucination's unjustifiably high likelihood.
    candidate = row('A short spoken sentence.', start=0, end=1.4, probability=-.2, compression=1.)
    assert recovery.candidate_decision([bad], [candidate]) == 'accepted'


def test_dense_text_without_a_loop_does_not_trigger_retry():
    assert recovery.find_regions([row('abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG', end=.1, probability=-.05)]) == []


def test_short_retry_cannot_silently_switch_from_a_known_source_language(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    def decode(path):
        return [SimpleNamespace(**row('Une phrase.', probability=-.1))], SimpleNamespace(language='fr')
    segments, receipts = recovery.retry_regions([row()], 10, Path('audio.wav'), decode, lambda: None, expected_language='en')
    assert segments[0].text == row()['text'] and receipts[0]['decision'] == 'language_changed'


def test_padding_words_are_not_copied_into_the_replaced_region(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    def decode(path):
        item = SimpleNamespace(**row('前文 核心内容 后文', start=0, end=12, probability=-.1))
        item.words = [SimpleNamespace(start=0, end=.8, word='前文'),
                      SimpleNamespace(start=1, end=11, word='核心内容'),
                      SimpleNamespace(start=11.2, end=12, word='后文')]
        return [item], None
    segments, receipts = recovery.retry_regions([row(start=3, end=13)], 16, Path('audio.wav'), decode, lambda: None)
    assert segments[0] == TranscriptSegment(3, 13, '核心内容')
    assert receipts[0]['decision'] == 'accepted'


def test_invalid_candidate_timestamps_preserve_original(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    def decode(path):
        return [SimpleNamespace(**row('候选', float('nan'), 10, -.1))], None
    segments, receipts = recovery.retry_regions([row()], 10, Path('audio.wav'), decode, lambda: None)
    assert segments[0].text == row()['text'] and receipts[0]['decision'] == 'retry_failed'


@pytest.mark.asyncio
async def test_multi_page_receipts_keep_page_local_times(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'task_directory', lambda _: tmp_path)
    pages = {'2': [{'start': 0, 'end': 10, 'decision': 'accepted', 'original': [row()]}]}
    await main.write_transcript_files('task', [TranscriptSegment(100, 110, '第二页')],
        dict(language='zh', source='bilibili_multi_page', asr_recovery_pages=pages))
    assert main.load_transcript_result(tmp_path)['asr_recovery_pages'] == pages


def test_cancellation_during_retry_generator_propagates(monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    event = threading.Event()
    def check():
        if event.is_set():
            raise whisper_asr.TranscriptionCancelledError()
    def decode(path):
        def generate():
            event.set()
            yield SimpleNamespace(**row('候选', probability=-.1))
        return generate(), None
    with pytest.raises(whisper_asr.TranscriptionCancelledError):
        recovery.retry_regions([row()], 10, Path('audio.wav'), decode, check)


def test_recovery_exception_does_not_mark_cuda_unusable(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_audio, 'write_clip', fake_clip)
    calls = []
    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError('retry failed')
            return [SimpleNamespace(**row())], SimpleNamespace(language='zh', language_probability=.9, duration=10, duration_after_vad=10)
    transcriber = whisper_asr.WhisperTranscriber(tmp_path)
    transcriber._models[('base', 'cuda')] = Model()
    transcriber._actual_models[('base', 'cuda')] = 'base'
    result = transcriber._transcribe_sync(tmp_path/'audio.wav', 'base', True, '视频标题')
    assert result['device'] == 'cuda' and not transcriber._gpu_unusable
    assert calls[0]['condition_on_previous_text'] is True and calls[0]['no_repeat_ngram_size'] == 0
    assert calls[1]['condition_on_previous_text'] is False and calls[1]['no_repeat_ngram_size'] == 10
    assert calls[1]['initial_prompt'] is None
    assert result['asr_recovery'][0]['decision'] == 'retry_failed'


def test_large_v3_requires_feature_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(whisper_asr, 'MIN_MODEL_FILE_BYTES', {name: 1 for name in whisper_asr.MIN_MODEL_FILE_BYTES})
    transcriber = whisper_asr.WhisperTranscriber(tmp_path)
    folder = tmp_path/'manual'/'large-v3'; folder.mkdir(parents=True)
    for name in ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.json']:
        (folder/name).write_bytes(b'fixture')
    assert transcriber._model_cache_status('large-v3') == 'incomplete'
    (folder/'preprocessor_config.json').write_text('{"feature_size":128}', encoding='utf-8')
    assert transcriber._model_cache_status('large-v3') == 'cached'


@pytest.mark.asyncio
async def test_recovery_receipt_survives_transcript_write_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'task_directory', lambda _: tmp_path)
    receipt = [{'start': 0, 'end': 10, 'original': [row()], 'candidate': [], 'decision': 'empty_candidate'}]
    segments = [TranscriptSegment(0, 10, row()['text'])]
    await main.write_transcript_files('task', segments, dict(language='zh', source='faster_whisper', asr_recovery=receipt))
    result = main.load_transcript_result(tmp_path)
    assert result['asr_recovery'] == receipt
    await main.write_transcript_files('task', result['segments'], result)
    assert main.load_transcript_result(tmp_path)['asr_recovery'] == receipt
