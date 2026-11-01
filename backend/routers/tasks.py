"""Task entry points, separate from pipeline orchestration and persistence."""
from fastapi import APIRouter


def create_router(start_summarize, start_transcribe, list_recent_tasks, get_task_status):
    router = APIRouter(tags=["tasks"])
    router.add_api_route("/api/summarize", start_summarize, methods=["POST"])
    router.add_api_route("/api/transcribe", start_transcribe, methods=["POST"])
    router.add_api_route("/api/tasks", list_recent_tasks, methods=["GET"])
    router.add_api_route("/api/task/{task_id}", get_task_status, methods=["GET"])
    return router
