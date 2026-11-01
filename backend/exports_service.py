"""Exports handlers with injected application services."""
from __future__ import annotations

async def get_task_transcript(app_services, 
    task_id: str, output_format: Literal["json", "markdown"] = "json"
) -> dict[str, Any]:
    """取该任务的带时间轴转录（`segments` 的时间单位是秒）。

    只看转录产物在不在，不看任务状态：即便笔记在第 6 步失败，已完成的转录
    照样能取走，不必重跑。`markdown` 返回人读版整篇文本，`json` 返回分段。
    """
    try:
        directory = app_services.task_directory(task_id)
    except ValueError as exc:
        raise app_services.HTTPException(status_code=400, detail="非法任务 ID") from exc
    json_path = directory / "transcript.json"
    if not json_path.is_file():
        raise app_services.HTTPException(
            status_code=409, detail="该任务还没有转录产物，可先调用 POST /api/transcribe"
        )
    try:
        payload = app_services.json.loads(json_path.read_text(encoding="utf-8"))
        from .task_recovery import valid_transcript
        if not valid_transcript(payload):
            raise ValueError('Invalid transcript structure')
    except (OSError, ValueError):
        raise app_services.HTTPException(status_code=500, detail="转录文件无法读取") from None

    task = app_services.tasks.get(task_id) or {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    manifest = app_services.read_task_manifest(task_id)
    title = str(
        result.get("title") or manifest.get("title") or manifest.get("uploaded_filename") or ""
    )

    if output_format == "markdown":
        markdown_path = directory / "transcript.md"
        if not markdown_path.is_file():
            raise app_services.HTTPException(status_code=409, detail="转录 Markdown 不存在")
        return {
            "task_id": task_id,
            "title": title,
            "format": "markdown",
            "text": markdown_path.read_text(encoding="utf-8"),
        }

    segments = payload.get("segments") or []
    return {
        "task_id": task_id,
        "title": title,
        "format": "json",
        "language": payload.get("language"),
        "transcript_source": payload.get("source"),
        "transcript_quality": payload.get("quality"),
        "segment_count": len(segments),
        "segments": segments,
    }


async def get_image(app_services, task_id: str, filename: str) -> app_services.FileResponse:
    if task_id not in app_services.tasks:
        raise app_services.HTTPException(status_code=404, detail="任务不存在")
    if app_services.Path(filename).name != filename:
        raise app_services.HTTPException(status_code=400, detail="非法文件名")
    try:
        task_dir = app_services.task_directory(task_id)
    except ValueError as error:
        raise app_services.HTTPException(status_code=400, detail="非法任务 ID") from error
    expected_parent = (task_dir / app_services.FRAMES_DIR_NAME).resolve()
    image_path = (expected_parent / filename).resolve()
    if image_path.parent != expected_parent or not image_path.is_file():
        raise app_services.HTTPException(status_code=404, detail="图片不存在")
    return app_services.FileResponse(image_path)


async def download_summary_markdown(app_services, task_id: str, format: Literal["markdown", "docx", "pdf", "enex", "html"] = "markdown") -> app_services.Response:
    app_services.ensure_task_loaded(task_id)
    task = app_services.tasks.get(task_id)
    if not task:
        raise app_services.HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "completed":
        raise app_services.HTTPException(status_code=409, detail="任务尚未完成")

    task_dir = app_services.task_directory(task_id)
    output_path = task_dir / "notes.md"
    if not output_path.is_file():
        # 纯转录任务按设计不产出 notes.md，能下载的就是带时间轴的转录
        output_path = task_dir / "transcript.md"
    if not output_path.is_file():
        raise app_services.HTTPException(status_code=404, detail="Markdown 笔记不存在")

    title = (task.get("result") or {}).get("title", "video-notes")
    safe_name = app_services.download_stem(
        str(title), app_services.read_task_manifest(task_id), str(task.get("output") or "note")
    )
    try:
        note_text = output_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise app_services.HTTPException(status_code=500, detail=f"读取笔记失败：{exc}")

    async def render_export(renderer, *args):
        try:
            return await app_services.asyncio.to_thread(renderer, *args)
        except (RuntimeError, ValueError, OSError, TimeoutError) as error:
            raise app_services.HTTPException(status_code=503, detail=f"笔记导出失败：{error}") from error

    if format == "html":
        from .note_export import save_note_export
        import tempfile
        from pathlib import Path
        def html_document():
            with tempfile.TemporaryDirectory(prefix="note-html-") as folder:
                saved = save_note_export(safe_name, note_text, task_dir, Path(folder), "html")
                return saved.read_bytes()
        content = await render_export(html_document)
        return app_services.Response(content=content, media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{app_services.quote(safe_name + '.html')}"})

    if format in {"pdf", "enex"}:
        if format == "pdf":
            from .pdf_export import build_pdf_document as render
            media_type = "application/pdf"
        else:
            from .enex_export import build_enex_document as render
            media_type = "application/xml"
        content = await render_export(render, note_text, task_dir, safe_name)
        return app_services.Response(content=content, media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{app_services.quote(safe_name + '.' + format)}"})

    if format == "docx":
        from .word_export import build_word_document
        content = await render_export(build_word_document, note_text, task_dir)
        return app_services.Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{app_services.quote(safe_name + '.docx')}"},
        )

    bundle = await app_services.asyncio.to_thread(
        app_services.build_note_bundle, note_text, task_dir, f"{safe_name}.md"
    )
    if bundle is not None:
        encoded_name = app_services.quote(f"{safe_name}.zip")
        return app_services.Response(
            content=bundle,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        )

    encoded_name = app_services.quote(f"{safe_name}.md")
    return app_services.FileResponse(
        output_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{safe_name}.md",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


