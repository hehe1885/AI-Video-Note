"""Observe Skill request metadata only; names are self-reported."""
import time
from urllib.parse import unquote


class SkillActivity:
    def __init__(self):
        self.clients = {}

    def begin(self, name):
        name = ''.join(c for c in name if c.isprintable()).strip()[:100]
        if name not in self.clients and len(self.clients) >= 64:
            idle = next((key for key, item in self.clients.items() if not item['active']), None)
            if idle is None:
                return None
            self.clients.pop(idle)
        client = self.clients.setdefault(name, {'active': 0, 'last_activity': 0})
        client['active'] += 1
        client['last_activity'] = time.time()
        return name

    def end(self, key):
        if key is not None and key in self.clients:
            self.clients[key]['active'] = max(0, self.clients[key]['active'] - 1)
            self.clients[key]['last_activity'] = time.time()

    def snapshot(self):
        now = time.time()
        return [{'name': name or None, 'identity_source': 'self_reported' if name else 'unknown',
                 'active_requests': item['active'], 'last_activity': item['last_activity'],
                 'recent': now - item['last_activity'] < 60}
                for name, item in self.clients.items()]


activity = SkillActivity()


class SkillActivityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers', []))
        is_skill = headers.get(b'user-agent', b'').startswith(b'AI-Video-Note-Skill/')
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/') or not is_skill:
            return await self.app(scope, receive, send)
        # Never retain request bodies, keys or task arguments.
        name = unquote(headers.get(b'x-ai-video-note-agent', b'').decode('utf-8', 'replace'))
        key = activity.begin(name)
        try:
            return await self.app(scope, receive, send)
        finally:
            activity.end(key)
