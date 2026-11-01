"""Atomic task runtime persistence, independent of the HTTP application."""
from __future__ import annotations
from .task_storage import update_manifest
import threading
import time
from datetime import datetime, timezone
from typing import Any

def persist_task_runtime(task_id: str, tasks, task_directory, task_elapsed_seconds) -> None:
    """Persist non-secret runtime state atomically beside task artifacts."""
    task = tasks.get(task_id)
    if not task or task.get('_deleting'):
        return
    directory = task_directory(task_id)

    def update(payload):
        # Fetch the current task only after acquiring the file lock, so a queued
        # save cannot restore a snapshot captured before cancellation/completion.
        task = tasks.get(task_id)
        if not task or task.get('_deleting'):
            return False
        payload.setdefault('task_id', task_id)
        result = task.get("result")
        result_metadata = None
        if isinstance(result, dict):
            result_metadata = {key: value for key, value in result.items() if key != "markdown"}
        payload["runtime"] = {
            "status": task.get("status"),
            "step": task.get("step", 0),
            "step_name": task.get("step_name", ""),
            "progress": task.get("progress", 0),
            "logs": list(task.get("logs", []))[-200:],
            "error": task.get("error"),
            "advisory": task.get("advisory"),
            "screenshot_warning": task.get("screenshot_warning"),
            "video_path": task.get("video_path"),
            "created_at": task.get("created_at"),
            "finished_at": task.get("finished_at"),
            "elapsed_seconds": task_elapsed_seconds(task),
            "result": result_metadata,
            "agent_runtime": task.get("agent_runtime"),
            "transcript_review": task.get("transcript_review"),
        }

    update_manifest(directory, update)



def new_task(status: str = "pending", task_id: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "step": 0,
        "step_name": "初始化",
        "progress_message": "等待任务开始",
        "progress": 0,
        "logs": [],
        "result": None,
        "error": None,
        "advisory": None,
        "screenshot_warning": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "elapsed_seconds": 0.0,
        "_task_id": task_id,
        "_cancel_requested": False,
        "_cancel_event": threading.Event(),
        "_started_monotonic": time.monotonic(),
    }


def task_elapsed_seconds(task: dict[str, Any]) -> float:
    if task.get("finished_at"):
        return float(task.get("elapsed_seconds") or 0)
    started = task.get("_started_monotonic")
    return max(0.0, time.monotonic() - float(started)) if started is not None else 0.0


def finish_task_timing(task: dict[str, Any]) -> float:
    elapsed = task_elapsed_seconds(task)
    task["elapsed_seconds"] = elapsed
    task["finished_at"] = datetime.now(timezone.utc).isoformat()
    return elapsed
