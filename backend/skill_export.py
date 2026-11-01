"""Export the bundled agent skill without touching any agent configuration."""
from pathlib import Path
import json
import sys
import uuid
import zipfile

INSTALLATION = """AI Video Note Skill 安装 / Installation

1. 解压 ZIP，保留 AI-Video-Note 文件夹（包含 SKILL.md 和 scripts/video_note.py）。
   Extract the ZIP and keep the whole AI-Video-Note folder.
2. 在支持 Skill 的 Agent 中导入该文件夹；如需手动安装，将整个文件夹复制到该客户端指定的 skills 目录。
   Import the folder in a skill-capable agent, or copy it to that client's skills directory.
   不同客户端的技能目录不同，请按客户端文档选择；不要只复制 SKILL.md。
   Follow your client's documentation; do not copy SKILL.md alone.
3. 刷新技能列表或重新启动 Agent，确认能找到 AI-Video-Note。
   Refresh skills or restart the agent and check that AI-Video-Note is discovered.
4. 向同一电脑上的 Agent 发送视频链接，例如：
   “使用 AI-Video-Note，把这个视频整理成笔记：<视频链接>”。
   Ask your agent on the same computer to use the skill for a video.

Skill 会复用已有服务，或按 startup.json 自动启动软件后台服务，等待就绪后提交任务，不弹出浏览器。
The skill reuses the service or launches it automatically using startup.json, without opening a browser.
保留 startup.json。软件移动或换电脑后重新导出，或设置 AI_VIDEO_NOTE_HOME 为软件目录或 exe 的绝对路径。
Keep startup.json. Re-export after moving the app, or set AI_VIDEO_NOTE_HOME to its absolute directory/exe path.
后台服务会在任务结束后保留，可从托盘退出；无需提前全天运行软件。--no-auto-start 可关闭自动启动。
The service remains running after tasks; exit from the tray when finished. Use --no-auto-start to disable startup.
Agent 需要 Python、运行配套脚本和访问本机 API 的权限。不需要额外配置 MCP。
The agent needs Python, permission to run the script and access the local API. MCP is optional.
笔记和仅字幕都需AI校对。可使用配置的API，或 --transcript-only --review-agent 由Agent校对；有建议时等待用户确认。
Both outputs require AI proofreading. Use a configured API or --transcript-only --review-agent; wait for user confirmation when changes are suggested.
"""


def export_skill(base: Path, directory: str) -> Path:
    folder = Path(directory)
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError("请选择有效的保存文件夹")
    source = base / 'skills' / 'AI-Video-Note'
    files = [Path('SKILL.md'), Path('scripts/video_note.py')]
    if not all((source / item).is_file() for item in files):
        raise FileNotFoundError('软件缺少 Skill 文件，请重新安装完整版本')
    # A unique filename preserves earlier exports; only ship explicit resources.
    target = folder / f'AI-Video-Note-Skill-{uuid.uuid4().hex[:12]}.zip'
    try:
        with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                archive.write(source / item, 'AI-Video-Note/' + item.as_posix())
            startup = {'application': str(Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else base.resolve())}
            if not getattr(sys, 'frozen', False):
                startup['python'] = str(Path(sys.executable).resolve())
            archive.writestr('AI-Video-Note/startup.json', json.dumps(startup, ensure_ascii=False, indent=2))
            archive.writestr('安装说明-Installation.txt', INSTALLATION)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target
