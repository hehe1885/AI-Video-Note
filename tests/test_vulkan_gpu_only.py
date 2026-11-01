"""GPU experiments must fail closed, before any CPU model initialization."""
from types import SimpleNamespace

import pytest

from backend import whisper_vulkan as v
from backend import whisper_vulkan_native as native


def test_gpu_only_missing_device_never_loads_model(monkeypatch, tmp_path):
    monkeypatch.setattr(v, 'ensure_runtime', lambda *args: tmp_path)
    monkeypatch.setattr(native, 'Native', lambda *args: SimpleNamespace(devices=lambda: []))
    monkeypatch.setattr(v, 'ensure_model', lambda *args: pytest.fail('no model initialization expected'))
    with pytest.raises(RuntimeError, match='禁止 CPU 回退'):
        v._transcribe(tmp_path, tmp_path/'input.wav', 'base', 'amd', None, None, None,
                      allow_cpu_fallback=False)


def test_gpu_only_failed_init_does_not_retry_on_cpu(monkeypatch, tmp_path):
    calls = []
    def initialize(path, params):
        calls.append(params.use_gpu)
        return None
    lib = SimpleNamespace(whisper_context_default_params=lambda: SimpleNamespace(),
                          whisper_init_from_file_with_params=initialize)
    monkeypatch.setattr(v, 'ensure_runtime', lambda *args: tmp_path)
    monkeypatch.setattr(v, 'ensure_model', lambda *args, **kwargs: tmp_path/'model.bin')
    monkeypatch.setattr(native, 'Native', lambda *args: SimpleNamespace(
        devices=lambda: [{'index': 0, 'name': 'AMD Radeon RX 6600'}], lib=lib))
    with pytest.raises(RuntimeError, match='禁止 CPU 回退'):
        v._transcribe(tmp_path, tmp_path/'input.wav', 'base', 'amd', None, None, None,
                      allow_cpu_fallback=False)
    assert calls == [True, True]  # FP16 then Q8, both on the same GPU.


def test_belle_download_uses_author_export(monkeypatch, tmp_path):
    calls = []
    def download(url, path, cancel):
        calls.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb') as stream:
            stream.write(b'lmgg')
            stream.truncate(31 * 1024 * 1024)
    monkeypatch.setattr(v, 'checked_download', download)
    monkeypatch.delenv('HF_ENDPOINT', raising=False)
    path = v.ensure_model(tmp_path, 'belle-turbo-zh')
    assert path.name == 'ggml-model.bin'
    assert len(calls) == 1
    assert '/BELLE-2/Belle-whisper-large-v3-turbo-zh-ggml/resolve/0a7f573/' in calls[0]
    assert v.cache_status(tmp_path, 'belle-turbo-zh') == 'cached'


def test_independent_windows_pass_zero_history_budget_to_native(monkeypatch, tmp_path):
    import numpy as np
    import faster_whisper.audio
    calls = []
    def transcribe(ctx, params, samples, count):
        calls.append((params.n_max_text_ctx, params.no_context, params.strategy))
        return 0
    lib = SimpleNamespace(
        whisper_context_default_params=lambda: SimpleNamespace(),
        whisper_init_from_file_with_params=lambda *args: 1,
        whisper_full_default_params=lambda strategy: native.Params(n_max_text_ctx=16384),
        whisper_full=transcribe, whisper_free=lambda *args: None,
        whisper_full_n_segments=lambda *args: 0,
        whisper_full_lang_id=lambda *args: 0, whisper_lang_str=lambda *args: b'zh')
    monkeypatch.setattr(v, 'ensure_runtime', lambda *args: tmp_path)
    monkeypatch.setattr(v, 'ensure_model', lambda *args, **kwargs: tmp_path/'model.bin')
    monkeypatch.setattr(native, 'Native', lambda *args: SimpleNamespace(
        devices=lambda: [{'index': 0, 'name': 'AMD Radeon RX 6600'}], lib=lib))
    monkeypatch.setattr(faster_whisper.audio, 'decode_audio', lambda *args, **kwargs: np.ones(16000, dtype=np.float32))
    result = v._transcribe(tmp_path, tmp_path/'input.wav', 'base', 'amd', None, None, None,
                           allow_cpu_fallback=False, decode_options={'n_max_text_ctx': 0})
    assert calls == [(0, True, 0)]
    assert result['decode_options']['n_max_text_ctx'] == 0
