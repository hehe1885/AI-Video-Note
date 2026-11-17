#!/usr/bin/env python3
"""AI Video Note Agent Skill 脚本：支持一键成稿、字幕校对、网页端任务队列接管、AI配图/视觉核对/区域裁剪及多格式导出。

自动探测本机 AI Video Note 服务端口（8765-8784），未运行时按需启动，仅依赖 Python 标准库。

用法:
    # 1. 提交视频/本地文件（Agent 自有模型路线，无需软件 API Key，支持配图与多格式导出）
    python video_note.py "<视频链接或本地文件路径>" --agent-note [--format docx] [--screenshot-density medium] [--vision-screenshots] [--auto-crop-screenshots]

    # 2. 提交视频/本地文件（仅转录并由 Agent 校对字幕）
    python video_note.py "<视频链接或本地文件路径>" --transcript-only --review-agent

    # 3. 提交视频/本地文件（软件内置模型 API 路线）
    python video_note.py "<视频链接或本地文件路径>" --style detailed

    # 4. 网页端 Agent 队列接管、字幕校对、AI 截图/看图核对/裁剪与结果回传导出
    python video_note.py list-jobs
    python video_note.py claim --task-id <ID> [--agent-name "Antigravity IDE"]
    python video_note.py get-material --task-id <ID> [--all-pages] [--out material.json]
    python video_note.py report-issues --task-id <ID> --claim-token <TOKEN> [--issues-file issues.json]
    python video_note.py capture-screenshots --task-id <ID> --claim-token <TOKEN> --times 12.5,48,120 [--download-dir frames_dir]
    python video_note.py fetch-image --task-id <ID> --filename frame1.jpg,frame2.jpg [--download-dir frames_dir]
    python video_note.py crop-prepare --task-id <ID> --claim-token <TOKEN> --filename frame1.jpg --box 0.1,0.1,0.9,0.85 [--download-dir frames_dir]
    python video_note.py crop-confirm --task-id <ID> --claim-token <TOKEN> --filename preview.jpg --approved --context-complete --clearly-better
    python video_note.py renew-lease --task-id <ID> --claim-token <TOKEN> [--release]
    python video_note.py submit-result --task-id <ID> --claim-token <TOKEN> --markdown-file note.md

退出码: 0 成功 | 1 参数/服务错误 | 2 任务失败 | 3 等待超时
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PORT_RANGE = range(8765, 8785)
POLL_INTERVAL_SECONDS = 3
FAST_POLL_INTERVAL_SECONDS = 2
SLOW_POLL_INTERVAL_SECONDS = 5
DEFAULT_WAIT_SECONDS = 3600
LONG_HTTP_TIMEOUT_SECONDS = 600
PROVIDERS = {"deepseek", "openai", "openai_gpt4", "openai_gpt35", "qwen", "glm", "moonshot", "custom"}
STYLES = ("detailed", "faithful", "concise", "obsidian", "learning", "tutorial", "cards", "outline", "meeting")
NOTE_FORMATS = ("markdown", "obsidian", "docx", "html", "json", "txt", "png", "pdf", "enex")
SCREENSHOT_DENSITIES = ("none", "few", "medium", "many")
TIMELINE_MODES = ("none", "heading", "paragraph_start", "paragraph_end", "all")
POSITIONS = ("none", "start", "end")
TEXT_DETAILS = ("brief", "standard", "detailed")
NOTE_LANGUAGES = ("zh-CN", "en")
OBSIDIAN_ATTACHMENT_MODES = ("vault_root", "note_folder", "note_subfolder", "specified_folder")
AGENT_ACTIONS = (
    "list-jobs",
    "claim",
    "get-material",
    "report-issues",
    "capture-screenshots",
    "fetch-image",
    "crop-prepare",
    "crop-confirm",
    "renew-lease",
    "submit-result",
    "wait-task",
    "get-transcript",
)
UPLOAD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
# Key 走命令行会留在 shell 历史与进程列表里，所以默认鼓励用环境变量或 stdin
API_KEY_ENV = "AI_VIDEO_NOTE_LLM_API_KEY"
SK_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")
ECHO_PATTERN = re.compile(
    r"\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|bearer|sessdata|bili_jct)"
    r"\b([\"'’：:=\s]{1,4})([A-Za-z0-9_\-\.]{8,})",
    re.IGNORECASE,
)
_KNOWN_SECRETS: list[str] = []
AGENT_NAME = os.environ.get('AI_VIDEO_NOTE_AGENT_NAME', '')


def client_headers():
    headers = {'User-Agent': 'AI-Video-Note-Skill/1.0'}
    if AGENT_NAME:
        from urllib.parse import quote
        headers['X-AI-Video-Note-Agent'] = quote(AGENT_NAME[:100], safe='')
    return headers


def remember_secret(value: str) -> None:
    if value and len(value) >= 8:
        _KNOWN_SECRETS.append(value)


def redact(text: str) -> str:
    """打印或抛出前先洗一遍：服务端的 4xx 详情、告警文本都可能带 Key 原文。"""
    cleaned = SK_KEY_PATTERN.sub("sk-****", str(text))
    cleaned = ECHO_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}****", cleaned)
    for secret in _KNOWN_SECRETS:
        cleaned = cleaned.replace(secret, secret[:4] + "****")
    return cleaned


def die(message: str, code: int = 1) -> "None":
    print(f"[AI Video Note] {redact(message)}", file=sys.stderr)
    sys.exit(code)


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **client_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise RuntimeError(redact(f"HTTP {exc.code} {url}: {detail or exc.reason}")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 {url}：{exc.reason}") from exc


def running_service() -> str | None:
    """Only accept the application's health signature, not any open port."""
    def probe(port):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok" and payload.get("service") == "AI Video Note":
                return url
        except Exception:
            pass
        return None
    with ThreadPoolExecutor(max_workers=len(PORT_RANGE)) as pool:
        return next((url for url in pool.map(probe, PORT_RANGE) if url), None)


