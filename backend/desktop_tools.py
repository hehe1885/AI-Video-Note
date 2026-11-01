"""Desktop result actions and guarded storage maintenance routes."""
import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .output_locations import locations
from .storage_usage import CleanupPlans, StorageError, inventory


class OpenOutput(BaseModel):
    target: Literal['file', 'folder']
    model_config = {'extra': 'forbid'}


class PreviewCleanup(BaseModel):
    kinds: list[Literal['audio', 'video']] = Field(min_length=1, max_length=2)
    model_config = {'extra': 'forbid'}


class ConfirmCleanup(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    model_config = {'extra': 'forbid'}


def busy(runtime):
    return (getattr(runtime, '_storage_cleaning', False)
            or any(task.get('status') in {'pending', 'queued', 'processing', 'cancelling', 'awaiting_agent'}
                   or any(task.get(flag) for flag in ('_deleting', '_exporting', '_agent_finishing', '_agent_auto_running', '_agent_capture'))
                   for task in runtime.tasks.values())
            or any(not job.done() for job in runtime.running_jobs.values())
            or any(not job.done() for job in runtime._active_transcriptions)
            or runtime.model_downloads.active()
            or runtime.environment_repair.snapshot()['status'] == 'running')


def create_router(runtime):
    router = APIRouter(tags=['desktop tools'])
    plans = CleanupPlans()

    @router.get('/api/task/{task_id}/output-location')
    async def output_location(task_id: str):
        return locations(runtime, task_id)

    @router.post('/api/task/{task_id}/output/open')
    async def open_output(task_id: str, body: OpenOutput):
        result = locations(runtime, task_id)
        import launcher
        path = Path(result['file_path'] if body.target == 'file' else result['directory'])
        try: await asyncio.to_thread(launcher.open_in_shell, path)
        except OSError as error:
            raise runtime.HTTPException(503, '无法打开输出位置，请检查文件关联或访问权限') from error
        return {'opened': True, **result}

    @router.get('/api/storage')
    async def storage():
        usage, entries = await asyncio.to_thread(inventory, runtime)
        return {**usage, 'can_cleanup': not busy(runtime)}

    @router.post('/api/storage/preview')
    async def preview(body: PreviewCleanup):
        result = await asyncio.to_thread(plans.preview, runtime, set(body.kinds))
        return {**result, 'can_cleanup': not busy(runtime)}

    @router.post('/api/storage/cleanup')
    async def cleanup(body: ConfirmCleanup):
        if busy(runtime): raise runtime.HTTPException(409, '请等待任务、模型下载或环境修复结束后再清理')
        runtime._storage_cleaning = True
        worker = asyncio.create_task(asyncio.to_thread(plans.execute, runtime, body.token))
        cancelled = False
        try:
            while True:
                try:
                    result = await asyncio.shield(worker)
                    break
                except asyncio.CancelledError:
                    if worker.cancelled(): raise
                    cancelled = True
            if cancelled: raise asyncio.CancelledError()
            return result
        except StorageError as error:
            raise runtime.HTTPException(409, str(error)) from error
        finally: runtime._storage_cleaning = False

    return router
