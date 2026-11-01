import asyncio
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import main, transcript_review as review, agent_queue, agent_auto
from backend.transcript import TranscriptSegment


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'notify_task', lambda *args: None)
    task_id = 'review'
    directory = main.task_directory(task_id)
    directory.mkdir(parents=True)
    main.tasks[task_id] = main.new_task(status='processing', task_id=task_id)
    request = main.SummarizeRequest(llm_config={}, summary_style='faithful')
    main.write_task_manifest(task_id, request, None, 'web')
    segments = [TranscriptSegment(0, 10, '学习率为 0.01。'), TranscriptSegment(10, 20, '随后执行 --model base。')]
    (directory / 'transcript.json').write_text(json.dumps({'language': 'zh', 'source': 'faster_whisper', 'segments': [s.to_dict() for s in segments]}), encoding='utf-8')
    return task_id, directory, segments


def question(index=0):
    return {'segment_index': index, 'quote': '0.01' if index == 0 else '--model', 'reason': '数值或参数与前文的表述不一致，需要核对原音频。'}


@pytest.mark.asyncio
async def test_detection_requires_exact_source_and_uses_auxiliary_phase(source):
    _, _, segments = source
    prompts = []
    async def complete(prompt):
        prompts.append(prompt)
        return json.dumps({'issues': [question()]})
    items = await review.detect(segments, '课程', complete)
    assert items[0]['original'] == segments[0].text and items[0]['start'] == 0
    assert 'before' not in items[0] and 'after' not in items[0]
    assert 'JSON' in prompts[0] and '不猜测替换' in prompts[0]
    with pytest.raises(ValueError):
        review.validate_issues([{'segment_index': 0, 'quote': '0.001', 'reason': 'guess'}], segments)
    with pytest.raises(ValueError): review.validate_issues([question(999)], segments)
    assert review.validate_issues([question(), question()], segments)[0]['reason'].count('需要核对') == 1


@pytest.mark.asyncio
async def test_confirmed_edit_releases_gate_and_preserves_raw_transcript_and_timing(source):
    task_id, directory, segments = source
    async def complete(prompt): return json.dumps({'issues': [question()]})
    job = asyncio.create_task(review.check_and_confirm(main, task_id, segments, '课程', complete))
    await asyncio.sleep(0)
    assert not job.done()
    metadata = main.tasks[task_id]['transcript_review']
    assert metadata['status'] == 'pending' and 'items' not in metadata
    page = await review.get_review(task_id)
    assert page['items'][0]['original'] == '学习率为 0.01。'
    assert await review.confirm_review(task_id, review.Confirmation(revision=page['revision'], decisions=[review.Decision(id='seg-0', text='学习率为 0.001。')])) == {'confirmed': True}
    result = await job
    assert result[0].text == '学习率为 0.001。'
    assert result[0].start == 0 and result[0].end == 10 and result[1] == segments[1]
    assert json.loads((directory / 'transcript_original.json').read_text(encoding='utf-8'))['segments'][0]['text'] == '学习率为 0.01。'
    assert '0.001' in (directory / 'transcript.md').read_text(encoding='utf-8')
    assert review.read_review(directory)['status'] == 'confirmed'
    assert main.task_status_payload(task_id, main.tasks[task_id])['transcript_review']['status'] == 'confirmed'


@pytest.mark.asyncio
async def test_keep_original_is_explicit_and_failed_stale_requests_do_not_release_gate(source):
    task_id, _, segments = source
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    event = main.tasks[task_id]['_transcript_review_event']
    for body in [review.Confirmation(revision='stale', keep_original=True), review.Confirmation(revision=value['revision'], decisions=[]),
                 review.Confirmation(revision=value['revision'], decisions=[review.Decision(id='wrong', text='change')])]:
        with pytest.raises(HTTPException): await review.confirm_review(task_id, body)
        assert not event.is_set()
    await review.confirm_review(task_id, review.Confirmation(revision=value['revision'], keep_original=True))
    assert await review.wait_for_confirmation(main, task_id) == segments
    with pytest.raises(HTTPException):
        await review.confirm_review(task_id, review.Confirmation(revision=value['revision'], keep_original=True))


