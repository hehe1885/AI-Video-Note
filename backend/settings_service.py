"""Settings handlers with injected application services."""
from __future__ import annotations

async def get_llm_keys(app_services, base_url: str = "", provider: str = "") -> dict[str, Any]:
    """列出本机保存的接口 Key（只有掩码）；带 base_url 时顺带回答"这个地址存了没有"。"""
    if app_services.config_store.keys_are_corrupt():
        return {"storage": app_services._key_storage_info(), "error": "corrupt_keys_file", "entries": []}
    payload: dict[str, Any] = {"storage": app_services._key_storage_info(), "entries": app_services.config_store.list_keys()}
    if base_url or provider:
        resolved = app_services.config_store.resolve_stored_key(model_type=provider, base_url=base_url)
        entry = resolved.get("entry")
        key_state = "none"
        if entry:
            key_state = "saved" if resolved.get("api_key") else "undecryptable"
        payload["match"] = {
            "saved": bool(entry),
            "key_state": key_state,
            "endpoint": app_services.normalize_endpoint_host(base_url, provider),
            **(entry or {}),
        }
    return payload


async def put_llm_key(app_services, payload: LLMKeyPayload) -> dict[str, Any]:
    """显式保存：把某个接口地址的 API Key 加密写入本机。清除只能走 DELETE。"""
    try:
        entry = app_services.config_store.put_key(
            provider=payload.provider,
            base_url=payload.base_url or "",
            api_key=payload.api_key.get_secret_value(),
            label=payload.label,
            model=payload.model,
        )
    except ValueError as error:
        raise app_services.HTTPException(status_code=400, detail=str(error)) from error
    except app_services.secret_box.SecretBoxError as error:
        raise app_services.HTTPException(status_code=500, detail=f"本机加密保存失败：{error}") from error
    return {"saved": True, **entry}


async def delete_llm_key(app_services, base_url: str = "", provider: str = "") -> dict[str, Any]:
    return {"removed": app_services.config_store.delete_key(base_url=base_url, model_type=provider)}


async def get_llm_config(app_services) -> dict[str, Any]:
    """旧接口：报告本机最近保存的一条配置（含掩码，不含 Key 原文），供 MCP 免传参复用。"""
    entry = app_services.config_store.most_recent_entry()
    if not entry or not entry["has_key"]:
        return {"saved": False}
    return {
        "saved": True,
        "model_type": entry["provider"],
        "model": entry["model"],
        "base_url": entry["base_url"] or None,
        "api_key_masked": entry["api_key_masked"] or None,
    }


async def save_llm_config(app_services, payload: LLMConfigPayload) -> dict[str, Any]:
    """旧接口：等价于按接口地址保存 Key（MCP 的 save_llm_config 仍走这里）。"""
    try:
        entry = app_services.config_store.put_key(
            provider=payload.model_type,
            base_url=payload.base_url or "",
            api_key=payload.api_key.get_secret_value(),
            label=payload.label,
            model=payload.model,
        )
    except ValueError as error:
        raise app_services.HTTPException(status_code=400, detail=str(error)) from error
    except app_services.secret_box.SecretBoxError as error:
        raise app_services.HTTPException(status_code=500, detail=f"本机加密保存失败：{error}") from error
    return {
        "saved": True,
        "model_type": entry["provider"],
        "model": entry["model"],
        "label": entry["label"],
    }


async def check_llm_vision(app_services, body: LLMConfig, refresh: bool = False) -> dict[str, Any]:
    base_url = body.base_url or body.custom_base_url
    try:
        api_key, _ = app_services.resolve_llm_credentials(model_type=body.model_type, base_url=base_url,
                                            api_key=body.api_key.get_secret_value())
        summarizer = app_services.LLMSummarizer(model_type=body.model_type, api_key=api_key,
                                   base_url=base_url, model=body.model or body.custom_model_name)
        return await app_services.probe_vision(summarizer, api_key, refresh=refresh)
    except Exception:
        return {"state": "error", "supported": False, "message": "请先配置有效的模型接口与 API Key"}


async def test_llm_connection(app_services, 
    payload: app_services.LLMTestPayload | None = None,
) -> dict[str, Any]:
    """用（可选覆盖的）LLM 配置做一次轻量连通测试。"""
    body = payload or app_services.LLMTestPayload()
    model_type = body.model_type or "deepseek"
    base_url = body.base_url or app_services.default_base_url(model_type) or None
    try:
        api_key, key_source = app_services.resolve_llm_credentials(
            model_type=model_type,
            base_url=base_url,
            api_key=body.api_key.get_secret_value() if body.api_key else "",
        )
    except RuntimeError as error:
        return {"ok": False, "error": str(error)}
    except Exception as error:
        # 密钥档案读不了（磁盘被拖垮 / DPAPI 失灵 / 文件损坏）不该裸 500：
        # 旧版悬案里前端只剩「（HTTP 500）」，就是这类异常漏出去了
        return {
            "ok": False,
            "error": app_services.utf8_safe(f"读取本机保存的 Key 失败：{type(error).__name__}: {error}"),
        }
    try:
        summarizer = app_services.LLMSummarizer(
            model_type=model_type,
            api_key=api_key,
            base_url=base_url,
            model=body.model,
        )
        ok, message, latency = await summarizer.test_connection()
        return {
            "ok": ok,
            "message": message,
            "latency_ms": int(round(latency * 1000)),
            "model": body.model or summarizer.model,
            "base_url": summarizer.base_url,
            "key_source": key_source,
        }
    except Exception as exc:
        return {"ok": False, "error": app_services.utf8_safe(f"初始化失败：{exc}")}


