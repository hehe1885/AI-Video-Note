import asyncio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend.browser_lifecycle import BrowserSessions
from backend import main

@pytest.mark.asyncio
async def test_last_page_closes_after_grace():
    sessions=BrowserSessions(.01); exited=[]
    sessions.on_empty=lambda:exited.append(True)
    sessions.connect('a'); sessions.disconnect('a')
    await asyncio.sleep(.03)
    assert exited == [True]

@pytest.mark.asyncio
async def test_refresh_reconnect_cancels_exit():
    sessions=BrowserSessions(.02); exited=[]
    sessions.on_empty=lambda:exited.append(True)
    sessions.connect('old'); sessions.disconnect('old')
    sessions.connect('new')
    await asyncio.sleep(.04)
    assert not exited
    assert sessions.clients == {'new'}

@pytest.mark.asyncio
async def test_multiple_tabs_require_last_to_close():
    sessions=BrowserSessions(.01); exited=[]
    sessions.on_empty=lambda:exited.append(True)
    sessions.connect('a');sessions.connect('b');sessions.disconnect('a')
    await asyncio.sleep(.03)
    assert not exited
    sessions.disconnect('b')
    await asyncio.sleep(.03)
    assert exited == [True]


def test_websocket_accepts_only_same_origin(monkeypatch):
    monkeypatch.setattr(main,'browser_sessions',BrowserSessions())
    with TestClient(main.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/browser-session',headers={'origin':'https://example.com'}):
                pass
        with client.websocket_connect('/api/browser-session',headers={'origin':'http://testserver'}) as socket:
            assert len(main.browser_sessions.clients)==1
            socket.send_text('alive')
    assert not main.browser_sessions.clients