@pytest.mark.asyncio
async def test_detector_failure_requires_manual_confirmation_and_empty_result_continues(source):
    task_id, _, segments = source
    async def fail(prompt): raise RuntimeError('fixture failure')
    job = asyncio.create_task(review.check_and_confirm(main, task_id, segments, '课程', fail))
    await asyncio.sleep(0)
    page = await review.get_review(task_id)
    assert page['manual_check'] and page['total'] == len(segments)
    assert not job.done()
    await review.confirm_review(task_id, review.Confirmation(revision=page['revision'], keep_original=True))
    assert await job == segments
    # A saved confirmation survives a retry without another model call.
    async def forbidden(prompt): raise AssertionError('Confirmed source must not be rescanned')
    assert await review.check_and_confirm(main, task_id, segments, '课程', forbidden) == segments


@pytest.mark.asyncio
async def test_no_issues_proceeds_without_popup(source):
    task_id, _, segments = source
    async def clear(prompt): return '{"issues":[]}'
    assert await review.check_and_confirm(main, task_id, segments, '课程', clear) == segments
    assert main.tasks[task_id]['transcript_review']['status'] == 'clear'
    assert '_transcript_review_event' not in main.tasks[task_id]


@pytest.mark.asyncio
async def test_cancellation_wakes_agent_review_and_prevents_confirmation(source):
    task_id, _, segments = source
    main.tasks[task_id]['status'] = 'awaiting_agent'
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    job = asyncio.create_task(review.wait_for_confirmation(main, task_id))
    await asyncio.sleep(0)
    await main.cancel_task(task_id)
    with pytest.raises(asyncio.CancelledError): await job
    with pytest.raises(HTTPException): await review.confirm_review(task_id, review.Confirmation(revision=value['revision'], keep_original=True))


@pytest.mark.asyncio
async def test_agent_report_blocks_result_until_user_confirmation(source):
    task_id, _, segments = source
    main.tasks[task_id]['status'] = 'awaiting_agent'
    agent_queue.write_job(task_id, {'settings': {'note_language': 'zh-CN'}, 'title': '课程', 'info': {}, 'screenshots': []})
    claim = await agent_queue.claim_job(task_id, agent_queue.Claim(agent_name='fixture'))
    response = await agent_queue.report_transcript_issues(task_id, agent_queue.TranscriptIssues(claim_token=claim['claim_token'], issues=[question()]))
    assert response['waiting_for_user']
    with pytest.raises(HTTPException) as error:
        await agent_queue.finish(task_id, agent_queue.Result(claim_token=claim['claim_token'], markdown='# Note'))
    assert error.value.status_code == 409
    page = await review.get_review(task_id)
    await review.confirm_review(task_id, review.Confirmation(revision=page['revision'], keep_original=True))
    packet = await agent_queue.get_packet(task_id)
    assert packet['segments'][0]['segment_index'] == 0
    assert packet['transcript_review']['status'] == 'confirmed'


@pytest.mark.asyncio
async def test_pending_review_restores_on_new_attempt_without_rescanning(source):
    task_id, directory, segments = source
    review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    next_id = 'continued'
    next_dir = main.task_directory(next_id); next_dir.mkdir(parents=True)
    main.tasks[next_id] = main.new_task(status='processing', task_id=next_id)
    main.write_task_manifest(next_id, main.SummarizeRequest(llm_config={}), task_id, 'web')
    (next_dir / 'transcript.json').write_bytes((directory / 'transcript.json').read_bytes())
    async def forbidden(prompt): raise AssertionError('Pending questions should be reused')
    job = asyncio.create_task(review.check_and_confirm(main, next_id, segments, '课程', forbidden, resume_directory=directory))
    await asyncio.sleep(0)
    page = await review.get_review(next_id)
    assert not page['manual_check'] and page['total'] == 1
    await review.confirm_review(next_id, review.Confirmation(revision=page['revision'], keep_original=True))
    assert await job == segments