def startup_command() -> tuple[list[str], Path]:
    script = Path(__file__).resolve()
    config_file = script.parent.parent / 'startup.json'
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.is_file() else {}
    if not isinstance(config, dict):
        raise RuntimeError('startup.json 格式错误，请重新导出 Skill')
    location = os.environ.get('AI_VIDEO_NOTE_HOME') or config.get('application')
    if location is not None and not isinstance(location, str):
        raise RuntimeError('startup.json 软件路径格式错误，请重新导出 Skill')
    application = Path(location).expanduser() if location else script.parents[3]
    if not application.is_absolute():
        raise RuntimeError('AI_VIDEO_NOTE_HOME / startup.json 必须使用软件的绝对路径')
    if application.is_file() and application.suffix.lower() == '.exe':
        return [str(application), '--no-browser'], application.parent
    if not (application / 'launcher.py').is_file():
        executable = application / 'AI-Video-Note.exe'
        if executable.is_file():
            return [str(executable), '--no-browser'], application
        raise RuntimeError('找不到 AI Video Note；请从软件重新导出 Skill，或设置 AI_VIDEO_NOTE_HOME 为软件目录或 exe 完整路径')
    python = application / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        python = Path(config.get('python') or sys.executable)
    if not python.is_absolute() or not python.is_file():
        raise RuntimeError('找不到软件的 Python 环境，请重新导出 Skill')
    return [str(python), str(application / 'launcher.py'), '--no-browser'], application


def find_service(*, auto_start=True, startup_timeout=90) -> str:
    base = running_service()
    if base:
        return base
    if not auto_start:
        die('未发现 AI Video Note 服务；已禁用自动启动，请手动启动软件。')
    try:
        command, directory = startup_command()
        log_path = Path(tempfile.gettempdir()) / f'AI-Video-Note-skill-startup-{uuid.uuid4().hex[:12]}.log'
        env = os.environ.copy()
        env['AI_VIDEO_NOTE_NO_BROWSER'] = '1'
        print('[AI Video Note] 正在启动本机后台服务…', file=sys.stderr)
        with log_path.open('wb') as log:
            subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                             start_new_session=os.name != 'nt')
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            base = running_service()
            if base:
                print('[AI Video Note] 后台服务已就绪。', file=sys.stderr)
                return base
            time.sleep(1)
        die(f'自动启动等待超时，未提交视频任务。请检查软件及日志：{log_path}')
    except (OSError, ValueError, RuntimeError) as error:
        die(f'无法自动启动 AI Video Note：{error}')


def pick_saved_channel(base: str) -> dict | None:
    """本机只保存过一个接口的 Key 时沿用它的通道；保存了多个则返回 None（不猜）。"""
    try:
        status = http_json("GET", f"{base}/api/llm-keys", timeout=10)
    except RuntimeError:
        return None
    entries = [entry for entry in (status.get("entries") or []) if entry.get("has_key")]
    if len(entries) != 1:
        return None
    entry = entries[0]
    channel = {
        "model_type": entry.get("provider") or "custom",
        "base_url": entry.get("base_url"),
        "model": entry.get("model"),
    }
    return {key: value for key, value in channel.items() if value}


def upload_file(base: str, path: Path) -> str:
    size = path.stat().st_size
    if size > UPLOAD_LIMIT_BYTES:
        die(f"文件超过上传上限 2GB：{path}（{size} 字节）")
    boundary = f"----ai-video-note{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"{base}/api/upload",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **client_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        die(f"上传失败：HTTP {exc.code} {detail or exc.reason}")
    print(f"[AI Video Note] 本地文件已上传（{size} 字节）")
    return payload["task_id"]


