"""Save independent notes and attachments using Obsidian's four folder modes."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote
from .note_markdown import normalize_bold_spacing, rewrite_frame_links


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("笔记和附件必须位于 Obsidian 仓库内")
    return resolved


def _relative_folder(value: str) -> Path:
    # Accept both separators even when validating on another platform.
    value = value.strip().replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or ":" in value or ".." in path.parts:
        raise ValueError("仓库内文件夹请填写相对路径，不能使用绝对路径或 ..")
    return path


def resolve_folders(vault: str, note_folder: str, attachment_mode: str,
                    attachment_folder: str) -> tuple[Path, Path, Path]:
    root = Path(vault.strip()).expanduser()
    if not vault.strip() or not root.is_absolute() or not root.is_dir():
        raise ValueError("请选择已存在的 Obsidian 仓库文件夹，填写完整绝对路径")
    root = root.resolve()
    notes = _inside(root, root / _relative_folder(note_folder))
    if attachment_mode == "vault_root":
        images = root
    elif attachment_mode == "note_folder":
        images = notes
    elif attachment_mode in {"note_subfolder", "specified_folder"}:
        if not attachment_folder.strip():
            raise ValueError("请填写附件子文件夹或指定文件夹")
        parent = notes if attachment_mode == "note_subfolder" else root
        images = _inside(root, parent / _relative_folder(attachment_folder))
    else:
        raise ValueError("不支持的附件保存方式")
    for folder in (notes, images):
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"保存位置不是文件夹：{folder}")
    return root, notes, images


def export_note(content: str, stem: str, screenshots: list[Path], *, vault: str,
                note_folder: str = "", attachment_mode: str = "note_subfolder",
                attachment_folder: str = "attachments", created_files: list[Path] | None = None) -> Path:
    root, notes, images = resolve_folders(vault, note_folder, attachment_mode, attachment_folder)
    notes.mkdir(parents=True, exist_ok=True)
    if screenshots:
        images.mkdir(parents=True, exist_ok=True)
    _inside(root, notes)
    _inside(root, images)
    token = uuid.uuid4().hex
    # The caller sanitizes the video title; enforce a basename here as well.
    if not stem or Path(stem).name != stem or "/" in stem or "\\" in stem:
        raise ValueError("无效的笔记文件名")
    note_path = _inside(root, notes / f"{stem}-{token}.md")
    for _ in range(100):
        if not note_path.exists() and not any(images.glob(f'video-{token}-*')):
            break
        token = uuid.uuid4().hex
        note_path = _inside(root, notes / f"{stem}-{token}.md")
    else:
        raise FileExistsError('无法创建不重复的 Obsidian 笔记与图片名称')
    created: list[Path] = []
    destinations = {}
    try:
        for index, source in enumerate(screenshots, start=1):
            target = _inside(root, images / f"video-{token}-{index}{source.suffix}")
            with target.open("xb") as destination:
                created.append(target)
                with source.open("rb") as image:
                    shutil.copyfileobj(image, destination)
            relative = Path(os.path.relpath(target, notes)).as_posix()
            destinations[source.name] = quote(relative, safe='/')
        content = rewrite_frame_links(normalize_bold_spacing(content), destinations)
        with note_path.open("x", encoding="utf-8") as output:
            created.append(note_path)
            output.write(content)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    if created_files is not None:
        created_files.extend(created)
    return note_path
