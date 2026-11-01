import json
from pathlib import Path

import launcher


def test_version_tuple_accepts_release_tags() -> None:
    assert launcher.version_tuple("v1.1.4") == (1, 1, 4)
    assert launcher.version_tuple("1.2.0-rc1") == (1, 2, 0)
    assert launcher.version_tuple("invalid") == (0,)


def test_server_alive_requires_matching_version(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok","service":"AI Video Note","mode":"portable","version":"1.1.7"}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    assert launcher.server_alive("http://127.0.0.1:8000", mode="portable", version="1.1.7")
    assert not launcher.server_alive("http://127.0.0.1:8000", mode="portable", version="1.1.8")


def test_configure_runtime_dirs_creates_custom_workspace(monkeypatch, tmp_path) -> None:
    """环境变量指定的 workdir 不存在时也要自动创建（打包版写日志前依赖此目录）。"""
    custom = tmp_path / "custom-ws"
    assert not custom.exists()
    monkeypatch.setenv("AI_VIDEO_NOTE_WORKSPACE", str(custom))
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)
    launcher.configure_runtime_dirs()
    assert custom.is_dir()


def test_latest_release_info_reads_tag_and_url(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "update_language", lambda: "en")
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"tag_name": "v1.2.0", "html_url": "https://github.com/example/release"}
            ).encode("utf-8")

    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, timeout))
        return FakeResponse()

    monkeypatch.setattr(launcher.urllib.request, "urlopen", fake_urlopen)
    assert launcher.latest_release_info() == {
        "tag_name": "v1.2.0",
        "html_url": "https://github.com/example/release",
        "source": "github",
        "source_name": "GitHub",
    }
    assert set(captured) == {(launcher.GITHUB_LATEST_RELEASE_API, 5),
                             (launcher.update_check.GITEE_LATEST_RELEASE_API, 5)}


def test_check_for_updates_opens_new_release_after_confirmation(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        launcher,
        "latest_release_info",
        lambda: {"tag_name": "v9.9.9", "html_url": "https://github.com/example/release"},
    )
    monkeypatch.setattr(launcher, "show_update_message", lambda *args, **kwargs: 6)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    launcher.check_for_updates()

    assert opened == ["https://github.com/example/release"]


def test_old_backend_is_not_reused_after_source_updates(monkeypatch):
    import io,json
    class Reply(io.BytesIO):
        status=200
    payload={"status":"ok","service":"AI Video Note","mode":"dev","version":"1.0.0"}
    monkeypatch.setattr(launcher.urllib.request,"urlopen",lambda *args,**kwargs:Reply(json.dumps(payload).encode()))
    assert not launcher.server_alive('http://127.0.0.1:8001',mode='dev',version='1.0.0',revision='new-source')
    payload['code_revision']='old-source'
    assert not launcher.server_alive('http://127.0.0.1:8001',mode='dev',version='1.0.0',revision='new-source')
    payload['code_revision']='new-source'
    assert launcher.server_alive('http://127.0.0.1:8001',mode='dev',version='1.0.0',revision='new-source')


def test_autostart_toggle_uses_current_user_and_quiet_start(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    values = {}
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def read(key, name):
        if name not in values: raise FileNotFoundError(name)
        return values[name], 1
    registry = SimpleNamespace(HKEY_CURRENT_USER=object(), REG_SZ=1,
        OpenKey=lambda root, path: Key(), CreateKey=lambda root, path: Key(),
        QueryValueEx=read, SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name,value),
        DeleteValue=lambda key, name: values.pop(name))
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    monkeypatch.setattr(launcher.sys, 'platform', 'win32')
    monkeypatch.setattr(launcher, 'base_dir', lambda: tmp_path / 'project with spaces')
    monkeypatch.setattr(launcher, 'is_frozen', lambda: False)
    assert not launcher.autostart_enabled()
    launcher.set_autostart(True)
    assert launcher.autostart_enabled()
    assert '--no-browser' in values[launcher.autostart_name()]
    values[launcher.autostart_name()] = launcher.subprocess.list2cmdline(launcher.launcher_command())
    assert launcher.autostart_enabled()
    assert '--no-browser' in values[launcher.autostart_name()]
    launcher.set_autostart(False)
    assert not launcher.autostart_enabled()


def test_tray_restart_and_exit_request_managed_shutdown(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from backend.ui_language import save_language
    save_language(tmp_path, 'zh-CN')
    for selected, expected in [('重启', 2), ('退出', 0)]:
        callbacks = {}
        stopped = []
        server = SimpleNamespace(should_exit=False)
        def shutdown():
            stopped.append(True)
            server.should_exit = True
        server.request_shutdown = shutdown
        def item(label, callback, **kwargs):
            label = label(None) if callable(label) else label
            callbacks[label] = callback
            return label
        class Icon:
            def __init__(self,*args): pass
            def run(self): callbacks[selected](self,None)
            def stop(self): pass
            def notify(self,*args): pass
        monkeypatch.setitem(sys.modules, 'pystray', SimpleNamespace(Menu=lambda *items:items, MenuItem=item, Icon=Icon))
        monkeypatch.setattr(launcher, 'tray_icon_image', lambda: None)
        assert launcher.run_tray('http://localhost:8000',tmp_path,server) == expected
        assert stopped == [True]
        assert '开机静默自启动' not in callbacks
        assert '查看日志' in callbacks and '打开界面' in callbacks


def test_browser_close_keeps_real_managed_server_running(tmp_path):
    import subprocess, sys
    code = r"""
import asyncio, os, time, urllib.request
os.environ['AI_VIDEO_NOTE_WORKSPACE'] = __import__('sys').argv[1]
import launcher
from backend.main import browser_sessions
port = next(p for p in range(18000, 18100) if launcher.port_free(p))
server, thread = launcher.start_server(port)
url = f'http://127.0.0.1:{port}'
assert launcher.wait_healthy(url, timeout=15)
assert browser_sessions.on_empty is None
async def close_page():
    import websockets
    async with websockets.connect(f'ws://127.0.0.1:{port}/api/browser-session', origin=url):
        pass
asyncio.run(close_page())
time.sleep(4.5)
assert launcher.server_alive(url)
server.request_shutdown().result(timeout=15)
thread.join(15)
assert not thread.is_alive()
print('managed server survives browser close and exits cleanly')
"""
    result = subprocess.run([sys.executable,'-c',code,str(tmp_path)],cwd=Path(launcher.__file__).parent,
                            capture_output=True,text=True,timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


def test_update_dialog_runs_outside_tray_thread_and_deduplicates(monkeypatch):
    import threading
    entered = threading.Event()
    dismiss = threading.Event()
    threads = []
    def check():
        threads.append(threading.get_ident())
        entered.set()
        assert dismiss.wait(3)
    monkeypatch.setattr(launcher, 'check_for_updates', check)
    worker = launcher.start_update_check()
    try:
        assert entered.wait(2)
        assert threads == [worker.ident]
        assert worker.ident != threading.get_ident()
        assert launcher.start_update_check() is None
    finally:
        dismiss.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not launcher._update_check_lock.locked()


def test_update_message_identifies_own_repository(monkeypatch):
    messages = []
    monkeypatch.setattr(launcher, 'latest_release_info', lambda: {'tag_name': launcher.VERSION,'html_url':'unused'})
    monkeypatch.setattr(launcher, 'show_update_message', lambda message, **kwargs: messages.append(message))
    launcher.check_for_updates()
    assert 'hehe1885/AI-Video-Note' in messages[0]
    assert '当前版本' in messages[0]
