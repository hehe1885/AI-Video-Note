"""Exit after the last browser page disconnects; allow brief refresh/reconnects."""
import asyncio
import inspect

class BrowserSessions:
    def __init__(self, grace_seconds=4.0):
        self.grace_seconds=grace_seconds
        self.clients=set()
        self.pending=None
        self.on_empty=None

    def connect(self, client):
        if self.pending:
            self.pending.cancel()
            self.pending=None
        self.clients.add(client)

    def disconnect(self, client):
        self.clients.discard(client)
        if not self.clients and self.on_empty and self.pending is None:
            self.pending=asyncio.create_task(self._exit_later())

    async def _exit_later(self):
        try:
            await asyncio.sleep(self.grace_seconds)
            if not self.clients and self.on_empty:
                result=self.on_empty()
                if inspect.isawaitable(result):
                    await result
        finally:
            if self.pending is asyncio.current_task():
                self.pending=None
