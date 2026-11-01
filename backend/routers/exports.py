"""Exports route registration."""
from fastapi import APIRouter


def create_router(handlers):
    router = APIRouter(tags=["exports"])
    router.add_api_route('/api/task/{task_id}/transcript', handlers['get_task_transcript'], methods=['GET'])
    router.add_api_route('/api/image/{task_id}/{filename}', handlers['get_image'], methods=['GET'])
    router.add_api_route('/api/download/{task_id}', handlers['download_summary_markdown'], methods=['GET'])
    return router
