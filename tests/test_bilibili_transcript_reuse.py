"""Part changes must not silently reuse another Bilibili transcript."""
import json
import os

import pytest

from backend import main
from backend.config_store import ConfigStore
from backend.transcript import TranscriptSegment
from backend.video_processor import BiliPage, BiliSubtitleOutcome, SubtitleResult, VideoProcessor


URL = 'https://www.bilibili.com/video/BV1miht6gERa'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'video_processor', VideoProcessor(tmp_path))
    monkeypatch.setattr(main, 'config_store', ConfigStore(tmp_path))
    monkeypatch.setattr(main.model_downloads, 'active', lambda: False)
    monkeypatch.setattr(main.environment_repair, 'snapshot', lambda: {'status': 'idle'})
    return tmp_path


def saved_task(task_id='old', url=URL, pages=None, **metadata):
    directory = main.task_directory(task_id)
    directory.mkdir(parents=True)
    (directory / 'task.json').write_text(json.dumps({
        'task_id': task_id, 'title': 'Old part', 'source_url': url,
        'bilibili_pages': pages, **metadata,
    }), encoding='utf-8')
    (directory / 'transcript.json').write_text(json.dumps({
        'source': 'bilibili_ai_subtitle', 'subtitle_policy': 'source-only-v1',
        'language': 'zh', 'segments': [{'start': 0, 'end': 10, 'text': 'Old P1 text'}],
    }), encoding='utf-8')
    return directory


@pytest.mark.parametrize('old_suffix,old_pages,new_suffix,new_pages,metadata,reuse', [
    ('?p=1', None, '?p=2', None, {}, False),
    ('?p=1', None, '/?p=1&share_source=copy', None, {}, True),
    ('', [1], '', [2], {}, False),
    ('', [1, 2], '', [2, 1, 1], {}, True),
    ('?p=1', [2], '?p=3', [2], {}, True),
    ('?p=1', None, '', [1], {}, True),
    ('', [1], '?p=1', None, {}, True),
    ('', None, '?p=1', None, {}, False),
    ('?p=1', None, '', None, {}, False),
    ('', None, '', None, {}, True),
    ('', None, '', [], {}, True),
    ('', [1, 2], '', None, {'bili_pages': [{'page': 1}, {'page': 2}], 'bili_total_pages': 2}, True),
    ('', None, '', [1, 2], {'bili_pages': [{'page': 1}, {'page': 2}], 'bili_total_pages': 2}, True),
    ('', None, '', None, {'bili_pages': [{'page': 1}], 'bili_total_pages': 2}, False),
    ('', [2], '', [2], {'bili_pages': [{'page': 1}], 'bili_total_pages': 2}, False),
    ('', None, '?p=1', None, {'bili_pages': [{'page': 1}], 'bili_total_pages': 1}, True),
    ('', [1], '', [1], {'bili_pages': [{'page': 'invalid'}]}, False),
    ('', [1], '', [1], {'bili_pages': [{}]}, False),
    ('', [0], '', [0], {}, False),
])
def test_automatic_reuse_matches_effective_part_range(
        workspace, old_suffix, old_pages, new_suffix, new_pages, metadata, reuse):
    saved_task(url=URL + old_suffix, pages=old_pages, **metadata)
    assert main.find_reusable_task(URL + new_suffix, new_pages) == ('old' if reuse else None)


def test_search_skips_newer_wrong_part_and_deleting_tasks(workspace):
    older = saved_task('matching', URL, [2])
    newer = saved_task('wrong-part', URL, [1])
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    assert main.find_reusable_task(URL, [2]) == 'matching'
    main.tasks['matching'] = {'_deleting': True}
    assert main.find_reusable_task(URL, [2]) is None


