import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.generation_store import GenerationStore
from backend.llm_summarizer import LLMSummarizer, IncompleteCompletionError
from backend.transcript import TranscriptSegment


def model(create):
    instance = LLMSummarizer.__new__(LLMSummarizer)
    instance.model_type, instance.model, instance.base_url = 'custom', 'test', 'https://example.test/v1'
    instance.warnings = []
    instance.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return instance


async def stream(text, finish='stop'):
    yield SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text), finish_reason=finish)])


@pytest.mark.asyncio
async def test_length_continues_same_task_without_breaking_sentence_or_prompt_phase():
    requests = []
    async def create(**kwargs):
        requests.append(kwargs)
        return stream('pip install ' if len(requests) == 1 else 'example',
                      'length' if len(requests) == 1 else 'stop')
    instance = model(create)
    result = await instance._complete('Source transcript', 200, effort='off', prompt_phase='material')
    assert result == 'pip install example'
    assert len(requests) == 2
    assert requests[1]['messages'][:2] == requests[0]['messages']
    assert requests[1]['messages'][2] == {'role': 'assistant', 'content': 'pip install '}
    assert requests[1]['messages'][3]['role'] == 'user'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['planning', 'vision', 'transcript_review'])
async def test_truncated_json_is_never_concatenated_or_accepted(phase):
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return stream('{"items":[', 'length')
    with pytest.raises(IncompleteCompletionError, match='length'):
        await model(create)._complete('JSON task', 200, effort='off', prompt_phase=phase)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_partial_note_failure_then_explicit_resume_preserves_prose(tmp_path):
    calls = []
    async def fail(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return stream('# Notes\n\nThe answer is ', 'length')
        raise RuntimeError('temporary model failure')
    first = model(fail)
    segments = [TranscriptSegment(0, 10, 'The answer is forty two.')]
    with pytest.raises(IncompleteCompletionError, match='继续生成'):
        await first.generate_summary('Fixture', segments, style='concise', checkpoint_directory=tmp_path / 'old')
    assert first._checkpoint.load('draft') is None
    receipts = list((tmp_path / 'old').glob('partial-*.json'))
    assert len(receipts) == 1
    key = receipts[0].stem
    assert first._checkpoint.load(key) is None
    assert first._checkpoint.load_incomplete(key) == '# Notes\n\nThe answer is '
    assert not list((tmp_path / 'old').glob('request-*.json'))

    resumed_requests = []
    async def finish(**kwargs):
        resumed_requests.append(kwargs)
        return stream('forty two.')
    second = model(finish)
    note = await second.generate_summary('Fixture', segments, style='concise',
        checkpoint_directory=tmp_path / 'new', checkpoint_sources=[tmp_path / 'old'])
    assert note == '# Notes\n\nThe answer is forty two.'
    assert len(resumed_requests) == 1
    assert resumed_requests[0]['messages'][2]['content'] == '# Notes\n\nThe answer is '
    assert second._checkpoint.load('draft') == note
    assert second._checkpoint.load_incomplete(key) is None
    # A fresh generation does not read partial prose from earlier attempts.
    assert GenerationStore(tmp_path / 'fresh', {'different': True}).load_incomplete(key) is None


@pytest.mark.asyncio
async def test_repeated_length_has_bounded_calls_and_no_completed_receipt(tmp_path):
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return stream(f'piece{len(calls)} ', 'length')
    instance = model(create)
    instance._checkpoint = GenerationStore(tmp_path, {'fixture': True})
    with pytest.raises(IncompleteCompletionError, match='仍未完成'):
        await instance._checkpoint_complete('Long task', 200, effort='off')
    assert len(calls) == 4
    entries = [json.loads(p.read_text()) for p in tmp_path.glob('partial-*.json')]
    assert [entry['status'] for entry in entries] == ['incomplete']
    assert not list(tmp_path.glob('request-*.json'))


@pytest.mark.asyncio
async def test_cancelled_stream_preserves_received_text_for_resume(tmp_path):
    async def cancelled():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='received '), finish_reason=None)])
        raise asyncio.CancelledError()
    async def create(**kwargs): return cancelled()
    instance = model(create)
    instance._checkpoint = GenerationStore(tmp_path, {'fixture': True})
    with pytest.raises(asyncio.CancelledError):
        await instance._complete('Task', 200, effort='off')
    key = next(tmp_path.glob('partial-*.json')).stem
    assert instance._checkpoint.load_incomplete(key) == 'received '
    assert instance._checkpoint.load(key) is None


@pytest.mark.asyncio
async def test_long_section_does_not_turn_incomplete_response_into_successful_raw_fallback():
    async def create(**kwargs): raise AssertionError('No live model calls')
    instance = model(create)
    calls = []
    async def incomplete(*args, **kwargs):
        calls.append(1)
        raise IncompleteCompletionError('Incomplete', 'partial')
    instance._complete = incomplete
    with pytest.raises(IncompleteCompletionError):
        await instance._write_one_section('Fixture', 1, 1, [TranscriptSegment(0, 120, 'Material')],
            '', 'off', 'off', lambda *args: None, None)
    assert calls == [1]
    assert not instance.warnings


@pytest.mark.asyncio
async def test_response_without_terminal_reason_is_not_marked_complete():
    async def create(**kwargs): return stream('partial', None)
    with pytest.raises(IncompleteCompletionError):
        await model(create)._complete('Task', 200, effort='off')


@pytest.mark.asyncio
async def test_replayed_prefix_is_not_duplicated_in_finished_note():
    calls = []
    async def create(**kwargs):
        calls.append(1)
        return stream('Existing ', 'length') if len(calls) == 1 else stream('Existing continued.')
    assert await model(create)._complete('Task', 200, effort='off') == 'Existing continued.'


@pytest.mark.asyncio
async def test_no_progress_keeps_original_partial_instead_of_replayed_duplicate(tmp_path):
    calls = []
    async def create(**kwargs):
        calls.append(1)
        return stream('Existing', 'length')
    instance = model(create)
    instance._checkpoint = GenerationStore(tmp_path, {'fixture': True})
    with pytest.raises(IncompleteCompletionError, match='未推进'):
        await instance._complete('Task', 200, effort='off')
    key = next(tmp_path.glob('partial-*.json')).stem
    assert instance._checkpoint.load_incomplete(key) == 'Existing'
    assert len(calls) == 2
