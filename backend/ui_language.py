"""Language preference shared by the web UI and native launcher."""
import ctypes
import locale
import sys
import json
import os
import tempfile
from pathlib import Path


def system_language() -> str:
    try:
        if sys.platform == 'win32':
            language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return 'zh-CN' if language_id & 0x3ff == 0x04 else 'en'
        language = locale.getlocale()[0] or ''
        return 'zh-CN' if language.lower().startswith('zh') else 'en'
    except (OSError, AttributeError, ValueError):
        return 'en'


def load_language(workspace: Path) -> str:
    try:
        value = json.loads((workspace / '_ui_language.json').read_text(encoding='utf-8'))
        return 'en' if value.get('language') == 'en' else 'zh-CN'
    except (OSError, ValueError, AttributeError):
        return system_language()


def save_language(workspace: Path, language: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='_ui_language-', suffix='.tmp', dir=workspace)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            json.dump({'language': language}, file)
        os.replace(name, workspace / '_ui_language.json')
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


TRAY_LABELS = {
    'open': ('打开界面', 'Open interface'),
    'restart': ('重启', 'Restart'),
    'autostart': ('开机静默自启动', 'Start silently with Windows'),
    'logs': ('查看日志', 'View logs'),
    'update': ('检查更新', 'Check for updates'),
    'quit': ('退出', 'Quit'),
}


def tray_label(workspace: Path, key: str) -> str:
    return TRAY_LABELS[key][1 if load_language(workspace) == 'en' else 0]
