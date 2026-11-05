"""AI Video Note 启动器。

开发模式（`python launcher.py`）：
  - Windows 默认驻留系统托盘；--console 可使用控制台模式

打包模式（PyInstaller onefile，--noconsole）：
  - 无控制台窗口，服务启动后驻留系统托盘
  - 托盘菜单：打开界面 / 重启 / 查看日志 / 退出
  - 日志写入工作目录的 _app.log
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from backend.ui_language import load_language, tray_label
from backend import update_check
from backend.runtime_revision import CODE_REVISION
from backend.update_check import GITHUB_LATEST_RELEASE_API, version_tuple

VERSION = "1.0.1"
DEFAULT_PORT = 8765
PORT_SCAN_RANGE = 20
START_TIMEOUT_SECONDS = 60
APP_NAME = f"AI Video Note v{VERSION}"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_runtime_dirs() -> None:
    """Keep user data beside the program; only explicit environment overrides differ."""
    import tempfile
    workspace = Path(os.environ.get("AI_VIDEO_NOTE_WORKSPACE") or base_dir() / "workspace").resolve()
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # Unique probe: never truncate an existing user file during startup.
        with tempfile.TemporaryFile(dir=workspace):
            pass
    except OSError as error:
        raise RuntimeError(f"数据目录无法写入：{workspace}\n请将软件放到可写目录。已有数据未迁移到其他位置。") from error
    os.environ["AI_VIDEO_NOTE_WORKSPACE"] = str(workspace)


RUN_MODE = "portable" if is_frozen() else "dev"


def server_alive(
    url: str,
    mode: str | None = None,
    version: str | None = None,
    revision: str | None = None,
) -> bool:
    """健康检查；可同时隔离运行模式和应用版本。

    新旧便携版不能只按 ``portable`` 复用，否则旧版服务会继续提供旧前端，
    造成页面脚本、HTML 和后端版本混用。
    """
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            if not (payload.get("status") == "ok" and payload.get("service") == "AI Video Note"):
                return False
            if mode is not None and payload.get("mode") != mode:
                return False
            if revision is not None and payload.get("code_revision") != revision:
                return False
            return version is None or payload.get("version") == version
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_available_port() -> int:
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_RANGE):
        url = f"http://127.0.0.1:{port}"
        if server_alive(url, mode=RUN_MODE, version=VERSION, revision=CODE_REVISION):
            return port  # 同模式实例已在运行，直接复用
        if port_free(port):
            return port
    raise RuntimeError(f"端口 {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SCAN_RANGE - 1} 均不可用，请稍后重试")


# --------------------------------------------------------------------------
# 服务启动（两种模式共用）
# --------------------------------------------------------------------------

def start_server(port: int) -> tuple[Any, threading.Thread]:
    """在线程中启动 uvicorn，返回 (server, thread)。"""
    import uvicorn

    # 让挂载的 MCP 端点（/mcp/sse）自调命中实际端口
    os.environ.setdefault("AI_VIDEO_NOTE_BACKEND_URL", f"http://127.0.0.1:{port}")
    os.environ["AI_VIDEO_NOTE_MANAGED_SERVER"] = "1"
    from backend.main import app, browser_sessions, shutdown_application
    browser_sessions.on_empty = None

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    async def serve():
        loop = asyncio.get_running_loop()
        server.request_shutdown = lambda: asyncio.run_coroutine_threadsafe(
            shutdown_application(lambda: setattr(server, "should_exit", True)), loop)
        signal = None
        try:
            if is_frozen():
                from backend.install_lifecycle import listen_for_shutdown
                signal = listen_for_shutdown(base_dir(), server.request_shutdown)
            await server.serve()
        finally:
            server.should_exit = True
            if signal is not None:
                signal.close()
    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    return server, thread


def wait_healthy(
    url: str,
    timeout: float = START_TIMEOUT_SECONDS,
    mode: str | None = None,
    version: str | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_alive(url, mode=mode, version=version):
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# 开发模式（控制台）
# --------------------------------------------------------------------------

def print_banner(url: str, workspace: Path) -> None:
    width = 58
    print("=" * width)
    print("  AI Video Note v" + VERSION + "（开发模式）")
    print(f"  界面地址: {url}")
    print(f"  工作目录: {workspace}")
    print("  关闭网页不会退出软件；按 Ctrl+C 退出。")
    print("=" * width, flush=True)


def run_console(url: str, workspace: Path, port: int) -> int:
    print_banner(url, workspace)
    print("  正在启动服务，请稍候…", flush=True)
    try:
        server, server_thread = start_server(port)
        if not wait_healthy(url, mode=RUN_MODE, version=VERSION):
            print(f"警告: {START_TIMEOUT_SECONDS} 秒内未就绪，请手动打开 {url}", flush=True)
        elif "--no-browser" not in sys.argv and os.environ.get("AI_VIDEO_NOTE_NO_BROWSER") != "1":
            webbrowser.open(url)
        while server_thread.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n已退出。再见！")
        server.request_shutdown()
        server_thread.join(15)
    except Exception as error:
        print(f"\n启动失败：{error}")
        input("按回车键退出…")
        return 1
    return 0


# --------------------------------------------------------------------------
# 打包模式（系统托盘）
# --------------------------------------------------------------------------

def setup_file_logging(workspace: Path) -> Path:
    """noconsole 模式下把 stdout/stderr 重定向到日志文件。"""
    log_path = workspace / "_app.log"
    from backend.log_retention import open_log_stream
    log_file = open_log_stream(workspace)
    sys.stdout = log_file
    sys.stderr = log_file
    return log_path


def show_error(message: str) -> None:
    """启动失败提示：Windows 弹消息框，其他平台写 stderr。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} 启动失败", 0x10)
    else:
        print(message, file=sys.stderr, flush=True)


