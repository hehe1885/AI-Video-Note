"""Persistent model location and deletion restricted to app-managed model folders."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

SETTINGS_FILE = "model_storage.json"


def load_directory(workspace: Path, default: Path) -> Path:
    path = workspace / SETTINGS_FILE
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or type(payload.get('schema', 1)) is not int or payload.get('schema', 1) > 1:
        raise ValueError('模型目录配置来自更新版本或格式无效，原文件已保留')
    value = payload.get("directory", "")
    if not isinstance(value, str):
        raise ValueError("模型目录配置无效")
    if not value:
        return default
    selected = Path(value).expanduser()
    return (selected if selected.is_absolute() else workspace.parent / selected).resolve()


def save_directory(workspace: Path, directory: str) -> None:
    existing = workspace / SETTINGS_FILE
    if existing.exists():
        # Validate before overwriting an unreadable/newer location record.
        load_directory(workspace, workspace / '_model_cache')
    if directory:
        selected = Path(directory).resolve()
        project = workspace.parent.resolve()
        if selected.is_relative_to(project):
            directory = selected.relative_to(project).as_posix()
    path = workspace / SETTINGS_FILE
    temporary = workspace / f".{SETTINGS_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"schema": 1, "directory": directory}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def model_directories(root: Path, model: str, repo: str | None = None) -> list[Path]:
    if model == "paraformer-zh":
        return [root / "sherpa" / name for name in ("asr", "punc", "vad")]
    if not repo:
        raise ValueError("缺少模型仓库信息")
    return [root / "manual" / model, root / "manual" / f"faster-whisper-{model}",
            root / f"models--{repo.replace('/', '--')}", root / "vulkan" / model]


def validate_deletion(root: Path, directories: list[Path]) -> list[Path]:
    """Validate every target before deleting anything; reject directory links/junctions."""
    boundary = root.resolve()
    existing = []
    for target in directories:
        resolved = target.resolve()
        if resolved == boundary or not resolved.is_relative_to(boundary):
            raise ValueError("模型目录超出了当前下载位置，拒绝删除")
        # Check intermediate directories as well (e.g. manual is a junction).
        for part in (target, *target.parents):
            if part == root:
                break
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise ValueError("模型目录包含链接，拒绝删除；请手动管理该目录")
        if not target.exists():
            continue
        if not target.is_dir():
            raise ValueError("模型路径不是文件夹")
        for current, dirs, _files in os.walk(target, followlinks=False):
            for name in dirs:
                child = Path(current) / name
                if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                    raise ValueError("模型目录包含链接，拒绝删除；请手动管理该目录")
        existing.append(target)
    return existing


def delete_directories(directories: list[Path]) -> None:
    for target in directories:
        shutil.rmtree(target)
