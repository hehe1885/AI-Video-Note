"""HTTP 单文件下载：断点续传（Range）+ 重试 + Content-Length 完整性校验。

whisper 与 paraformer 两条模型下载线共用；损坏缓存的历史教训都在这个实现里：
连接中断但未抛异常时会得到截断文件，必须保留 .part 断点续传，
绝不把不完整文件改名成正式文件。
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path


_progress_local = threading.local()

@contextmanager
def report_downloads(callback):
    previous = getattr(_progress_local, "callback", None)
    _progress_local.callback = callback
    try:
        yield
    finally:
        _progress_local.callback = previous


def report_progress(target, received, total):
    callback = getattr(_progress_local, "callback", None)
    if callback:
        callback(str(target), received, total)


def _download_part(url, target, part, headers, cancel_event, timeout):
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError("任务已取消")
    resume = part.stat().st_size if part.is_file() else 0
    request_headers = dict(headers)
    if resume:
        request_headers["Range"] = f"bytes={resume}-"
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        length_header = response.headers.get("Content-Length", "")
        length = int(length_header) if length_header.isdigit() else None
        if response.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if match is None:
                raise RuntimeError("下载响应缺少有效的 Content-Range")
            start, end, total = map(int, match.groups())
            if start != resume or not start <= end < total or (length is not None and length != end - start + 1):
                raise RuntimeError("下载响应的字节范围与请求不一致")
            response_end = end + 1
        elif response.status == 200:
            resume = 0  # The server ignored Range and returned the full file.
            response_end = total = length
        else:
            raise RuntimeError(f"下载响应状态异常：HTTP {response.status}")

        report_progress(target, resume, total)
        with part.open("ab" if resume else "wb") as out:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("任务已取消")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                if response_end is not None and out.tell() + len(chunk) > response_end:
                    out.truncate(resume)
                    raise RuntimeError("下载内容超出响应声明的字节范围")
                out.write(chunk)
                report_progress(target, out.tell(), total)

    size = part.stat().st_size
    if not size:
        part.unlink(missing_ok=True)
        raise RuntimeError("下载内容为空")
    if response_end is not None and size != response_end:
        raise RuntimeError(f"下载不完整：已接收 {size} 字节，预期 {response_end} 字节")
    return total is None or size == total


def download_to_file(
    url: str,
    target: Path,
    headers: dict[str, str],
    cancel_event: threading.Event | None = None,
    attempts: int = 3,
    timeout: int = 60,
) -> tuple[bool, Exception | None]:
    """下载 url 到 target。返回 (是否成功, 最后一次错误)；成功时错误为 None。"""
    part = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    restarted = False
    for attempt in range(attempts):
        try:
            while True:
                try:
                    if _download_part(url, target, part, headers, cancel_event, timeout):
                        break
                except urllib.error.HTTPError as exc:
                    if exc.code != 416 or restarted or not part.is_file():
                        raise
                    # Revalidate from byte zero instead of promoting an unverified partial file.
                    exc.close()
                    part.unlink(missing_ok=True)
                    restarted = True
            part.replace(target)
            return True, None
        except Exception as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                # 仓库里没有这个文件：重试只会白等，立刻返回让调用方换名或换源
                return False, exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    return False, last_error