def open_in_shell(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def update_language() -> str:
    from backend.ui_language import load_language
    workspace = Path(os.environ.get("AI_VIDEO_NOTE_WORKSPACE") or base_dir() / "workspace")
    return load_language(workspace)


def latest_release_info() -> dict[str, str]:
    """Use the same two-host release check as the web UI."""
    language = update_language()
    info = update_check.check_all_sources(VERSION, language=language)
    if not info["ok"]:
        raise RuntimeError("No usable release from GitHub or Gitee" if language == "en" else
                           "GitHub 和 Gitee 均未取得可用发布版本，请稍后重试。")
    return {"tag_name": info["latest_version"], "html_url": info["release_url"],
            "source": info["source"], "source_name": info["source_name"]}


def show_update_message(message: str, title: str = APP_NAME, flags: int = 0x40) -> int:
    """显示更新提示；开发模式/非 Windows 下退回日志，便于测试。"""
    if sys.platform == "win32":
        try:
            return show_update_link_dialog(message, title, flags)
        except Exception as error:
            print(f"Update dialog fallback: {error}", file=sys.stderr, flush=True)
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(None, message, title, flags))
    print(f"{title}: {message}", file=sys.stderr, flush=True)
    return 0


def update_dialog_links(message: str) -> list[tuple[str, str]]:
    return [(name, url) for name, url in (
        ("GitHub", update_check.DEFAULT_RELEASE_URL),
    ) if f"{name}: {url}" in message.splitlines()]


