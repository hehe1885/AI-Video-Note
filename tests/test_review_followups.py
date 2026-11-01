"""Damaged history isolation, forced-ASR part routing, and durable Agent discovery."""
import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from backend import main, transcript_review
from backend.config_store import ConfigStore
from backend.transcript import TranscriptSegment
from backend.video_processor import BiliPage, BiliVideoInfo, VideoProcessor


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'video_processor', VideoProcessor(tmp_path))
    monkeypatch.setattr(main, 'config_store', ConfigStore(tmp_path))
    monkeypatch.setattr(main, 'notify_task', lambda *args: None)
    return tmp_path, TestClient(main.app)


def record(name, status='completed', output='note', runtime=None):
    directory = main.task_directory(name)
    directory.mkdir(parents=True)
    saved = {'status': status, 'logs': [], **(runtime or {})}
    (directory / 'task.json').write_text(json.dumps({'task_id': name, 'origin': 'web',
        'title': name, 'output': output, 'runtime': saved}), encoding='utf-8')
    return directory


@pytest.mark.parametrize('output,data', [
    ('note', b'\xff\xfeinvalid UTF-8'), ('note', b'  \n\t'),
    ('transcript', b'{'), ('transcript', b'[]'), ('transcript', b'{"segments":[]}'),
    ('transcript', b'{"segments":[null]}'),
    ('transcript', b'{"segments":[{"start":0,"end":10,"text":23}]}'),
    ('transcript', b'{"segments":[{"start":0,"end":NaN,"text":"text"}]}'),
    ('transcript', b'{"segments":[{"start":10,"end":0,"text":"text"}]}'),
])
def test_damaged_artifact_is_failed_without_stopping_other_tasks(app_data, output, data):
    _, client = app_data
    good = record('good')
    (good / 'notes.md').write_text('# Valid note', encoding='utf-8')
    bad = record('bad', output=output)
    artifact = bad / ('transcript.json' if output == 'transcript' else 'notes.md')
    artifact.write_bytes(data)
    main.restore_tasks_from_workspace()
    assert main.tasks['good']['status'] == 'completed'
    assert main.tasks['bad']['status'] == 'failed'
    assert '损坏' in main.tasks['bad']['error']
    main.tasks.clear()
    rows = {row['task_id']: row['status'] for row in client.get('/api/tasks').json()['tasks']}
    assert rows == {'good': 'completed', 'bad': 'failed'}
    assert client.get('/api/task/bad').json()['status'] == 'failed'
    if output == 'transcript':
        assert client.get('/api/task/bad/transcript').status_code == 500
    assert artifact.read_bytes() == data


def test_repaired_file_invalidates_validation_cache(app_data):
    _, client = app_data
    directory = record('repaired')
    note = directory / 'notes.md'
    note.write_bytes(b'\xffinvalid')
    assert client.get('/api/tasks').json()['tasks'][0]['status'] == 'failed'
    before = note.stat().st_mtime_ns
    note.write_text('# Valid restored note', encoding='utf-8')
    os.utime(note, ns=(before + 1000000000, before + 1000000000))
    assert client.get('/api/tasks').json()['tasks'][0]['status'] == 'completed'
    assert client.get('/api/task/repaired').json()['result']['markdown'] == '# Valid restored note'


