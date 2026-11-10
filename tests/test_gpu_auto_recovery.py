import asyncio
import os
from types import SimpleNamespace
import pytest
from backend import gpu_devices as gpu, main, asr_process
from backend.whisper_asr import TranscriptionCancelledError


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'nt', reason='Windows CUDA recheck')
@pytest.mark.parametrize('failure', [RuntimeError('driver probe failed'), asyncio.TimeoutError(), ValueError('bad JSON'), None])
async def test_empty_or_failed_full_probe_rechecks_cuda(monkeypatch, failure):
    monkeypatch.setattr(gpu, '_cached_devices', None)
    calls = []
    device = {'name': 'NVIDIA RTX', 'cuda': True}
    async def query(cuda_only=False):
        calls.append(cuda_only)
        if not cuda_only:
            if failure is not None: raise failure
            return []
        return [device]
    monkeypatch.setattr(gpu, '_query_devices', query)
    assert await gpu._probe_devices() == [device]
    assert calls == [False, True]
    assert gpu._cached_devices == [device]


@pytest.mark.asyncio
async def test_empty_probe_is_not_cached(monkeypatch):
    monkeypatch.setattr(gpu, '_cached_devices', None)
    calls = []
    async def query(cuda_only=False):
        calls.append(cuda_only)
        return []
    monkeypatch.setattr(gpu, '_query_devices', query)
    assert await gpu._probe_devices() == []
    assert gpu._cached_devices is None
    await gpu._probe_devices()
    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_query_failure_reports_stderr_and_separates_stdin(monkeypatch):
    class Process:
        returncode = 7
        async def communicate(self): return b'', b'driver initialization failed'
    async def spawn(*args, **kwargs):
        assert kwargs['stdin'] == asyncio.subprocess.DEVNULL
        assert kwargs['stderr'] == asyncio.subprocess.PIPE
        assert '--cuda-only' in args
        return Process()
    monkeypatch.setattr(gpu.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(RuntimeError, match='driver initialization failed'):
        await gpu._query_devices(cuda_only=True)


@pytest.mark.asyncio
async def test_server_passes_resolved_device_without_losing_auto_failover(monkeypatch, tmp_path):
    calls = []
    async def resolve(*args): return 'cuda'
    async def child(*args, **kwargs):
        calls.append(kwargs)
        return {'device': 'cuda'}
    monkeypatch.setattr(main, 'resolve_backend', resolve)
    monkeypatch.setattr(main.asr_process, 'run_in_child', child)
    result = await main.run_transcription(tmp_path/'audio', 'turbo', True, None, None, gpu_backend='auto')
    assert result['device'] == 'cuda'
    assert calls == [{'gpu_backend': 'auto', 'resolved_backend': 'cuda'}]


@pytest.mark.asyncio
async def test_worker_uses_server_resolution_without_reprobing(monkeypatch, tmp_path):
    async def resolve(*args): raise AssertionError('Worker must not re-probe')
    async def transcribe(*args, **kwargs):
        assert kwargs['allow_cpu_fallback'] is False
        return {'device': 'cuda'}
    monkeypatch.setattr(main, 'resolve_backend', resolve)
    monkeypatch.setattr(main.transcriber, 'transcribe', transcribe)
    assert (await main.run_transcription_local(tmp_path/'audio','turbo',True,None,None,gpu_backend='auto',resolved_backend='cuda'))['device'] == 'cuda'


@pytest.mark.asyncio
async def test_cancelled_parent_does_not_log_expected_worker_cancellation(monkeypatch, tmp_path):
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    errors = []
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    ready, release = asyncio.Event(), asyncio.Event()
    async def child(*args, **kwargs):
        ready.set()
        await release.wait()
        raise TranscriptionCancelledError('expected cancellation')
    monkeypatch.setattr(main.asr_process, 'use_child_process', lambda: True)
    monkeypatch.setattr(main.asr_process, 'run_in_child', child)
    parent = asyncio.create_task(main.run_transcription(tmp_path/'audio','turbo',True,None,None))
    try:
        await ready.wait()
        parent.cancel()
        with pytest.raises(asyncio.CancelledError): await parent
        release.set()
        await asyncio.sleep(.05)
        assert not errors
        assert not main._active_transcriptions
    finally:
        release.set()
        loop.set_exception_handler(previous)