def poll_task(base: str, task_id: str, wait_seconds: int, *, block_on_user_review: bool = False) -> dict:
    started = time.monotonic()
    deadline = started + wait_seconds
    printed_logs = 0
    notified_pending = False
    while True:
        task = http_json("GET", f"{base}/api/task/{task_id}", timeout=20)
        logs = task.get("logs") or []
        for line in logs[printed_logs:]:
            print(f"  | {redact(line)}")
        printed_logs = len(logs)
        status = task.get("status")
        if status == "completed":
            return task
        if status == "failed":
            error_text = str(task.get("error") or "未知原因")
            print(f"[AI Video Note] 任务失败：{redact(error_text)}", file=sys.stderr)
            if "API Key" in error_text:
                print(
                    "[AI Video Note] 提示：该接口地址本机没有可复用的 Key。把 Key 交给环境变量"
                    f" {API_KEY_ENV}（或 --api-key - 从标准输入读）后重跑，"
                    "或在网页「总结模型」填入 Key 并点「保存到本机」。",
                    file=sys.stderr,
                )
            print(f"[AI Video Note] 可复用中间产物重试，task_id={task_id}", file=sys.stderr)
            sys.exit(2)
        if status == "cancelled":
            die(f"任务已取消（task_id={task_id}）", code=2)
        if status == 'awaiting_agent':
            return task
        if (task.get('transcript_review') or {}).get('status') == 'pending':
            if not notified_pending:
                print(f'[AI Video Note] 等待用户确认字幕：{base}/?task={task_id}')
                notified_pending = True
            if not block_on_user_review:
                return task
        else:
            notified_pending = False
        now = time.monotonic()
        if now > deadline:
            print(
                f"[AI Video Note] 等待超时（{wait_seconds} 秒），任务仍在后台运行：task_id={task_id}",
                file=sys.stderr,
            )
            print(f"[AI Video Note] 稍后可用 wait-task --task-id {task_id} 或 GET {base}/api/task/{task_id} 继续查询", file=sys.stderr)
            sys.exit(3)
        elapsed = now - started
        interval = FAST_POLL_INTERVAL_SECONDS if elapsed < 30 else SLOW_POLL_INTERVAL_SECONDS
        time.sleep(interval)


def resolve_api_key(args) -> str:
    """Key 的来源：`--api-key -` 读标准输入 > `--api-key` 明文 > 环境变量。

    命令行参数会留在 shell 历史与进程列表里（任何同用户进程都能读到），
    所以用到那一种时明确提示换法。一个都不给时后端按接口地址复用本机已存 Key。
    """
    raw = (args.api_key or "").strip()
    if raw == "-":
        if sys.stdin.isatty():
            print("[AI Video Note] 请输入 API Key（一行，回车结束）：", file=sys.stderr)
        # 只读一行：管道里 printf 出来的 Key 不必等 EOF，终端里手输回车即可
        return sys.stdin.readline().strip()
    if raw:
        print(
            f"[AI Video Note] 提示：--api-key 会留在 shell 历史与进程列表里；下次请改用环境变量"
            f" {API_KEY_ENV}，或 --api-key - 从标准输入读。",
            file=sys.stderr,
        )
        return raw
    return os.environ.get(API_KEY_ENV, "").strip()