def test_other_videos_and_non_bilibili_queries_keep_their_identity(workspace):
    saved_task(url='https://www.youtube.com/watch?v=first')
    assert main.find_reusable_task('https://www.youtube.com/watch?v=second') is None
    assert main.find_reusable_task('https://www.youtube.com/watch?v=first') == 'old'
    assert main.find_reusable_task(URL, [1]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('transcript_only', [False, True])
@pytest.mark.parametrize('explicit_resume', [False, True])
async def test_submission_does_not_reuse_a_different_part(
        workspace, monkeypatch, transcript_only, explicit_resume):
    saved_task(url=URL + '?p=1', pages=[1])
    queued = []

    async def capture(task_id, request):
        queued.append((task_id, request))

    monkeypatch.setattr(main, 'run_queued_video_task', capture)
    fields = dict(video_url=URL + '?p=2', bilibili_pages=[2], llm_config={})
    if explicit_resume:
        fields['resume_task_id'] = 'old'
    if transcript_only:
        result = await main.start_transcribe(main.TranscribeRequest(**fields))
    else:
        result = await main.start_summarize(main.SummarizeRequest(**fields))
    await main.running_jobs[result['task_id']]
    assert result['reused_task_id'] is None
    assert main.tasks[result['task_id']]['resume_task_id'] is None
    assert main.read_task_manifest(result['task_id'])['bilibili_pages'] == [2]
    assert len(queued) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['same-range', 'resume-only', 'changed-range', 'restart'])
async def test_explicit_resume_preserves_matching_ranges_and_finds_matching_history(
        workspace, monkeypatch, mode):
    saved_task('old', URL + '?p=1', [2, 1])
    saved_task('other-range', URL, [3])

    async def capture(*args):
        pass

    monkeypatch.setattr(main, 'run_queued_video_task', capture)
    request = main.SummarizeRequest(video_url=URL, bilibili_pages=[1, 2],
                                    resume_task_id='old', llm_config={})
    expected = 'old'
    if mode == 'resume-only':
        request.video_url = ''
        request.bilibili_pages = None
    elif mode == 'changed-range':
        request.bilibili_pages = [3]
        expected = 'other-range'
    elif mode == 'restart':
        request.processing_mode = 'restart'
        expected = None
    result = await main.start_summarize(request)
    await main.running_jobs[result['task_id']]
    assert result['reused_task_id'] == expected
    if mode == 'resume-only':
        manifest = main.read_task_manifest(result['task_id'])
        assert manifest['source_url'] == URL + '?p=1'
        assert manifest['bilibili_pages'] == [2, 1]


@pytest.mark.asyncio
async def test_changed_part_reads_new_subtitles_through_the_pipeline(workspace, monkeypatch):
    old = saved_task(url=URL + '?p=1', pages=[1])
    original = (old / 'transcript.json').read_bytes()
    calls = []

    async def info(*args, **kwargs):
        calls.append('metadata')
        return {'title': 'Part 2', 'source': 'bilibili', 'duration': 10}

    async def platform_subtitles(*args, **kwargs):
        return None

    async def bilibili_subtitles(url, cookie, only_pages=None):
        calls.append(('subtitles', only_pages))
        subtitle = SubtitleResult([TranscriptSegment(0, 10, 'New P2 text')], 'zh', 'bilibili_ai_subtitle')
        return BiliSubtitleOutcome(subtitle, 'ok', title='Part 2', total_pages=2,
            pages=(BiliPage(page=2, part='Part 2', cid=102, duration=10),))

    async def run_pipeline(task_id, request):
        await main.process_video_task(task_id, request)

    monkeypatch.setattr(main.video_processor, 'get_video_info', info)
    monkeypatch.setattr(main.video_processor, 'fetch_subtitles', platform_subtitles)
    monkeypatch.setattr(main.video_processor, 'fetch_bilibili_subtitles', bilibili_subtitles)
    monkeypatch.setattr(main, 'run_queued_video_task', run_pipeline)
    result = await main.start_summarize(main.SummarizeRequest(
        video_url=URL + '?p=2', bilibili_pages=[2], resume_task_id='old',
        generation_mode='agent', llm_config={},
    ))
    await main.running_jobs[result['task_id']]
    task = main.tasks[result['task_id']]
    assert task['status'] == 'awaiting_agent', task.get('error')
    transcript = main.load_transcript_result(main.task_directory(result['task_id']))
    assert [segment.text for segment in transcript['segments']] == ['New P2 text']
    assert calls == ['metadata', ('subtitles', [2])]
    assert main.read_task_manifest(result['task_id'])['bili_pages'][0]['page'] == 2
    assert (old / 'transcript.json').read_bytes() == original