@pytest.mark.asyncio
async def test_write_failure_does_not_confirm_or_release_waiting_task(source, monkeypatch):
    task_id, directory, segments = source
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    original = (directory / 'transcript.json').read_bytes()
    write = main.write_transcript_files
    calls = 0
    async def fail_once(*args):
        nonlocal calls
        calls += 1
        if calls == 1: raise OSError('fixture disk error')
        return await write(*args)
    monkeypatch.setattr(main, 'write_transcript_files', fail_once)
    with pytest.raises(OSError):
        await review.confirm_review(task_id, review.Confirmation(revision=value['revision'], decisions=[review.Decision(id='seg-0', text='changed')]))
    assert main.load_transcript_result(directory)['segments'] == segments
    assert review.read_review(directory)['status'] == 'pending'
    assert not main.tasks[task_id]['_transcript_review_event'].is_set()


def test_corrections_replace_only_exact_unique_quotes_and_lower_ambiguous_confidence():
    segments = [TranscriptSegment(2, 9, '梯度下将采用随即梯度。')]
    issues = [{'segment_index': 0, 'quote': '下将', 'replacement': '下降', 'reason': '术语', 'confidence': .96},
              {'segment_index': 0, 'quote': '随即', 'replacement': '随机', 'reason': '术语', 'confidence': .92}]
    item = review.validate_issues(issues + [issues[0]], segments)[0]
    assert item['suggestion'] == '梯度下降采用随机梯度。'
    assert item['original'] == segments[0].text and (item['start'], item['end']) == (2, 9)
    assert item['confidence'] == .92
    unresolved = {'segment_index': 0, 'quote': '梯度', 'replacement': '其他', 'reason': '重复位置不明确', 'confidence': .99}
    item = review.validate_issues(issues + [unresolved], segments)[0]
    assert item['suggestion'] == '梯度下降采用随机梯度。'
    assert item['confidence'] < .6
    overlap = {**issues[0], 'quote': '梯度下将', 'replacement': '全新事实'}
    assert review.validate_issues([issues[0], overlap], segments)[0]['confidence'] < .6


@pytest.mark.parametrize('confidence', [True, -1, 1.1, float('nan'), float('inf'), None, '0.95'])
def test_replacement_requires_valid_numeric_confidence(source, confidence):
    with pytest.raises(ValueError):
        review.validate_issues([{**question(), 'replacement': '0.001', 'confidence': confidence}], source[2])


@pytest.mark.asyncio
async def test_ai_retry_is_revision_guarded_and_does_not_write_before_user_confirms(source):
    task_id, directory, segments = source
    value = review.begin_review(main, task_id, segments, review.validate_issues([question()], segments), manual=True)
    original = (directory / 'transcript.json').read_bytes()
    async def complete(prompt):
        return json.dumps({'issues': [{**question(), 'replacement': '0.001', 'confidence': .72}]})
    main.tasks[task_id]['_transcript_review_complete'] = complete
    with pytest.raises(HTTPException) as error:
        await review.revise_with_ai(task_id, review.AIReviewRequest(revision='stale'))
    assert error.value.status_code == 409
    updated = await review.revise_with_ai(task_id, review.AIReviewRequest(revision=value['revision']))
    assert not updated['queued'] and updated['revision'] != value['revision']
    row = (await review.get_review(task_id))['items'][0]
    assert row['suggestion'] == '学习率为 0.001。' and row['confidence'] == .72
    assert (directory / 'transcript.json').read_bytes() == original
    assert not main.tasks[task_id]['_transcript_review_event'].is_set()
    async def fail(prompt): raise RuntimeError('model unavailable')
    main.tasks[task_id]['_transcript_review_complete'] = fail
    with pytest.raises(HTTPException) as error:
        await review.revise_with_ai(task_id, review.AIReviewRequest(revision=updated['revision']))
    assert error.value.status_code == 503
    assert review.read_review(directory)['revision'] == updated['revision']
    assert (directory / 'transcript.json').read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize('has_issue', [True, False])
