from types import SimpleNamespace
from backend import ui_language


def test_windows_simplified_and_traditional_default_to_chinese(monkeypatch):
    monkeypatch.setattr(ui_language.sys, 'platform', 'win32')
    for language_id in [0x0804, 0x0404, 0x0c04, 0x1004, 0x1404]:
        monkeypatch.setattr(ui_language.ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=lambda: language_id)), raising=False)
        assert ui_language.system_language() == 'zh-CN'
    for language_id in [0x0409, 0x0411, 0x0407]:
        monkeypatch.setattr(ui_language.ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=lambda: language_id)), raising=False)
        assert ui_language.system_language() == 'en'


def test_saved_language_controls_tray_and_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_language, 'system_language', lambda: 'en')
    assert ui_language.load_language(tmp_path) == 'en'
    assert ui_language.tray_label(tmp_path, 'quit') == 'Quit'
    ui_language.save_language(tmp_path, 'zh-CN')
    assert ui_language.tray_label(tmp_path, 'quit') == '退出'
    ui_language.save_language(tmp_path, 'en')
    assert ui_language.tray_label(tmp_path, 'restart') == 'Restart'
    assert not list(tmp_path.glob('*.tmp'))
