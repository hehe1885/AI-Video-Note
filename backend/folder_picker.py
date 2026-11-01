"""Native folder selection on the computer running the local application."""
from pathlib import Path
import threading

_dialog_lock = threading.Lock()

class PickerBusyError(RuntimeError):
    pass

def choose_directory(initial_directory="", *, default_directory="", language="zh-CN", title=None):
    # Do not queue additional dialogs while the user is making a selection.
    if not _dialog_lock.acquire(blocking=False):
        raise PickerBusyError("文件夹选择窗口已打开")
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        initial = Path(default_directory) if default_directory else Path.home()
        if initial_directory:
            requested = Path(initial_directory).expanduser()
            if requested.is_absolute() and requested.is_dir():
                initial = requested
        if not initial.is_dir():
            initial = Path.home()
        # Create and destroy Tcl on the same worker thread; never block the API loop.
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        directory = filedialog.askdirectory(
            parent=root,
            title=title or ("Select note save folder" if language == "en" else "选择笔记保存文件夹"),
            initialdir=str(initial), mustexist=True,
        )
        if not directory:
            return ""
        selected = Path(directory)
        if not selected.is_absolute() or not selected.is_dir():
            raise ValueError("请选择有效的文件夹")
        return str(selected.resolve())
    finally:
        try:
            if root is not None:
                root.destroy()
        finally:
            _dialog_lock.release()
