import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from backend import asr_diagnostics as diagnostics, asr_process, main, video_pipeline
from backend.whisper_asr import WhisperTranscriber, TranscriptionCancelledError, CudaUnavailableError


@pytest.mark.asyncio
async def test_diagnostics_are_task_local_and_reach_inference_threads():
    async def job(label):
        messages = []
        with diagnostics.listen(messages.append):
            await asyncio.sleep(0)
            await asyncio.to_thread(diagnostics.emit, label)
        return messages
    assert await asyncio.gather(job('first'), job('second')) == [['first'], ['second']]


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['load', 'generator'])
async def test_cuda_failure_and_cpu_precision_are_logged_before_cpu_inference(tmp_path, monkeypatch, stage):
    messages = []

    class Model:
        def __init__(self, *args, **options):
            self.device = options['device']
            self.model = SimpleNamespace(device=self.device, compute_type=options['compute_type'], device_index=[0])
            if self.device == 'cuda' and stage == 'load':
                raise RuntimeError('test CUDA initialization failure')
            if self.device == 'cpu':
                assert any('失败' in m and 'CUDA' in m for m in messages)
                assert any('CPU' in m for m in messages)

        def transcribe(self, *args, **kwargs):
            def segments():
                if self.device == 'cuda':
                    assert any('CUDA:0' in m and 'float16' in m for m in messages)
                    raise RuntimeError('test CUDA inference failure')
                assert any('设备 CPU' in m and '精度 int8' in m for m in messages)
                yield SimpleNamespace(start=0, end=1, text='test')
            return segments(), SimpleNamespace(duration=1, duration_after_vad=1, language='en', language_probability=1)

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    transcriber = WhisperTranscriber(tmp_path)
    monkeypatch.setattr(transcriber, '_download_model_files', lambda *args: None)
    monkeypatch.setattr(sys.modules['backend.whisper_asr'], 'preferred_cuda_compute_type', lambda *args: 'float16')
    with diagnostics.listen(messages.append):
        result = await transcriber.transcribe(tmp_path / 'audio', 'base', True)
    assert result['device'] == 'cpu'
    assert result['compute_type'] == 'int8'


@pytest.mark.asyncio
async def test_loaded_cuda_device_is_visible_even_when_transcription_is_cancelled(tmp_path, monkeypatch):
    messages = []

    class Model:
        def __init__(self, *args, **kwargs):
            self.model = SimpleNamespace(device='cuda', device_index=[0], compute_type='int8_float16')

        def transcribe(self, *args, **kwargs):
            assert any('CUDA:0' in m and 'int8_float16' in m for m in messages)
            raise TranscriptionCancelledError()

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    transcriber = WhisperTranscriber(tmp_path)
    monkeypatch.setattr(transcriber, '_download_model_files', lambda *args: None)
    with diagnostics.listen(messages.append), pytest.raises(asyncio.CancelledError):
        await transcriber.transcribe(tmp_path / 'audio', 'turbo', True)
    assert not any('CUDA 计算失败' in m for m in messages)


@pytest.mark.asyncio
async def test_auto_failover_reports_each_switch_before_starting_next_backend(tmp_path, monkeypatch):
    messages = []

    async def resolve(*args):
        return 'cuda'

    async def whisper(media, model, use_gpu, **options):
        if use_gpu:
            raise CudaUnavailableError('test missing cudnn')
        assert any('Vulkan' in m and 'CPU' in m and 'test Vulkan failure' in m for m in messages)
        return {'device': 'cpu'}

    async def vulkan(*args):
        assert any('CUDA 不可用' in m and 'test missing cudnn' in m for m in messages)
        raise RuntimeError('test Vulkan failure')

    monkeypatch.setattr(main, 'resolve_backend', resolve)
    monkeypatch.setattr(main.transcriber, 'transcribe', whisper)
    monkeypatch.setattr(main.whisper_vulkan, 'transcribe', vulkan)
    with diagnostics.listen(messages.append):
        result = await main.run_transcription_local(tmp_path / 'audio', 'turbo', True, None, None, gpu_backend='auto')
    assert result['device'] == 'cpu'


@pytest.mark.asyncio
async def test_child_streams_diagnostic_before_completion_and_preserves_it_on_cancel(tmp_path, monkeypatch):
    script = """import json,sys
sys.stdin.readline()
print(json.dumps({'event':'diagnostic','message':'CUDA:0 float16'}),flush=True)
for line in sys.stdin:
    if json.loads(line).get('cmd')=='cancel':
        print(json.dumps({'event':'cancelled'}),flush=True)
        break
"""
    monkeypatch.setattr(asr_process, 'child_command', lambda: [sys.executable, '-c', script])
    ready = asyncio.Event()
    cancel = threading.Event()
    messages = []

    def report(message):
        messages.append(message)
        ready.set()

    with diagnostics.listen(report):
        worker = asyncio.create_task(asr_process.run_in_child(tmp_path / 'audio', 'turbo', True, None, cancel))
        try:
            await asyncio.wait_for(ready.wait(), 10)
            assert not worker.done()
            cancel.set()
            with pytest.raises(TranscriptionCancelledError):
                await asyncio.wait_for(worker, 10)
        finally:
            cancel.set()
            if not worker.done():
                await asyncio.gather(worker, return_exceptions=True)
    assert messages == ['CUDA:0 float16']


@pytest.mark.asyncio
async def test_child_main_emits_diagnostic_protocol(monkeypatch):
    # child_main owns an event loop, so execute it in a thread just as a separate worker.
    import io
    events = []
    monkeypatch.setattr(asr_process, 'use_utf8_pipes', lambda: None)
    monkeypatch.setattr(asr_process.sys, 'stdin', io.StringIO('{"model_name":"turbo"}\n'))
    monkeypatch.setattr(asr_process, '_emit', events.append)

    async def local(*args, **kwargs):
        await asyncio.to_thread(diagnostics.emit, 'CUDA:0 float16')
        return {'segments': [], 'device': 'cuda'}

    monkeypatch.setattr(main, 'run_transcription_local', local)
    assert await asyncio.to_thread(asr_process.child_main) == 0
    assert events[0] == {'event': 'diagnostic', 'message': 'CUDA:0 float16'}
    assert events[-1]['event'] == 'done'


@pytest.mark.asyncio
async def test_pipeline_persists_diagnostics_on_main_thread_before_cancellation(monkeypatch):
    task = {'status': 'processing', 'logs': []}
    saved = []
    thread_id = threading.get_ident()

    def persist(task_id):
        assert threading.get_ident() == thread_id
        saved.append(list(task['logs']))

    runtime = SimpleNamespace(tasks={'test': task}, persist_task_runtime=persist)

    async def pipeline(*args):
        await asyncio.to_thread(diagnostics.emit, 'CUDA:0 float16')
        await asyncio.sleep(0)
        assert saved == [['CUDA:0 float16']]
        raise asyncio.CancelledError()

    monkeypatch.setattr(video_pipeline, '_process_video_task', pipeline)
    with pytest.raises(asyncio.CancelledError):
        await video_pipeline.process_video_task(runtime, 'test', None)
    assert task['logs'] == ['CUDA:0 float16']