def show_update_link_dialog(message: str, title: str, flags: int) -> int:
    """Centered tray dialog with local brand icons and website buttons."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    result = 0
    try:
        root.withdraw()
        root.title(title)
        root.resizable(True, False)
        english = title.endswith(" Update")
        links = update_dialog_links(message)
        link_lines = {f"{name}: {url}" for name, url in links}
        body = "\n".join(line for line in message.splitlines() if line not in link_lines).strip()
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=body, wraplength=620, justify="left").pack(fill="x", pady=(0, 12))
        sites = ttk.Frame(frame)
        sites.pack(anchor="w", pady=(0, 8))
        icons = []  # Tk images must stay alive until the dialog closes.
        for name, url in links:
            image = None
            try:
                image = tk.PhotoImage(master=root, file=str(
                    Path(__file__).resolve().parent / "frontend" / "icons" / (name.lower() + ".png")
                )).subsample(2, 2)
                icons.append(image)
            except (OSError, tk.TclError):
                pass
            button = ttk.Button(sites, text=name, image=image or "", compound="left",
                                padding=(12, 8), cursor="hand2",
                                command=lambda address=url: webbrowser.open(address))
            button.pack(side="left", padx=(0, 12))
            button.bind("<Return>", lambda _event, address=url: webbrowser.open(address))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))

        def close(value: int = 0) -> None:
            nonlocal result
            result = value
            root.quit()

        ttk.Button(actions, text="Close" if english else "关闭", command=close).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", close)
        root.bind("<Escape>", lambda _event: close())
        root.update_idletasks()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.deiconify()
        root.mainloop()
        return result
    finally:
        root.destroy()


def update_release_links(language: str) -> str:
    return f"GitHub: {update_check.DEFAULT_RELEASE_URL}"


def check_for_updates(_icon: Any = None, _item: Any = None) -> None:
    """Check the UI-language release source without blocking startup."""
    language = update_language()
    english = language == "en"
    source = update_check.source_for_language(language)
    label = update_check.source_name(source)
    title = f"{APP_NAME} Update" if english else f"{APP_NAME} 更新"
    links = update_release_links(language)
    try:
        release = latest_release_info()
        label = release.get("source_name") or label
        origin = (f"Checked GitHub and Gitee. Release source: {label} · {update_check.REPOSITORY}" if english else f"已检查 GitHub 和 Gitee。发布来源：{label} · {update_check.REPOSITORY}") + "\n\n" + links
        if version_tuple(release["tag_name"]) <= version_tuple(VERSION):
            message = f"Current version v{VERSION}; no newer release available." if english else f"当前版本 v{VERSION}，暂无更高发布版本。"
            show_update_message(message + "\n\n" + origin, title=title, flags=0x40)
            return
        message = (f"New version {release['tag_name']} available (current v{VERSION}).\n\n{origin}\nChoose a website below to view the release." if english else
                   f"检测到新版本 {release['tag_name']}（当前 v{VERSION}）。\n\n{origin}\n点击下方网站按钮查看发布版本。")
        result = show_update_message(message, title=title, flags=0x24)
        if result == 6:
            webbrowser.open(update_check.DEFAULT_RELEASE_URL if release.get("source") == "gitee" else release["html_url"])
    except urllib.error.HTTPError as error:
        if error.code == 404:
            message = ("No usable release from GitHub or Gitee. Please try again later." if english else
                       "GitHub 和 Gitee 均未取得可用发布版本，请稍后重试。")
            show_update_message(message + "\n\n" + links, title=title, flags=0x40)
        else:
            message = "Unable to check GitHub and Gitee. Please try again later." if english else "无法从 GitHub 和 Gitee 获取更新，请稍后重试。"
            show_update_message(message + "\n\n" + links, title=title, flags=0x10)
    except Exception as error:
        message = "No usable release from GitHub or Gitee. Please try again later." if english else "GitHub 和 Gitee 均未取得可用发布版本，请稍后重试。"
        show_update_message(message + "\n\n" + links, title=title, flags=0x10)


_update_check_lock = threading.Lock()


def start_update_check(_icon: Any = None, _item: Any = None):
    """Keep networking and modal dialogs out of the Win32 tray message callback."""
    if not _update_check_lock.acquire(blocking=False):
        return None
    def worker():
        try:
            check_for_updates()
        finally:
            _update_check_lock.release()
    thread = threading.Thread(target=worker, name='ai-video-note-update-check', daemon=True)
    try:
        thread.start()
    except Exception:
        _update_check_lock.release()
        raise
    return thread


def tray_icon_image() -> Any:
    from PIL import Image

    if is_frozen():
        resource = Path(getattr(sys, "_MEIPASS", "")) / "sources" / "icon.png"
    else:
        resource = base_dir() / "sources" / "icon.png"
    if resource.is_file():
        return Image.open(resource).resize((64, 64))
    # 兜底：生成一个简单占位图标
    image = Image.new("RGB", (64, 64), (69, 200, 144))
    return image


def run_tray(url: str, workspace: Path, server: Any) -> int:
    import pystray

    log_path = workspace / "_app.log"
    print(f"AI Video Note v{VERSION} 已启动: {url}（托盘图标可退出）", flush=True)

    def on_open(_icon: Any, _item: Any) -> None:
        webbrowser.open(url)

    def on_logs(_icon: Any, _item: Any) -> None:
        if log_path.is_file():
            open_in_shell(log_path)

    action = {"restart": False, "stopping": False}
    def stop(_icon: Any, restart: bool = False) -> None:
        if action["stopping"]:
            return
        action.update(restart=restart, stopping=True)
        server.request_shutdown()
    def on_quit(_icon: Any, _item: Any) -> None:
        stop(_icon)
    def on_restart(_icon: Any, _item: Any) -> None:
        stop(_icon, restart=True)

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: tray_label(workspace, "open"), on_open, default=True),
        pystray.MenuItem(lambda item: tray_label(workspace, "restart"), on_restart),
        pystray.MenuItem(lambda item: tray_label(workspace, "logs"), on_logs),
        pystray.MenuItem(lambda item: tray_label(workspace, "update"), start_update_check),
        pystray.MenuItem(lambda item: tray_label(workspace, "quit"), on_quit),
    )
    icon = pystray.Icon("ai-video-note", tray_icon_image(), f"{APP_NAME} · {url}", menu)
    # 任务结果通知：把 backend 的完成/失败事件接到托盘气泡（Windows 通知）
    try:
        from backend import main as backend_main

        backend_main.register_task_notify(
            lambda title, message: icon.notify(message, f"AI Video Note · {title}")
        )
    except Exception:
        pass  # 通知注册失败不影响服务
    def watch_server():
        previous_language = load_language(workspace)
        while not server.should_exit:
            time.sleep(0.2)
            current_language = load_language(workspace)
            if current_language != previous_language:
                previous_language = current_language
                icon.update_menu()
        icon.stop()
    threading.Thread(target=watch_server, daemon=True).start()
    icon.run()
    return 2 if action["restart"] else 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_name():
    import hashlib
    return 'AI-Video-Note-' + hashlib.sha256(str(base_dir()).casefold().encode()).hexdigest()[:12]


def launcher_command(no_browser=False):
    if is_frozen():
        command = [sys.executable]
    else:
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        command = [str(pythonw if pythonw.is_file() else sys.executable), str(base_dir() / 'launcher.py'), '--tray']
    if no_browser:
        command.append('--no-browser')
    return command


def autostart_enabled(migrate=True):
    if sys.platform != 'win32':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            value, _ = winreg.QueryValueEx(key, autostart_name())
            quiet_command = subprocess.list2cmdline(launcher_command(no_browser=True))
            legacy_command = subprocess.list2cmdline(launcher_command())
            if value == legacy_command:
                # Preserve an existing enabled setting, upgrading it to quiet startup.
                if migrate:
                    set_autostart(True)
                return True
            return value == quiet_command
    except FileNotFoundError:
        return False
    except OSError:
        if not migrate:
            raise
        return False


def set_autostart(enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, autostart_name(), 0, winreg.REG_SZ,
                             subprocess.list2cmdline(launcher_command(no_browser=True)))
        else:
            try:
                winreg.DeleteValue(key, autostart_name())
            except FileNotFoundError:
                pass


def restart_launcher():
    command = launcher_command()
    env = os.environ.copy()
    env.pop('AI_VIDEO_NOTE_BACKEND_URL', None)
    return subprocess.Popen(command, cwd=base_dir(), env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)


def application_main() -> int:
    if '--gpu-runtime-probe' in sys.argv[1:]:
        from backend.gpu_runtime_install import probe_main
        return probe_main(sys.argv[sys.argv.index('--gpu-runtime-probe')+1])
    if '--gpu-video-worker' in sys.argv[1:]:
        from backend.gpu_video_worker import worker_main
        return worker_main(sys.argv[sys.argv.index('--gpu-video-worker')+1])
    if '--video-codec-probe' in sys.argv[1:]:
        from backend.video_codecs import child_main
        return child_main(sys.argv[sys.argv.index('--video-codec-probe')+1])
    if "--gpu-probe" in sys.argv[1:]:
        from backend.gpu_devices import child_main
        return child_main()
    if "--asr-child" in sys.argv[1:]:
        # 转写工人：只干活，不起服务、不进托盘、不重定向 stdout（主程序在用管道读它）
        configure_runtime_dirs()
        from backend.asr_process import child_main

        return child_main()
    configure_runtime_dirs()
    workspace = Path(os.environ["AI_VIDEO_NOTE_WORKSPACE"])
    no_browser = os.environ.get("AI_VIDEO_NOTE_NO_BROWSER") == "1" or "--no-browser" in sys.argv

    try:
        port = find_available_port()
    except RuntimeError as error:
        show_error(str(error))
        return 1

    url = f"http://127.0.0.1:{port}"

    if server_alive(url, mode=RUN_MODE, version=VERSION, revision=CODE_REVISION):
        # 同模式服务已在运行，直接打开界面
        if not no_browser:
            webbrowser.open(url)
        return 0

    if not is_frozen() and (sys.platform != "win32" or "--console" in sys.argv):
        return run_console(url, workspace, port)

    # ---- 打包模式 ----
    log_path = setup_file_logging(workspace)
    try:
        server, server_thread = start_server(port)
        if not wait_healthy(url, mode=RUN_MODE, version=VERSION):
            server.should_exit = True
            show_error(f"服务启动超时，请查看日志：{log_path}")
            return 1
        from backend.first_run import open_interface
        if not no_browser:
            # Keep the tray responsive while the first-run window is open.
            from backend.first_run import welcome_completed
            if welcome_completed(workspace):
                open_interface(url, workspace, webbrowser.open)
            else:
                threading.Thread(target=open_interface, args=(url, workspace, webbrowser.open),
                                 name='ai-video-note-welcome', daemon=True).start()
        result = run_tray(url, workspace, server)
        server_thread.join(20)
        if server_thread.is_alive():
            show_error("后台尚未完全停止，请稍后重试。")
            return 1
        return result
    except Exception as error:
        show_error(f"启动失败：{error}\n日志：{log_path}")
        return 1


def main():
    if any(flag in sys.argv for flag in ('--gpu-probe', '--asr-child', '--video-codec-probe', '--gpu-video-worker', '--gpu-runtime-probe')):
        return application_main()
    # Direct python.exe launches also detach into the tray on Windows.
    if sys.platform == 'win32' and not is_frozen() and '--console' not in sys.argv and Path(sys.executable).name.lower() != 'pythonw.exe':
        command = launcher_command(no_browser='--no-browser' in sys.argv)
        subprocess.Popen(command, cwd=base_dir(), creationflags=subprocess.CREATE_NO_WINDOW)
        return 0
    from filelock import FileLock, Timeout
    try:
        configure_runtime_dirs()
    except RuntimeError as error:
        show_error(str(error))
        return 1
    workspace = Path(os.environ['AI_VIDEO_NOTE_WORKSPACE'])
    try:
        with FileLock(str(workspace / '_launcher.lock'), timeout=2):
            result = application_main()
    except Timeout:
        for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_RANGE):
            url = f'http://127.0.0.1:{port}'
            if server_alive(url, mode=RUN_MODE, version=VERSION):
                if '--no-browser' not in sys.argv and os.environ.get('AI_VIDEO_NOTE_NO_BROWSER') != '1':
                    webbrowser.open(url)
                return 0
        show_error('软件已经启动或正在启动，请稍候从托盘打开界面。')
        return 1
    if result == 2:
        restart_launcher()
        return 0
    return result


if __name__ == "__main__":
    sys.exit(main())
