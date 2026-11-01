"""Prepare a browser-compatible audio excerpt from the task's own source media."""
from __future__ import annotations
import asyncio
import math
import wave
from pathlib import Path
from fastapi import HTTPException
from .generation_store import fingerprint
from .note_review import find_media


def page_ranges(manifest, start, end):
    """Map a combined timeline to exact page-local intervals, including boundaries."""
    pages = manifest.get('bili_pages') or []
    if not pages:
        selected = manifest.get('bilibili_pages') or []
        return [(selected[0] if len(selected) == 1 else None, start, end)]
    if len(pages) == 1:
        return [(pages[0]['page'], start, end)]
    intervals, offset = [], 0.0
    for page in pages:
        duration = page.get('duration')
        if type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0:
            raise HTTPException(422, '无法定位分 P 音频，请检查视频时长')
        left, right = max(start, offset), min(end, offset + duration)
        if right > left:
            intervals.append((page['page'], left - offset, right - offset))
        offset += duration
    if not intervals:
        raise HTTPException(422, '音频时间超出视频范围')
    return intervals


async def source_parts(runtime, task_id, start, end, should_abort):
    manifest = runtime.read_task_manifest(task_id)
    source = manifest.get('source_url')
    if not source:
        media = find_media(task_id, runtime.WORKSPACE_DIR, runtime.tasks, runtime.read_task_manifest)
        if media is None:
            raise HTTPException(404, '没有可播放的本地音频，请重新上传源文件')
        return [(media, start, end)]
    task = runtime.tasks[task_id]
    options = task.get('_review_audio_options') or {}
    cookie = options.get('cookie')
    if cookie is None and runtime.video_processor.detect_source(source) == runtime.VideoSource.BILIBILI:
        cookie = runtime.config_store.load_bili_credentials()
    parts = []
    for page, left, right in page_ranges(manifest, start, end):
        url = runtime.bilibili_page_url(source, page) if page else source
        media = await runtime.video_processor.download_audio(
            url, task_id, cookie, media_name=f'audio_p{page}' if page else 'audio',
            should_abort=should_abort, notes=task['logs'],
            video_directory=manifest.get('video_download_directory') or '')
        parts.append((media, left, right))
    runtime.persist_task_runtime(task_id)
    return parts


def write_clip(parts, target: Path, should_abort=lambda: False):
    """Decode audio only, seek to the excerpt, and trim PCM at sample boundaries."""
    import av
    rate, samples = 24000, 0
    temporary = target.with_suffix('.tmp.wav')
    try:
        with wave.open(str(temporary), 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
            for path, start, end in parts:
                with av.open(str(path)) as media:
                    if not media.streams.audio:
                        raise ValueError('No source audio track')
                    stream = media.streams.audio[0]
                    base = float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0
                    try:
                        media.seek(int((start + base) / stream.time_base), stream=stream, backward=True)
                    except (ValueError, av.error.FFmpegError):
                        media.seek(0)
                    resampler = av.AudioResampler(format='s16', layout='mono', rate=rate)
                    cursor = 0.0
                    def write_frame(frame):
                        nonlocal cursor, samples
                        position = float(frame.time) - base if frame.time is not None else cursor
                        cursor = position + frame.samples / rate
                        left = max(0, min(frame.samples, round((start - position) * rate)))
                        right = max(0, min(frame.samples, round((end - position) * rate)))
                        if right > left:
                            data = frame.to_ndarray().reshape(-1)[left:right].astype('<i2', copy=False)
                            output.writeframesraw(data.tobytes()); samples += right - left
                    for decoded in media.decode(stream):
                        if should_abort(): raise asyncio.CancelledError()
                        for frame in resampler.resample(decoded): write_frame(frame)
                        if decoded.time is not None and float(decoded.time) - base >= end: break
                    for frame in resampler.resample(None): write_frame(frame)
        if not samples: raise ValueError('No audio in requested interval')
        if should_abort(): raise asyncio.CancelledError()
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


async def prepare_clip(runtime, task_id, revision, item):
    task = runtime.tasks[task_id]
    try:
        start, end = float(item['start']), float(item['end'])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, '音频时间无效') from None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise HTTPException(422, '音频时间无效')
    def should_abort():
        metadata = task.get('transcript_review') or {}
        return (task.get('_cancel_requested') or task.get('_deleting') or
                task.get('status') not in {'processing', 'awaiting_agent'} or
                metadata.get('status') != 'pending' or metadata.get('revision') != revision)
    async with task.setdefault('_transcript_audio_lock', asyncio.Lock()):
        if should_abort(): raise HTTPException(409, '待确认内容已变化，请刷新任务后重试')
        directory = runtime.task_directory(task_id) / 'review-audio'
        directory.mkdir(exist_ok=True)
        target = directory / (fingerprint([revision, item['id'], start, end]) + '.wav')
        if target.is_file() and target.stat().st_size > 44: return target
        try:
            parts = await source_parts(runtime, task_id, start, end, should_abort)
            await asyncio.to_thread(write_clip, parts, target, should_abort)
            if should_abort(): raise HTTPException(409, '待确认内容已变化，请刷新任务后重试')
            return target
        except HTTPException:
            raise
        except asyncio.CancelledError:
            raise HTTPException(409, '任务已停止或待确认内容已变化') from None
        except Exception:
            raise HTTPException(502, '音频准备失败，请检查源文件、网络或平台访问凭据后重试') from None