@pytest.mark.parametrize('runtime', [
    {'step': 'bad'}, {'progress': []}, {'elapsed_seconds': float('nan')},
    {'logs': 123}, {'result': []}, {'step': float('inf')},
])
def test_malformed_runtime_fields_cannot_prevent_startup(app_data, runtime):
    directory = record('old-format', runtime=runtime)
    (directory / 'notes.md').write_text('# Valid note', encoding='utf-8')
    main.restore_tasks_from_workspace()
    assert main.tasks['old-format']['status'] == 'completed'
    assert app_data[1].get('/api/tasks').status_code == 200
    assert app_data[1].get('/api/task/old-format').status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix,selection,expected', [
    ('?p=1', [2], [2]), ('?p=2', [3, 1], [1, 3]),
    ('?p=2', None, [2]), ('', None, [1, 2, 3]),
    ('?p=2', [], [2]), ('?p=2', [99], [2]),
])
async def test_forced_asr_downloads_exact_selected_parts(app_data, monkeypatch, suffix, selection, expected):
    root, _ = app_data
    pages = tuple(BiliPage(page=i, cid=100 + i, part=f'Part {i}', duration=10) for i in (1, 2, 3))
    urls = []

    async def info(*args, **kwargs):
        return {'title': 'Selected parts', 'source': 'bilibili', 'duration': 30}
    async def inventory(*args, **kwargs):
        return BiliVideoInfo('BV1miht6gERa', None, 'Selected parts', pages)
    async def audio(url, task_id, cookie, **kwargs):
        urls.append(url)
        return root / (kwargs.get('media_name', 'audio') + '.wav')
    async def transcribe(path, *args, **kwargs):
        return {'segments': [TranscriptSegment(0, 10, f'Original speech from {path.stem}')],
                'model': 'base', 'requested_model': 'base', 'device': 'cpu', 'duration': 10, 'language': 'en'}
    async def review(runtime, task_id, request, segments, *args, **kwargs):
        return segments
    async def no_subtitle_request(*args, **kwargs):
        pytest.fail('Forced ASR must not request subtitles')
    monkeypatch.setattr(main.video_processor, 'get_video_info', info)
    monkeypatch.setattr(main.video_processor, 'bilibili_video_pages', inventory)
    monkeypatch.setattr(main.video_processor, 'download_audio', audio)
    monkeypatch.setattr(main.video_processor, 'fetch_bilibili_subtitles', no_subtitle_request)
    monkeypatch.setattr(main, 'run_transcription', transcribe)
    monkeypatch.setattr(main, 'archive_note', lambda *args, **kwargs: None)
    monkeypatch.setattr(transcript_review, 'review_transcript_output', review)
    directory = record('asr', status='processing', output='transcript')
    main.tasks['asr'] = main.new_task(task_id='asr')
    request = main.SummarizeRequest(video_url='https://www.bilibili.com/video/BV1miht6gERa' + suffix,
        prefer_subtitles=False, bilibili_pages=selection, output='transcript', llm_config={})
    main.write_task_manifest('asr', request, None, 'web')
    await main.process_video_task('asr', request)
    assert main.tasks['asr']['status'] == 'completed', main.tasks['asr'].get('error')
    from urllib.parse import parse_qs, urlsplit
    assert [int(parse_qs(urlsplit(url).query)['p'][0]) for url in urls] == expected
    manifest = main.read_task_manifest('asr')
    assert [page['page'] for page in manifest['bili_pages']] == expected
    transcript = main.load_transcript_result(directory)
    assert [segment.start for segment in transcript['segments']] == [10 * index for index in range(len(expected))]
    assert all(f'audio_p{page}' in segment.text for page, segment in zip(expected, transcript['segments']))


@pytest.mark.asyncio
async def test_forced_asr_stops_when_part_inventory_is_unavailable(app_data, monkeypatch):
    async def info(*args, **kwargs):
        return {'title': 'Unavailable inventory', 'source': 'bilibili', 'duration': 10}
    async def inventory(*args, **kwargs):
        return None
    async def forbidden(*args, **kwargs):
        pytest.fail('Never default to P1 when requested parts cannot be resolved')
    monkeypatch.setattr(main.video_processor, 'get_video_info', info)
    monkeypatch.setattr(main.video_processor, 'bilibili_video_pages', inventory)
    monkeypatch.setattr(main.video_processor, 'download_audio', forbidden)
    record('asr', status='processing')
    main.tasks['asr'] = main.new_task(task_id='asr')
    await main.process_video_task('asr', main.SummarizeRequest(
        video_url='https://www.bilibili.com/video/BV1miht6gERa',
        prefer_subtitles=False, bilibili_pages=[2], llm_config={}))
    assert main.tasks['asr']['status'] == 'failed'
    assert '分 P 信息' in main.tasks['asr']['error']


