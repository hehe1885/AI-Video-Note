"""One-time native welcome window; never shown during quiet startup."""
import json
import os
from pathlib import Path
from backend.ui_language import load_language


def welcome_completed(workspace):
    try:
        return json.loads((Path(workspace) / '_welcome.json').read_text(encoding='utf-8')).get('completed') is True
    except (OSError, ValueError, AttributeError):
        return False


def mark_completed(workspace):
    target = Path(workspace) / '_welcome.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'completed': True}), encoding='utf-8')
    os.replace(temporary, target)


def open_interface(url, workspace, browser_open, quiet=False):
    if quiet:
        return
    if welcome_completed(workspace):
        browser_open(url)
        return
    show_welcome(url, workspace, browser_open)


def show_welcome(url, workspace, browser_open):
    import tkinter as tk
    from tkinter import messagebox
    from launcher import autostart_enabled, set_autostart
    chinese = load_language(workspace) != 'en'
    def text(zh, en):
        return zh if chinese else en
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (OSError, AttributeError):
                pass
    root = tk.Tk()
    font_family = 'Microsoft YaHei UI' if chinese else 'Segoe UI'
    scale = root.winfo_fpixels('1i') / 96
    root.tk.call('tk', 'scaling', root.winfo_fpixels('1i') / 72)
    root.title('AI Video Note')
    root.configure(bg='#f4f7fb')
    root.resizable(False, False)
    width, height = round(600 * scale), round(570 * scale)
    root.geometry(f'{width}x{height}+{max(0, (root.winfo_screenwidth()-width)//2)}+{max(0, (root.winfo_screenheight()-height)//2)}')
    panel = tk.Frame(root, bg='#ffffff', padx=30, pady=26)
    panel.pack(fill='both', expand=True, padx=16, pady=16)
    tk.Label(panel, text='AI Video Note', bg='white', fg='#172b45', font=(font_family, 24, 'bold')).pack(anchor='w')
    tk.Label(panel, text=text('把视频和音频，整理成你的笔记', 'Turn videos and audio into useful notes'), bg='white', fg='#2376cb', font=(font_family, 12)).pack(anchor='w', pady=(6, 18))
    description = text(
        '提交视频链接、本地视频或音频，提取字幕与语音，使用 AI 整理笔记，并导出 Word、Markdown 等格式。\n\n软件界面会在浏览器中打开。以后双击软件快捷方式，将直接打开软件界面，不再显示此窗口。\n\n软件常驻任务栏的系统托盘。右键图标可打开界面、查看日志、重启或退出。开机自启动可在网页左侧的软件设置中调整。关闭浏览器页面后，软件仍在后台运行。',
        'Submit a video link, local video or audio. Extract subtitles or speech, create AI notes, and export to Word, Markdown and more.\n\nThe interface opens in your browser. Future launches open it directly without showing this welcome window.\n\nThe app stays in the system tray. Right-click its icon to open the interface, view logs, restart or quit. Configure startup under Software settings in the left sidebar. Closing the browser keeps the app running.')
    tk.Label(panel, text=description, wraplength=round(520 * scale), justify='left', bg='white', fg='#34465e', font=(font_family, 12)).pack(anchor='w')
    enabled = tk.BooleanVar(value=autostart_enabled())
    def toggle():
        try:
            set_autostart(enabled.get())
        except OSError as error:
            enabled.set(not enabled.get())
            messagebox.showerror('AI Video Note', str(error), parent=root)
    if os.name == 'nt':
        tk.Checkbutton(panel, text=text('开机静默启动（只在后台运行，不打开界面）', 'Start silently with Windows (keep the interface closed)'), variable=enabled, command=toggle, bg='white', activebackground='white', fg='#172b45', font=(font_family, 12)).pack(anchor='w', pady=(18, 14))
    def open_now():
        try:
            if browser_open(url) is False:
                messagebox.showerror('AI Video Note', text('无法打开浏览器，请稍后从托盘重试。', 'Unable to open your browser. Please try again from the tray.'), parent=root)
                return
            mark_completed(workspace)
            root.destroy()
        except OSError as error:
            messagebox.showerror('AI Video Note', str(error), parent=root)
    tk.Button(panel, text=text('打开软件界面', 'Open app interface'), command=open_now, bg='#2479dc', fg='white', activebackground='#1767bf', activeforeground='white', relief='flat', padx=22, pady=10, font=(font_family, 13, 'bold')).pack(anchor='e')
    root.mainloop()