async def clear_llm_config(app_services) -> dict[str, Any]:
    """旧接口：只清除它刚报告的那一条，不会一次清空全部本机 Key。"""
    entry = app_services.config_store.most_recent_entry()
    removed = bool(entry) and app_services.config_store.delete_key(
        base_url=entry["base_url"], model_type=entry["provider"]
    )
    return {"saved": False, "removed": removed}


async def get_bili_credentials(app_services) -> dict[str, Any]:
    """Return saved Bilibili credential status without exposing cookie values.

    走 ``bili_credentials_status()``：只用保存时写死的掩码，全程不解密，
    所以换机器解不开时这个端点仍然有得报。
    """
    return app_services.config_store.bili_credentials_status()


async def save_bili_credentials(app_services, payload: BiliCredentialsPayload) -> dict[str, Any]:
    try:
        app_services.config_store.save_bili_credentials(
            {"sessdata": payload.sessdata, "bili_jct": payload.bili_jct, "buvid3": payload.buvid3}
        )
    except ValueError as error:
        raise app_services.HTTPException(status_code=400, detail=str(error)) from error
    except app_services.secret_box.SecretBoxError as error:
        raise app_services.HTTPException(status_code=500, detail=f"本机加密保存失败：{error}") from error
    return {"saved": True}


async def clear_bili_credentials(app_services) -> dict[str, bool]:
    await app_services.bili_login_manager.cancel()
    app_services.config_store.clear_bili_credentials()
    return {"saved": False}


async def get_ui_language(app_services):
    from .ui_language import load_language
    return {"language": load_language(app_services.WORKSPACE_DIR),
            "saved": (app_services.WORKSPACE_DIR / '_ui_language.json').is_file()}


async def set_ui_language(app_services, payload: UiLanguagePayload):
    from .ui_language import save_language
    save_language(app_services.WORKSPACE_DIR, payload.language)
    return {"language": payload.language}


async def get_autostart(app_services):
    import launcher
    if launcher.sys.platform != 'win32':
        return {'supported': False, 'enabled': False}
    try:
        enabled = await app_services.asyncio.to_thread(launcher.autostart_enabled, migrate=False)
    except OSError as error:
        raise app_services.HTTPException(status_code=503, detail='读取开机自启动设置失败') from error
    return {'supported': True, 'enabled': enabled}


async def set_autostart(app_services, payload):
    import launcher
    if launcher.sys.platform != 'win32':
        raise app_services.HTTPException(status_code=400, detail='当前系统暂不支持开机自启动')
    try:
        await app_services.asyncio.to_thread(launcher.set_autostart, payload.enabled)
        return await get_autostart(app_services)
    except OSError as error:
        raise app_services.HTTPException(status_code=503, detail='设置开机自启动失败') from error


async def open_app_log(app_services):
    import launcher
    path = app_services.WORKSPACE_DIR / '_app.log'
    if not path.is_file():
        raise app_services.HTTPException(status_code=404, detail='运行日志尚未生成')
    try:
        await app_services.asyncio.to_thread(launcher.open_in_shell, path)
    except OSError as error:
        raise app_services.HTTPException(status_code=503, detail='打开日志失败') from error
    return {'opened': True}




async def _preferences_operation(app_services, payload=None):
    from .preferences_store import PreferencesStore, PreferencesError
    from filelock import Timeout
    store = PreferencesStore(app_services.WORKSPACE_DIR)
    try:
        if payload is None:
            return await app_services.asyncio.to_thread(store.load)
        return await app_services.asyncio.to_thread(store.save, payload.preferences, payload.expected_revision, payload.import_source)
    except PreferencesError as error:
        raise app_services.HTTPException(error.status, detail={'code': error.code, 'message': str(error)}) from error
    except (OSError, Timeout) as error:
        raise app_services.HTTPException(503, detail={'code': 'preferences_unavailable', 'message': '设置暂时无法读写，已有文件保持不变'}) from error


async def get_preferences(app_services):
    return await _preferences_operation(app_services)


async def put_preferences(app_services, payload):
    return await _preferences_operation(app_services, payload)


async def preferences_backup_operation(app_services, action, payload=None):
    from . import preferences_backup as backup
    from .preferences_store import PreferencesError
    from filelock import Timeout
    try:
        if action == 'export': return await app_services.asyncio.to_thread(backup.export_settings, app_services.WORKSPACE_DIR)
        args = [app_services.WORKSPACE_DIR, payload.document]
        if action == 'preview': return await app_services.asyncio.to_thread(backup.preview_restore, *args)
        return await app_services.asyncio.to_thread(backup.restore_settings, *args, payload.backup_revision, payload.current_revision)
    except PreferencesError as error:
        raise app_services.HTTPException(error.status, detail={'code': error.code, 'message': str(error)}) from error
    except (OSError, Timeout) as error:
        raise app_services.HTTPException(503, detail={'code': 'backup_unavailable', 'message': '备份暂时无法读写，未修改设置'}) from error
