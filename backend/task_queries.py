"""Task history and status queries with injected application services."""
from __future__ import annotations

def task_status_payload(runtime, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in task.items() if not key.startswith("_")}
    payload["elapsed_seconds"] = runtime.task_elapsed_seconds(task)
    manifest = runtime.read_task_manifest(task_id)
    payload["note_format"] = manifest.get("note_format", "markdown")
    payload["generation_mode"] = manifest.get("generation_mode", "api")
    payload["generation_action"] = manifest.get("generation_action", "regenerate")
    payload["agent_auto_running"] = bool(task.get("_agent_auto_running"))
    source_url = str(manifest.get("source_url") or "")
    payload["source_url"] = source_url if source_url.startswith(("http://", "https://")) else None
    payload["uploaded_filename"] = payload.get("uploaded_filename") or manifest.get(
        "uploaded_filename"
    )
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    payload["download_name"] = runtime.download_stem(
        str(
            result.get("title")
            or manifest.get("title")
            or manifest.get("uploaded_filename")
            or ""
        ),
        manifest,
        str(task.get("output") or manifest.get("output") or "note"),
    )
    return payload


async def list_recent_tasks(runtime, **filters):
    from .history_queries import query_history
    return await query_history(runtime, **filters)


async def get_task_status(runtime, task_id: str) -> dict[str, Any]:
    runtime.ensure_task_loaded(task_id)
    task = runtime.tasks.get(task_id)
    if task is None:
        raise runtime.HTTPException(status_code=404, detail="任务不存在或已被清理")
    return runtime.task_status_payload(task_id, task)


