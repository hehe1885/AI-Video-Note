"""Navigation readiness, forwarded browser startup and useful export errors."""
from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from backend import math_render


def test_ready_waits_for_complete_document_and_formula_element(monkeypatch):
    responses = [RuntimeError('Execution context was destroyed during navigation'),
                 {'result': {'value': False}}, {'result': {'value': True}}]
    expressions = []
    def call(method, params, session):
        assert method == 'Runtime.evaluate' and session == 'page'
        expressions.append(params['expression'])
        result = responses.pop(0)
        if isinstance(result, Exception): raise result
        return result
    monkeypatch.setattr(math_render.time, 'sleep', lambda _: None)
    math_render.wait_for_formula_page(call, 'page')
    assert len(expressions) == 3
    assert all("document.readyState === 'complete'" in value and "getElementById('formula')" in value for value in expressions)


def test_ready_error_includes_browser_diagnostic():
    def call(*args):
        return {'exceptionDetails': {'exception': {'description': 'Missing renderer dependency'}}}
    with pytest.raises(RuntimeError, match='Missing renderer dependency'):
        math_render.wait_for_formula_page(call, 'page', timeout=0)


def test_render_exception_keeps_real_browser_error(tmp_path, monkeypatch):
    @contextmanager
    def session():
        def call(method, params=None, session=None):
            if method == 'Target.createTarget':
                assert params['url'] == 'about:blank'
                return {'targetId': 'target'}
            if method == 'Target.attachToTarget': return {'sessionId': 'page'}
            if method == 'Runtime.evaluate':
                if 'document.readyState' in params['expression']: return {'result': {'value': True}}
                return {'exceptionDetails': {'exception': {'description': 'TypeError: render target is null'}}}
            return {}
        yield call, tmp_path
    monkeypatch.setattr(math_render, 'browser_session', session)
    monkeypatch.setattr(math_render, '_cache', {})
    with pytest.raises(RuntimeError, match='render target is null'):
        math_render.render_formulas([('v=at', False)])
    assert math_render._cache == {}


def test_browser_forwarding_exit_waits_for_port_and_cleans_profile(tmp_path, monkeypatch):
    from websockets.sync import client
    profile = []
    closed = []
    class Process:
        def poll(self): return 0  # Edge's forwarding launcher has exited successfully.
        def wait(self, timeout): return 0
    def launch(args, **kwargs):
        profile.append(Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--user-data-dir='))))
        return Process()
    class Socket:
        def send(self, value): self.request = json.loads(value)
        def recv(self, timeout): return json.dumps({'id': self.request['id'], 'result': {}})
        def close(self): closed.append(True)
    def port_appears(_):
        (profile[0] / 'DevToolsActivePort').write_text('12345\n/devtools/browser/owned-test\n')
    monkeypatch.setattr(math_render, 'browser_path', lambda: 'owned-browser')
    monkeypatch.setattr(math_render.subprocess, 'Popen', launch)
    monkeypatch.setattr(math_render.time, 'sleep', port_appears)
    monkeypatch.setattr(client, 'connect', lambda *args, **kwargs: Socket())
    with math_render.browser_session() as (call, folder):
        assert folder == profile[0] and folder.is_dir()
    assert closed and not profile[0].exists()
