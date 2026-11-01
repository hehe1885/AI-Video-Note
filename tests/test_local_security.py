"""Request boundaries: browsers, native local clients and streaming responses."""
from fastapi.testclient import TestClient
import pytest
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from backend import main


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:8765', 'http://localhost:8765', 'http://[::1]:8765',
    'http://127.0.0.2:8766', 'http://[::ffff:127.0.0.1]:8765',
])
def test_local_http_clients_without_origin_are_supported(url):
    with TestClient(main.app, base_url=url) as client:
        assert client.get('/api/tasks').status_code == 200


@pytest.mark.parametrize('host', ['untrusted.example:8765', '127.0.0.1.untrusted.example',
    'testserver', '192.168.1.2:8765', 'user@localhost:8765', 'localhost:bad', 'localhost:0'])
def test_foreign_or_malformed_host_is_rejected(host):
    with TestClient(main.app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 1234)) as client:
        assert client.get('/api/tasks', headers={'host': host}).status_code == 403


@pytest.mark.parametrize('origin', ['https://untrusted.example', 'null', 'http://localhost:8765',
    'http://127.0.0.1:9999', 'https://127.0.0.1:8765', 'http://user@127.0.0.1:8765',
    'http://127.0.0.1:8765/path', 'http://127.0.0.1:8765?query=1'])
def test_cross_origin_posts_never_reach_handler(monkeypatch, origin):
    calls = []
    async def cancel():
        calls.append(True)
        return {'ok': True}
    monkeypatch.setattr(main.bili_login_manager, 'cancel', cancel)
    with TestClient(main.app, base_url='http://127.0.0.1:8765') as client:
        response = client.post('/api/bili-login/cancel', headers={'origin': origin})
    assert response.status_code == 403
    assert not calls


@pytest.mark.parametrize('headers', [{}, {'origin': 'http://127.0.0.1:8765'},
    {'origin': 'http://127.0.0.1:8765', 'sec-fetch-site': 'same-origin'}])
def test_same_origin_and_native_posts_reach_handler(monkeypatch, headers):
    calls = []
    async def cancel():
        calls.append(True)
        return {'ok': True}
    monkeypatch.setattr(main.bili_login_manager, 'cancel', cancel)
    with TestClient(main.app, base_url='http://127.0.0.1:8765') as client:
        assert client.post('/api/bili-login/cancel', headers=headers).status_code == 200
    assert calls == [True]


@pytest.mark.parametrize('site', ['cross-site', 'same-site'])
def test_browser_fetch_metadata_rejects_unsafe_request_without_origin(site):
    with TestClient(main.app, base_url='http://localhost:8765') as client:
        assert client.post('/api/bili-login/cancel', headers={'sec-fetch-site': site}).status_code == 403


def test_default_port_origin_is_equivalent(monkeypatch):
    async def cancel():
        return {'ok': True}
    monkeypatch.setattr(main.bili_login_manager, 'cancel', cancel)
    with TestClient(main.app, base_url='http://localhost') as client:
        assert client.post('/api/bili-login/cancel', headers={'origin': 'http://localhost:80'}).status_code == 200


def test_duplicate_origins_are_rejected():
    with TestClient(main.app, base_url='http://localhost:8765') as client:
        assert client.post('/api/bili-login/cancel', headers=[
            ('origin', 'http://localhost:8765'), ('origin', 'http://localhost:8765')]).status_code == 403


def test_websocket_rejects_foreign_host_even_with_matching_origin():
    with TestClient(main.app, base_url='http://untrusted.example:8765') as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('ws://untrusted.example:8765/api/browser-session', headers={'origin': 'http://untrusted.example:8765'}):
                pass


@pytest.mark.parametrize('authority', ['localhost:8765', '127.0.0.1:8765', '[::1]:8765'])
def test_real_local_host_websocket_and_mcp_work(authority, monkeypatch):
    from backend.browser_lifecycle import BrowserSessions
    monkeypatch.setattr(main, 'browser_sessions', BrowserSessions())
    with TestClient(main.app, base_url='http://' + authority) as client:
        with client.websocket_connect('ws://' + authority + '/api/browser-session',
                                      headers={'origin': 'http://' + authority}) as socket:
            assert len(main.browser_sessions.clients) == 1
            socket.send_text('alive')
        response = client.post('/mcp/http', headers={'accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18', 'capabilities': {},
                'clientInfo': {'name': 'isolated-regression', 'version': '1.0'},
            },
        })
        assert response.status_code == 200
        assert 'serverInfo' in response.text and 'protocolVersion' in response.text


def test_security_middleware_preserves_streaming_and_headers():
    async def handler(scope, receive, send):
        async def body():
            yield b'data: one\n\n'
            yield b'data: two\n\n'
        await StreamingResponse(body(), media_type='text/event-stream')(scope, receive, send)
    client = TestClient(main.LocalSecurityMiddleware(handler), base_url='http://localhost:8765')
    response = client.get('/mcp/sse')
    assert response.status_code == 200
    assert response.text == 'data: one\n\ndata: two\n\n'
    assert response.headers['x-content-type-options'] == 'nosniff'
