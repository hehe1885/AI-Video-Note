"""Keep generated text usable when optional exports fail, without blocking requests."""
from __future__ import annotations

import asyncio


async def archive_outputs(runtime, task_id, title, content, *, note_format="markdown",
                          save_directory="", obsidian=None, screenshots=(),
                          transcript=False, secret=""):
    task = runtime.tasks[task_id]
    runtime.raise_if_cancel_requested(task)
    task.update(step_name="保存输出", progress_message="正在保存输出文件")
    runtime.persist_task_runtime(task_id)
    task["_exporting"] = True

    def render():
        files, obsidian_files, warnings, logs = [], [], [], []
        archived = obsidian_path = None
        label = "转录稿" if transcript else "笔记"
        try:
            if transcript:
                archived = runtime.archive_note(title, content, folder=runtime.TRANSCRIPTS_DIR_NAME,
                                                created_files=files)
            else:
                archived = runtime.archive_note(title, content, task_id, note_format=note_format,
                                                created_files=files,
                                                **({"save_directory": save_directory} if save_directory else {}))
            if archived is None:
                raise OSError("无法写入保存位置")
            logs.append(f"{label}已归档：{archived}")
        except Exception as exc:
            detail = runtime.redact_secrets(str(exc), secret)
            warnings.append(f"{label}自动保存失败：{detail}。正文已保留，可使用下载按钮重试导出。")
        if obsidian is not None:
            try:
                obsidian_path = runtime.obsidian_export.export_note(
                    content, runtime._safe_filename(title), list(screenshots),
                    created_files=obsidian_files, **obsidian,
                )
                logs.append(f"Obsidian 笔记与附件已保存：{obsidian_path}")
            except Exception as exc:
                detail = runtime.redact_secrets(str(exc), secret)
                warnings.append(f"Obsidian 保存失败：{detail}。笔记已保留在本机工作目录，可下载后手动放入仓库。")
        return {
            "archived_path": str(archived) if archived else None,
            "note_export_files": [str(path) for path in files],
            "obsidian_path": str(obsidian_path) if obsidian_path else None,
            "obsidian_files": [str(path) for path in obsidian_files],
            "export_status": "failed" if warnings else "completed",
            "export_warning": "\n".join(warnings) or None,
        }, logs

    worker = asyncio.create_task(asyncio.to_thread(render))
    cancelled = False
    try:
        # Cancelling an asyncio waiter cannot stop a filesystem/rendering thread.
        # Keep deletion and duplicate submissions blocked until it has settled.
        while True:
            try:
                result, logs = await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                if worker.cancelled():
                    raise
                cancelled = True
        task["logs"].extend(logs)
        warning = result["export_warning"]
        if warning:
            task["logs"].append(warning)
            task["advisory"] = "\n".join(filter(None, (task.get("advisory"), warning)))
        if cancelled or task.get("_cancel_requested"):
            # Retain ownership of files already written so later deletion can
            # clean them up, without turning a cancelled task into a completion.
            task["result"] = {**(task.get("result") or {}), **result, "title": title,
                              "output_directory": str(runtime.task_directory(task_id))}
            raise asyncio.CancelledError()
        return result
    finally:
        task["_exporting"] = False
