import asyncio
import json
from backend import mcp_activity as module


def test_named_sse_connections_are_merged_and_disconnect_updates(monkeypatch):
    state = module.McpActivity()
    monkeypatch.setattr(module, 'activity', state)
    async def run():
        release = asyncio.Event()
        ids = ['a' * 32, 'b' * 32]
        async def stream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/event-stream')]})
            await send({'type': 'http.response.body', 'body': ('event: endpoint\ndata: /mcp/messages/?session_id=' + scope['sid'] + '\n\n').encode(), 'more_body': True})
            await release.wait()
        async def sink(message): pass
        middleware = module.McpActivityMiddleware(stream)
        tasks = [asyncio.create_task(middleware({'type':'http', 'path':'/mcp/sse', 'sid':sid}, None, sink)) for sid in ids]
        await asyncio.sleep(0)
        assert state.snapshot()['unknown_connected']
        async def post(scope, receive, send):
            message = await receive()
            assert json.loads(message['body'])['method'] == 'initialize'
            await send({'type':'http.response.start', 'status':202, 'headers':[]})
        for sid in ids:
            async def receive():
                return {'type':'http.request', 'body': json.dumps({'method':'initialize', 'params':{'clientInfo':{'name':'Codex', 'version':'1.2'}}}).encode()}
            await module.McpActivityMiddleware(post)({'type':'http','path':'/mcp/messages/','query_string':('session_id='+sid).encode()}, receive, sink)
        result=state.snapshot()
        assert result['connected']==2 and len(result['clients'])==1
        assert result['clients'][0]['name']=='Codex' and result['clients'][0]['connected']
        assert not result['unknown_connected']
        release.set()
        await asyncio.gather(*tasks)
        assert state.snapshot()['connected']==0
        assert not state.snapshot()['clients'][0]['connected']
    asyncio.run(run())


def test_stateless_http_identity_is_recent_not_connected(monkeypatch):
    state=module.McpActivity()
    monkeypatch.setattr(module, 'activity', state)
    async def run():
        async def server(scope, receive, send):
            await receive()
            await send({'type':'http.response.start', 'status':200,'headers':[]})
        async def receive():
            return {'type':'http.request', 'body':b'{"method":"initialize","params":{"clientInfo":{"name":"Codex","version":"1"}}}'}
        async def send(message): pass
        await module.McpActivityMiddleware(server)({'type':'http','path':'/mcp/http'},receive,send)
    asyncio.run(run())
    assert state.snapshot()['clients'][0]['name']=='Codex'
    assert not state.snapshot()['clients'][0]['connected']
    monkeypatch.setattr(module.time, 'time', lambda: state.last_activity+61)
    assert state.snapshot()['clients'][0]['name']=='Codex'
    assert not state.snapshot()['clients'][0]['recent']
    assert not state.snapshot()['clients'][0]['connected']
