"""Settings route registration."""
from fastapi import APIRouter


def create_router(handlers):
    router = APIRouter(tags=["settings"])
    router.add_api_route('/api/llm-keys', handlers['get_llm_keys'], methods=['GET'])
    router.add_api_route('/api/llm-keys', handlers['put_llm_key'], methods=['PUT'])
    router.add_api_route('/api/llm-keys', handlers['delete_llm_key'], methods=['DELETE'])
    router.add_api_route('/api/llm-config', handlers['get_llm_config'], methods=['GET'])
    router.add_api_route('/api/llm-config', handlers['save_llm_config'], methods=['POST'])
    router.add_api_route('/api/llm-vision', handlers['check_llm_vision'], methods=['POST'])
    router.add_api_route('/api/llm-test', handlers['test_llm_connection'], methods=['POST'])
    router.add_api_route('/api/llm-config', handlers['clear_llm_config'], methods=['DELETE'])
    router.add_api_route('/api/bili-credentials', handlers['get_bili_credentials'], methods=['GET'])
    router.add_api_route('/api/bili-credentials', handlers['save_bili_credentials'], methods=['POST'])
    router.add_api_route('/api/bili-credentials', handlers['clear_bili_credentials'], methods=['DELETE'])
    router.add_api_route('/api/ui/language', handlers['get_ui_language'], methods=['GET'])
    router.add_api_route('/api/ui/language', handlers['set_ui_language'], methods=['POST'])
    router.add_api_route('/api/autostart', handlers['get_autostart'], methods=['GET'])
    router.add_api_route('/api/autostart', handlers['set_autostart'], methods=['PUT'])
    router.add_api_route('/api/log/open', handlers['open_app_log'], methods=['POST'])
    router.add_api_route('/api/gpu-runtime-install', handlers['start_gpu_runtime_install'], methods=['POST'])
    router.add_api_route('/api/gpu-runtime-install', handlers['get_gpu_runtime_install'], methods=['GET'])
    router.add_api_route('/api/preferences', handlers['get_preferences'], methods=['GET'])
    router.add_api_route('/api/preferences', handlers['put_preferences'], methods=['PUT'])
    router.add_api_route('/api/preferences/export', handlers['export_preferences'], methods=['GET'])
    router.add_api_route('/api/preferences/restore/preview', handlers['preview_preferences_restore'], methods=['POST'])
    router.add_api_route('/api/preferences/restore', handlers['restore_preferences'], methods=['POST'])
    return router