async def test_transcript_only_manual_agent_must_review_then_waits_for_user(source, has_issue):
    task_id, directory, segments = source
    request = main.SummarizeRequest(output='transcript', generation_mode='agent', llm_config={}, screenshot_density='none')
    job = asyncio.create_task(review.review_transcript_output(main, task_id, request, segments, '课程'))
    await asyncio.sleep(0)
    assert main.tasks[task_id]['status'] == 'awaiting_agent' and not job.done()
    packet = await agent_queue.get_packet(task_id)
    assert '不生成笔记' in packet['prompt']
    token = (await agent_queue.claim_job(task_id, agent_queue.Claim()))['claim_token']
    result = await agent_queue.report_transcript_issues(task_id, agent_queue.TranscriptIssues(claim_token=token, issues=[question()] if has_issue else []))
    if has_issue:
        await asyncio.sleep(0)
        assert not job.done()
        page = await review.get_review(task_id)
        queued = await review.revise_with_ai(task_id, review.AIReviewRequest(revision=page['revision']))
        assert queued['queued']
        event = main.tasks[task_id]['_transcript_review_event']
        await agent_queue.report_transcript_issues(task_id, agent_queue.TranscriptIssues(claim_token=token, issues=[{**question(), 'replacement': '0.001', 'confidence': .65}]))
        page = await review.get_review(task_id)
        assert not event.is_set() and main.tasks[task_id]['_transcript_review_event'] is event
        assert page['items'][0]['suggestion'] == '学习率为 0.001。'
        await review.confirm_review(task_id, review.Confirmation(revision=page['revision'], keep_original=True))
    else:
        assert not result['waiting_for_user'] and review.read_review(directory)['policy_version'] == 'proofread-v2'
    assert await asyncio.wait_for(job, 1) == segments


@pytest.mark.asyncio
async def test_transcript_auto_agent_only_reviews_without_note_generation(source, monkeypatch):
    task_id, directory, segments = source
    calls = []
    async def cli(*args, **kwargs):
        calls.append(kwargs)
        return '{"issues":[]}'
    monkeypatch.setattr(agent_auto, 'run_cli', cli)
    request = main.SummarizeRequest(output='transcript', generation_mode='agent', agent_execution='auto', llm_config={})
    assert await review.review_transcript_output(main, task_id, request, segments, '课程') == segments
    assert len(calls) == 1 and calls[0]['schema'] == review.SCHEMA
    assert not (directory / 'notes.md').exists()


@pytest.mark.parametrize('status', ['processing', 'awaiting_agent', 'cancelled'])
def test_restart_cannot_treat_unconfirmed_transcript_as_completed(source, status):
    task_id, directory, segments = source
    main.write_task_manifest(task_id, main.SummarizeRequest(output='transcript', llm_config={}), None, 'web')
    review.begin_review(main, task_id, segments, review.validate_issues([question()], segments))
    main.tasks[task_id]['status'] = status
    main.persist_task_runtime(task_id)
    main.tasks.clear()
    main.restore_tasks_from_workspace()
    assert main.tasks[task_id]['status'] == ('cancelled' if status == 'cancelled' else 'failed')
    assert review.read_review(directory)['status'] == 'pending'
    assert not (directory / 'transcript_original.json').exists()


@pytest.mark.asyncio
async def test_cancel_manual_agent_subtitle_wait_does_not_hang(source):
    task_id, _, segments = source
    request = main.SummarizeRequest(output='transcript', generation_mode='agent', llm_config={}, screenshot_density='none')
    job = asyncio.create_task(review.review_transcript_output(main, task_id, request, segments, '课程'))
    await asyncio.sleep(0)
    await main.cancel_task(task_id)
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
