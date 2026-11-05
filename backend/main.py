from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import aiofiles
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from .note_review import find_media, replay_link
from .agent_model_runtime import AgentModelSettings
from starlette.exceptions import HTTPException as StarletteHTTPException


class NoCacheStaticFiles(StaticFiles):
    """前端静态资源每次都重新校验，避免浏览器缓存旧版本。

    HTML 入口页更是直接 no-store：旧 HTML 与新版 script.js 混用会导致
    页面元素对不上、按钮全部"点了没反应"，因此入口页不允许任何缓存。
    """

    def file_response(self, *args: Any, **kwargs: Any) -> FileResponse:
        response = super().file_response(*args, **kwargs)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response
from pydantic import BaseModel, Field, SecretStr, field_validator

from . import paraformer_asr, secret_box, model_storage, obsidian_export
from .note_styles import NOTE_STYLES
from .ai_screenshots import plan_screenshots, screenshot_instructions, insert_screenshots, interval_screenshots
from . import asr_process
from . import update_check, task_storage
from .generation_store import continuation_sources
from .transcript_reuse import bilibili_pages_match
from .task_recovery import INTERRUPTED_STATUSES, recovered_status, safe_number, valid_transcript
from .vision_screenshots import probe_vision, candidate_times, verify_screenshots
from .task_cleanup import exported_files, unlink_exports
from .video_cleanup import downloaded_videos
from .cache_index import remove_file_entries
from .note_options import note_language_prompt, apply_source_link, note_option_prompt, apply_timeline, obsidian_feature_prompt
from .runtime_revision import CODE_REVISION
from .config_store import BiliCredentialsUnavailable, LLM_KEYS_FILE, ConfigStore
from .llm_summarizer import (
    LONG_TRANSCRIPT_CHARACTERS,
    LLMSummarizer,
    default_base_url,
    env_proxy_summary,
    mask_proxy_target,
    normalize_endpoint_host,
    utf8_safe,
)
from .bili_qr_login import BiliQrLoginManager as BiliLoginManager
from .douyin_login import DouyinLoginManager
from .transcript import (
    TranscriptSegment,
    format_timestamp,
    segments_to_prompt,
    transcript_quality,
)
from .video_processor import (
    FRAMES_DIR_NAME,
    VideoProcessor,
    VideoSource,
    bilibili_page_url,
    merge_bilibili_pages,
    normalize_video_input,
)
from .paraformer_asr import ParaformerTranscriber
from .whisper_asr import WHISPER_MODEL_REPOS, WhisperTranscriber
from . import whisper_vulkan
from .gpu_devices import resolve_backend, preferred_vulkan_precision
from .model_downloads import ModelDownloads
from .environment import EnvironmentRepair, diagnose

model_downloads = ModelDownloads()
environment_repair = EnvironmentRepair()
from .gpu_runtime_install import GpuRuntimeInstalls
gpu_runtime_installs = GpuRuntimeInstalls()


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
WORKSPACE_DIR = Path(
    os.getenv("AI_VIDEO_NOTE_WORKSPACE", str(BASE_DIR / "workspace"))
).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_WHISPER_CACHE_DIR = Path(
    os.getenv("WHISPER_CACHE_DIR", str(WORKSPACE_DIR / "_model_cache"))
).resolve()
WHISPER_CACHE_DIR = model_storage.load_directory(WORKSPACE_DIR, DEFAULT_WHISPER_CACHE_DIR)
# workspace/ 下两个面向人的产物目录。转录稿单独一个目录而不是混进 notes/：先转写、后生成
# 笔记的同一视频会各留一份，分开摆一眼看得清，不用靠【转录】后缀在一堆文件里认。
NOTES_DIR_NAME = "notes"
TRANSCRIPTS_DIR_NAME = "transcripts"
FRONTEND_DIR = BASE_DIR / "frontend"
APP_ICON_PATH = BASE_DIR / "sources" / "icon.png"
FAVICON_PATH = BASE_DIR / "sources" / "icon.ico"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# 超过该体积的本地视频上传后自动提取音频（16kHz m4a）并删除原视频，
# 保留与在线视频一致的轻量工作目录；勾选“视频截图”时保留原视频。
LARGE_UPLOAD_EXTRACT_MB = int(os.getenv("LARGE_UPLOAD_EXTRACT_MB", "300"))
LARGE_UPLOAD_EXTRACT_BYTES = LARGE_UPLOAD_EXTRACT_MB * 1024 * 1024
MAX_CONCURRENT_TASKS = max(1, int(os.getenv("MAX_CONCURRENT_TASKS", "1")))
TASK_HISTORY_LIMIT = max(10, int(os.getenv("TASK_HISTORY_LIMIT", "100")))
ALLOWED_MEDIA_SUFFIXES = {
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
}
WHISPER_MODELS = set(WHISPER_MODEL_REPOS)
ASR_MODELS = WHISPER_MODELS | {"paraformer-zh"}
DOUYIN_HINT = (
    "抖音链接解析失败。可以先点击“打开抖音浏览器”，在本机窗口完成登录/验证后重试；"
    "也可能是媒体地址已过期，请重新提交链接。"
)
NOTE_STEP_NAME = "生成笔记"
API_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")
# 网关报错常把整个请求头或 JSON 回显出来；只认 sk- 前缀会漏掉 Bearer 与非 sk- 格式的凭据。
# 字段名用 \b 框住，否则 "tokenizer.json" 会被当成 "token" + 值咬掉。
SECRET_ECHO_PATTERN = re.compile(
    r"\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|bearer|sessdata|bili_jct)"
    r"\b([\"'’：:=\s]{1,4})([A-Za-z0-9_\-\.]{8,})",
    re.IGNORECASE,
)
# 历史笔记里写的是 ./images/，而磁盘上一直是 frames/；两种都要认，
# 否则历史任务的笔记重新导出时仍然带不回截图。
NOTE_IMAGE_REF_RE = re.compile(
    rf"(!\[[^\]]*\]\()(?:\./)?(?:{FRAMES_DIR_NAME}|images)/([^)]+)\)"
)

app = FastAPI(title="AI Video Note API", version="1.0.1")


def is_loopback_client(host: str | None) -> bool:
    if not host or host == "testclient":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() == "localhost"
    if address.is_loopback:
        return True
    return bool(address.version == 6 and address.ipv4_mapped and address.ipv4_mapped.is_loopback)


SECURITY_HEADERS = {
    # style-src 放开 inline style：KaTeX 产物的上下标/矩阵定位全靠 inline style 属性。
    # 模型产出的 HTML 仍被前端 DOMPurify 剥掉 style（FORBID_ATTR），放行的只有
    # 本机 KaTeX 渲染结果。注意 CSP 的 hash 与 'unsafe-inline' 并存时后者会被
    # 忽略，必须整体替换；此处与 frontend/index.html 的 meta 是两份独立生效的
    # CSP，改一处就要同步改另一处。
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class LocalSecurityMiddleware:
    """纯 ASGI 中间件：只允许本机回环客户端访问，并为响应附加安全头。

    不用 Starlette 的 BaseHTTPMiddleware（@app.middleware("http")）：
    它对流式响应（如 /mcp/sse 的 SSE 长连接）会在转发响应体时遇到第二次
    http.response.start 而触发 AssertionError，导致 MCP 会话请求直接崩溃；
    这里直接包装 ASGI 应用，流式响应可以原样透传。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_host = client[0] if isinstance(client, (tuple, list)) and client else None
        from .local_security import request_origin_error
        rejection = ("AI Video Note 仅允许本机访问" if not is_loopback_client(client_host)
                     else request_origin_error(scope))
        if rejection:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = json.dumps(
                {"detail": rejection}, ensure_ascii=False
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("latin-1")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def wrapped_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                existing = {key.lower() for key, _value in headers}
                for key, value in SECURITY_HEADERS.items():
                    if key.lower() not in existing:
                        headers.append((key.encode("latin-1"), value.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, wrapped_send)


from .mcp_activity import McpActivityMiddleware, activity as mcp_activity
app.add_middleware(McpActivityMiddleware)
from .skill_activity import SkillActivityMiddleware, activity as skill_activity
app.add_middleware(SkillActivityMiddleware)
app.add_middleware(LocalSecurityMiddleware)

from .browser_lifecycle import BrowserSessions
browser_sessions = BrowserSessions()


async def shutdown_application(callback):
    model_downloads.pause()
    if model_downloads.thread is not None:
        await asyncio.to_thread(model_downloads.thread.join, 3)
    for task in tasks.values():
        task['_cancel_requested'] = True
        event = task.get("_cancel_event")
        if event is not None:
            event.set()
    from . import agent_auto
    managed_jobs = set(running_jobs.values()) | set(agent_auto.workers.values())
    for job in managed_jobs:
        job.cancel()
    pending_jobs = set(_active_transcriptions) | managed_jobs
    if pending_jobs:
        await asyncio.wait(pending_jobs, timeout=3)
    await asr_process.stop_workers()
    callback()


def register_browser_exit(callback):
    # Legacy opt-in only. Normal launcher shutdown is controlled by the tray.
    async def stop_application():
        await shutdown_application(callback)
    browser_sessions.on_empty = stop_application


@app.websocket("/api/browser-session")
async def browser_session(socket: WebSocket):
    origin = urlsplit(socket.headers.get("origin", ""))
    if (not is_loopback_client(socket.client.host if socket.client else None)
            or origin.scheme not in {"http", "https"}
            or origin.netloc != socket.headers.get("host")):
        await socket.close(code=1008)
        return
    await socket.accept()
    client = uuid.uuid4().hex
    browser_sessions.connect(client)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        browser_sessions.disconnect(client)



_STARTED_MONO = time.monotonic()
_LAST_BACKEND_ERRORS: deque[str] = deque(maxlen=5)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """没兜住的异常一律回 JSON，而不是 uvicorn 那句纯文本 Internal Server Error。

    前端 `extractErrorMessage` 只在响应是 JSON 时才取得到原因，裸 500 会退化成
    "测试请求失败（HTTP 500）"——群里报障时既分不清是后端崩了、还是端口被别的
    服务占着，也拿不到任何线索。traceback 由 Starlette 原样继续上抛，仍会进
    `_app.log`；最近几条（脱敏后）留在 `_LAST_BACKEND_ERRORS`，`/api/diagnostics`
    会带出来当悬案线索。
    """
    try:
        reason = utf8_safe(f"{type(exc).__name__}: {exc}")[:300]
        reason = redact_secrets(reason)
        _LAST_BACKEND_ERRORS.append(f"{request.url.path}: {reason}")
    except Exception:
        # 兜错误的兜底自己也不能再炸：炸了就把话说到"有异常但构造不出说明"为止
        reason = "后端内部错误（构造错误说明时又出了岔子，详情见本机 _app.log）"
    return JSONResponse(
        status_code=500,
        content={"detail": f"后端内部错误：{reason}（详情见本机 _app.log）"},
    )


FRONTEND_MISSING_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AI Video Note 界面没加载出来</title></head>
<body style="font-family:system-ui,sans-serif;max-width:34em;margin:14vh auto;padding:0 1.2em;line-height:1.9">
<h1>界面没加载出来</h1>
<p><strong>这样做就好：</strong>在右下角任务栏找到 AI Video Note 图标，右键选「退出」，
再双击打开程序。找不到那个图标的话，直接再双击一次程序也行。</p>
<p>要是反复出现，请把程序目录里 <code>workspace\\_app.log</code> 发给作者，
并说一下你地址栏里的端口号（这台机器上是 __PORT__）。</p>
<p style="color:#666">程序本身在正常运行，只是界面文件没读到——不是端口被别的软件占了。</p>
</body></html>
"""


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """从浏览器地址栏打开的 404 说人话；/api 与 /mcp 仍按原样回 JSON。

    打包版是 onefile，运行时把前端解压到临时目录。那个目录一旦被安全软件或磁盘清理
    删掉，`/api/*` 全部正常而 `/` 只剩一句 `{"detail":"Not Found"}`——连排查的人都会
    误判成端口问题，用户只能靠重启软件碰运气（重启会重新解压，所以"重启就好了"）。
    """
    if exc.status_code == 404 and not request.url.path.startswith(("/api", "/mcp")):
        port = (request.scope.get("server") or (None, None))[1]
        return HTMLResponse(
            status_code=404,
            content=FRONTEND_MISSING_HTML.replace("__PORT__", str(port or "未知")),
        )
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
    )


video_processor = VideoProcessor(WORKSPACE_DIR)
transcriber = WhisperTranscriber(WHISPER_CACHE_DIR)
paraformer_transcriber = ParaformerTranscriber(WHISPER_CACHE_DIR)
_active_transcriptions: set[asyncio.Task[Any]] = set()


def _transcription_finished(job: asyncio.Task[Any]) -> None:
    _active_transcriptions.discard(job)
    # A cancelled parent no longer awaits this worker; consume its terminal exception.
    if not job.cancelled():
        job.exception()


async def run_transcription(
    media_path: Path,
    model_name: str,
    use_gpu: bool,
    title: str | None,
    cancel_event: Any,
    progress_callback: Any = None,
    gpu_backend: str = "cuda",
) -> dict:
    """转写入口：打包版交给工人子进程，源码版在原进程里跑。

    CTranslate2 / sherpa-onnx 在打包环境里出过一次访问违例（崩的是整个进程，
    托盘和界面一起没，日志一个字都没留下），所以正式产物里的转写必须死在外头。
    """
    options = {"gpu_backend": gpu_backend} if use_gpu and gpu_backend != "cuda" else {}
    if asr_process.use_child_process() or options or os.environ.get("AI_VIDEO_NOTE_MANAGED_SERVER") == "1" or browser_sessions.on_empty is not None:
        operation = asr_process.run_in_child(
            media_path, model_name, use_gpu, title, cancel_event, progress_callback, **options
        )
    else:
        operation = run_transcription_local(
            media_path, model_name, use_gpu, title, cancel_event, progress_callback, **options
        )
    job = asyncio.create_task(operation)
    _active_transcriptions.add(job)
    job.add_done_callback(_transcription_finished)
    # Cancellation can finish the UI task before its native worker stops. Keep tracking
    # that worker so files cannot be removed or the model directory switched meanwhile.
    return await asyncio.shield(job)