def http_bytes(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, method="GET", headers=client_headers())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise RuntimeError(redact(f"HTTP {exc.code} {url}: {detail or exc.reason}")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 {url}：{exc.reason}") from exc


def download_task_image(base: str, task_id: str, filename: str, download_dir: str | None = None) -> Path:
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename or "/" in filename or "\\" in filename:
        die(f"无效截图文件名：{filename}")
    target_dir = (
        Path(download_dir).expanduser().resolve()
        if download_dir
        else Path(tempfile.gettempdir()) / f"AI-Video-Note-frames-{Path(task_id).name}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    encoded_task = urllib.parse.quote(task_id, safe="")
    encoded_file = urllib.parse.quote(safe_name, safe="")
    content = http_bytes(f"{base}/api/image/{encoded_task}/{encoded_file}", timeout=60)
    target_path = target_dir / safe_name
    target_path.write_bytes(content)
    return target_path


def parse_float_list(raw: str, flag_name: str) -> list[float]:
    parts = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not parts:
        die(f"请通过 {flag_name} 提供逗号分隔的数值列表")
    try:
        return [float(part) for part in parts]
    except ValueError:
        die(f"{flag_name} 包含无效数值：{raw}")


def parse_int_list(raw: str, flag_name: str) -> list[int]:
    parts = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not parts:
        die(f"请通过 {flag_name} 提供逗号分隔的整数列表")
    try:
        return [int(part) for part in parts]
    except ValueError:
        die(f"{flag_name} 包含无效整数：{raw}")


def read_text_input(file_path: str | None, inline_value: str | None, label: str) -> str:
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_file():
            die(f"找不到{label}文件：{path}")
        return path.read_text(encoding="utf-8")
    if inline_value == "-":
        return sys.stdin.read()
    if inline_value is not None:
        return inline_value
    die(f"请提供{label}（可通过文件或 - 从标准输入读取）")


def build_extra_summarize_fields(args) -> dict:
    extra: dict = {}
    if getattr(args, "format", None):
        extra["note_format"] = args.format
    if getattr(args, "note_language", None):
        extra["note_language"] = args.note_language
    if getattr(args, "text_detail", None):
        extra["text_detail"] = args.text_detail
    if getattr(args, "timeline_mode", None):
        extra["timeline_mode"] = args.timeline_mode
    if getattr(args, "source_link_position", None):
        extra["source_link_position"] = args.source_link_position
    if getattr(args, "ai_commentary_position", None):
        extra["ai_commentary_position"] = args.ai_commentary_position
        extra["ai_commentary"] = args.ai_commentary_position != "none"
    elif getattr(args, "ai_commentary", False):
        extra["ai_commentary"] = True
    if getattr(args, "custom_prompt", None):
        extra["custom_prompt"] = args.custom_prompt
    if getattr(args, "save_dir", None):
        extra["note_save_directory"] = args.save_dir
    if getattr(args, "screenshot_density", None):
        extra["screenshot_density"] = args.screenshot_density
    if getattr(args, "vision_screenshots", False):
        extra["vision_screenshots"] = True
    if getattr(args, "auto_crop_screenshots", False):
        extra["auto_crop_screenshots"] = True
        extra["vision_screenshots"] = True
    if getattr(args, "timed_screenshots", False):
        extra["timed_screenshots"] = True
    if getattr(args, "video_resolution", None):
        extra["video_resolution"] = args.video_resolution
    if getattr(args, "bilibili_pages", None):
        extra["bilibili_pages"] = parse_int_list(args.bilibili_pages, "--bilibili-pages")
    if getattr(args, "use_gpu", False):
        extra["use_gpu"] = True
    if getattr(args, "gpu_backend", None):
        extra["gpu_backend"] = args.gpu_backend
    vault = (getattr(args, "obsidian_vault", None) or "").strip()
    if vault or getattr(args, "format", None) == "obsidian" or getattr(args, "style", None) == "obsidian":
        if vault:
            extra["obsidian"] = {
                "vault": vault,
                "note_folder": getattr(args, "obsidian_note_folder", "") or "",
                "attachment_mode": getattr(args, "obsidian_attachment_mode", "note_subfolder") or "note_subfolder",
                "attachment_folder": getattr(args, "obsidian_attachment_folder", "attachments") or "attachments",
            }
        if getattr(args, "obsidian_tags", False):
            extra["obsidian_tags"] = True
        if getattr(args, "obsidian_callouts", False):
            extra["obsidian_callouts"] = True
        if getattr(args, "no_obsidian_properties", False):
            extra["obsidian_properties"] = False
    return extra


def run_agent_action(action: str, args) -> None:
    base = find_service(auto_start=not args.no_auto_start)
    if action == "list-jobs":
        data = http_json("GET", f"{base}/api/agent/jobs", timeout=30)
        print(json.dumps({"base_url": base, **data}, ensure_ascii=False, indent=2))
        return

    task_id = (args.task_id or "").strip()
    if not task_id:
        die(f"{action} 需要提供 --task-id <任务ID>")
    encoded_task = urllib.parse.quote(task_id, safe="")

    if action == "claim":
        agent_label = AGENT_NAME or "AI-Video-Note-Skill"
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/claim",
            {"agent_name": agent_label},
            timeout=30,
        )
        print(json.dumps({"base_url": base, **data}, ensure_ascii=False, indent=2))
        return

    if action == "get-material":
        offset = max(0, args.offset)
        limit = max(1, min(args.limit, 500))
        query = urllib.parse.urlencode({"offset": offset, "limit": limit})
        packet = http_json("GET", f"{base}/api/agent/jobs/{encoded_task}?{query}", timeout=60)
        if args.all_pages:
            segments = list(packet.get("segments") or [])
            next_offset = packet.get("next_offset")
            while next_offset is not None:
                page_query = urllib.parse.urlencode({"offset": next_offset, "limit": 500})
                page = http_json("GET", f"{base}/api/agent/jobs/{encoded_task}?{page_query}", timeout=60)
                segments.extend(page.get("segments") or [])
                next_offset = page.get("next_offset")
            packet["segments"] = segments
            packet["next_offset"] = None
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {
                "task_id": packet.get("task_id"),
                "title": packet.get("title"),
                "saved_to": str(out_path),
                "total_segments": packet.get("total_segments"),
                "fetched_segments": len(packet.get("segments") or []),
                "next_offset": packet.get("next_offset"),
                "can_capture": packet.get("can_capture"),
                "screenshots_count": len(packet.get("screenshots") or []),
                "transcript_review": packet.get("transcript_review"),
                "settings": packet.get("settings"),
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        return

    if action == "report-issues":
        token = (args.claim_token or "").strip()
        if not token:
            die("report-issues 需要提供 --claim-token")
        if args.issues_file or args.issues_json is not None:
            raw_issues = read_text_input(args.issues_file, args.issues_json, "校对疑点 JSON")
            try:
                parsed = json.loads(raw_issues)
            except ValueError as exc:
                die(f"校对疑点 JSON 解析失败：{exc}")
            issues = parsed.get("issues") if isinstance(parsed, dict) and "issues" in parsed else parsed
            if not isinstance(issues, list):
                die("校对疑点必须是 JSON 数组，或包含 issues 数组的 JSON 对象")
        else:
            issues = []
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/transcript-issues",
            {"claim_token": token, "issues": issues},
            timeout=60,
        )
        if data.get("waiting_for_user"):
            data["web_url"] = f"{base}{data.get('web_path', '/?task=' + task_id)}"
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if action == "capture-screenshots":
        token = (args.claim_token or "").strip()
        if not token:
            die("capture-screenshots 需要提供 --claim-token")
        times = parse_float_list(args.times or "", "--times")
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/screenshots",
            {"claim_token": token, "times": times},
            timeout=LONG_HTTP_TIMEOUT_SECONDS,
        )
        local_map: dict[str, str] = {}
        for item in data.get("screenshots") or []:
            fname = item.get("filename")
            if fname:
                local_path = download_task_image(base, task_id, fname, args.download_dir)
                item["local_path"] = str(local_path)
                local_map[fname] = str(local_path)
        for group in data.get("candidate_groups") or []:
            group_files = []
            for stamp in group.get("candidates") or []:
                matched = next(
                    (s for s in (data.get("screenshots") or []) if abs(float(s.get("time", -1)) - float(stamp)) < 1e-3),
                    None,
                )
                if matched:
                    group_files.append(
                        {
                            "time": stamp,
                            "filename": matched.get("filename"),
                            "markdown_ref": matched.get("markdown_ref"),
                            "local_path": matched.get("local_path"),
                        }
                    )
            if group_files:
                group["candidate_files"] = group_files
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if action == "fetch-image":
        raw_names = [part.strip() for part in (args.filename or "").split(",") if part.strip()]
        if not raw_names:
            die("fetch-image 需要提供 --filename <文件名，多个用逗号分隔>")
        images = []
        for name in raw_names:
            path = download_task_image(base, task_id, name, args.download_dir)
            images.append({"filename": Path(name).name, "markdown_ref": f"frames/{Path(name).name}", "local_path": str(path)})
        print(json.dumps({"task_id": task_id, "images": images}, ensure_ascii=False, indent=2))
        return

    if action == "crop-prepare":
        token = (args.claim_token or "").strip()
        if not token:
            die("crop-prepare 需要提供 --claim-token")
        filename = (args.filename or "").strip()
        if not filename:
            die("crop-prepare 需要提供 --filename <原图文件名>")
        box = parse_float_list(args.box or "", "--box")
        if len(box) != 4:
            die("--box 必须是 4 个 0 到 1 之间的数值：left,top,right,bottom")
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/crop",
            {"claim_token": token, "action": "prepare", "filename": filename, "box": box},
            timeout=120,
        )
        if data.get("preview") and isinstance(data["preview"], dict):
            orig_path = download_task_image(base, task_id, filename, args.download_dir)
            prev_name = data["preview"].get("filename")
            prev_path = download_task_image(base, task_id, prev_name, args.download_dir) if prev_name else None
            data["original_local_path"] = str(orig_path)
            if prev_path:
                data["preview"]["local_path"] = str(prev_path)
                data["preview_local_path"] = str(prev_path)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if action == "crop-confirm":
        token = (args.claim_token or "").strip()
        if not token:
            die("crop-confirm 需要提供 --claim-token")
        filename = (args.filename or "").strip()
        if not filename:
            die("crop-confirm 需要提供 --filename <裁剪预览文件名>")
        approved = bool(args.approved and not args.reject)
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/crop",
            {
                "claim_token": token,
                "action": "confirm",
                "filename": filename,
                "approved": approved,
                "context_complete": bool(args.context_complete and not args.reject),
                "clearly_better": bool(args.clearly_better and not args.reject),
            },
            timeout=120,
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if action == "renew-lease":
        token = (args.claim_token or "").strip()
        if not token:
            die("renew-lease 需要提供 --claim-token")
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/lease",
            {"claim_token": token, "release": bool(args.release)},
            timeout=30,
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if action == "submit-result":
        token = (args.claim_token or "").strip()
        if not token:
            die("submit-result 需要提供 --claim-token")
        markdown = read_text_input(args.markdown_file, args.markdown, "笔记正文 Markdown")
        if not markdown.strip():
            die("笔记正文不能为空")
        data = http_json(
            "POST",
            f"{base}/api/agent/jobs/{encoded_task}/result",
            {"claim_token": token, "markdown": markdown},
            timeout=LONG_HTTP_TIMEOUT_SECONDS,
        )
        try:
            task = http_json("GET", f"{base}/api/task/{encoded_task}", timeout=30)
            task_result = task.get("result") or {}
            summary_result = {k: v for k, v in task_result.items() if k != "markdown"}
            data["result"] = summary_result
            if task.get("export_status"):
                data["export_status"] = task.get("export_status")
            if task.get("export_warning"):
                data["export_warning"] = task.get("export_warning")
        except Exception:
            pass
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if action == "wait-task":
        task = (
            poll_task(base, task_id, args.wait, block_on_user_review=True)
            if getattr(args, "wait_for_user_confirm", False)
            else poll_task(base, task_id, args.wait)
        )
        task_result = task.get("result") or {}
        if args.out and task_result.get("markdown"):
            Path(args.out).expanduser().write_text(task_result["markdown"], encoding="utf-8")
        review_state = task.get("transcript_review") or {}
        waiting_user = review_state.get("status") == "pending"
        summary = {
            "task_id": task_id,
            "status": task.get("status"),
            "step_name": task.get("step_name"),
            "progress": task.get("progress"),
            "waiting_for_user": waiting_user,
            "transcript_review": task.get("transcript_review"),
            "result": {k: v for k, v in task_result.items() if k != "markdown"},
        }
        if waiting_user:
            summary["web_url"] = f"{base}/?task={task_id}"
            summary["next"] = "请提醒用户打开 web_url 确认字幕修改；确认后再运行 wait-task 继续等待。"
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    if action == "get-transcript":
        fmt = args.transcript_format or "markdown"
        transcript = http_json(
            "GET",
            f"{base}/api/task/{encoded_task}/transcript?output_format={urllib.parse.quote(fmt, safe='')}",
            timeout=60,
        )
        if args.out:
            out_path = Path(args.out).expanduser()
            if fmt == "markdown":
                out_path.write_text(transcript.get("text") or "", encoding="utf-8")
            else:
                out_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[AI Video Note] 转录已保存：{out_path}")
        elif fmt == "markdown":
            print(transcript.get("text") or "")
        else:
            print(json.dumps(transcript, ensure_ascii=False, indent=2))
        return


