import asyncio
from urllib.parse import quote
from fastapi.testclient import TestClient
from backend import skill_activity as module, main
from test_skill_video_note import video_note


def test_named_skill_activity_tracks_requests_and_expires(monkeypatch):
    state = module.SkillActivity()
    monkeypatch.setattr(module, 'activity', state)
    async def run():
        async def app(scope, receive, send):
            assert state.snapshot()[0]['active_requests'] == 1
            # Metadata tracking never reads or stores this request body.
            raise RuntimeError('simulated request failure')
        headers = [(b'user-agent', b'AI-Video-Note-Skill/1.0'),
                   (b'x-ai-video-note-agent', quote('测试 Agent').encode())]
        try:
            await module.SkillActivityMiddleware(app)({'type':'http','path':'/api/transcribe','headers':headers}, None, None)
        except RuntimeError:
            pass
    asyncio.run(run())
    item = state.snapshot()[0]
    assert item['name'] == '测试 Agent' and item['active_requests'] == 0 and item['recent']
    monkeypatch.setattr(module.time, 'time', lambda: item['last_activity'] + 61)
    assert not state.snapshot()[0]['recent']


def test_real_api_reports_skill_unknown_without_guessing_and_named_header(monkeypatch):
    state = module.SkillActivity()
    monkeypatch.setattr(module, 'activity', state)
    monkeypatch.setattr(main, 'skill_activity', state)
    with TestClient(main.app) as client:
        client.get('/api/health')  # Browser/API access must not be labeled Skill.
        assert state.snapshot() == []
        client.get('/api/health', headers={'User-Agent':'AI-Video-Note-Skill/1.0'})
        monkeypatch.setattr(video_note, 'AGENT_NAME', 'Codex')
        client.get('/api/health', headers=video_note.client_headers())
        items = client.get('/api/mcp/activity').json()['skill_clients']
    assert {item['name'] for item in items} == {None, 'Codex'}
    assert next(item for item in items if item['name'] is None)['identity_source'] == 'unknown'
    assert all(item['active_requests'] == 0 for item in items)
