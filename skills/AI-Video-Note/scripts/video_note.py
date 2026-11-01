#!/usr/bin/env python3
"""AI Video Note Agent Skill 脚本：一条命令完成"视频 → 笔记"。

自动探测本机 AI Video Note 服务端口（8765-8784），未运行时按需启动，提交任务、轮询进度、
输出完整 Markdown 笔记。仅依赖 Python 标准库。

用法:
    python video_note.py "<视频链接或本地文件路径>" [选项]

常用选项:
    --style {detailed,faithful,concise}   笔记风格（默认 detailed）
    --transcript-only                     生成带时间轴字幕，包含AI校对
    --no-reuse                            禁止复用同链接已有转录，强制重新转写
    --no-auto-start                       不自动启动本机服务
    --provider {deepseek,openai,qwen,glm,moonshot,custom}
    --api-key -                           大模型 API Key（- 表示从标准输入读）
                                          也可设环境变量 AI_VIDEO_NOTE_LLM_API_KEY；
                                          该地址已保存到本机时两者都可省略
    --wait SECONDS                        最长等待秒数（默认 1800）
    --out PATH                            把笔记写入文件（默认打印到 stdout）

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
import urllib.request
import uuid
from pathlib import Path

PORT_RANGE = range(8765, 8785)
POLL_INTERVAL_SECONDS = 3
PROVIDERS = {"deepseek", "openai", "openai_gpt4", "openai_gpt35", "qwen", "glm", "moonshot", "custom"}
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


def poll_task(base: str, task_id: str, wait_seconds: int) -> dict:
    deadline = time.monotonic() + wait_seconds
    printed_logs = 0
    while True:
        task = http_json("GET", f"{base}/api/task/{task_id}", timeout=15)
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
            print(f'[AI Video Note] 等待用户确认字幕：{base}/?task={task_id}')
        if time.monotonic() > deadline:
            print(
                f"[AI Video Note] 等待超时（{wait_seconds} 秒），任务仍在后台运行：task_id={task_id}",
                file=sys.stderr,
            )
            print(f"[AI Video Note] 稍后可用 GET {base}/api/task/{task_id} 继续查询", file=sys.stderr)
            sys.exit(3)
        time.sleep(POLL_INTERVAL_SECONDS)


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


def main() -> None:
    global AGENT_NAME
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="AI Video Note：视频 → 带时间轴的 Markdown 笔记")
    parser.add_argument("source", help="视频链接（B站/抖音/YouTube）或本地媒体文件路径")
    parser.add_argument("--style", choices=["detailed", "faithful", "concise"], default="detailed")
    parser.add_argument(
        "--transcript-only",
        action="store_true",
        help="生成带时间轴字幕，使用配置的AI进行校对",
    )
    parser.add_argument('--review-agent', action='store_true', help='字幕交给调用此Skill的Agent校对，返回待领取任务而不等待导出')
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="禁止复用同链接已有转录，强制重新转写（默认只在笔记模式从头处理）",
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
    parser.add_argument("--include-screenshots", action="store_true", help="提取视频截图")
    parser.add_argument("--screenshot-interval", type=int, default=30)
    parser.add_argument("--wait", type=int, default=1800, help="最长等待秒数（默认 1800）")
    parser.add_argument("--out", default=None, help="把笔记写入该 Markdown 文件")
    parser.add_argument("--no-auto-start", action="store_true", help="不自动启动后台服务")
    parser.add_argument("--agent-name", default=AGENT_NAME, help="调用此 Skill 的 Agent 名称（用于界面显示）")
    args = parser.parse_args()
    AGENT_NAME = args.agent_name

    if args.provider and args.provider not in PROVIDERS:
        die(f"不支持的供应商：{args.provider}（可选：{', '.join(sorted(PROVIDERS))}）")
    if args.provider == "custom" and not (args.base_url and args.custom_model):
        die("custom 供应商需要 --base-url 和 --custom-model")
    transcript_only = args.transcript_only
    if args.review_agent and not transcript_only:
        die('--review-agent 需与 --transcript-only 一起使用')
    if transcript_only and args.include_screenshots:
        die('仅转录字幕不做截图，请去掉 --include-screenshots')

    base = find_service(auto_start=not args.no_auto_start)
    print(f"[AI Video Note] 服务已连接：{base}")

    api_key = '' if args.review_agent else resolve_api_key(args)
    if api_key: remember_secret(api_key)
    llm_config = {'model_type': args.provider or 'deepseek'}
    if api_key: llm_config['api_key'] = api_key
    if args.base_url: llm_config['base_url'] = args.base_url
    if args.custom_model: llm_config['model'] = args.custom_model
    if not args.review_agent and not api_key and not args.provider:
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
    else:
        endpoint = "/api/summarize"
        payload = {
            "summary_style": args.style,
            "prefer_subtitles": True,
            "processing_mode": "restart",
            "whisper_model": args.whisper_model,
            "include_screenshots": args.include_screenshots,
            "screenshot_interval": max(5, min(300, args.screenshot_interval)),
            "llm_config": llm_config,
        }

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
        if transcript_only:
            die(f"提交失败：{exc}")
        if "API Key" in str(exc) or "422" in str(exc):
            die(
                f"提交失败：{exc}\n提示：该接口地址本机没有可复用的 Key。可用"
                " --transcript-only --review-agent 由你校对；使用API时，"
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

    task = poll_task(base, task_id, args.wait)
    if task.get('status') == 'awaiting_agent':
        print(json.dumps({'task_id': task_id, 'status': 'awaiting_agent', 'base_url': base,
                          'next': '领取 /api/agent/jobs/{task_id}/claim，读取全部分页材料，再提交 transcript-issues；等待用户确认后取字幕。'}, ensure_ascii=False))
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