def main() -> None:
    global AGENT_NAME
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="AI Video Note：视频 → 带时间轴的 Markdown 笔记与 Agent 队列工具")
    parser.add_argument(
        "source",
        nargs="?",
        default="",
        help=f"视频链接（B站/抖音/YouTube）、本地媒体文件路径，或 Agent 动作（{', '.join(AGENT_ACTIONS)}）",
    )
    parser.add_argument("--action", choices=AGENT_ACTIONS, default=None, help="执行指定的 Agent 队列/截图/裁剪/回传动作")
    parser.add_argument("--style", choices=STYLES, default="detailed")
    parser.add_argument(
        "--agent-note",
        action="store_true",
        help="以 Agent 模式创建笔记任务（使用调用方自身的模型生成笔记、配图并回传导出，无需软件 API Key）",
    )
    parser.add_argument("--claim", action="store_true", help="创建 Agent 任务 reaching awaiting_agent 后自动领取并返回 claim_token")
    parser.add_argument(
        "--transcript-only",
        action="store_true",
        help="生成带时间轴字幕，使用配置的AI进行校对",
    )
    parser.add_argument('--review-agent', action='store_true', help='字幕交给调用此Skill的Agent校对，返回待领取任务而不等待导出')
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="禁止复用同链接已有转录，强制重新转写（默认只在 API 笔记模式从头处理）",
    )
    parser.add_argument("--provider", default=None, help=f"大模型供应商：{', '.join(sorted(PROVIDERS))}")
    parser.add_argument(
        "--api-key",
        default="",
        help=(
            f"大模型 API Key（该接口地址已保存到本机时可省略）。推荐 `--api-key -` 从标准输入读，"
            f"或设环境变量 {API_KEY_ENV}；直接写在命令行会留在 shell 历史与进程列表里"
        ),
    )
    parser.add_argument("--base-url", default=None, help="custom 供应商的接口地址")
    parser.add_argument("--custom-model", default=None, help="custom 供应商的模型名")
    parser.add_argument("--whisper-model", default="base", help="本地转写模型（默认 base）")
    parser.add_argument("--use-gpu", action="store_true", help="启用显卡加速本地语音转写")
    parser.add_argument("--gpu-backend", choices=["auto", "cuda", "amd", "intel", "vulkan"], default=None)
    parser.add_argument("--include-screenshots", action="store_true", help="提取视频截图")
    parser.add_argument("--screenshot-density", choices=SCREENSHOT_DENSITIES, default=None, help="AI 配图密度：none/few/medium/many")
    parser.add_argument("--vision-screenshots", action="store_true", help="开启视觉核对候选截图（每时间点提取多张候选供看图筛选）")
    parser.add_argument("--auto-crop-screenshots", action="store_true", help="开启截图保守区域裁剪（自动同时开启视觉核对）")
    parser.add_argument("--timed-screenshots", action="store_true", help="按固定时间间隔提取截图，替代 AI 选点")
    parser.add_argument("--screenshot-interval", type=int, default=30)
    parser.add_argument("--video-resolution", type=int, choices=[360, 480, 720, 1080], default=None, help="截图视频清晰度（默认 720）")
    parser.add_argument("--format", "--note-format", dest="format", choices=NOTE_FORMATS, default=None, help="笔记导出格式（markdown/docx/pdf/enex/html/txt/png/json/obsidian）")
    parser.add_argument("--note-language", choices=NOTE_LANGUAGES, default=None, help="笔记语言：zh-CN 或 en")
    parser.add_argument("--text-detail", choices=TEXT_DETAILS, default=None, help="文字详略：brief/standard/detailed")
    parser.add_argument("--timeline-mode", choices=TIMELINE_MODES, default=None, help="时间线位置：none/heading/paragraph_start/paragraph_end/all")
    parser.add_argument("--source-link-position", choices=POSITIONS, default=None, help="原视频链接位置：none/start/end")
    parser.add_argument("--ai-commentary", action="store_true", help="包含独立 AI 点评")
    parser.add_argument("--ai-commentary-position", choices=POSITIONS, default=None, help="独立 AI 点评位置：none/start/end")
    parser.add_argument("--custom-prompt", default=None, help="生成笔记的自定义提示词要求")
    parser.add_argument("--save-dir", default=None, help="导出笔记保存目录的绝对路径")
    parser.add_argument("--obsidian-vault", default=None, help="Obsidian 仓库根目录绝对路径")
    parser.add_argument("--obsidian-note-folder", default="", help="Obsidian 笔记相对目录")
    parser.add_argument("--obsidian-attachment-mode", choices=OBSIDIAN_ATTACHMENT_MODES, default="note_subfolder")
    parser.add_argument("--obsidian-attachment-folder", default="attachments")
    parser.add_argument("--obsidian-tags", action="store_true", help="启用 Obsidian 标签")
    parser.add_argument("--obsidian-callouts", action="store_true", help="启用 Obsidian Callouts")
    parser.add_argument("--no-obsidian-properties", action="store_true", help="禁用 Obsidian 属性块")
    parser.add_argument("--bilibili-pages", default=None, help="B 站多分 P 页码，逗号分隔（如 1,2,3）")
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT_SECONDS, help=f"最长等待秒数（默认 {DEFAULT_WAIT_SECONDS}）")
    parser.add_argument("--wait-for-user-confirm", action="store_true", help="遇到待用户确认字幕时不立即退出，继续在终端阻塞等待")
    parser.add_argument("--out", default=None, help="把结果写入该文件")
    parser.add_argument("--no-auto-start", action="store_true", help="不自动启动后台服务")
    parser.add_argument("--agent-name", default=AGENT_NAME, help="调用此 Skill 的 Agent 名称（用于界面显示）")

    # Agent 队列与截图/裁剪/回传专用参数
    parser.add_argument("--task-id", default="", help="目标任务 ID")
    parser.add_argument("--claim-token", default="", help="claim 返回的领取令牌")
    parser.add_argument("--offset", type=int, default=0, help="get-material 分页起始偏移（默认 0）")
    parser.add_argument("--limit", type=int, default=100, help="get-material 单页条数（默认 100，最大 500）")
    parser.add_argument("--all-pages", action="store_true", help="get-material 自动拉取并合并全部分页字幕")
    parser.add_argument("--issues-file", default=None, help="report-issues 的 JSON 文件路径")
    parser.add_argument("--issues-json", default=None, help="report-issues 的 JSON 字符串（或 - 从标准输入读）")
    parser.add_argument("--times", default=None, help="capture-screenshots 的截图时间点（秒），逗号分隔，如 12.5,45,90")
    parser.add_argument("--filename", default=None, help="fetch-image / crop-prepare / crop-confirm 的截图文件名")
    parser.add_argument("--box", default=None, help="crop-prepare 的归一化裁剪框：left,top,right,bottom（0~1）")
    parser.add_argument("--approved", action="store_true", help="crop-confirm 确认采用该裁剪预览")
    parser.add_argument("--reject", action="store_true", help="crop-confirm 拒绝裁剪预览并保留原图")
    parser.add_argument("--context-complete", action="store_true", help="crop-confirm 确认必要上下文完整")
    parser.add_argument("--clearly-better", action="store_true", help="crop-confirm 确认裁剪后明显优于全图")
    parser.add_argument("--download-dir", default=None, help="截图下载保存目录（供 Agent 用 read_file 查看图片）")
    parser.add_argument("--release", action="store_true", help="renew-lease 释放任务领取状态")
    parser.add_argument("--markdown-file", default=None, help="submit-result 的 Markdown 正文文件路径")
    parser.add_argument("--markdown", default=None, help="submit-result 的 Markdown 正文内容（或 - 从标准输入读）")
    parser.add_argument("--transcript-format", choices=["markdown", "json"], default="markdown", help="get-transcript 输出格式")

    args = parser.parse_args()
    AGENT_NAME = args.agent_name

    action = args.action
    if not action and args.source in AGENT_ACTIONS:
        action = args.source
    if action:
        try:
            run_agent_action(action, args)
        except RuntimeError as exc:
            die(str(exc))
        return

    if not args.source:
        die("请提供视频链接、本地文件路径，或指定 Agent 动作（如 list-jobs / claim / get-material / submit-result）")

    if args.provider and args.provider not in PROVIDERS:
        die(f"不支持的供应商：{args.provider}（可选：{', '.join(sorted(PROVIDERS))}）")
    if args.provider == "custom" and not (args.base_url and args.custom_model):
        die("custom 供应商需要 --base-url 和 --custom-model")
    transcript_only = args.transcript_only
    agent_note = args.agent_note
    if transcript_only and agent_note:
        die("--transcript-only 与 --agent-note 不能同时使用")
    if args.review_agent and not transcript_only:
        die('--review-agent 需与 --transcript-only 一起使用')
    if transcript_only and (args.include_screenshots or args.screenshot_density not in (None, "none") or args.vision_screenshots or args.auto_crop_screenshots or args.timed_screenshots):
        die('仅转录字幕不做截图，请去掉截图相关参数')

    base = find_service(auto_start=not args.no_auto_start)
    print(f"[AI Video Note] 服务已连接：{base}")

    use_agent_mode = bool(args.review_agent or agent_note)
    api_key = '' if use_agent_mode else resolve_api_key(args)
    if api_key: remember_secret(api_key)
    llm_config = {'model_type': args.provider or 'deepseek'}
    if api_key: llm_config['api_key'] = api_key
    if args.base_url: llm_config['base_url'] = args.base_url
    if args.custom_model: llm_config['model'] = args.custom_model
    if not use_agent_mode and not api_key and not args.provider:
        llm_config = pick_saved_channel(base) or llm_config
    source_path = Path(args.source)
    if transcript_only:
        endpoint = "/api/transcribe"
        payload: dict = {
            "prefer_subtitles": True,
            "processing_mode": "restart" if args.no_reuse else "reuse",
            "whisper_model": args.whisper_model,
            "generation_mode": 'agent' if args.review_agent else 'api',
            "agent_execution": 'manual',
            "llm_config": {} if args.review_agent else llm_config,
        }
        if args.bilibili_pages:
            payload["bilibili_pages"] = parse_int_list(args.bilibili_pages, "--bilibili-pages")
        if args.use_gpu:
            payload["use_gpu"] = True
        if args.gpu_backend:
            payload["gpu_backend"] = args.gpu_backend
    else:
        endpoint = "/api/summarize"
        payload = {
            "summary_style": args.style,
            "prefer_subtitles": True,
            "processing_mode": ("restart" if args.no_reuse else "reuse") if agent_note else "restart",
            "whisper_model": args.whisper_model,
            "include_screenshots": args.include_screenshots,
            "screenshot_interval": max(5, min(300, args.screenshot_interval)),
            "llm_config": {} if agent_note else llm_config,
        }
        if agent_note:
            payload["generation_mode"] = "agent"
            payload["agent_execution"] = "manual"
        payload.update(build_extra_summarize_fields(args))

    if source_path.is_file():
        payload["upload_task_id"] = upload_file(base, source_path)
        payload["video_url"] = ""
    else:
        if not args.source.strip().lower().startswith(("http://", "https://")) and "b23.tv" not in args.source:
            die(f"输入既不是有效链接也不是存在的本地文件：{args.source}")
        payload["video_url"] = args.source.strip()

    print("[AI Video Note] 正在提交任务…")
    try:
        result = http_json("POST", f"{base}{endpoint}", payload, timeout=60)
    except RuntimeError as exc:
        if transcript_only or agent_note:
            die(f"提交失败：{exc}")
        if "API Key" in str(exc) or "422" in str(exc):
            die(
                f"提交失败：{exc}\n提示：该接口地址本机没有可复用的 Key。可用"
                " --agent-note（生成笔记/配图/多格式导出）或 --transcript-only --review-agent（仅字幕）由你处理；使用API时，"
                f"让用户把 Key 交给环境变量 {API_KEY_ENV} 或以 --api-key - 从标准输入传入"
                "（配合 --provider，custom 另需 --base-url/--custom-model）。"
                "不要把 Key 写进命令行参数。",
                code=1,
            )
        raise

    task_id = result.get("task_id")
    if not task_id:
        die(f"提交失败：服务未返回 task_id（{result}）")
    if result.get("reused_task_id"):
        print(f"[AI Video Note] 已复用任务 {result['reused_task_id']} 的转录结果")
    print(f"[AI Video Note] 任务已创建：{task_id}，开始等待完成…")

    task = (
        poll_task(base, task_id, args.wait, block_on_user_review=True)
        if getattr(args, "wait_for_user_confirm", False)
        else poll_task(base, task_id, args.wait)
    )
    if (task.get('transcript_review') or {}).get('status') == 'pending':
        print(json.dumps({
            'task_id': task_id,
            'status': task.get('status'),
            'waiting_for_user': True,
            'base_url': base,
            'web_url': f'{base}/?task={task_id}',
            'next': '请提醒用户打开 web_url 在网页确认字幕修改；用户确认后运行 wait-task --task-id <ID> 继续等待完成。',
        }, ensure_ascii=False))
        return
    if task.get('status') == 'awaiting_agent':
        output_payload = {
            'task_id': task_id,
            'status': 'awaiting_agent',
            'base_url': base,
            'mode': 'agent_note' if agent_note else 'transcript_review',
            'next': (
                '执行 claim 领取任务 -> get-material --all-pages 读取字幕与提示词 -> report-issues 上报字幕校对结果 -> '
                '(如需配图) capture-screenshots / crop-prepare / crop-confirm -> submit-result 回传笔记并完成导出。'
                if agent_note
                else '领取 /api/agent/jobs/{task_id}/claim，读取全部分页材料，再提交 transcript-issues；等待用户确认后取字幕。'
            ),
        }
        if args.claim:
            claim_data = http_json(
                "POST",
                f"{base}/api/agent/jobs/{urllib.parse.quote(task_id, safe='')}/claim",
                {"agent_name": AGENT_NAME or "AI-Video-Note-Skill"},
                timeout=30,
            )
            output_payload["claim_token"] = claim_data.get("claim_token")
            output_payload["lease_seconds"] = claim_data.get("lease_seconds")
        print(json.dumps(output_payload, ensure_ascii=False))
        return
    task_result = task.get("result") or {}

    if transcript_only:
        transcript = http_json(
            "GET",
            f"{base}/api/task/{task_id}/transcript?output_format=markdown",
            timeout=60,
        )
        body = transcript.get("text") or ""
        label = "转录"
    else:
        body = task_result.get("markdown") or ""
        label = "笔记"
    if not body:
        die(f"任务完成但未返回{label}内容", code=2)
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"[AI Video Note] {label}已保存：{args.out}")
    else:
        print("\n" + "=" * 58 + "\n")
        print(body)
    output_dir = task_result.get("output_directory")
    if output_dir:
        print(f"\n[AI Video Note] 产物目录：{output_dir}")


if __name__ == "__main__":
    main()
