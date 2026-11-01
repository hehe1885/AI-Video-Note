"""Observe MCP handshakes; never retain tool arguments or credentials."""
import json
import re
import time
import uuid
from urllib.parse import parse_qs


class McpActivity:
    def __init__(self):
        self.streams = set()
        self.requests = set()
        self.last_activity = None
        self.identities = {}
        self.stream_sessions = {}

    def remember(self, session, info):
        if not isinstance(info, dict) or not isinstance(info.get("name"), str):
            return
        name = ''.join(c for c in info['name'] if c.isprintable()).strip()[:100]
        version = ''.join(c for c in str(info.get('version', '')) if c.isprintable())[:60]
        if name:
            self.identities[session] = {"name": name, "version": version, "last_activity": time.time()}
            while len(self.identities) > 128:
                self.identities.pop(next(iter(self.identities)))

    def snapshot(self):
        now = time.time()
        active_sessions = {self.stream_sessions.get(key, key) for key in self.streams}
        clients = {}
        for session, info in list(self.identities.items()):
            connected = session in active_sessions
            key = (info['name'], info['version'])
            previous = clients.get(key)
            clients[key] = {**info, "connected": connected or bool(previous and previous['connected']),
                            "last_activity": max(info['last_activity'], previous['last_activity'] if previous else 0),
                            "recent": now - max(info['last_activity'], previous['last_activity'] if previous else 0) < 60}
        unknown = any(session not in self.identities for session in active_sessions)
        return {"connected": len(self.streams), "active_requests": len(self.requests),
                "last_activity": self.last_activity,
                "recent": self.last_activity is not None and now - self.last_activity < 60,
                "clients": list(clients.values()), "unknown_connected": unknown}


activity = McpActivity()


class McpActivityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").rstrip("/")
        if scope["type"] != "http" or path not in ("/mcp/sse", "/mcp/http", "/mcp/messages"):
            return await self.app(scope, receive, send)
        key = uuid.uuid4().hex
        headers = dict(scope.get('headers', []))
        session = headers.get(b'mcp-session-id', b'').decode('utf-8', 'replace')[:200]
        if not session:
            session = parse_qs(scope.get('query_string', b'').decode('utf-8', 'replace')).get('session_id', [key])[0][:200]
        successful = False
        client_info = None
        body = bytearray()
        body_oversized = False
        endpoint_buffer = bytearray()

        async def observed_receive():
            nonlocal client_info, body_oversized
            message = await receive()
            if message['type'] == 'http.request' and not body_oversized:
                chunk = message.get('body', b'')
                if len(body) + len(chunk) <= 65536:
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        try:
                            payload = json.loads(body)
                            if isinstance(payload, dict) and payload.get('method') == 'initialize':
                                params = payload.get('params', {})
                                if isinstance(params, dict):
                                    client_info = params.get('clientInfo')
                        except (ValueError, TypeError):
                            pass
                        body.clear()
                else:
                    body_oversized = True
                    body.clear()
            return message

        async def observed_send(message):
            nonlocal successful, session
            if message["type"] == "http.response.start":
                successful = 200 <= message["status"] < 300
                response_headers = dict(message.get("headers", []))
                session = response_headers.get(b'mcp-session-id', session.encode()).decode('utf-8', 'replace')[:200]
                streaming = successful and b"text/event-stream" in response_headers.get(b"content-type", b"")
                if successful:
                    activity.last_activity = time.time()
                    activity.requests.add(key)
                    if streaming:
                        activity.streams.add(key)
                        activity.stream_sessions[key] = session
                    activity.remember(session, client_info)
                    if session in activity.identities:
                        activity.identities[session]['last_activity'] = time.time()
            elif message['type'] == 'http.response.body' and path == '/mcp/sse' and len(endpoint_buffer) < 4096:
                endpoint_buffer.extend(message.get('body', b'')[:4096-len(endpoint_buffer)])
                match = re.search(rb'session_id=([a-fA-F0-9]{32})', endpoint_buffer)
                if match:
                    activity.stream_sessions[key] = match.group(1).decode()
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            activity.streams.discard(key)
            activity.stream_sessions.pop(key, None)
            activity.requests.discard(key)
            if successful:
                activity.last_activity = time.time()
                if session in activity.identities:
                    activity.identities[session]['last_activity'] = time.time()