def agent_job(name, output='note', lease_until=0):
    directory = record(name, 'awaiting_agent', output)
    (directory / 'agent_job.json').write_text(json.dumps({
        'title': name, 'settings': {'output': output}, 'lease_until': lease_until,
    }), encoding='utf-8')
    return directory


def test_queue_discovers_old_pending_jobs_without_loading_all_notes(app_data, monkeypatch):
    _, client = app_data
    monkeypatch.setattr(main, 'TASK_HISTORY_LIMIT', 2)
    waiting = agent_job('old-waiting')
    os.utime(waiting, (1000, 1000))
    for index in range(5):
        directory = record(f'new-{index}')
        (directory / 'notes.md').write_text('# Newer note', encoding='utf-8')
        os.utime(directory, (2000 + index, 2000 + index))
    main.restore_tasks_from_workspace()
    assert len(main.tasks) == 2 and 'old-waiting' not in main.tasks
    jobs = client.get('/api/agent/jobs').json()['jobs']
    assert [job['task_id'] for job in jobs] == ['old-waiting']
    assert len(main.tasks) == 3, 'Discovery must not load all completed note bodies'
    assert client.post('/api/agent/jobs/old-waiting/claim', json={}).status_code == 200


def test_direct_claim_lazily_loads_old_jobs_and_honors_existing_lease(app_data):
    _, client = app_data
    free = agent_job('free')
    occupied = agent_job('occupied', lease_until=time.time() + 600)
    before = (occupied / 'agent_job.json').read_bytes()
    assert not main.tasks
    assert client.post('/api/agent/jobs/free/claim', json={}).status_code == 200
    assert client.post('/api/agent/jobs/occupied/claim', json={}).status_code == 409
    assert (occupied / 'agent_job.json').read_bytes() == before
    assert free.exists()


def test_corrupt_agent_packet_does_not_hide_other_jobs_or_revive_transcript_waits(app_data):
    _, client = app_data
    agent_job('valid')
    bad = agent_job('corrupt')
    (bad / 'agent_job.json').write_text('[]', encoding='utf-8')
    agent_job('transcript', output='transcript')
    jobs = client.get('/api/agent/jobs').json()['jobs']
    assert [job['task_id'] for job in jobs] == ['valid']
    assert client.post('/api/agent/jobs/transcript/claim', json={}).status_code == 409
    assert main.tasks['transcript']['status'] == 'failed'


@pytest.mark.parametrize('payload', [[], {}, {'segments': [None]},
    {'segments': [{'start': 0, 'end': 1, 'text': []}]}])
def test_corrupt_transcript_is_not_reused_when_continuing(app_data, payload):
    directory = record('retry', status='failed')
    (directory / 'transcript.json').write_text(json.dumps(payload), encoding='utf-8')
    assert main.load_transcript_result(directory) is None


def test_corrupt_local_note_does_not_break_resume_source_discovery(app_data, monkeypatch):
    _, client = app_data
    directory = record('retry', status='failed')
    (directory / 'notes.md').write_bytes(b'\xffinvalid-utf8')
    (directory / 'input.wav').write_bytes(b'original upload placeholder')
    async def queued(*args, **kwargs):
        pass
    monkeypatch.setattr(main, 'run_queued_video_task', queued)
    response = client.post('/api/summarize', json={'resume_task_id': 'retry', 'llm_config': {}})
    assert response.status_code == 200, response.text
    assert response.json()['reused_task_id'] == 'retry'
    assert (directory / 'notes.md').read_bytes() == b'\xffinvalid-utf8'


def test_corrupt_legacy_note_does_not_break_reused_title_discovery(app_data):
    directory = record('retry', status='failed')
    (directory / 'task.json').write_text('{"title":""}', encoding='utf-8')
    (directory / 'notes.md').write_bytes(b'\xffinvalid-utf8')
    result = main.load_reused_video_info(directory, {'segments': [TranscriptSegment(0, 1, 'text')]}, None)
    assert result['title'] == '已恢复任务 retry'
