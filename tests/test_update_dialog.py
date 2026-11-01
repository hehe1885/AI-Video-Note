from types import SimpleNamespace
import sys
import pytest
import launcher
from backend import update_check


def test_native_update_message_uses_windows_system_dialog(monkeypatch):
    import ctypes
    calls = []
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "show_update_link_dialog", lambda *args: (_ for _ in ()).throw(RuntimeError("Tk unavailable")))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(
        MessageBoxW=lambda *args: calls.append(args) or 6)), raising=False)
    assert launcher.show_update_message("update links", "AI Video Note", 0x24) == 6
    assert calls == [(None, "update links", "AI Video Note", 0x24)]


@pytest.mark.parametrize("language,count", [("zh-CN", 1), ("en", 1)])
def test_centered_logo_buttons_open_matching_sites(monkeypatch, language, count):
    buttons, opened, geometry, images = [], [], [], []
    class Image:
        def __init__(self, **kwargs):
            from pathlib import Path
            assert Path(kwargs['file']).is_file()
            images.append(kwargs['file'])
        def subsample(self, *args): return self
    class Widget:
        def __init__(self, *args, **kwargs):
            self.events, self.options = {}, kwargs
            if "command" in kwargs: buttons.append(self)
        def bind(self, event, callback): self.events[event] = callback
        def __getattr__(self, name): return lambda *args, **kwargs: None
    class Root(Widget):
        def winfo_reqwidth(self): return 640
        def winfo_reqheight(self): return 300
        def winfo_screenwidth(self): return 1920
        def winfo_screenheight(self): return 1080
        def geometry(self, value): geometry.append(value)
        def mainloop(self):
            sites = buttons[:-1]
            assert [button.options['text'] for button in sites] == (['GitHub', 'Gitee'] if count == 2 else ['GitHub'])
            for button in sites:
                assert button.options['compound'] == 'left'
                assert isinstance(button.options['image'], Image)
                button.options['command']()
                button.events['<Return>'](None)
            buttons[-1].options['command']()
    ttk = SimpleNamespace(Frame=Widget, Label=Widget, Button=Widget)
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=Root, ttk=ttk, PhotoImage=Image, TclError=RuntimeError))
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    title = "AI Video Note Update" if language == "en" else "AI Video Note 更新"
    assert launcher.show_update_link_dialog(launcher.update_release_links(language), title, 0x24) == 0
    expected = [update_check.DEFAULT_RELEASE_URL]
    if count == 2: expected.append(update_check.GITEE_RELEASE_URL)
    assert opened == [url for url in expected for _ in range(2)]
    assert len(images) == count
    assert geometry == ["640x300+640+390"]


@pytest.mark.parametrize("versions,expected", [
    ({"github": "v2.0.0", "gitee": "v3.0.0"}, "gitee"),
    ({"github": "v3.0.0", "gitee": "v2.0.0"}, "github"),
    ({"github": "v2.0.0", "gitee": None}, "github"),
    ({"github": None, "gitee": "v2.0.0"}, "gitee"),
    ({"github": None, "gitee": None}, None),
])
def test_both_hosts_checked_and_newest_usable_selected(monkeypatch, versions, expected):
    calls = []
    def check(current, timeout, source):
        calls.append(source)
        return {"ok": versions[source] is not None, "latest_version": versions[source], "source": source}
    monkeypatch.setattr(update_check, "check_summary", check)
    result = update_check.check_all_sources("1.0.0")
    assert set(calls) == {"github", "gitee"}
    assert result["ok"] is (expected is not None)
    if expected: assert result["source"] == expected


@pytest.mark.parametrize("language,expected", [("zh-CN", "gitee"), ("en", "github")])
def test_equal_versions_follow_language_preference(monkeypatch, language, expected):
    monkeypatch.setattr(update_check, "check_summary", lambda current, timeout, source:
                        {"ok": True, "latest_version": "v2.0.0", "source": source})
    assert update_check.check_all_sources("1.0.0", language=language)["source"] == expected