async def _vulkan_or_cpu(media_path, model_name, backend, title, cancel_event, progress_callback):
    try:
        return await whisper_vulkan.transcribe(WHISPER_CACHE_DIR, media_path, model_name,
            backend, title, cancel_event, progress_callback)
    except (RuntimeError, OSError) as error:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("转写已取消") from error
        result = await transcriber.transcribe(media_path, model_name, False,
            initial_prompt=title, cancel_event=cancel_event, progress_callback=progress_callback)
        result["fallback_note"] = f"Vulkan 加速未能启动，已使用 CPU；请检查运行库和显卡驱动：{error}"
        return result


async def run_transcription_local(
    media_path: Path,
    model_name: str,
    use_gpu: bool,
    title: str | None,
    cancel_event: Any,
    progress_callback: Any = None,
    gpu_backend: str = "cuda",
) -> dict:
    """按所选模型路由转写引擎：paraformer-zh 走 sherpa-onnx，其余走 faster-whisper。

    progress_callback(已转写秒数, 总秒数) 仅 whisper 路径支持（paraformer
    一次性出结果，且 RTF ≈ 0.05 本来就不需要心跳）。
    """
    if use_gpu and gpu_backend == "auto":
        effective = await resolve_backend("auto", model_name)
        if effective == "cuda":
            from .whisper_asr import CudaUnavailableError
            try:
                return await transcriber.transcribe(media_path, model_name, True,
                    initial_prompt=title, cancel_event=cancel_event, progress_callback=progress_callback,
                    allow_cpu_fallback=False)
            except CudaUnavailableError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("转写已取消") from exc
                if model_name in whisper_vulkan.MODELS:
                    result = await _vulkan_or_cpu(media_path, model_name, "vulkan", title, cancel_event, progress_callback)
                    message = f"CUDA 不可用，自动改用 Vulkan：{exc}"
                    result["fallback_note"] = message + (f"；{result['fallback_note']}" if result.get("fallback_note") else "")
                    return result
                result = await transcriber.transcribe(media_path, model_name, False,
                    initial_prompt=title, cancel_event=cancel_event, progress_callback=progress_callback)
                result["fallback_note"] = f"CUDA 不可用，此中文模型暂不支持 Vulkan，已使用 CPU：{exc}"
                return result
        if effective == "cpu":
            return await run_transcription_local(media_path, model_name, False, title, cancel_event, progress_callback)
        return await _vulkan_or_cpu(media_path, model_name, effective, title, cancel_event, progress_callback)
    if use_gpu and gpu_backend != "cuda":
        return await whisper_vulkan.transcribe(WHISPER_CACHE_DIR, media_path, model_name, gpu_backend, title, cancel_event, progress_callback)
    if model_name == "paraformer-zh":
        return await paraformer_transcriber.transcribe(
            media_path, model_name, use_gpu, initial_prompt=title, cancel_event=cancel_event
        )
    return await transcriber.transcribe(
        media_path,
        model_name,
        use_gpu,
        initial_prompt=title,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
    )


