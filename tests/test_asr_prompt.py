from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.asr_prompt import title_prompt_mismatches_speech
from backend.whisper_asr import WhisperTranscriber


@pytest.mark.parametrize('prompt,language,probability,expected', [
    ('睡眠是我们的超能力 TED演讲', 'en', .99, True),
    ('三种人工智能的未来', 'en', .9, True),
    ('学习法语的技巧', 'fr', .9, True),
    ('安装 Windows 和 Ubuntu', 'zh', .99, False),
    ('睡眠是我们的超能力', 'en', .5, False),
    ('睡眠是我们的超能力', 'en', float('nan'), False),
    ('睡眠是我们的超能力', 'en', None, False),
    ('睡眠是我们的超能力', 'unknown', .99, False),
    ('睡眠是我们的超能力', 'ja', .99, False),
    ('Sleep is your superpower', 'en', .99, False),
    ('AI 中文', 'en', .99, False),
    (None, 'en', 1., False),
])
def test_metadata_language_compatibility(prompt, language, probability, expected):
    assert title_prompt_mismatches_speech(prompt, SimpleNamespace(language=language, language_probability=probability)) is expected


def test_mismatched_title_generator_is_never_consumed():
    calls, consumed = [], []
    class Model:
        model = SimpleNamespace(is_multilingual=True)
        def transcribe(self, path, **options):
            calls.append(options)
            attempt = len(calls)
            def segments():
                consumed.append(attempt)
                yield 'spoken words'
            return segments(), SimpleNamespace(language='en', language_probability=.99)
    raw, info = WhisperTranscriber._run_model(Model(), Path('audio.wav'), '睡眠是我们的超能力', 'cuda')
    assert consumed == [] and list(raw) == ['spoken words'] and consumed == [2]
    assert len(calls) == 2 and calls[0]['initial_prompt'] == '睡眠是我们的超能力' and calls[1]['initial_prompt'] is None
    assert calls[0] | {'initial_prompt': None} == calls[1]
    assert calls[1]['task'] == 'transcribe' and calls[1]['multilingual'] is True
    assert info._title_prompt_filtered is True


def test_chinese_audio_retains_its_title_and_only_prepares_once():
    calls = []
    class Model:
        def transcribe(self, path, **options):
            calls.append(options)
            return [], SimpleNamespace(language='zh', language_probability=.99)
    WhisperTranscriber._run_model(Model(), Path('audio.wav'), '安装双系统', 'cpu')
    assert len(calls) == 1 and calls[0]['initial_prompt'] == '安装双系统'


def test_recovery_without_title_does_not_add_language_probe():
    calls = []
    class Model:
        def transcribe(self, path, **options):
            calls.append(options)
            return [], SimpleNamespace(language='en', language_probability=.99)
    WhisperTranscriber._run_model(Model(), Path('audio.wav'), None, 'cuda', recovery=True)
    assert len(calls) == 1 and calls[0]['condition_on_previous_text'] is False
