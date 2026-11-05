"""Visible stage logs and bounded heartbeat frequency for long-running work."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import time

HEARTBEAT_SECONDS = 20


def active(task):
    return (task.get('status') in {'processing', 'awaiting_agent'} and
            not task.get('_cancel_requested') and not task.get('_deleting'))


def emit(runtime, task_id, message, update_message=True):
    task = runtime.tasks.get(task_id)
    if not task or not active(task): return
    task['logs'].append(message)
    if update_message: task['progress_message'] = message
    runtime.persist_task_runtime(task_id)


@asynccontextmanager
async def activity(runtime, task_id, stage, waiting='正在等待模型回复', interval=None):
    task = runtime.tasks[task_id]
    previous, previous_message = task.get('_activity_token'), task.get('progress_message')
    token = object()
    task['_activity_token'] = token
    started = time.monotonic()
    emit(runtime, task_id, f'{stage}：开始')
    async def report(message):
        if task.get('_activity_token') is token:
            emit(runtime, task_id, f'{stage}：{message}')
    async def heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS if interval is None else interval)
            if not active(task): return
            await report(f'{waiting}，已用 {int(time.monotonic() - started)} 秒')
    worker = asyncio.create_task(heartbeat())
    try:
        yield report
    except BaseException as error:
        emit(runtime, task_id, f'{stage}：' + ('已取消' if isinstance(error, asyncio.CancelledError) else '未完成，请查看错误提示'))
        raise
    else:
        emit(runtime, task_id, f'{stage}：完成，用时 {int(time.monotonic() - started)} 秒')
    finally:
        worker.cancel()
        # Cancellation is consumed by this callback without adding an extra yield
        # between a completed model response and publishing its review gate.
        worker.add_done_callback(lambda job: None if job.cancelled() else job.exception())
        if task.get('_activity_token') is token:
            task['_activity_token'] = previous
            if previous is not None and active(task):
                task['progress_message'] = previous_message


@asynccontextmanager
async def watch_pipeline(runtime, task_id, interval=None):
    """Observe changing media stages; waiting for the user's confirmation stays quiet."""
    task = runtime.tasks[task_id]
    async def heartbeat():
        last_stage, started, next_report = None, time.monotonic(), 0.
        while True:
            now = time.monotonic()
            stage = task.get('step_name') or '视频处理'
            paused = (not active(task) or task.get('status') != 'processing' or
                      (task.get('transcript_review') or {}).get('status') == 'pending' or
                      task.get('_activity_token') is not None)
            if paused or stage != last_stage:
                last_stage, started = stage, now
                next_report = now + (HEARTBEAT_SECONDS if interval is None else interval)
            elif now >= next_report:
                emit(runtime, task_id, f'{stage}：仍在处理，阶段已用 {int(now - started)} 秒', update_message=False)
                next_report = now + (HEARTBEAT_SECONDS if interval is None else interval)
            await asyncio.sleep(1 if interval is None else min(1, interval))
    worker = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