def transcribe_progress_reporter(
    task: dict[str, Any], model_name: str, page_label: str = ""
) -> Any:
    """转写心跳回调：工作线程里只经 call_soon_threadsafe 回主循环改任务状态。

    set_progress 会写任务字典并落盘运行时状态，不能在转写线程里直接调。
    """
    loop = asyncio.get_running_loop()

    def _clock(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def report(position: float, duration: float) -> None:
        total = _clock(duration) if duration > 0 else "未知时长"
        message = (
            f"正在转写{page_label}音频（{model_name}）："
            f"已推进到 {_clock(position)} / {total}"
        )
        loop.call_soon_threadsafe(set_progress, task, 4, "语音转写", 38, message)

    return report


bili_login_manager = BiliLoginManager(WORKSPACE_DIR)
douyin_login_manager = DouyinLoginManager(WORKSPACE_DIR)
config_store = ConfigStore(WORKSPACE_DIR)
tasks: dict[str, dict[str, Any]] = {}
running_jobs: dict[str, asyncio.Task[None]] = {}
_storage_cleaning = False
task_slots = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


class BilibiliCookie(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class DouyinCookie(BaseModel):
    """浏览器验证后短暂回传的抖音会话字段；不写入任务清单。"""

    model_config = {"extra": "allow"}


class LLMConfigPayload(BaseModel):
    model_type: str = "deepseek"
    api_key: SecretStr
    base_url: str | None = None
    model: str | None = None
    label: str | None = None


class LLMKeyPayload(BaseModel):
    """按接口地址保存一把 API Key：provider + base_url 决定它属于哪个端点。"""

    api_key: SecretStr
    provider: str = "deepseek"
    base_url: str | None = None
    label: str | None = None
    model: str | None = None


class LLMTestPayload(BaseModel):
    """测试连接的可选覆盖参数。

    字段不再与已保存配置逐项混合（那会把一个端点的 Key 配到另一个端点上），
    只有 API Key 会在地址完全一致时复用本机已存的那一把。
    """

    model_type: str | None = None
    api_key: SecretStr | None = None
    base_url: str | None = None
    model: str | None = None


class BiliCredentialsPayload(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class LLMConfig(BaseModel):
    model_type: str = "deepseek"
    api_key: SecretStr = SecretStr("")
    base_url: str | None = None
    model: str | None = None
    custom_base_url: str | None = None
    custom_model_name: str | None = None


class ObsidianExportPayload(BaseModel):
    vault: str = Field(max_length=4096)
    note_folder: str = Field(default="", max_length=1024)
    attachment_mode: Literal["vault_root", "note_folder", "note_subfolder", "specified_folder"] = "note_subfolder"
    attachment_folder: str = Field(default="attachments", max_length=1024)


class SummarizeRequest(AgentModelSettings):
    # Source media reuse and prose reuse are separate decisions.
    generation_action: Literal["continue", "regenerate"] = "regenerate"
    video_url: str = ""
    upload_task_id: str | None = None
    resume_task_id: str | None = None
    processing_mode: Literal["reuse", "restart"] = "reuse"
    # transcript: ASR plus AI proofreading; no note generation
    output: Literal["note", "transcript"] = "note"
    generation_mode: Literal["api", "agent"] = "api"
    agent_execution: Literal["manual", "auto"] = "manual"
    agent_provider: Literal["codex", "claude", "antigravity", "qwen", "custom"] = "codex"
    agent_command: list[str] = Field(default_factory=list, max_length=30)
    note_language: Literal["zh-CN", "en"] = "zh-CN"
    ai_commentary: bool = False
    ai_commentary_position: Literal["none", "start", "end"] | None = None
    summary_style: Literal["detailed", "faithful", "concise", "obsidian", "learning", "tutorial", "cards", "outline", "meeting"] = "detailed"
    @field_validator('summary_style', mode='before')
    @classmethod
    def migrate_retired_style(cls, value):
        return 'faithful' if value == 'science' else value

    note_format: Literal["markdown", "obsidian", "docx", "html", "json", "txt", "png", "pdf", "enex"] = "markdown"
    obsidian_tags: bool = False
    obsidian_callouts: bool = False
    obsidian_properties: bool = True
    custom_prompt: str = Field(default="", max_length=8000)
    note_save_directory: str = Field(default="", max_length=2048)
    obsidian: ObsidianExportPayload | None = None
    reasoning_effort: Literal["auto", "off", "high", "max"] = "auto"
    prefer_subtitles: bool = True
    include_screenshots: bool = False
    # Legacy clients may still send interval; AI selection never uses it.
    screenshot_interval: int = Field(default=30, ge=1, le=86400)
    vision_screenshots: bool = False
    auto_crop_screenshots: bool = False
    timed_screenshots: bool = False
    timeline_mode: Literal["none", "heading", "paragraph_start", "paragraph_end", "all"] = "all"
    source_link_position: Literal["none", "start", "end"] = "end"
    text_detail: Literal["brief", "standard", "detailed"] = "detailed"
    screenshot_density: Literal["none", "few", "medium", "many"] | None = None
    video_resolution: Literal[360, 480, 720, 1080] = 720
    video_encoding: Literal['compatibility','size'] = 'compatibility'
    video_download_directory: str = Field(default="", max_length=2048)
    whisper_model: str = "base"
    use_gpu: bool = False
    gpu_backend: Literal["auto", "cuda", "amd", "intel", "vulkan"] = "cuda"
    # B 站分 P 显式选择（页码从 1 起）；None/空 = 按 URL ?p=N 规则（缺省全部）
    bilibili_pages: list[int] | None = None
    bilibili_cookie: BilibiliCookie | None = None
    douyin_cookie: DouyinCookie | None = None
    llm_config: LLMConfig


class TranscribeRequest(AgentModelSettings):
    """Transcription plus AI proofreading, using the selected API or Agent."""
    llm_config: LLMConfig = Field(default_factory=LLMConfig)
    generation_mode: Literal["api", "agent"] = "api"
    agent_execution: Literal["manual", "auto"] = "manual"
    agent_provider: Literal["codex", "claude", "antigravity", "qwen", "custom"] = "codex"
    agent_command: list[str] = Field(default_factory=list, max_length=30)
    note_language: Literal["zh-CN", "en"] = "zh-CN"

    video_url: str = ""
    upload_task_id: str | None = None
    resume_task_id: str | None = None
    processing_mode: Literal["reuse", "restart"] = "reuse"
    prefer_subtitles: bool = True
    whisper_model: str = "base"
    use_gpu: bool = False
    gpu_backend: Literal["auto", "cuda", "amd", "intel", "vulkan"] = "cuda"
    # B 站分 P 显式选择（页码从 1 起）；None/空 = 按 URL ?p=N 规则（缺省全部）
    bilibili_pages: list[int] | None = None
    bilibili_cookie: BilibiliCookie | None = None
    douyin_cookie: DouyinCookie | None = None


class BiliPagesRequest(BaseModel):
    """`POST /api/bili-pages` 的请求体：提交前预览 B 站分 P 清单。"""

    video_url: str
    bilibili_cookie: BilibiliCookie | None = None


def new_task(status: str = "pending", task_id: str | None = None) -> dict[str, Any]:
    from .task_runtime import new_task as operation
    return operation(status, task_id)


def task_elapsed_seconds(task: dict[str, Any]) -> float:
    from .task_runtime import task_elapsed_seconds as operation
    return operation(task)


def finish_task_timing(task: dict[str, Any]) -> float:
    from .task_runtime import finish_task_timing as operation
    return operation(task)


def set_progress(
    task: dict[str, Any], step: int, name: str, progress: int, message: str
) -> None:
    # Worker reports are scheduled on the event loop and can arrive after cancel
    # or completion. Do not let them overwrite the final/cancelling step or log.
    if task.get("status") in {"completed", "failed", "cancelled", "cancelling"} or task.get("_cancel_requested") or task.get("_deleting"):
        return
    task.update(step=step, step_name=name, progress=progress, progress_message=message)
    task["logs"].append(message)
    task_id = task.get("_task_id")
    if task_id:
        persist_task_runtime(str(task_id))


def task_directory(task_id: str) -> Path:
    from .task_storage import directory
    return directory(WORKSPACE_DIR, task_id)


def persist_task_runtime(task_id: str) -> None:
    from .task_runtime import persist_task_runtime as persist
    persist(task_id, tasks, task_directory, task_elapsed_seconds)


def raise_if_cancel_requested(task: dict[str, Any]) -> None:
    if task.get("_cancel_requested"):
        raise asyncio.CancelledError


# 任务结果系统通知：由启动器把托盘气泡接进来（打包模式才有效），
# backend 不直接依赖 pystray，避免引入循环依赖。
_task_notify_hook: Callable[[str, str], None] | None = None


def register_task_notify(callback: Callable[[str, str], None]) -> None:
    """注册通知回调：callback(title, message)。"""
    global _task_notify_hook
    _task_notify_hook = callback


def notify_task(title: str, message: str) -> None:
    """发送系统通知；异步执行且绝不影响任务主流程。"""
    callback = _task_notify_hook
    if callback is None:
        return

    def _run() -> None:
        try:
            callback(title, message)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def normalize_source_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/")
    query = "" if hostname == "bilibili.com" or hostname.endswith(".bilibili.com") else parsed.query
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def source_url_from_task(task_dir: Path) -> str | None:
    manifest_path = task_dir / "task.json"
    if manifest_path.is_file():
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8")).get("source_url")
            if value:
                return str(value)
        except (OSError, ValueError, TypeError):
            pass

    notes_path = task_dir / "notes.md"
    if notes_path.is_file():
        try:
            for line in reversed(notes_path.read_text(encoding="utf-8").splitlines()):
                if line.startswith("来源："):
                    return line.removeprefix("来源：").strip()
        except (OSError, UnicodeError):
            pass
    return None


def find_reusable_task(source_url: str, bilibili_pages: list[int] | None = None) -> str | None:
    normalized_source = normalize_source_url(source_url)
    candidates = sorted(
        (path for path in task_storage.directories(WORKSPACE_DIR) if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if tasks.get(candidate.name, {}).get("_deleting"):
            continue
        if not (candidate / "transcript.json").is_file():
            continue
        existing_source = source_url_from_task(candidate)
        if (existing_source and normalize_source_url(existing_source) == normalized_source
                and bilibili_pages_match(source_url, bilibili_pages, existing_source,
                                         task_storage.read_manifest(candidate))):
            return candidate.name
    return None


def reuse_preview_video(request: SummarizeRequest, task_id: str, source_url: str) -> Path | None:
    """Find an intact cached video across matching tasks, independent of note style."""
    from .video_cache import cache_directory, cache_lock, find_cached, inspect_video, record_cache
    root = Path(request.video_download_directory).expanduser() if request.video_download_directory.strip() else WORKSPACE_DIR / "video"
    destination = cache_directory(root, source_url, request.video_resolution, request.video_encoding)
    cached = find_cached(destination, WORKSPACE_DIR, request.video_encoding)
    if cached:
        return cached
    normalized = normalize_source_url(source_url)
    for directory in sorted((p for p in task_storage.directories(WORKSPACE_DIR) if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        if directory.name == task_id or tasks.get(directory.name, {}).get("_deleting"):
            continue
        manifest = read_task_manifest(directory.name)
        old_source = manifest.get("source_url")
        if manifest.get('video_encoding','compatibility') != request.video_encoding:
            continue
        if not old_source or normalize_source_url(old_source) != normalized:
            continue
        if cache_directory(root, old_source, request.video_resolution, request.video_encoding) != destination:
            continue
        if sorted(manifest.get("bilibili_pages") or []) != sorted(request.bilibili_pages or []):
            continue
        result = (manifest.get("runtime") or {}).get("result") or {}
        # A new quality selection may require a different download; old tasks record the limit.
        if (result.get("video_resolution") or manifest.get("video_resolution")) != request.video_resolution:
            continue
        recorded = result.get("video_path") or (manifest.get("runtime") or {}).get("video_path")
        candidates = [Path(recorded)] if recorded else list(directory.glob("preview.*"))
        for source in candidates:
            if source.suffix.lower() not in {".mp4", ".mkv", ".webm", ".m4s", ".mov", ".avi"}:
                continue
            try:
                if not source.is_file() or source.stat().st_size == 0:
                    continue
                if not inspect_video(source):
                    continue  # Legacy silent screenshot downloads need one repaired download.
                from .video_codecs import matches_policy
                if not matches_policy(source,request.video_encoding):
                    continue
                with cache_lock(destination):
                    cached = find_cached(destination, WORKSPACE_DIR, request.video_encoding)
                    if cached: return cached
                    destination.mkdir(parents=True, exist_ok=True)
                    target = destination / ("preview" + source.suffix)
                    shutil.copy2(source, target)
                    return record_cache(destination, target, True, source_url=source_url, title=manifest.get("title", ""), height=request.video_resolution, storage_id=directory.name, workspace=WORKSPACE_DIR)
            except (OSError, ValueError):
                continue
    return None


def load_transcript_result(task_dir: Path) -> dict[str, Any] | None:
    path = task_dir / "transcript.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not valid_transcript(payload):
            return None
        segments = [
            TranscriptSegment(float(item["start"]), float(item["end"]), str(item["text"]))
            for item in payload.get("segments", [])
            if str(item.get("text", "")).strip()
        ]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not segments:
        return None
    return {
        "segments": segments,
        "language": payload.get("language") or "unknown",
        "source": payload.get("source") or "reused_transcript",
        "quality": payload.get("quality"),
        **{key: payload[key] for key in ("asr_recovery", "asr_recovery_pages", "subtitle_policy") if payload.get(key)},
    }


def load_reused_video_info(
    task_dir: Path,
    transcript_result: dict[str, Any],
    source_url: str | None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    manifest_path = task_dir / "task.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass

    title = str(manifest.get("title") or "").strip()
    if not title:
        notes_path = task_dir / "notes.md"
        try:
            first_line = notes_path.read_text(encoding="utf-8").splitlines()[0]
            if first_line.startswith("# 视频笔记：《") and first_line.endswith("》"):
                title = first_line.removeprefix("# 视频笔记：《")[:-1]
        except (OSError, UnicodeError, IndexError):
            pass
    if not title:
        title = f"已恢复任务 {task_dir.name}"

    segments: list[TranscriptSegment] = transcript_result["segments"]
    duration = float(manifest.get("duration") or max(segment.end for segment in segments))
    if source_url:
        source = video_processor.detect_source(source_url).value
    else:
        source = VideoSource.LOCAL.value
    return {
        "title": title,
        "source": source,
        "duration": duration,
        "owner": str(manifest.get("owner") or ""),
        "upload_date": str(manifest.get("upload_date") or ""),
        "timestamp": manifest.get("timestamp") or 0,
        "view_count": manifest.get("view_count") or 0,
        "like_count": manifest.get("like_count") or 0,
        "bili_pages": manifest.get("bili_pages"),
        "bili_total_pages": manifest.get("bili_total_pages"),
        "description": "",
    }


def update_task_manifest(task_id: str, info: dict[str, Any]) -> None:
    def update(payload):
        payload.setdefault("task_id", task_id)
        payload.update(
            title=info.get("title"),
            owner=info.get("owner"),
            duration=info.get("duration"),
            source=info.get("source"),
            upload_date=info.get("upload_date"),
            timestamp=info.get("timestamp"),
            view_count=info.get("view_count"),
            like_count=info.get("like_count"),
        )
        # Missing page information must not erase a previous multi-page selection.
        for key in ("bili_pages", "bili_total_pages"):
            if info.get(key) is not None:
                payload[key] = info[key]
    task_storage.update_manifest(task_directory(task_id), update)


def read_task_manifest(task_id: str) -> dict[str, Any]:
    return task_storage.read_manifest(task_directory(task_id))


def write_task_manifest(
    task_id: str,
    request: SummarizeRequest,
    reused_task_id: str | None,
    origin: str,
) -> None:
    uploaded_filename = tasks.get(task_id, {}).get("uploaded_filename")
    payload = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": request.video_url if request.video_url and not uploaded_filename else None,
        "normalized_source_url": (
            normalize_source_url(request.video_url)
            if request.video_url and not uploaded_filename
            else None
        ),
        "uploaded_filename": uploaded_filename,
        "processing_mode": request.processing_mode,
        "generation_action": request.generation_action,
        "output": request.output,
        # 纯转录任务也进入网页历史，可以查看与下载字幕稿。
        "origin": origin,
        "summary_style": request.summary_style,
        "ai_commentary": request.ai_commentary,
        "ai_commentary_position": request.ai_commentary_position,
        "note_language": request.note_language,
        "note_format": request.note_format,
        "generation_mode": request.generation_mode,
        "obsidian_tags": request.obsidian_tags,
        "obsidian_callouts": request.obsidian_callouts,
        "obsidian_properties": request.obsidian_properties,
        "timeline_mode": request.timeline_mode,
        "text_detail": request.text_detail,
        "source_link_position": request.source_link_position,
        "video_download_directory": request.video_download_directory,
        "note_save_directory": request.note_save_directory,
        "video_resolution": request.video_resolution,
        "video_encoding": request.video_encoding,
        "reasoning_effort": request.reasoning_effort,
        "reused_task_id": reused_task_id,
        # 分 P 勾选随任务落盘：同一视频重新处理时，/bili-pages 用它预勾上次的范围
        "bilibili_pages": getattr(request, "bilibili_pages", None),
    }
    task_storage.update_manifest(task_directory(task_id), lambda current: current.update(payload))


def write_upload_manifest(task_id: str, filename: str) -> None:
    payload = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": None,
        "uploaded_filename": filename,
    }
    task_storage.update_manifest(task_directory(task_id), lambda current: current.update(payload))


async def start_summarize(request: SummarizeRequest) -> dict[str, str | None]:
    return await submit_video_task(request, origin="web")


async def start_transcribe(request: TranscribeRequest) -> dict[str, str | None]:
    """Produce timestamped subtitles after AI proofreading and confirmation."""
    return await submit_video_task(
        SummarizeRequest(
            **request.model_dump(), output="transcript"
        ),
        origin="agent",
    )


async def submit_video_task(
    request: SummarizeRequest, origin: str
) -> dict[str, str | None]:
    if _storage_cleaning:
        raise HTTPException(409, "缓存清理中，请稍后再提交任务")
    if model_downloads.active() or environment_repair.snapshot()["status"] == "running":
        raise HTTPException(409, "请先暂停模型下载或等待环境修复完成后再生成笔记")
    if request.output == "note" and (request.summary_style == "obsidian" or request.obsidian is not None):
        if request.obsidian is None:
            raise HTTPException(status_code=422, detail="请填写 Obsidian 仓库和保存设置")
        try:
            obsidian_export.resolve_folders(**request.obsidian.model_dump())
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.whisper_model not in ASR_MODELS:
        raise HTTPException(status_code=422, detail="不支持的转写模型")
    if request.use_gpu and request.gpu_backend not in {"auto", "cuda"} and request.whisper_model not in whisper_vulkan.MODELS:
        raise HTTPException(status_code=422, detail="AMD/Intel 加速请选择 Whisper 多语言或英语模型；中文专用模型可关闭加速运行")
    uploaded_task = None
    if request.upload_task_id:
        # 本地上传的媒体位置记在上传任务上；此时 video_url 是后端目录里的文件路径，
        # 不是链接，不能进链接校验（否则必 422）。
        uploaded_task = tasks.get(request.upload_task_id)
        if not uploaded_task or uploaded_task.get("status") != "uploaded":
            raise HTTPException(status_code=400, detail="上传任务不存在或已经处理")
        request.video_url = ""
    elif request.video_url:
        normalized = normalize_video_input(request.video_url)
        if not normalized:
            raise HTTPException(
                status_code=422,
                detail="无法识别视频链接：支持 http(s) 链接，或包含 B 站/抖音链接的分享文本",
            )
        request.video_url = normalized
    wants_video = request.output == "note" and (request.timed_screenshots or (request.screenshot_density != "none" if request.screenshot_density else request.include_screenshots))
    if wants_video and not request.upload_task_id and request.video_encoding == 'size':
        from .video_codecs import capabilities
        if not (await capabilities())['available']:
            raise HTTPException(400,'未验证到可用的 HEVC 硬件编解码，请使用兼容性优先并检查显卡驱动')
    if request.note_save_directory.strip():
        directory = Path(request.note_save_directory.strip()).expanduser()
        if not directory.is_absolute():
            raise HTTPException(400, "笔记保存位置必须是文件夹绝对路径")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            import tempfile
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as exc:
            raise HTTPException(400, f"笔记保存位置不可写：{exc}") from exc
        request.note_save_directory = str(directory.resolve())
    if wants_video and not request.upload_task_id and request.video_download_directory.strip():
        directory = Path(request.video_download_directory.strip()).expanduser()
        if not directory.is_absolute():
            raise HTTPException(status_code=422, detail="视频下载位置请填写完整绝对路径")
        if directory.exists() and not directory.is_dir():
            raise HTTPException(status_code=422, detail="视频下载位置必须是文件夹")
        request.video_download_directory = str(directory.resolve())
    reused_task_id = request.resume_task_id
    if request.processing_mode == "restart":
        reused_task_id = None
    else:
        if reused_task_id:
            if tasks.get(reused_task_id, {}).get("_deleting"):
                raise HTTPException(status_code=409, detail="该任务正在删除，请稍后重试")
            resume_dir = task_directory(reused_task_id)
            if not resume_dir.is_dir():
                raise HTTPException(status_code=404, detail="要继续的任务目录不存在")
            previous_source = source_url_from_task(resume_dir)
            previous_manifest = read_task_manifest(reused_task_id)
            if previous_source:
                if not request.video_url:
                    request.video_url = previous_source
                    if not request.bilibili_pages:
                        request.bilibili_pages = previous_manifest.get("bilibili_pages")
                if (normalize_source_url(request.video_url) != normalize_source_url(previous_source)
                        or not bilibili_pages_match(request.video_url, request.bilibili_pages,
                                                    previous_source, previous_manifest)):
                    reused_task_id = None
        if not reused_task_id and request.video_url:
            reused_task_id = find_reusable_task(request.video_url, request.bilibili_pages)

    if reused_task_id and tasks.get(reused_task_id, {}).get("_deleting"):
        raise HTTPException(status_code=409, detail="该任务正在删除，请稍后重试")
    if reused_task_id:
        resume_dir = task_directory(reused_task_id)
        if not resume_dir.is_dir():
            raise HTTPException(status_code=404, detail="要继续的任务目录不存在")

    if _storage_cleaning:
        raise HTTPException(409, "缓存清理中，请稍后再提交任务")
    task_id = request.upload_task_id or str(uuid.uuid4())
    if uploaded_task is not None:
        tasks[task_id] = new_task(task_id=task_id)
        tasks[task_id]["uploaded_file_path"] = uploaded_task.get("uploaded_file_path")
        tasks[task_id]["uploaded_filename"] = uploaded_task.get("uploaded_filename")
    else:
        if request.video_url:
            try:
                video_processor.detect_source(request.video_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        elif not reused_task_id:
            raise HTTPException(status_code=422, detail="请提供视频链接、上传文件或可继续的任务")
        tasks[task_id] = new_task(task_id=task_id)
        task_directory(task_id).mkdir(parents=True, exist_ok=False)

    tasks[task_id]["resume_task_id"] = reused_task_id
    tasks[task_id]["output"] = request.output
    tasks[task_id]["_origin"] = origin
    if reused_task_id:
        old_task = tasks.get(reused_task_id, {})
        if old_task.get("uploaded_file_path"):
            tasks[task_id]["uploaded_file_path"] = old_task["uploaded_file_path"]
        old_filename = old_task.get("uploaded_filename") or read_task_manifest(
            reused_task_id
        ).get("uploaded_filename")
        if old_filename:
            tasks[task_id]["uploaded_filename"] = old_filename
        tasks[task_id]["logs"].append(f"将从任务 {reused_task_id} 复用已有产物")
    write_task_manifest(task_id, request, reused_task_id, origin)
    persist_task_runtime(task_id)

    job = asyncio.create_task(run_queued_video_task(task_id, request))
    running_jobs[task_id] = job
    job.add_done_callback(lambda _job, current_id=task_id: running_jobs.pop(current_id, None))
    return {"task_id": task_id, "reused_task_id": reused_task_id}


def task_status_payload(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    from .task_queries import task_status_payload as query
    return query(sys.modules[__name__], task_id, task)


async def list_recent_tasks(q: str = "", status: str = "", output: str = "", start_date: str = "", end_date: str = "", utc_offset: int = 0, offset: int = 0, limit: int = 20):
    from .task_queries import list_recent_tasks as query
    return await query(sys.modules[__name__], q=q, status=status, output=output,
        start_date=start_date, end_date=end_date, utc_offset=utc_offset, offset=offset, limit=limit)


async def get_task_status(task_id: str) -> dict[str, Any]:
    from .task_queries import get_task_status as query
    return await query(sys.modules[__name__], task_id)


async def get_task_transcript(
    task_id: str, output_format: Literal["json", "markdown"] = "json"
) -> dict[str, Any]:
    from .exports_service import get_task_transcript as handler
    return await handler(sys.modules[__name__], task_id, output_format)


@app.get("/api/task/{task_id}/review")
async def get_note_review(task_id: str) -> dict[str, Any]:
    payload = await get_task_transcript(task_id)
    manifest = read_task_manifest(task_id)
    source = str(manifest.get("source_url") or "")
    media = find_media(task_id, WORKSPACE_DIR, tasks, read_task_manifest)
    pages = manifest.get("bili_pages") or []
    # Cached multi-page previews may contain just one page; use the original page links.
    if len(pages) > 1 and source: media = None
    segments = []
    for segment in payload["segments"]:
        link = replay_link(source, segment.get("start", 0), pages)
        segments.append({**segment, "replay_url": link["url"], "precise_jump": link["precise"]})
    return {
        **payload, "segments": segments,
        "source_url": replay_link(source, 0)["url"],
        "media_url": f"/api/task/{quote(task_id, safe='')}/media" if media else None,
        "media_kind": "audio" if media and media.suffix.lower() in {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma"} else "video",
    }


@app.get("/api/task/{task_id}/media")
async def get_task_media(task_id: str) -> FileResponse:
    try:
        directory = task_directory(task_id)
    except ValueError as exc:
        raise HTTPException(400, "非法任务 ID") from exc
    if not directory.is_dir(): raise HTTPException(404, "任务不存在")
    path = find_media(task_id, WORKSPACE_DIR, tasks, read_task_manifest)
    if path is None: raise HTTPException(404, "没有可回放的本地媒体，请打开原视频")
    # Starlette supports byte-range responses so the player can seek without downloading everything.
    return FileResponse(path)


class GpuRuntimeInstallRequest(BaseModel):
    model_config = {"extra": "forbid"}
    backend: Literal["auto", "cuda", "vulkan"] = "auto"


async def start_gpu_runtime_install(payload: GpuRuntimeInstallRequest, request: Request) -> dict[str, Any]:
    origin = request.headers.get("origin")
    if (origin and urlsplit(origin).netloc != request.headers.get("host")) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从本机软件界面主动发起安装")
    from .gpu_devices import detect
    selected = await detect(payload.backend)
    try:
        return gpu_runtime_installs.start(selected["backend"])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


async def get_gpu_runtime_install() -> dict[str, Any]:
    return gpu_runtime_installs.snapshot()


@app.get("/api/gpu-device")
async def gpu_device(backend: Literal["auto", "cuda", "amd", "intel", "vulkan"] = "vulkan") -> dict[str, Any]:
    from .gpu_devices import detect
    from .gpu_runtime import dependency_status
    result = await detect(backend)
    result["runtime"] = await asyncio.to_thread(dependency_status, result["backend"])
    return result


@app.get("/api/whisper-models")
async def whisper_models_status(gpu_backend: Literal["auto", "cuda", "amd", "intel", "vulkan"] = "cuda") -> dict[str, Any]:
    """返回各 Whisper 模型在本机的缓存状态（供前端标注下拉框）。"""
    requested_backend = gpu_backend
    gpu_backend = await resolve_backend(gpu_backend)
    models = [
        {
            "id": model_id,
            "status": whisper_vulkan.cache_status(WHISPER_CACHE_DIR, model_id) if gpu_backend not in {"cuda", "cpu"} else transcriber._model_cache_status(model_id),
        }
        for model_id in sorted(WHISPER_MODELS)
    ]
    models.append({"id": "paraformer-zh", "status": "unsupported" if gpu_backend != "cuda" and requested_backend != "auto" else paraformer_transcriber.cache_status()})
    for item in models:
        model = item["id"]
        ct2_status = paraformer_transcriber.cache_status() if model == "paraformer-zh" else transcriber._model_cache_status(model)
        vulkan_status = whisper_vulkan.cache_status(WHISPER_CACHE_DIR, model)
        statuses = {ct2_status, vulkan_status}
        item["installation_status"] = "cached" if "cached" in statuses else ("incomplete" if "incomplete" in statuses else "missing")
        item["compatible"] = item["status"] != "unsupported"
        item["formats"] = {"standard": ct2_status, "vulkan": vulkan_status}
        if model in whisper_vulkan.MODELS:
            item['vulkan_precisions']={kind:whisper_vulkan.cache_status(WHISPER_CACHE_DIR,model,kind) for kind in ('float16','q8_0')}
            if gpu_backend not in {'cuda','cpu'}:
                item['compute_type']=await preferred_vulkan_precision(model,gpu_backend)
                item['status']=item['vulkan_precisions'][item['compute_type']]

        if requested_backend == "auto":
            item["acceleration"] = "cpu" if model == "paraformer-zh" else gpu_backend
            item["acceleration_note"] = "此模型使用 CPU" if item["acceleration"] == "cpu" else ""
        directories = model_storage.model_directories(
            WHISPER_CACHE_DIR, model,
            transcriber._model_repo(model) if model in WHISPER_MODELS else None,
        )
        item["managed_paths"] = [str(path) for path in directories if path.exists()]
    return {
        "models": models, "manual_dir": str(WHISPER_CACHE_DIR / "manual"),
        "directory": str(WHISPER_CACHE_DIR),
        "default_directory": str(DEFAULT_WHISPER_CACHE_DIR),
        "resolved_backend": gpu_backend,
    }


class ModelDirectoryPayload(BaseModel):
    directory: str = Field(default="", max_length=4096)


def require_models_idle() -> None:
    if model_downloads.active() or environment_repair.snapshot()["status"] == "running":
        raise HTTPException(status_code=409, detail="请先暂停模型下载或等待环境修复完成")
    if any(not job.done() for job in _active_transcriptions) or any(task.get("status") in {"pending", "queued", "processing", "cancelling"}
           for task in tasks.values()) or any(not job.done() for job in running_jobs.values()):
        raise HTTPException(status_code=409, detail="请等待任务完成或取消后，再修改模型位置或删除模型")


class ModelDownloadPayload(BaseModel):
    model: str
    gpu_backend: Literal["auto", "cuda", "amd", "intel", "vulkan"] = "cuda"


@app.get("/api/model-download")
async def model_download_status():
    return model_downloads.snapshot()


@app.post("/api/model-download")
async def start_model_download(payload: ModelDownloadPayload):
    require_models_idle()
    if payload.model not in ASR_MODELS:
        raise HTTPException(422, "不支持的模型")
    backend = await resolve_backend(payload.gpu_backend, payload.model)
    backend = "cuda" if backend == "cpu" else backend
    if backend != "cuda" and payload.model not in whisper_vulkan.MODELS:
        raise HTTPException(422, "此模型不支持当前显卡模式，请关闭显卡加速后下载")
    try:
        options={'precision':await preferred_vulkan_precision(payload.model,backend)} if backend!='cuda' else {}
        return model_downloads.start(payload.model, backend, WHISPER_CACHE_DIR, transcriber, paraformer_transcriber,**options)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/model-download/pause")
async def pause_model_download():
    return model_downloads.pause()


@app.get("/api/environment")
async def environment_status():
    return {**await asyncio.to_thread(diagnose, BASE_DIR), "repair":environment_repair.snapshot()}


@app.post("/api/environment/repair")
async def repair_environment():
    require_models_idle()
    try:
        return environment_repair.start(BASE_DIR)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.put("/api/whisper-models/directory")
async def set_model_directory(payload: ModelDirectoryPayload) -> dict[str, Any]:
    global WHISPER_CACHE_DIR, transcriber, paraformer_transcriber
    require_models_idle()
    value = payload.directory.strip()
    directory = Path(value).expanduser() if value else DEFAULT_WHISPER_CACHE_DIR
    if not directory.is_absolute():
        raise HTTPException(status_code=422, detail="请输入模型文件夹的完整绝对路径")
    try:
        directory = directory.resolve()
        # Initialize both engines before saving or replacing the running configuration.
        next_whisper = WhisperTranscriber(directory)
        next_paraformer = ParaformerTranscriber(directory)
        probe = directory / f".write-test-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"")
        finally:
            probe.unlink(missing_ok=True)
        model_storage.save_directory(WORKSPACE_DIR, str(directory) if value else "")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"无法使用此模型目录：{exc}") from exc
    WHISPER_CACHE_DIR = directory
    transcriber, paraformer_transcriber = next_whisper, next_paraformer
    return await whisper_models_status()


@app.delete("/api/whisper-models/{model}")
async def delete_model(model: str) -> dict[str, Any]:
    if model not in ASR_MODELS:
        raise HTTPException(status_code=422, detail="不支持的转写模型")
    require_models_idle()
    directories = model_storage.model_directories(
        WHISPER_CACHE_DIR, model,
        transcriber._model_repo(model) if model in WHISPER_MODELS else None,
    )
    try:
        targets = model_storage.validate_deletion(WHISPER_CACHE_DIR, directories)
        # Drop loaded native models before deleting their files (especially on Windows).
        if model == "paraformer-zh":
            paraformer_transcriber._models = None
        else:
            transcriber._models.clear()
            transcriber._actual_models.clear()
        model_storage.delete_directories(targets)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"无法删除模型：{exc}") from exc
    return {"deleted_paths": [str(path) for path in targets], **await whisper_models_status()}


class WhisperManualFolderPayload(BaseModel):
    model: str
    open: bool = True
    gpu_backend: Literal["auto", "cuda", "amd", "intel", "vulkan"] = "cuda"


def _open_in_file_manager(path: Path) -> bool:
    """用系统文件管理器打开目录；失败时返回 False（前端展示路径兜底）。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


PARAFORMER_IMPORT_LABELS = {
    "asr": "转写主模型",
    "punc": "标点模型（可以不放：缺了会自动降级为无标点转写）",
    "vad": "语音活动检测",
}


@app.post("/api/whisper-models/manual-folder")
async def open_whisper_manual_folder(payload: WhisperManualFolderPayload) -> dict[str, Any]:
    """创建并返回手动导入模型的引导（大模型网络下载失败时的替代方案）。

    open=false 只给引导信息，供前端先展示步骤、用户确认后再开文件夹。
    一条模型可能由多个组件目录组成（paraformer-zh 是 asr / punc / vad 三份，
    而且和 faster-whisper 系根本不是一个缓存目录），所以引导统一返回 folders 列表。
    """
    if payload.model not in ASR_MODELS:
        raise HTTPException(status_code=422, detail="不支持的转写模型")

    backend = await resolve_backend(payload.gpu_backend, payload.model)
    if backend not in {"cuda", "cpu"}:
        if payload.model not in whisper_vulkan.MODELS:
            raise HTTPException(status_code=422, detail="此模型不支持 AMD/Intel 加速")
        directory = whisper_vulkan.model_directory(WHISPER_CACHE_DIR, payload.model)
        directory.mkdir(parents=True, exist_ok=True)
        folders = [{"label": "Vulkan 转写模型", "path": str(directory),
                    "files": [whisper_vulkan.MODELS[payload.model]],
                    "download_url": "https://huggingface.co/ggerganov/whisper.cpp/tree/main"}]
        open_path = directory
    elif payload.model == "paraformer-zh":
        folders = []
        for component, spec in paraformer_asr.MODEL_SPECS.items():
            directory = paraformer_transcriber._component_dir(component)
            directory.mkdir(parents=True, exist_ok=True)
            folders.append(
                {
                    "label": PARAFORMER_IMPORT_LABELS[component],
                    "path": str(directory),
                    "files": list(spec["files"]),
                    "download_url": f"https://hf-mirror.com/{spec['repo']}/tree/main",
                }
            )
        open_path = paraformer_transcriber.download_root
    else:
        manual_dir = WHISPER_CACHE_DIR / "manual" / payload.model
        manual_dir.mkdir(parents=True, exist_ok=True)
        folders = [
            {
                "label": "模型文件",
                "path": str(manual_dir),
                "files": transcriber.manual_import_files(payload.model),
                "download_url": f"https://hf-mirror.com/{transcriber._model_repo(payload.model)}/tree/main",
            }
        ]
        open_path = manual_dir

    return {
        "folders": folders,
        "open_path": str(open_path),
        "opened": _open_in_file_manager(open_path) if payload.open else False,
    }


def _key_storage_info() -> dict[str, Any]:
    return {"algorithm": secret_box.storage_backend(), "secure": secret_box.is_secure_storage()}


def resolve_llm_credentials(*, model_type: str, base_url: str | None, api_key: str) -> tuple[str, str]:
    """显式 Key 永远优先；否则只按接口地址复用本机已存 Key。

    地址不一致就直接失败，不会"先拿本机某把 Key 试试"——那正是把 DeepSeek 的
    Key 发给第三方网关的路径。返回 ``(api_key, 来源说明)``，来源说明进任务日志。
    """
    explicit = (api_key or "").strip()
    if explicit:
        return explicit, "本次请求提供"
    resolved = config_store.resolve_stored_key(model_type=model_type, base_url=base_url or "")
    secret = str(resolved.get("api_key") or "")
    if secret:
        entry = resolved.get("entry") or {}
        where = str(entry.get("label") or resolved.get("host") or "")
        masked = str(entry.get("api_key_masked") or "")
        return secret, f"本机已存 {masked}（{where}）"
    raise RuntimeError(_missing_key_message(model_type, base_url, resolved))


def _missing_key_message(model_type: str, base_url: str | None, resolved: dict[str, Any]) -> str:
    target = (base_url or "").strip() or default_base_url(model_type) or "未指定接口地址"
    reason = str(resolved.get("reason") or "")
    detail = {
        "undecryptable": (
            f"为 {target} 保存的 Key 无法解密（可能来自其他机器或其他 Windows 账户），"
            "请重新填写并保存"
        ),
        "corrupt_keys_file": (
            f"本机密钥文件 {LLM_KEYS_FILE} 已损坏，请在模型配置页清除本机配置后重新保存"
        ),
        "unknown_endpoint": f"接口地址无法识别（{target}），不会复用任何本机 Key",
    }.get(reason) or f"本机没有为 {target} 保存 Key"
    return (
        f"未提供 API Key，{detail}。"
        "请在模型配置页填写 API Key 并「保存到本机」，或在请求中直接带上 api_key。"
    )


async def get_llm_keys(base_url: str = "", provider: str = "") -> dict[str, Any]:
    from .settings_service import get_llm_keys as handler
    return await handler(sys.modules[__name__], base_url, provider)


async def put_llm_key(payload: LLMKeyPayload) -> dict[str, Any]:
    from .settings_service import put_llm_key as handler
    return await handler(sys.modules[__name__], payload)


async def delete_llm_key(base_url: str = "", provider: str = "") -> dict[str, Any]:
    from .settings_service import delete_llm_key as handler
    return await handler(sys.modules[__name__], base_url, provider)


async def get_llm_config() -> dict[str, Any]:
    from .settings_service import get_llm_config as handler
    return await handler(sys.modules[__name__])


async def save_llm_config(payload: LLMConfigPayload) -> dict[str, Any]:
    from .settings_service import save_llm_config as handler
    return await handler(sys.modules[__name__], payload)


async def check_llm_vision(body: LLMConfig, refresh: bool = False) -> dict[str, Any]:
    from .settings_service import check_llm_vision as handler
    return await handler(sys.modules[__name__], body, refresh)


async def test_llm_connection(
    payload: LLMTestPayload | None = None,
) -> dict[str, Any]:
    from .settings_service import test_llm_connection as handler
    return await handler(sys.modules[__name__], payload)


async def clear_llm_config() -> dict[str, Any]:
    from .settings_service import clear_llm_config as handler
    return await handler(sys.modules[__name__])


async def get_bili_credentials() -> dict[str, Any]:
    from .settings_service import get_bili_credentials as handler
    return await handler(sys.modules[__name__])


async def save_bili_credentials(payload: BiliCredentialsPayload) -> dict[str, Any]:
    from .settings_service import save_bili_credentials as handler
    return await handler(sys.modules[__name__], payload)


async def clear_bili_credentials() -> dict[str, bool]:
    from .settings_service import clear_bili_credentials as handler
    return await handler(sys.modules[__name__])


def previous_bili_pages(normalized_url: str) -> list[int] | None:
    """该视频最近一次任务显式勾选的分 P（新任务在前扫描，没有则 None）。

    重新处理多分 P 视频时，面板默认沿用上次的范围，不用重新勾一遍；
    只认显式下发过的 bilibili_pages（None 表示当时的「全部」，不预勾）。
    """
    for candidate in sorted(
        (path for path in task_storage.directories(WORKSPACE_DIR) if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        manifest_path = candidate / "task.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("normalized_source_url") != normalized_url:
            continue
        pages = manifest.get("bilibili_pages")
        if isinstance(pages, list) and pages:
            return [int(page) for page in pages]
    return None


@app.post("/api/bili-pages")
async def preview_bilibili_pages(request: BiliPagesRequest) -> dict[str, Any]:
    """提交任务前预览 B 站分 P 清单，让用户显式勾选要转写的分 P。

    只读 view 接口，不触发下载；凭据可选（公开接口一般无需登录，
    携带凭据可降低风控概率）。front 端拿 url 的 ?p= 自行决定默认勾选。
    """
    url = normalize_video_input(request.video_url) or request.video_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="请先粘贴 B 站视频链接")
    try:
        source = video_processor.detect_source(url)
    except ValueError:
        source = None
    if source != VideoSource.BILIBILI:
        raise HTTPException(status_code=400, detail="分 P 预览仅支持 B 站视频链接")
    if request.bilibili_cookie:
        cookie: dict[str, str] | None = request.bilibili_cookie.model_dump()
    else:
        try:
            cookie = config_store.load_bili_credentials()
        except BiliCredentialsUnavailable:
            cookie = None
    video = await video_processor.bilibili_video_pages(url, cookie)
    if video is None:
        raise HTTPException(
            status_code=400, detail="未能从链接解析出 B 站视频（BV/av 号）"
        )
    if not video.pages:
        raise HTTPException(
            status_code=502,
            detail="B 站接口未返回分 P 信息（网络或风控异常），可稍后重试或填写凭据",
        )
    return {
        "title": video.title,
        "total_pages": len(video.pages),
        "pages": [
            {"page": page.page, "part": page.part, "duration": page.duration}
            for page in video.pages
        ],
        # 该视频上次任务显式勾选的分 P；前端拿它做默认勾选（优先于 ?p=N）
        "previous_pages": previous_bili_pages(normalize_source_url(url)),
    }


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "AI Video Note",
        "version": app.version,
        "code_revision": CODE_REVISION,
        # portable/dev 互不复用对方实例，避免 dev 服务占用端口导致打包版不驻留托盘
        "mode": "portable" if getattr(sys, "frozen", False) else "dev",
        # 本机保存的 API Key 是加密还是明文回退，答疑时看一眼健康接口即可确认
        "llm_key_storage": secret_box.storage_backend(),
        # 前端上传上限跟随本机配置，不再自己写死一个数字
        "max_upload_mb": MAX_UPLOAD_MB,
        "video_download_default_directory": str(WORKSPACE_DIR / "video"),
        "dependencies": {
            "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
            "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
            "openai": importlib.util.find_spec("openai") is not None,
            # MCP 的 SSE 端点是否真的挂上了（缺 mcp 依赖时前端要说"不通"而不是给个死地址）
            "mcp_sse": MCP_SSE_ENABLED,
            "mcp_streamable_http": MCP_HTTP_ENABLED,
        },
    }


class NoteDirectorySelection(BaseModel):
    current_directory: str = Field(default="", max_length=2048)
    language: Literal["zh-CN", "en"] = "zh-CN"


@app.post("/api/note-directory/select")
async def select_note_directory(request: NoteDirectorySelection) -> dict[str, Any]:
    from . import folder_picker
    try:
        directory = await asyncio.to_thread(
            folder_picker.choose_directory, request.current_directory,
            default_directory=str(WORKSPACE_DIR / "notes"), language=request.language,
        )
    except folder_picker.PickerBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="无法打开文件夹选择窗口，请确认软件运行在有桌面的电脑上。") from error
    return {"directory": directory, "cancelled": not bool(directory)}


@app.post("/api/skill/export")
async def export_agent_skill(request: NoteDirectorySelection) -> dict[str, Any]:
    from . import folder_picker, skill_export
    try:
        directory = await asyncio.to_thread(
            folder_picker.choose_directory, request.current_directory,
            language=request.language,
            title="Select Skill export folder" if request.language == "en" else "选择 Skill 保存文件夹",
        )
        if not directory:
            return {"cancelled": True, "path": ""}
        path = await asyncio.to_thread(skill_export.export_skill, BASE_DIR, directory)
        return {"cancelled": False, "path": str(path)}
    except folder_picker.PickerBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="Skill 导出失败，请检查文件夹权限及桌面窗口。") from error


@app.get("/api/update/check")
async def check_app_update(language: Literal["zh-CN", "en"] | None = None) -> dict[str, Any]:
    """Check both release hosts; UI language decides equal-version preference."""
    from .ui_language import load_language
    selected = language or load_language(WORKSPACE_DIR)
    return await asyncio.to_thread(update_check.check_all_sources, app.version, language=selected)


class LogRetentionPayload(BaseModel):
    days: int = Field(ge=1, le=365, strict=True)


@app.get("/api/log-settings")
async def get_log_settings():
    from .log_retention import load_days
    return {"days": await asyncio.to_thread(load_days, WORKSPACE_DIR)}


@app.put("/api/log-settings")
async def set_log_settings(payload: LogRetentionPayload):
    from .log_retention import save_days
    await asyncio.to_thread(save_days, WORKSPACE_DIR, payload.days)
    return {"days": payload.days}


def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(url: str, timeout: float = 4.0, *, use_proxy: bool) -> dict[str, Any]:
    """只看"HTTP 层有没有应答"的探测：任何状态码都算通，应答体截一小段当证据。"""
    try:
        if use_proxy:
            opener = urllib.request.build_opener()
        else:
            # 本机回环与直连对照都必须绕开代理：urllib 在 Windows 会读注册表系统代理
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(url, headers={"User-Agent": "AI-Video-Note-diagnostics"})
        with opener.open(request, timeout=timeout) as response:
            body = response.read(240)
            return {
                "ok": True,
                "status": response.status,
                "body": utf8_safe(body.decode("utf-8", "replace")),
            }
    except urllib.error.HTTPError as exc:
        # 收得到状态码说明网络层是通的（403/404 也算）
        return {"ok": True, "status": exc.code, "body": ""}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {utf8_safe(str(exc))}"[:200]}


def _resolve_host(host: str) -> dict[str, Any]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {utf8_safe(str(exc))}"[:160]}
    return {"ok": True, "addresses": sorted({info[4][0] for info in infos})[:3]}


def _winsock_probe() -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return {"ok": True, "port": sock.getsockname()[1]}
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _wininet_proxy_snapshot() -> dict[str, Any]:
    """Windows 注册表里的系统代理：浏览器和 urllib 认它，httpx 不认——分开报。"""
    if os.name != "nt":
        return {"available": False}
    try:
        import winreg
    except ImportError:
        return {"available": False}
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = bool(winreg.QueryValueEx(key, "ProxyEnable")[0])
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
            try:
                override = str(winreg.QueryValueEx(key, "ProxyOverride")[0] or "")
            except FileNotFoundError:
                override = ""
    except FileNotFoundError:
        return {"available": True, "enabled": False}
    except OSError as exc:
        return {"available": True, "error": utf8_safe(str(exc))[:120]}
    if not enabled or not server:
        return {"available": True, "enabled": False}
    # ProxyServer 可能是 "host:port"，也可能按协议写成 "http=…;https=…"
    target = server
    if "=" in server:
        for part in server.split(";"):
            if part.lower().startswith(("https=", "http=")):
                target = part.split("=", 1)[1]
                break
    host, _, port_text = target.rpartition(":")
    port = int(port_text) if port_text.isdigit() else 80
    return {
        "available": True,
        "enabled": True,
        "server": mask_proxy_target(target),
        "override": override[:160],
        "listening": _tcp_reachable(host or target, port, 1.0),
    }


def _count_free_cdp_ports(start: int = 9333, count: int = 20) -> int:
    free = 0
    for port in range(start, start + count):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                free += 1
        except OSError:
            continue
    return free


@app.get("/api/diagnostics")
async def run_diagnostics(request: Request) -> dict[str, Any]:
    """本机自检：现场回答"测试连接/扫码登录 500"这类悬案能查的问题。

    只读、零密钥：响应里永远不出现 Key 明文、cookie、代理地址的账号段。
    """
    server_port = request.url.port or (request.scope.get("server") or (None, None))[1]
    checks: dict[str, Any] = {}
    hints: list[str] = []

    checks["service"] = {
        "pid": os.getpid(),
        "version": app.version,
        "mode": "portable" if getattr(sys, "frozen", False) else "dev",
        "port": server_port,
        "uptime_s": int(time.monotonic() - _STARTED_MONO),
    }

    loopback = await asyncio.wait_for(
        asyncio.to_thread(
            _http_probe, f"http://127.0.0.1:{server_port}/api/health", 3.0, use_proxy=False
        ),
        timeout=6,
    )
    identity = None
    try:
        identity = json.loads(str(loopback.get("body") or "null"))
    except ValueError:
        pass
    is_ours = isinstance(identity, dict) and identity.get("service") == "AI Video Note"
    checks["loopback_http"] = {**loopback, "is_ours": is_ours}
    if loopback.get("ok") and not is_ours:
        hints.append(
            f"127.0.0.1:{server_port} 上应答的不是本程序（开头是：{str(loopback.get('body') or '')[:60]}）"
            "——端口被别的软件占着，或本机代理在截本机请求"
        )
    elif not loopback.get("ok"):
        hints.append("本机 HTTP 服务没答上话——后端可能刚被搞挂，看 workspace\\_app.log")

    checks["winsock"] = await asyncio.to_thread(_winsock_probe)

    registry = await asyncio.to_thread(_wininet_proxy_snapshot)
    checks["system_proxy"] = {"env": env_proxy_summary(), "registry": registry}
    if registry.get("enabled") and registry.get("listening") is False:
        hints.append(
            f"Windows 系统代理指向 {registry.get('server')}，但那个地址没有进程在听"
            "——代理/加速器多半僵死了：关掉它或重启电脑，软件不用重装"
        )

    dns_checks = {
        host: await asyncio.wait_for(asyncio.to_thread(_resolve_host, host), timeout=8)
        for host in ("api.deepseek.com", "passport.bilibili.com")
    }
    checks["dns"] = dns_checks
    if all(not entry.get("ok") for entry in dns_checks.values()):
        hints.append("两个域名都解析失败——DNS 或网络本身有问题：换手机热点试一次，不行再重启电脑")

    direct = await asyncio.wait_for(
        asyncio.to_thread(_http_probe, "https://api.deepseek.com", 5.0, use_proxy=False),
        timeout=9,
    )
    via_proxy = await asyncio.wait_for(
        asyncio.to_thread(_http_probe, "https://api.deepseek.com", 5.0, use_proxy=True),
        timeout=9,
    )
    checks["outbound"] = {"direct": direct, "with_system_proxy": via_proxy}
    if direct.get("ok") and not via_proxy.get("ok"):
        hints.append("直连通、带系统代理出不去——就是本机代理/加速器的问题，关掉它即可，不用重启电脑")
    if not direct.get("ok"):
        hints.append("不走代理也出不去——到 api.deepseek.com 整条路不通（网卡/防火墙/驱动）；重启电脑仍不行就换网络")

    checks["cdp_ports_free"] = await asyncio.to_thread(_count_free_cdp_ports)
    try:
        checks["llm_key_storage"] = {
            "backend": secret_box.storage_backend(),
            "corrupt": bool(config_store.keys_are_corrupt()),
        }
    except Exception as exc:
        checks["llm_key_storage"] = {"error": f"{type(exc).__name__}: {utf8_safe(str(exc))}"[:120]}

    recent = list(_LAST_BACKEND_ERRORS)
    checks["recent_backend_errors"] = recent
    if recent:
        hints.append("后端近期抛过未兜住的异常（见 recent_backend_errors，对照 _app.log 的 Traceback）")

    if not hints:
        hints.append("各项自检没发现异常——把这份报告整段发给作者/群里，比一句 500 有用得多")
    return {"ok": True, "checks": checks, "hints": hints}


@app.post("/api/bili-login/start")
async def bili_login_start() -> dict[str, Any]:
    return await bili_login_manager.start()


@app.get("/api/bili-login/status")
async def bili_login_status() -> dict[str, Any]:
    return await bili_login_manager.status()


@app.post("/api/bili-login/cancel")
async def bili_login_cancel() -> dict[str, Any]:
    return await bili_login_manager.cancel()


@app.post("/api/douyin-login/start")
async def douyin_login_start() -> dict[str, Any]:
    """打开独立的本机浏览器 profile，用户可手动完成抖音登录/验证。"""
    return await douyin_login_manager.start()


@app.get("/api/douyin-login/status")
async def douyin_login_status() -> dict[str, Any]:
    return await douyin_login_manager.status()


@app.post("/api/douyin-login/cancel")
async def douyin_login_cancel() -> dict[str, Any]:
    return await douyin_login_manager.cancel()


# 视频容器后缀（可能带音轨、需要时提取）；纯音频后缀直接使用
VIDEO_CONTAINER_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...),
    include_screenshots: str | None = Form(default=None),
    retain_media: bool = Form(default=False),
) -> dict[str, str]:
    if _storage_cleaning:
        raise HTTPException(409, "缓存清理中，请稍后再上传")
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_MEDIA_SUFFIXES:
        raise HTTPException(status_code=415, detail="不支持的媒体文件类型")

    task_id = str(uuid.uuid4())
    task_dir = task_directory(task_id)
    task_dir.mkdir(parents=True, exist_ok=False)
    file_path = task_dir / f"input{suffix}"
    wants_screenshots = (include_screenshots or "").strip().lower() in {
        "1", "true", "yes"
    }
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件超过大小限制（上限 {MAX_UPLOAD_MB} MB）",
                    )
                await destination.write(chunk)
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    log_lines: list[str] = [f"文件已上传：{filename}"]
    # 大视频且不需要截图时：提取音频、删除原视频，减少工作目录体积
    if (
        written >= LARGE_UPLOAD_EXTRACT_BYTES
        and suffix in VIDEO_CONTAINER_SUFFIXES
        and not wants_screenshots
        and retain_media is not True
    ):
        audio_path = task_dir / "input.m4a"
        try:
            extracted = await asyncio.to_thread(
                video_processor.extract_audio_track, file_path, audio_path
            )
        except Exception:
            extracted = None
        if extracted is not None and extracted.is_file():
            original_size = written
            file_path.unlink(missing_ok=True)
            file_path = audio_path
            log_lines.append(
                f"大视频已提取音频：{format_bytes_size(original_size)} → "
                f"{format_bytes_size(audio_path.stat().st_size)}（原视频已清理；"
                "如需截图请取消勾选该任务后重新上传）"
            )
        else:
            log_lines.append(
                "视频较大，未能提取音频，将直接用原文件转写（不影响使用）"
            )

    tasks[task_id] = new_task(status="uploaded", task_id=task_id)
    tasks[task_id].update(
        uploaded_file_path=str(file_path),
        uploaded_filename=filename,
        logs=log_lines,
    )
    write_upload_manifest(task_id, filename)
    persist_task_runtime(task_id)
    return {"task_id": task_id, "file_path": str(file_path), "filename": filename}


async def get_image(task_id: str, filename: str) -> FileResponse:
    from .exports_service import get_image as handler
    return await handler(sys.modules[__name__], task_id, filename)


async def download_summary_markdown(task_id: str, format: Literal["markdown", "docx", "pdf", "enex", "html"] = "markdown") -> Response:
    from .exports_service import download_summary_markdown as handler
    return await handler(sys.modules[__name__], task_id, format)


def build_note_bundle(note_text: str, task_dir: Path, note_filename: str) -> bytes | None:
    """把笔记和它引用的截图打成一个包；正文里没有可解析的截图时返回 None。"""
    frames_dir = task_dir / FRAMES_DIR_NAME
    names: list[str] = []
    for match in NOTE_IMAGE_REF_RE.finditer(note_text):
        name = Path(match.group(2)).name
        if name not in names and (frames_dir / name).is_file():
            names.append(name)
    if not names:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(note_filename, rewrite_note_image_refs(note_text, "./"))
        for name in names:
            archive.writestr(
                f"{FRAMES_DIR_NAME}/{name}", (frames_dir / name).read_bytes()
            )
    return buffer.getvalue()


@app.post("/api/task/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="任务已经结束")

    if task["status"] == "awaiting_agent":
        task.update(status="cancelled", step_name="已取消", _cancel_requested=True)
        checked_event = task.get('_transcript_checked_event')
        if checked_event: checked_event.set()
        review_event = task.get("_transcript_review_event")
        if review_event: review_event.set()
        finish_task_timing(task)
        persist_task_runtime(task_id)
        return {"cancelled": True, "status": "cancelled", "elapsed_seconds": task_elapsed_seconds(task)}

    first_request = not task.get("_cancel_requested")
    task["_cancel_requested"] = True
    cancel_event = task.get("_cancel_event")
    if cancel_event is not None:
        cancel_event.set()
    task.update(status="cancelling", step_name="取消中", error=None)
    task["logs"].append("已收到取消请求；正在尽快停止当前步骤")
    persist_task_runtime(task_id)
    if first_request:
        # 取消的唯一收口：直接掐掉 asyncio 任务，让 CancelledError 落在当前 await 点。
        # should_abort / cancel_event 只是让后台线程早点收工的优化，新增阶段忘了接线
        # 也不会再让取消"失效"（v1.2.3 的 412 回退把没接线的读取信息/查找字幕阶段
        # 拉长，表现为取消不动）。这一句之后不能再有 await，否则会覆盖处理器写的终态。
        job = running_jobs.get(task_id)
        if job is not None and not job.done():
            job.cancel()
    return {
        "cancelled": False,
        "status": task["status"],
        "elapsed_seconds": task_elapsed_seconds(task),
    }


class DeleteTasksRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=100)


@app.post("/api/tasks/delete")
async def delete_tasks(body: DeleteTasksRequest) -> dict[str, Any]:
    deleted, failed = [], []
    for task_id in dict.fromkeys(body.task_ids):
        try:
            await delete_task(task_id)
            deleted.append(task_id)
        except HTTPException as exc:
            failed.append({"task_id": task_id, "error": exc.detail})
    return {"deleted": deleted, "failed": failed}


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str) -> dict[str, bool]:
    if _storage_cleaning:
        raise HTTPException(409, "缓存清理中，请稍后再删除任务")
    ensure_task_loaded(task_id)
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        directory = task_directory(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法任务 ID") from None
    job = running_jobs.get(task_id)
    if tasks[task_id].get("_exporting") or tasks[task_id].get("_agent_finishing") or tasks[task_id].get("_agent_auto_running") or tasks[task_id].get("_agent_capture") or (job and not job.done()) or tasks[task_id].get("status") in {"queued", "processing", "cancelling"}:
        raise HTTPException(status_code=409, detail="任务仍在运行，请先取消并等待其停止")
    from .task_dependencies import preserve_reused_inputs
    try:
        preserve_reused_inputs(sys.modules[__name__], task_id)
    except OSError as exc:
        raise HTTPException(409, detail=f"复用素材保留失败，未删除源任务：{exc}") from exc
    manifest = read_task_manifest(task_id)
    result = tasks[task_id].get("result") or (manifest.get("runtime") or {}).get("result") or {}
    result = dict(result)
    result["video_path"] = result.get("video_path") or tasks[task_id].get("video_path") or (manifest.get("runtime") or {}).get("video_path")
    videos = downloaded_videos(WORKSPACE_DIR, task_id, manifest, result)
    if videos:
        source_url = str(manifest.get("source_url") or "")
        for other_id, other in tasks.items():
            if other_id == task_id:
                continue
            other_job = running_jobs.get(other_id)
            active = (other.get("status") in {"pending", "queued", "processing", "cancelling", "awaiting_agent"}
                      or other.get("_agent_auto_running") or other.get("_agent_capture")
                      or (other_job and not other_job.done()))
            if not active:
                continue
            other_manifest = read_task_manifest(other_id)
            other_runtime = other_manifest.get("runtime") or {}
            other_result = other.get("result") or other_runtime.get("result") or {}
            other_video = other.get("video_path") or other_result.get("video_path") or other_runtime.get("video_path")
            same_file = bool(other_video and Path(other_video).resolve() in videos)
            other_source = str(other_manifest.get("source_url") or "")
            same_source = bool(source_url and other_source and normalize_source_url(source_url) == normalize_source_url(other_source))
            if same_file or same_source:
                raise HTTPException(status_code=409, detail="该视频正在被另一任务使用，请等待其完成或取消后删除")
    exports = exported_files(WORKSPACE_DIR, task_id, manifest, result)
    # Preserve any exact output file still owned by another task.
    for other_id, other in tasks.items():
        if other_id == task_id:
            continue
        other_manifest = read_task_manifest(other_id)
        other_result = other.get("result") or (other_manifest.get("runtime") or {}).get("result") or {}
        exports -= exported_files(WORKSPACE_DIR, other_id, other_manifest, other_result)
    tasks[task_id]["_deleting"] = True
    try:
        unlink_exports(exports | videos)
        remove_file_entries(WORKSPACE_DIR, "video", videos)
        await video_processor.cleanup(task_id)
    except OSError as exc:
        tasks[task_id].pop("_deleting", None)
        if directory.is_dir():
            # Restore metadata without overwriting any newer runtime snapshot.
            task_storage.update_manifest(directory, lambda current: current.update(
                {key: value for key, value in manifest.items() if key != "runtime"}
            ))
            persist_task_runtime(task_id)
        raise HTTPException(status_code=409, detail=f"文件清理未完成，请关闭占用文件的程序后重试：{exc}") from exc
    running_jobs.pop(task_id, None)
    tasks.pop(task_id, None)
    video = result.get("video_path")
    if video and Path(video).resolve().parent.name == task_id:
        try:
            Path(video).resolve().parent.rmdir()
        except OSError:
            pass
    custom_downloads = manifest.get("video_download_directory")
    if custom_downloads and Path(custom_downloads).is_absolute():
        try:
            (Path(custom_downloads).resolve() / task_id).rmdir()
        except OSError:
            pass
    return {"deleted": True}


def _format_page_nums(pages) -> str:
    """把分 P 对象 / 页码列表格式化为 “1、2、3”。"""
    nums = sorted(int(page.page if hasattr(page, "page") else page) for page in pages)
    return "、".join(str(num) for num in nums)


def append_asr_notes(task: dict[str, Any], whisper_result: dict[str, Any]) -> None:
    """转写阶段的降级说明与耗时入日志（单文件与 B 站多分 P 共用）。"""
    note = whisper_result.get("fallback_note")
    if note and note not in task["logs"]:
        task["logs"].append(note)
    if whisper_result.get("title_prompt_filtered"):
        task["logs"].append("标题提示与检测到的语音语言不匹配，已忽略标题提示后识别")
    recovery = whisper_result.get("asr_recovery") or []
    if recovery:
        attempts = sum(item["decision"] != "budget_exhausted" for item in recovery)
        accepted = sum(item["decision"] == "accepted" for item in recovery)
        task["logs"].append(
            f"重复异常检查：局部重试 {attempts} 处，采用候选 {accepted} 处；原文与候选保存在 transcript.json，可供核对"
        )
    load_seconds = float(whisper_result.get("model_load_seconds") or 0)
    if load_seconds >= 1:
        task["logs"].append(
            f"模型加载用时 {load_seconds} 秒，转写用时 "
            f"{whisper_result.get('transcribe_seconds')} 秒"
        )


async def run_queued_video_task(task_id: str, request: SummarizeRequest) -> None:
    task = tasks[task_id]
    task.update(status="queued", step_name="等待执行", progress=0)
    task["logs"].append(
        f"任务已进入队列；当前最多同时处理 {MAX_CONCURRENT_TASKS} 个任务"
    )
    persist_task_runtime(task_id)
    try:
        async with task_slots:
            raise_if_cancel_requested(task)
            await process_video_task(task_id, request)
    except asyncio.CancelledError:
        finish_task_timing(task)
        task.update(status="cancelled", step_name="已取消", error=None)
        task["logs"].append("任务已取消；已生成的中间文件将保留")
        persist_task_runtime(task_id)


async def process_video_task(task_id: str, request: SummarizeRequest) -> None:
    from .video_pipeline import process_video_task as process
    await process(sys.modules[__name__], task_id, request)


async def write_transcript_files(
    task_id: str, segments: list[TranscriptSegment], metadata: dict[str, Any]
) -> None:
    """转录文件先写 .tmp 再改名。

    取消能落在任意 await 点（见 cancel_task），而「复用转录」以 transcript.json
    是否存在为准——半截文件会比没有文件更糟。
    """
    task_dir = task_directory(task_id)
    json_payload = {
        "language": metadata["language"],
        "source": metadata["source"],
        "quality": metadata.get("quality"),
        "segments": [segment.to_dict() for segment in segments],
        **{key: metadata[key] for key in ("asr_recovery", "asr_recovery_pages", "subtitle_policy") if metadata.get(key)},
    }
    await _write_atomically(
        task_dir / "transcript.json",
        (json.dumps(json_payload, ensure_ascii=False, indent=2),),
    )
    await _write_atomically(
        task_dir / "transcript.md",
        ("# 带时间戳转录\n\n", segments_to_prompt(segments), "\n"),
    )


async def _write_atomically(path: Path, chunks: tuple[str, ...]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    async with aiofiles.open(temporary, "w", encoding="utf-8") as file:
        for chunk in chunks:
            await file.write(chunk)
    os.replace(temporary, path)


def friendly_task_error(
    message: str,
    *,
    source: VideoSource | None = None,
    step_name: str = "",
) -> str:
    """把已知的平台限制错误转成对用户友好的提示。

    平台文案只在对应平台的任务里出现，模型通道的错误不套平台说法：旧版之前
    任何含 403 的异常都说成「抖音媒体地址已过期」，而实际触发它的是 B 站任务里
    模型接口返回的 403，用户据此重投了三次没问题的链接。
    """
    lowered = message.lower()
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return (
            "Whisper 模型下载时 TLS 证书校验失败。程序已尝试备用下载源；"
            "如果仍失败，请检查系统时间、网络代理或企业根证书，"
            "并通过 HF_ENDPOINT 指定能正常验证证书的镜像后重试。"
        )
    if step_name == NOTE_STEP_NAME:
        return friendly_llm_error(lowered, message)
    if source == VideoSource.DOUYIN:
        if "fresh cookies" in lowered or "challenge" in lowered or "验证码" in message:
            return "抖音要求浏览器验证。点击输入区下方的“打开抖音浏览器”，完成登录/验证后重新提交。"
        if "403" in lowered or "forbidden" in lowered:
            return "抖音媒体地址已过期或被拒绝，请重新提交链接；如果仍失败，先用本机抖音浏览器完成验证。"
        if "douyin" in lowered or "iesdouyin" in lowered or "抖音" in message:
            return DOUYIN_HINT
    if source == VideoSource.BILIBILI and any(
        marker in lowered for marker in ("403", "412", "forbidden", "precondition")
    ):
        return (
            "B 站拒绝了本次请求（风控或访问凭据失效）。请在「B 站访问凭据」里重新扫码导入 "
            "SESSDATA 后重投链接；也可以稍等几分钟再试。"
        )
    return message


# 各家对"上下文窗口不够"的说法不一样（ollama 两种、OpenAI/vLLM 一种、LM Studio 一种），
# 但都带这些字样。刻意不含 "too many tokens"：OpenAI 限流的原文就是
# `Too many tokens per minute`，按它判会把限流说成窗口不够。
_CONTEXT_LIMIT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "prompt is too long",
    "num_ctx",
    "maximum capacity of",
)


def friendly_llm_error(lowered: str, message: str) -> str:
    """生成笔记阶段的失败来自模型通道，重投视频链接不会有任何改变。"""
    # 放在额度/429 之前：下面几条判的是裸子串 "429"/"403"，而本类报错里的数字是
    # token 数（`requested 9403 tokens` 就含 "403"），先判会把窗口不够说成权限问题。
    if any(marker in lowered for marker in _CONTEXT_LIMIT_MARKERS):
        return (
            "模型的上下文窗口装不下本次请求（这是模型侧的容量上限，不是本程序的笔记长度设置）。"
            "本地模型（ollama / LM Studio / llama.cpp）请把上下文调到 16K 以上再重投："
            "ollama 设 OLLAMA_CONTEXT_LENGTH=16384 后重启服务，或在 Modelfile 里写 "
            "`num_ctx 16384`。在线通道多为该模型或网关的上限较低，"
            "可换上下文更大的模型，或改用「精简摘要」减少单次送入的材料。"
        )
    if "insufficient_quota" in lowered or "quota" in lowered or "余额" in message:
        return (
            "模型通道的额度或余额不足，笔记生成中断。"
            "请在「模型配置」里换一个通道（Base URL 与 Key），或给当前通道续额后重投。"
        )
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return (
            "模型通道限流。可以稍后重投，或改用付费通道；"
            "长视频换用「精简摘要」能明显减少请求数。"
        )
    if "401" in lowered or "invalid_api_key" in lowered or "incorrect api key" in lowered:
        return (
            "模型接口拒绝了本机保存的 Key（401）。"
            "请在「模型配置」里重新填写该 Base URL 对应的 Key 后重投。"
        )
    if "403" in lowered or "permission" in lowered or "forbidden" in lowered:
        return (
            "模型接口拒绝了本次请求（403）：本机保存的 Key 可能没有该模型的权限，"
            "或 Base URL 与模型名不匹配。请核对「模型配置」后重投，"
            "任务日志里「调用模型」一行就是本次实际使用的通道。"
        )
    if "404" in lowered or "not found" in lowered or "does not exist" in lowered:
        return "模型接口找不到该模型（404）。请检查「模型配置」里的模型名与 Base URL。"
    if "timeout" in lowered or "timed out" in lowered or "connection" in lowered:
        return "与模型接口的连接中断，多为网络抖动或通道限流。可以直接重投任务，或换一个通道。"
    return message


def redact_secrets(text: str, secret: str = "") -> str:
    """把文本里的凭据换成掩码。

    比正则更可靠的一层是 `secret`：调用方手里就有本次用的明文 Key，
    整串替换不依赖网关把回显写成什么格式。
    """
    cleaned = API_KEY_PATTERN.sub("sk-****", text)
    cleaned = SECRET_ECHO_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}****", cleaned
    )
    if secret and len(secret) >= 8:
        cleaned = cleaned.replace(secret, secret_box.mask_secret(secret))
    return cleaned


def scrub_technical_error(raw: str, secret: str = "") -> str:
    """异常文本进日志前的脱敏与截断：通道报错可能整段回显 JSON。"""
    return redact_secrets(" ".join(raw.split()), secret)[:400]


def format_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    return format_timestamp(float(seconds))


def format_bytes_size(num_bytes: float) -> str:
    """把字节数格式化为人类可读大小（MB / GB）。"""
    size = max(0.0, float(num_bytes))
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 ** 3):.2f} GB"
    return f"{size / (1024 ** 2):.2f} MB"


def format_publish_time(info: dict[str, Any]) -> str:
    """优先使用 yt-dlp 的发布日期；没有日期时再使用 Unix 时间戳。"""
    raw_date = str(info.get("upload_date") or info.get("release_date") or "").strip()
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    raw_timestamp = info.get("timestamp") or info.get("release_timestamp")
    try:
        timestamp = float(raw_timestamp)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def add_note_header_metadata(summary: str, title: str, info: dict[str, Any]) -> str:
    """在模型标题下放置可验证的视频元信息，不让模型负责记住固定排版。"""
    value = summary.strip()
    lines = value.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).lstrip()
    else:
        heading = f"# 视频笔记：《{title}》"
        body = value

    body = strip_model_metadata_block(body)

    metadata: list[str] = []
    owner = str(info.get("owner") or "").strip()
    published_at = format_publish_time(info)
    duration = format_duration(info.get("duration", 0))
    view_count = int(info.get("view_count") or 0)
    like_count = int(info.get("like_count") or 0)
    if owner:
        metadata.append(f"UP主：{owner}")
    if published_at:
        metadata.append(f"发布时间：{published_at}")
    if duration != "未知":
        metadata.append(f"时长：{duration}")
    if view_count > 0:
        metadata.append(f"播放：{view_count:,}")
    if like_count > 0:
        metadata.append(f"点赞：{like_count:,}")
    if not metadata:
        return value or heading
    metadata_block = "> " + " · ".join(metadata)
    return f"{heading}\n\n{metadata_block}" + (f"\n\n{body}" if body else "")


def strip_model_metadata_block(body: str) -> str:
    """移除模型紧跟标题生成的重复元信息，保留正文内容。

    提示词已经把作者、时间、时长和文字来源作为参考资料传给模型，
    但模型仍可能输出一行 ``作者：…｜发布时间：…``。这些字段由代码
    确定性写入标题下方，因此只清理标题后的重复元信息块。
    """
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    start = index
    metadata_labels = ("作者", "UP主", "发布时间", "时长", "播放", "点赞", "文字来源")
    removed = 0
    while index < len(lines):
        line = lines[index].strip().lstrip("> ")
        label_count = sum(f"{label}：" in line or f"{label}:" in line for label in metadata_labels)
        if label_count < 2:
            break
        removed += 1
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    if not removed:
        return body
    return "\n".join(lines[index:]).lstrip()


def summary_style_label(style: str) -> str:
    return {
        "detailed": "整理＋点评（正文后追加 AI 分析）",
        "faithful": "详细整理（按主题保留要点与细节）",
        "concise": "重点摘要（快速了解主要内容）",
        "obsidian": "Obsidian 笔记",
        **{key: value["label"] for key, value in NOTE_STYLES.items()},
    }.get(style, style)


def add_obsidian_properties(summary: str, title: str, source_url: str | None, enabled: bool = True) -> str:
    if not enabled:
        from .note_options import strip_note_properties
        return strip_note_properties(summary)
    # JSON-quoted strings are valid YAML and safely handle colons, quotes and newlines.
    properties = ["---", f"title: {json.dumps(title, ensure_ascii=False)}",
                  f"source: {json.dumps(source_url or '', ensure_ascii=False)}",
                  "---", ""]
    return "\n".join(properties) + "\n" + summary.lstrip()


def append_note_footer(
    summary: str,
    source_url: str | None,
    model_type: str,
    model: str | None,
    style: str,
    include_source: bool = True,
) -> str:
    """确定性追加来源与生成配置，避免不同风格遗漏关键追溯信息。"""
    source = source_url.strip() if source_url else ""
    source_line = source or "本地文件（未提供视频链接）"
    model_line = model.strip() if model else "未指定模型"
    footer = "\n".join(
        [
            "---",
            *([f"来源：{source_line}"] if include_source else []),
            f"生成模型：{model_type} · {model_line}",
            f"笔记风格：{summary_style_label(style)}",
        ]
    )
    return f"{summary.rstrip()}\n\n{footer}\n"


SAFE_NAME_LIMIT = 80


def _safe_filename(value: str) -> str:
    """把标题清洗为安全的文件名：只去掉 Windows 非法字符，保留中文标点。"""
    invalid = set('\\/:*?"<>|')
    cleaned = "".join(char for char in value if char not in invalid and ord(char) >= 32)
    return (cleaned.strip() or "video-notes")[:SAFE_NAME_LIMIT]


def download_stem(title: str, meta: dict[str, Any], output: str = "note") -> str:
    """产物文件名主干（不含扩展名），Markdown 下载、压缩包、归档和网页端共用。

    B 站分 P 与纯转录都要带进名字：同一视频逐 P 跑出来的笔记、以及同一视频
    先转写后生成的两份产物，否则在下载目录里只剩一串相同或时间戳文件名。
    分 P 标记与转录正文里用的「【P3 分P名】」保持同一写法。
    """
    base = str(title or "").strip() or "video-notes"
    suffix = ""
    pages = [page for page in (meta.get("bili_pages") or []) if isinstance(page, dict)]
    total = int(meta.get("bili_total_pages") or 0)
    if total > 1 and pages:
        if len(pages) == 1:
            page = pages[0]
            part = str(page.get("part") or "").strip()
            suffix = f"【P{page.get('page')} {part}】" if part else f"【P{page.get('page')}】"
        elif len(pages) < total:
            suffix = f"【P{_format_page_nums([int(page['page']) for page in pages])}】"
    if output == "transcript":
        suffix += "【转录】"
    if suffix and len(base) + len(suffix) > SAFE_NAME_LIMIT:
        base = base[: max(1, SAFE_NAME_LIMIT - len(suffix))]
    return _safe_filename(f"{base}{suffix}")


def rewrite_note_image_refs(content: str, prefix: str) -> str:
    return NOTE_IMAGE_REF_RE.sub(
        lambda match: f"{match.group(1)}{prefix}{FRAMES_DIR_NAME}/{match.group(2)})",
        content,
    )


def archive_note(
    title: str,
    content: str,
    task_id: str | None = None,
    folder: str = NOTES_DIR_NAME,
    note_format: str = "markdown",
    save_directory: str = "",
    created_files: list[Path] | None = None,
) -> Path | None:
    """把产物按标题命名归档到 workspace/<folder>/ 下（重名自动加序号），便于集中回顾。

    默认 notes/；纯转录走 transcripts/，与笔记分开摆。

    Markdown 笔记使用 notes/<笔记名>/<笔记名>.md；仅复制正文引用的图片到
    同一文件夹，改写为相对引用。任务目录中的全部原始截图保持不变。

    返回归档路径；写入失败时返回 None（不影响任务本身）。
    """
    if note_format == "markdown" and folder == NOTES_DIR_NAME:
        from .note_export import save_markdown_folder
        destination = Path(save_directory).expanduser() if save_directory else WORKSPACE_DIR / folder
        if save_directory and not destination.is_absolute():
            raise ValueError("笔记保存位置必须是绝对路径")
        try:
            return save_markdown_folder(title, content, task_directory(task_id) if task_id else None,
                                        destination, created_files)
        except OSError:
            if save_directory:
                raise
            return None
    if save_directory or note_format not in {"markdown", "obsidian", "docx"}:
        from .note_export import save_note_export
        destination = Path(save_directory).expanduser() if save_directory else WORKSPACE_DIR / folder
        if save_directory and not destination.is_absolute():
            raise ValueError("笔记保存位置必须是绝对路径")
        return save_note_export(title, content, task_directory(task_id), destination, note_format, created_files)
    archive_root = WORKSPACE_DIR / folder
    original_content = content
    if task_id:
        content = rewrite_note_image_refs(content, "../" + task_directory(task_id).relative_to(WORKSPACE_DIR).as_posix() + "/")
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
        safe = _safe_filename(title)
        extension = "docx" if note_format == "docx" else "md"
        candidate = archive_root / f"{safe}.{extension}"
        index = 2
        if note_format == "docx":
            from .word_export import build_word_document
            data = build_word_document(original_content, task_directory(task_id))
        else:
            data = content.encode("utf-8")
        while True:
            try:
                output = candidate.open("xb")
                break
            except FileExistsError:
                candidate = archive_root / f"{safe}-{index}.{extension}"
                index += 1
        try:
            with output:
                output.write(data)
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
        if created_files is not None:
            created_files.append(candidate)
        return candidate
    except OSError:
        return None


def ensure_task_loaded(task_id: str) -> bool:
    if task_id not in tasks:
        try:
            directory = task_directory(task_id)
        except ValueError:
            return False
        if not directory.is_symlink() and (directory / 'task.json').is_file():
            restore_tasks_from_workspace({task_id})
    return task_id in tasks


def restore_tasks_from_workspace(task_ids: set[str] | None = None) -> None:
    """Rebuild recent task status from manifests after a service restart."""
    try:
        candidates = sorted(
            (
                path
                for path in task_storage.directories(WORKSPACE_DIR)
                if path.is_dir() and (path / "task.json").is_file()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates = candidates[:TASK_HISTORY_LIMIT] if task_ids is None else [p for p in candidates if p.name in task_ids]
    except OSError:
        return

    for task_dir in candidates:
        if task_ids is not None and task_dir.name in tasks:
            continue
        try:
            manifest = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                continue
        except (OSError, ValueError, TypeError):
            continue
        task_id = task_dir.name
        runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
        # Restore from the saved lifecycle state, not the presence of a body
        # written before export. History uses the same policy for unloaded tasks.
        transcript_only = manifest.get("output") == "transcript"
        notes_path = task_dir / "notes.md"
        uploaded_media = next(
            (path for path in task_dir.glob("input.*") if path.is_file()), None
        )
        saved_status = str(runtime.get("status") or "")
        interrupted = (saved_status in INTERRUPTED_STATUSES
                       or (transcript_only and saved_status == "awaiting_agent"))
        status = recovered_status(task_dir, manifest)

        task = new_task(status=status, task_id=task_id)
        task["_started_monotonic"] = None
        task.update(
            step=int(safe_number(runtime.get("step"))),
            step_name=str(runtime.get("step_name") or "已恢复"),
            progress=int(safe_number(runtime.get("progress"), 100 if status == "completed" else 0)),
            logs=[line for line in runtime.get("logs", []) if isinstance(line, str)]
                 if isinstance(runtime.get("logs"), list) else [],
            error=runtime.get("error"),
            advisory=runtime.get("advisory"),
            screenshot_warning=runtime.get("screenshot_warning"),
            video_path=runtime.get("video_path"),
            agent_runtime=runtime.get("agent_runtime"),
            transcript_review=runtime.get("transcript_review"),
            created_at=runtime.get("created_at") or manifest.get("created_at"),
            finished_at=runtime.get("finished_at"),
            elapsed_seconds=max(0, safe_number(runtime.get("elapsed_seconds"))),
            output=str(manifest.get("output") or "note"),
            # 旧版manifest 没有 origin：按 agent 处理，维持这类任务此前不进历史的表现
            _origin=str(manifest.get("origin") or "agent"),
        )
        if interrupted:
            task.update(
                step_name="服务已重启",
                error="任务因服务重启而中断；已有转录和音频仍可复用",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            task["logs"].append("服务重启时任务尚未结束，已标记为中断")
        elif saved_status == "completed" and status == "failed" and not task.get("error"):
            task.update(
                step_name="正文不可用",
                error="任务正文文件缺失或为空，或文件已损坏；请继续生成以恢复",
                finished_at=task["finished_at"] or datetime.now(timezone.utc).isoformat(),
            )
        if status == "uploaded" and uploaded_media:
            task["uploaded_file_path"] = str(uploaded_media)
            task["uploaded_filename"] = manifest.get("uploaded_filename") or uploaded_media.name
        if status == "completed" and transcript_only:
            # 纯转录任务没有 notes.md，正文留在 transcript.json 里
            result = dict(runtime["result"]) if isinstance(runtime.get("result"), dict) else {}
            result.update(
                title=result.get("title") or manifest.get("title") or "",
                output="transcript",
                output_directory=str(task_dir),
                processing_seconds=task["elapsed_seconds"],
            )
            task["result"] = result
            task["finished_at"] = task["finished_at"] or datetime.now(timezone.utc).isoformat()
        elif status == "completed":
            try:
                markdown = notes_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                task.update(status="failed", step_name="正文不可用",
                            error="笔记正文无法读取；请继续生成以恢复")
            else:
                result = dict(runtime["result"]) if isinstance(runtime.get("result"), dict) else {}
                result.update(
                    title=result.get("title") or manifest.get("title") or "video-notes",
                    markdown=markdown,
                    output_directory=str(task_dir),
                    processing_seconds=task["elapsed_seconds"],
                )
                task["result"] = result
                task["finished_at"] = task["finished_at"] or datetime.now(timezone.utc).isoformat()
        tasks[task_id] = task
        if interrupted:
            persist_task_runtime(task_id)


from .cache_index import migrate_workspace
migrate_workspace(WORKSPACE_DIR)
restore_tasks_from_workspace()


class UiLanguagePayload(BaseModel):
    language: Literal["zh-CN", "en"]


async def get_ui_language():
    from .settings_service import get_ui_language as handler
    return await handler(sys.modules[__name__])


async def set_ui_language(payload: UiLanguagePayload):
    from .settings_service import set_ui_language as handler
    return await handler(sys.modules[__name__], payload)


class PreferencesPayload(BaseModel):
    client_schema: Literal[3]
    preferences: dict[str, Any]
    expected_revision: str | None
    import_source: dict[str, Any] | None = None


async def get_preferences():
    from .settings_service import get_preferences as handler
    return await handler(sys.modules[__name__])


async def put_preferences(payload: PreferencesPayload):
    from .settings_service import put_preferences as handler
    return await handler(sys.modules[__name__], payload)


class PreferencesBackupSource(BaseModel):
    model_config = {'extra': 'forbid'}
    document: dict[str, Any]


class PreferencesRestorePayload(PreferencesBackupSource):
    backup_revision: str = Field(min_length=64, max_length=64)
    current_revision: str | None


async def export_preferences():
    from .settings_service import preferences_backup_operation
    return await preferences_backup_operation(sys.modules[__name__], 'export')


async def preview_preferences_restore(payload: PreferencesBackupSource):
    from .settings_service import preferences_backup_operation
    return await preferences_backup_operation(sys.modules[__name__], 'preview', payload)


async def restore_preferences(payload: PreferencesRestorePayload):
    from .settings_service import preferences_backup_operation
    return await preferences_backup_operation(sys.modules[__name__], 'restore', payload)


class AutostartPayload(BaseModel):
    enabled: bool = Field(strict=True)


async def get_autostart():
    from .settings_service import get_autostart as handler
    return await handler(sys.modules[__name__])


async def set_autostart(payload: AutostartPayload):
    from .settings_service import set_autostart as handler
    return await handler(sys.modules[__name__], payload)


async def open_app_log():
    from .settings_service import open_app_log as handler
    return await handler(sys.modules[__name__])


@app.get("/api/mcp/activity")
async def get_mcp_activity():
    state = mcp_activity.snapshot()
    state['skill_clients'] = skill_activity.snapshot()
    local_agents = []
    for task_id, task in list(tasks.items()):
        try:
            job = json.loads((task_directory(task_id) / 'agent_job.json').read_text(encoding='utf-8'))
            if job.get('settings', {}).get('agent_execution') == 'auto':
                name = job.get('agent_name') or job['settings'].get('agent_provider', 'agent')
                local_agents.append({'name': name, 'running': bool(task.get('_agent_auto_running'))})
        except (OSError, ValueError, KeyError):
            pass
    state['local_agents'] = local_agents[-5:]
    return state


# MCP 端点：SSE 传输（/mcp/sse，供 Cherry Studio 等 MCP 客户端接入）
MCP_SSE_ENABLED = False
MCP_HTTP_ENABLED = False
try:
    from .mcp_server import use_in_process_backend, mcp as mcp_app
    # 同进程调用端点函数：HTTP 自调会被 SSE 长连接阻塞（自调死锁）
    use_in_process_backend()
    transports = mcp_app.sse_app()
    transports.router.routes.extend(mcp_app.streamable_http_app().router.routes)

    @asynccontextmanager
    async def mcp_lifespan(_app):
        # The SDK manager is single-use; recreate it for each app lifespan.
        # Keep the same registered tools while refreshing the HTTP ASGI endpoint.
        mcp_app._session_manager = None
        http_transport = mcp_app.streamable_http_app()
        transports.router.routes[:] = [route for route in transports.router.routes
                                       if getattr(route, "path", None) != "/http"]
        transports.router.routes.extend(http_transport.router.routes)
        async with mcp_app.session_manager.run():
            yield

    app.router.lifespan_context = mcp_lifespan
    app.mount("/mcp", transports)
    MCP_SSE_ENABLED = True
    MCP_HTTP_ENABLED = True
except ImportError:
    # mcp 依赖未安装时跳过，不影响主服务
    pass


@app.get('/api/video-codecs')
async def video_codec_capabilities(refresh: bool = False):
    from .video_codecs import capabilities
    return await capabilities(refresh=refresh)

@app.get("/icon.png", include_in_schema=False)
async def app_icon() -> FileResponse:
    if not APP_ICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="应用图标不存在")
    return FileResponse(APP_ICON_PATH, media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    if not FAVICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="应用图标不存在")
    return FileResponse(FAVICON_PATH, media_type="image/x-icon")


from .agent_queue import router as agent_router
app.include_router(agent_router)



from .routers.tasks import create_router as create_task_router
app.include_router(create_task_router(start_summarize, start_transcribe, list_recent_tasks, get_task_status))

from .routers.settings import create_router as create_settings_router
app.include_router(create_settings_router({
    'get_llm_keys': get_llm_keys,
    'put_llm_key': put_llm_key,
    'delete_llm_key': delete_llm_key,
    'get_llm_config': get_llm_config,
    'save_llm_config': save_llm_config,
    'check_llm_vision': check_llm_vision,
    'test_llm_connection': test_llm_connection,
    'clear_llm_config': clear_llm_config,
    'get_bili_credentials': get_bili_credentials,
    'save_bili_credentials': save_bili_credentials,
    'clear_bili_credentials': clear_bili_credentials,
    'export_preferences': export_preferences,
    'preview_preferences_restore': preview_preferences_restore,
    'restore_preferences': restore_preferences,
    'get_preferences': get_preferences,
    'put_preferences': put_preferences,
    'get_ui_language': get_ui_language,
    'set_ui_language': set_ui_language,
    'get_autostart': get_autostart,
    'set_autostart': set_autostart,
    'open_app_log': open_app_log,
    'start_gpu_runtime_install': start_gpu_runtime_install,
    'get_gpu_runtime_install': get_gpu_runtime_install,
}))

from .transcript_review import router as transcript_review_router
app.include_router(transcript_review_router)

from .routers.exports import create_router as create_exports_router
app.include_router(create_exports_router({
    'get_task_transcript': get_task_transcript,
    'get_image': get_image,
    'download_summary_markdown': download_summary_markdown,
}))

from .desktop_tools import create_router as create_desktop_router
app.include_router(create_desktop_router(sys.modules[__name__]))

if FRONTEND_DIR.is_dir():
    app.mount(
        "/",
        NoCacheStaticFiles(directory=FRONTEND_DIR, html=True),
        name="frontend",
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    os.environ.setdefault("AI_VIDEO_NOTE_BACKEND_URL", f"http://127.0.0.1:{port}")
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
    )
