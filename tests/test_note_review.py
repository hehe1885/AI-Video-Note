import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import pytest
from fastapi import HTTPException
from backend import main
from backend.note_review import find_media, replay_link

def test_replay_links_handle_platforms_and_page_offsets():
    pages = [{'page':2,'duration':60},{'page':4,'duration':120}]
    link = replay_link('https://www.bilibili.com/video/BV123?p=2&start_progress=10#old', 75, pages)
    assert link['precise']
    assert parse_qs(urlsplit(link['url']).query) == {'p':['4'],'t':['15']}
    assert parse_qs(urlsplit(replay_link('https://youtu.be/abc?t=100#t=999',12)['url']).query)['t'] == ['12']
    assert replay_link('https://www.bilibili.com/video/BV123',75,[{'page':1},{'page':2}])['precise'] is False
    assert replay_link('https://douyin.com/video/1',30)['precise'] is False
    assert replay_link('javascript:alert(1)',3)['url'] is None
    assert replay_link('https://youtube.com.evil.test/video',3)['precise'] is False

def setup_task(tmp_path, monkeypatch, task_id='one', **manifest):
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path.resolve())
    monkeypatch.setattr(main,'tasks',{})
    directory = tmp_path/task_id
    directory.mkdir()
    (directory/'task.json').write_text(json.dumps(manifest),encoding='utf-8')
    (directory/'transcript.json').write_text(json.dumps({'segments':[{'start':75,'end':80,'text':'原字幕 <script>text</script>'}]}),encoding='utf-8')
    return directory

@pytest.mark.asyncio
async def test_review_and_media_are_read_only_and_support_restored_tasks(tmp_path, monkeypatch):
    directory = setup_task(tmp_path,monkeypatch,source_url='https://youtu.be/abc')
    media = directory/'input.mp4'; media.write_bytes(b'video')
    result = await main.get_note_review('one')
    assert result['media_url'] == '/api/task/one/media'
    assert result['segments'][0]['replay_url'].endswith('t=75')
    response = await main.get_task_media('one')
    assert response.path == media
    assert not main.tasks
    assert (directory/'input.mp4').read_bytes() == b'video'

@pytest.mark.asyncio
async def test_media_reuse_and_unavailable_fallback(tmp_path,monkeypatch):
    directory=setup_task(tmp_path,monkeypatch,reused_task_id='older')
    older=tmp_path/'older'; older.mkdir()
    (older/'input.m4a').write_bytes(b'audio')
    result=await main.get_note_review('one')
    assert result['media_kind']=='audio'
    assert (await main.get_task_media('one')).path == older/'input.m4a'
    (older/'input.m4a').unlink()
    assert (await main.get_note_review('one'))['media_url'] is None
    with pytest.raises(HTTPException) as error: await main.get_task_media('one')
    assert error.value.status_code==404
    with pytest.raises(HTTPException): await main.get_task_media('../escape')

def test_media_does_not_serve_unrelated_recorded_paths(tmp_path,monkeypatch):
    directory=setup_task(tmp_path,monkeypatch)
    arbitrary=tmp_path/'private.mp4'; arbitrary.write_bytes(b'private')
    main.tasks['one']={'video_path':str(arbitrary)}
    assert find_media('one',tmp_path,main.tasks,main.read_task_manifest) is None
    (directory/'input.txt').write_text('private')
    assert find_media('one',tmp_path,main.tasks,main.read_task_manifest) is None

@pytest.mark.asyncio
async def test_multi_page_preview_does_not_play_wrong_page(tmp_path,monkeypatch):
    directory=setup_task(tmp_path,monkeypatch,source_url='https://bilibili.com/video/BV123',bili_pages=[{'page':2,'duration':60},{'page':4,'duration':120}])
    (directory/'preview.mp4').write_bytes(b'one page')
    result=await main.get_note_review('one')
    assert result['media_url'] is None
    assert 'p=4&t=15' in result['segments'][0]['replay_url']


@pytest.mark.asyncio
async def test_media_range_seek_response(tmp_path,monkeypatch):
    import httpx
    directory=setup_task(tmp_path,monkeypatch)
    (directory/'input.mp4').write_bytes(b'0123456789')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://localhost') as client:
        response=await client.get('/api/task/one/media',headers={'Range':'bytes=3-5'})
    assert response.status_code==206
    assert response.content==b'345'
    assert response.headers['content-range']=='bytes 3-5/10'


@pytest.mark.asyncio
async def test_upload_retains_video_for_replay_without_screenshots(tmp_path,monkeypatch):
    import httpx
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path.resolve())
    monkeypatch.setattr(main,'tasks',{})
    monkeypatch.setattr(main,'LARGE_UPLOAD_EXTRACT_BYTES',1)
    def do_not_extract(*args,**kwargs):
        raise AssertionError('Replay upload must retain its original media')
    monkeypatch.setattr(main.video_processor,'extract_audio_track',do_not_extract)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://localhost') as client:
        response=await client.post('/api/upload',files={'file':('lesson.mp4',b'video','video/mp4')},data={'include_screenshots':'0','retain_media':'true'})
    assert response.status_code==200
    uploaded=Path(response.json()['file_path'])
    assert uploaded.suffix=='.mp4'
    assert uploaded.read_bytes()==b'video'
