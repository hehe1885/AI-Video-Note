"""Map screenshot timestamps to the media used by the transcript."""
import asyncio
import math
from pathlib import Path

from .video_processor import bilibili_page_url


def screenshot_sources(local_path, url, pages):
    if local_path or not pages:
        return [{"url": url, "path": str(local_path) if local_path else None, "start": 0, "end": None}]
    if len(pages) == 1:
        return [{"url": bilibili_page_url(url, pages[0]["page"]), "path": None, "start": 0, "end": None}]
    sources = []
    start = 0.0
    for page in pages:
        duration = float(page.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("B 站分 P 时长无效，无法匹配截图时间")
        end = start + duration
        sources.append({"url": bilibili_page_url(url, page["page"]), "path": None, "start": start, "end": end})
        start = end
    return sources


def _contains(source, time):
    return time >= source["start"] and (source["end"] is None or time < source["end"])


async def prepare_screenshot_sources(runtime, request, task_id, sources, cookie, should_abort, notes, title, times=None):
    for source in sources:
        if source["path"] or (times is not None and not any(_contains(source, time) for time in times)):
            continue
        runtime.raise_if_cancel_requested(runtime.tasks[task_id])
        url = source["url"] or ""
        path = await asyncio.to_thread(runtime.reuse_preview_video, request, task_id, url)
        if path is None:
            path = await runtime.video_processor.download_preview_video(
                url, task_id, cookie, should_abort, notes,
                max_height=request.video_resolution, download_directory=request.video_download_directory,
                title=title, **({"video_encoding": request.video_encoding} if request.video_encoding == "size" else {}),
            )
            runtime.tasks[task_id]["logs"].append(f"截图视频已保存：{path}（最高 {request.video_resolution}p）")
        else:
            runtime.tasks[task_id]["logs"].append(f"已复用下载的视频，无需重新下载：{path}")
        source["path"] = str(path)


async def extract_screenshot_frames(processor, task_id, times, should_abort, sources):
    frames = {}
    for source in sources:
        if not source["path"]:
            continue
        local_times = {time - source["start"]: time for time in times if _contains(source, time)}
        if local_times:
            captured = await processor.extract_frames_at(Path(source["path"]), task_id, list(local_times), should_abort)
            frames.update({local_times[time]: path for time, path in captured.items()})
    return frames
