"""Video orchestration with explicitly injected application services."""
from __future__ import annotations
from .video_processor import BiliSubtitleOutcome, select_bilibili_pages
from .task_exports import archive_outputs


def _apply_bilibili_metadata(runtime, task_id, info, outcome):
    task = runtime.tasks[task_id]
    if outcome.title:
        info['title'] = outcome.title
    if outcome.pages:
        info['duration'] = sum(page.duration for page in outcome.pages) or info.get('duration')
        info['bili_pages'] = [{'page': page.page, 'part': page.part, 'duration': page.duration}
                              for page in outcome.pages]
        info['bili_total_pages'] = outcome.total_pages
        if outcome.total_pages > 1:
            task['logs'].append(f'检测到 B 站分 P 视频：共 {outcome.total_pages} 个分 P，本次处理 {runtime._format_page_nums(outcome.pages)}')
        runtime.update_task_manifest(task_id, info)

async def process_video_task(runtime, task_id: str, request) -> None:
    task = runtime.tasks[task_id]
    task["status"] = "processing"
    runtime.persist_task_runtime(task_id)

    def should_abort() -> bool:
        return bool(task.get("_cancel_requested"))

    douyin_cookie = request.douyin_cookie.model_dump() if request.douyin_cookie else None
    # 终态通知会用 title 组文案：早期失败（读凭据、取元数据）时 title 还没赋值，
    # 少了这个初值 except 分支自己会 NameError，任务就只改了内存、task.json 留在 processing
    title = request.video_url or "上传的视频"
    # B 站视频页被风控时（Issue #1）处理链会回退到开放接口直连，回退说明经这里进任务日志
    fallback_notes: list[str] = []
    # except 分支靠它决定能不能套用平台文案，而 detect_source 自己就可能抛错（非 http 链接）
    source_kind = runtime.VideoSource.LOCAL
    # 通道报错里可能整段回显本次用的明文 Key，落盘前要换成掩码；
    # 放在 try 之外初始化，因为早期失败根本没走到 resolve_llm_credentials
    used_api_key = ""

    def note_api_fallback() -> None:
        for note in fallback_notes:
            if note not in task["logs"]:
                task["logs"].append(note)

    try:
        runtime.raise_if_cancel_requested(task)
        bili_cookie = (
            request.bilibili_cookie.model_dump()
            if request.bilibili_cookie
            else runtime.config_store.load_bili_credentials()
        )
        resume_task_id = task.get("resume_task_id")
        resume_dir = runtime.task_directory(resume_task_id) if resume_task_id else None
        source_url = request.video_url or (runtime.source_url_from_task(resume_dir) if resume_dir else None)
        uploaded_path = task.get("uploaded_file_path")
        if not uploaded_path and resume_dir:
            uploaded_media = next(
                (path for path in resume_dir.glob("input.*") if path.is_file()), None
            )
            uploaded_path = str(uploaded_media) if uploaded_media else None
        is_local = bool(uploaded_path) or not source_url
        source_kind = (
            runtime.video_processor.detect_source(source_url) if source_url else runtime.VideoSource.LOCAL
        )
        cookie = douyin_cookie if source_kind == runtime.VideoSource.DOUYIN else bili_cookie
        # Playback uses the same per-request credentials; secrets remain memory-only.
        task['_review_audio_options'] = {'cookie': cookie}
        transcript_result = runtime.load_transcript_result(resume_dir) if resume_dir else None
        if (transcript_result and source_kind == runtime.VideoSource.BILIBILI
                and transcript_result.get('source') in {'bilibili_ai_subtitle', 'bilibili_multi_page', 'platform_subtitle', 'platform_auto_caption'}
                and transcript_result.get('subtitle_policy') != 'source-only-v1'):
            task['logs'].append('旧平台字幕未核对是否为翻译轨，本次重新查找原文字幕；无合适原文时进行语音识别')
            transcript_result = None
        bili_pages_to_transcribe: list = []

        if transcript_result:
            runtime.set_progress(task, 1, "恢复任务信息", 8, "正在读取已有任务信息")
            info = runtime.load_reused_video_info(resume_dir, transcript_result, source_url)
        else:
            media_input = uploaded_path or source_url or ""
            runtime.set_progress(task, 1, "读取视频信息", 8, "正在读取视频信息")
            info = await runtime.video_processor.get_video_info(
                media_input, cookie, allow_local=is_local, notes=fallback_notes
            )
            note_api_fallback()
        runtime.raise_if_cancel_requested(task)
        if is_local:
            # 上传落地文件统一叫 input.<ext>，路径推出来的标题就是占位名 input
            original_name = str(task.get("uploaded_filename") or "").strip()
            if original_name:
                info["title"] = runtime.Path(original_name).stem
        title = info["title"]
        task["title"] = title
        runtime.update_task_manifest(task_id, info)
        task["logs"].append(f"标题：{title}")
        if info.get("duration"):
            task["logs"].append(f"时长：{runtime.format_duration(info['duration'])}")

        if transcript_result:
            runtime.set_progress(task, 4, "复用转录", 48, "已读取已有转录，跳过字幕、音频和 Whisper")
            task["logs"].append(
                f"复用任务 {resume_task_id} 的 {len(transcript_result['segments'])} 个转录分段"
            )
        elif not request.prefer_subtitles and source_kind == runtime.VideoSource.BILIBILI:
            runtime.set_progress(task, 2, "读取分 P 信息", 16, "正在确定本次语音转写范围")
            video = await runtime.video_processor.bilibili_video_pages(source_url or "", cookie)
            runtime.raise_if_cancel_requested(task)
            if video is None or not video.pages:
                raise RuntimeError("无法读取 B 站分 P 信息；已停止以避免转写错误的分 P，请稍后重试")
            selected = select_bilibili_pages(video.pages, source_url or "", request.bilibili_pages)
            subtitle_outcome = BiliSubtitleOutcome(None, "no_track", title=video.title,
                total_pages=len(video.pages), pages=tuple(selected), pages_to_transcribe=tuple(selected))
            bili_pages_to_transcribe = list(selected)
            _apply_bilibili_metadata(runtime, task_id, info, subtitle_outcome)
            title = info['title']
        elif request.prefer_subtitles and not is_local:
            runtime.set_progress(task, 2, "查找平台字幕", 16, "正在查找平台字幕")
            subtitle = await runtime.video_processor.fetch_subtitles(source_url or "", info, cookie, notes=task["logs"])
            if subtitle:
                transcript_result = {
                    "segments": subtitle.segments,
                    "language": subtitle.language,
                    "source": subtitle.source,
                }
                task["logs"].append(
                    f"已使用平台字幕：{subtitle.language}，共 {len(subtitle.segments)} 段"
                )
            else:
                is_bilibili = (
                    runtime.video_processor.detect_source(source_url or "")
                    == runtime.VideoSource.BILIBILI
                )
                subtitle_outcome = (
                    await runtime.video_processor.fetch_bilibili_subtitles(
                        source_url or "", cookie, only_pages=request.bilibili_pages or None
                    )
                    if is_bilibili
                    else None
                )
                if subtitle_outcome is not None:
                    bili_pages_to_transcribe = list(subtitle_outcome.pages_to_transcribe)
                    _apply_bilibili_metadata(runtime, task_id, info, subtitle_outcome)
                if subtitle_outcome is not None and subtitle_outcome.result:
                    transcript_result = {
                        "segments": subtitle_outcome.result.segments,
                        "language": subtitle_outcome.result.language,
                        "source": subtitle_outcome.result.source,
                    }
                    task["logs"].append(
                        f"已使用 B 站 AI 字幕：{subtitle_outcome.result.language}，"
                        f"共 {len(subtitle_outcome.result.segments)} 段"
                    )
                    if bili_pages_to_transcribe:
                        task["logs"].append(
                            "分 P "
                            f"{runtime._format_page_nums(bili_pages_to_transcribe)}"
                            " 无 AI 字幕，将通过语音转写补齐"
                        )
                elif subtitle_outcome is not None:
                    reason = subtitle_outcome.reason
                    if reason == "credentials_missing":
                        task["logs"].append(
                            "未读取到可用的 B 站字幕轨；AI 字幕需要填写当前账号的 "
                            "SESSDATA 等访问凭据"
                        )
                    elif reason == "no_track":
                        task["logs"].append(
                            "当前账号已登录，但未找到可确认的原文字幕轨；翻译轨不会当作原文使用"
                        )
                    elif reason == "error":
                        task["logs"].append(
                            "读取 B 站 AI 字幕失败（网络或接口异常，"
                            f"{subtitle_outcome.detail}）"
                        )
                    elif reason == "empty":
                        task["logs"].append("B 站字幕轨存在但内容为空")
                    if bili_pages_to_transcribe:
                        task["logs"].append(
                            f"分 P {runtime._format_page_nums(bili_pages_to_transcribe)} 将通过语音转写"
                        )
                    task["logs"].append("未找到可用平台字幕，将进行语音转写")
                else:
                    task["logs"].append("本次未能读取可用平台字幕，将使用语音识别；具体原因见上方字幕日志")
            title = info["title"]

        runtime.raise_if_cancel_requested(task)
        if transcript_result is None or bili_pages_to_transcribe:
            runtime.set_progress(task, 3, "准备音频", 25, "正在准备音频")
            if bili_pages_to_transcribe:
                # 多分 P：对没有 AI 字幕的分 P 逐个下载音频并转写，再按顺序合并
                whisper_by_page: dict[int, dict] = {}
                for page in bili_pages_to_transcribe:
                    runtime.raise_if_cancel_requested(task)
                    runtime.set_progress(
                        task, 3, "准备音频", 25, f"正在准备分 P {page.page} 音频"
                    )
                    media_path = await runtime.video_processor.download_audio(
                        runtime.bilibili_page_url(source_url or "", page.page),
                        task_id,
                        cookie,
                        media_name=f"audio_p{page.page}",
                        should_abort=should_abort,
                        notes=fallback_notes,
                        **({"video_directory": request.video_download_directory} if request.video_download_directory else {}),
                    )
                    note_api_fallback()
                    runtime.raise_if_cancel_requested(task)
                    runtime.set_progress(
                        task,
                        4,
                        "语音转写",
                        38,
                        f"正在转写分 P {page.page}（{request.whisper_model} 模型）",
                    )
                    whisper_result = await runtime.run_transcription(
                        media_path,
                        request.whisper_model,
                        request.use_gpu,
                        title,
                        task.get("_cancel_event"),
                        progress_callback=runtime.transcribe_progress_reporter(
                            task, request.whisper_model, f"分 P {page.page} "
                        ),
                        **({"gpu_backend": request.gpu_backend} if request.use_gpu and request.gpu_backend != "cuda" else {}),
                    )
                    runtime.raise_if_cancel_requested(task)
                    whisper_by_page[page.page] = whisper_result
                    task["logs"].append(
                        f"分 P {page.page} 语音转写完成：{len(whisper_result['segments'])} 段，"
                        f"设备 {whisper_result['device']}"
                    )
                    runtime.append_asr_notes(task, whisper_result)
                    if whisper_result["model"] != whisper_result["requested_model"]:
                        task["logs"].append(
                            f"所选 Whisper {whisper_result['requested_model']} 未缓存或无法加载，"
                            f"已自动降级为本机缓存的 {whisper_result['model']}"
                        )
                if subtitle_outcome is not None and subtitle_outcome.pages:
                    merged_segments, language = runtime.merge_bilibili_pages(
                        subtitle_outcome.pages,
                        dict(subtitle_outcome.subtitle_by_page),
                        whisper_by_page,
                    )
                else:
                    merged_segments = [
                        seg
                        for whisper in whisper_by_page.values()
                        for seg in (whisper.get("segments") or [])
                    ]
                    language = next(
                        (
                            str(whisper.get("language") or "zh")
                            for whisper in whisper_by_page.values()
                        ),
                        "zh",
                    )
                transcript_result = {
                    "segments": merged_segments,
                    "language": language,
                    "source": "bilibili_multi_page",
                    "asr_recovery_pages": {str(page): result["asr_recovery"]
                        for page, result in whisper_by_page.items() if result.get("asr_recovery")},
                }
                if not info.get("duration"):
                    info["duration"] = sum(
                        float(whisper.get("duration") or 0)
                        for whisper in whisper_by_page.values()
                    )
                task["logs"].append(f"分 P 转录合并完成：共 {len(merged_segments)} 段")
            elif transcript_result is None:
                runtime.set_progress(task, 3, "准备音频", 25, "正在准备音频")
                if is_local:
                    media_path = runtime.Path(uploaded_path)
                else:
                    media_path = await runtime.video_processor.download_audio(
                        source_url or "", task_id, cookie, should_abort=should_abort,
                        notes=fallback_notes,
                        **({"video_directory": request.video_download_directory} if request.video_download_directory else {}),
                    )
                runtime.raise_if_cancel_requested(task)
                note_api_fallback()
                runtime.set_progress(
                    task,
                    4,
                    "语音转写",
                    38,
                    f"正在加载 {request.whisper_model}（未缓存时可能下载模型）",
                )
                whisper_result = await runtime.run_transcription(
                    media_path,
                    request.whisper_model,
                    request.use_gpu,
                    title,
                    task.get("_cancel_event"),
                    progress_callback=runtime.transcribe_progress_reporter(
                        task, request.whisper_model
                    ),
                    **({"gpu_backend": request.gpu_backend} if request.use_gpu and request.gpu_backend != "cuda" else {}),
                )
                runtime.raise_if_cancel_requested(task)
                transcript_result = {
                    "segments": whisper_result["segments"],
                    "language": whisper_result["language"],
                    "asr_recovery": whisper_result.get("asr_recovery", []),
                    "source": (
                        "paraformer"
                        if request.whisper_model == "paraformer-zh"
                        else whisper_result.get("engine", "faster_whisper")
                    ),
                }
                if not info.get("duration"):
                    info["duration"] = whisper_result["duration"]
                task["logs"].append(
                    f"语音转写完成：{len(whisper_result['segments'])} 段，设备 {whisper_result['device']}"
                )
                runtime.append_asr_notes(task, whisper_result)
                if whisper_result["model"] != whisper_result["requested_model"]:
                    task["logs"].append(
                        f"所选 Whisper {whisper_result['requested_model']} 未缓存或无法加载，"
                        f"已自动降级为本机缓存的 {whisper_result['model']}"
                    )

        segments: list[TranscriptSegment] = transcript_result["segments"]
        if not segments:
            raise RuntimeError("没有从视频中获得可总结的文字")
        quality = runtime.transcript_quality(segments, float(info.get("duration") or 0))
        transcript_result["quality"] = quality
        task["logs"].append(
            f"文字质量检查：{quality['characters']} 字，语音覆盖 {quality['speech_coverage']:.0%}"
        )
        if transcript_result.get('source') in {'bilibili_ai_subtitle', 'bilibili_multi_page', 'platform_subtitle', 'platform_auto_caption'}:
            transcript_result['subtitle_policy'] = 'source-only-v1'
        await runtime.write_transcript_files(task_id, segments, transcript_result)
        if (
            transcript_result["source"] in {"faster_whisper", "whisper_vulkan", "paraformer"}
            and quality["insufficient"]
        ):
            stage = "调用大模型" if request.output == "note" else "返回转录"
            raise RuntimeError(
                f"可识别语音覆盖过低，已在{stage}前停止。源视频可能被静音、替换、"
                "受版权或审核限制，或主要内容并非语音；请先检查原视频。"
            )

        if request.output == "transcript":
            from .transcript_review import review_transcript_output
            segments = await review_transcript_output(runtime, task_id, request, segments, title, resume_dir)
            quality = runtime.transcript_quality(segments, float(info.get("duration") or 0))
            transcript_result.update(segments=segments, quality=quality)
            runtime.raise_if_cancel_requested(task)
            await runtime.write_transcript_files(task_id, segments, transcript_result)
            # 正文只留在 transcript.json / transcript.md：result 会被 wait_for_task
            # 每 2 秒轮询一次并由 persist_task_runtime 反复落盘，不能放大文本
            # 归档读的是刚落盘的那份 transcript.md（与下载端点同一个文件），不在这里
            # 重新拼一遍格式——两处各拼一次迟早会对不上。
            try:
                transcript_markdown = (
                    runtime.task_directory(task_id) / "transcript.md"
                ).read_text(encoding="utf-8")
            except OSError:
                transcript_markdown = ""
            exports = await archive_outputs(
                runtime, task_id, runtime.download_stem(title, info, "transcript"),
                transcript_markdown, transcript=True,
            )
            elapsed = runtime.finish_task_timing(task)
            task["result"] = {
                "title": title,
                "output": "transcript",
                "source": runtime.VideoSource.LOCAL.value if is_local else info.get("source", "other"),
                "duration": info.get("duration", 0),
                "segment_count": len(segments),
                "characters": quality["characters"],
                "transcript_source": transcript_result["source"],
                "transcript_language": transcript_result["language"],
                "transcript_quality": quality,
                "output_directory": str(runtime.task_directory(task_id)),
                **exports,
                "processing_seconds": elapsed,
            }
            task.update(
                status="completed", step=7, step_name="转录完成", progress=100, error=None
            )
            task["logs"].append(
                f"转录完成：{len(segments)} 段，可用 GET /api/task/{task_id}/transcript 取正文"
            )
            runtime.notify_task("转录完成", f"已生成带时间轴转录：{title}")
            runtime.persist_task_runtime(task_id)
            return

        if request.generation_mode == "agent":
            from .agent_queue import prepare
            await prepare(task_id, request, title, info, segments,
                          runtime.Path(uploaded_path) if is_local and uploaded_path else None,
                          source_url, cookie)
            return

        config = request.llm_config
        base_url = config.base_url or config.custom_base_url
        model = config.model or config.custom_model_name
        api_key, key_source = runtime.resolve_llm_credentials(
            model_type=config.model_type,
            base_url=base_url,
            api_key=config.api_key.get_secret_value(),
        )
        used_api_key = api_key
        summarizer = runtime.LLMSummarizer(
            model_type=config.model_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        summarizer._note_language = request.note_language
        task["logs"].append(
            f"调用模型：Provider={config.model_type}，Model={model}，"
            f"Base URL={base_url}，Key={key_source}"
        )
        task["logs"].append(
            f"推理设置：{summarizer.describe_effort(request.reasoning_effort, request.summary_style)}"
        )

        reviewer = getattr(summarizer, "review_transcript", None)
        if reviewer:
            runtime.set_progress(task, 4, "AI字幕校对", 50, "正在校对字幕并生成修改建议")
            segments = await reviewer(runtime, task_id, segments, title, resume_directory=resume_dir, should_abort=should_abort)
            quality = runtime.transcript_quality(segments, float(info.get("duration") or 0))
            transcript_result.update(segments=segments, quality=quality)
            runtime.raise_if_cancel_requested(task)

        screenshots: list[runtime.Path] = []
        screenshot_points = []
        screenshot_frames = {}
        density = request.screenshot_density or ("medium" if request.include_screenshots else "none")
        audio_only = is_local and runtime.Path(uploaded_path or "").suffix.lower() in {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma"}
        if audio_only:
            task["logs"].append("当前输入为音频，生成文字笔记并跳过视频截图")
        if not audio_only and (request.timed_screenshots or density != "none"):
            screenshot_stage = "定时截图" if request.timed_screenshots else "AI 选择截图"
            runtime.set_progress(task, 5, screenshot_stage, 50, f"正在准备每隔 {request.screenshot_interval} 秒截图" if request.timed_screenshots else "正在分析内容并选择截图时间")

            async def report_screenshot(message: str) -> None:
                runtime.raise_if_cancel_requested(task)
                runtime.set_progress(task, 5, screenshot_stage, 50, message)

            try:
                if request.timed_screenshots:
                    screenshot_points = runtime.interval_screenshots(segments, info.get("duration"), request.screenshot_interval)
                else:
                    screenshot_points = await runtime.plan_screenshots(
                        summarizer, segments, density, should_abort, report_screenshot
                    )
                runtime.raise_if_cancel_requested(task)
                if screenshot_points:
                    video_path = runtime.Path(uploaded_path) if is_local else await runtime.asyncio.to_thread(
                        runtime.reuse_preview_video, request, task_id, source_url or ""
                    )
                    if video_path is not None and not is_local:
                        task["logs"].append(f"已复用下载的视频，无需重新下载：{video_path}")
                    if video_path is None:
                        try:
                            video_path = await runtime.video_processor.download_preview_video(
                                source_url or "", task_id, cookie, should_abort, fallback_notes,
                                max_height=request.video_resolution,
                                **({'video_encoding':request.video_encoding} if request.video_encoding == 'size' else {}),
                                download_directory=request.video_download_directory,
                                title=title,
                            )
                        except Exception:
                            task["screenshot_warning"] = "视频下载失败，本次笔记没有配图。文字笔记仍会生成；可稍后重试，或导入本地视频后生成。"
                            runtime.persist_task_runtime(task_id)
                            raise
                    note_api_fallback()
                    if not is_local:
                        task["logs"].append(f"截图视频已保存：{video_path}（最高 {request.video_resolution}p）")
                        task["video_path"] = str(video_path)
                    screenshot_frames = await runtime.video_processor.extract_frames_at(
                        video_path, task_id, runtime.candidate_times(screenshot_points, info.get("duration")) if request.vision_screenshots and not request.timed_screenshots else [point.time for point in screenshot_points], should_abort
                    )
                    if hasattr(runtime.video_processor,'take_gpu_notes'):
                        task['logs'].extend(runtime.video_processor.take_gpu_notes(task_id))
                    runtime.raise_if_cancel_requested(task)
                    if request.vision_screenshots and not request.timed_screenshots:
                        try:
                            original_frames = dict(screenshot_frames)
                            async def capture_retry(times):
                                captured = await runtime.video_processor.extract_frames_at(
                                    video_path, task_id, times, should_abort)
                                original_frames.update(captured)
                                if hasattr(runtime.video_processor, 'take_gpu_notes'):
                                    task['logs'].extend(runtime.video_processor.take_gpu_notes(task_id))
                                return captured
                            from .vision_screenshots import save_review_trace
                            review_audit = []
                            try:
                                screenshot_points, screenshot_frames = await runtime.verify_screenshots(
                                    summarizer, screenshot_points, screenshot_frames, info.get("duration"), should_abort, report_screenshot,
                                    capture=capture_retry, segments=segments, audit=review_audit
                                )
                                if request.auto_crop_screenshots:
                                    from .screenshot_crop import crop_screenshots
                                    screenshot_points, screenshot_frames = await crop_screenshots(
                                        summarizer, screenshot_points, screenshot_frames, should_abort,
                                        report_screenshot, audit=review_audit)
                            finally:
                                save_review_trace(runtime.task_directory(task_id), review_audit)
                            keep = set(screenshot_frames.values())
                            for path in original_frames.values():
                                if path not in keep: path.unlink(missing_ok=True)
                            task["logs"].append(f"视觉核对完成：保留 {len(screenshot_points)} 张相关截图")
                            if not screenshot_points:
                                task["advisory"] = "视觉模型未找到合适的相关画面，本次生成文字笔记。"
                        except Exception as exc:
                            for path in original_frames.values():
                                try:
                                    path.unlink(missing_ok=True)
                                except OSError:
                                    pass
                            task["screenshot_warning"] = "视觉核对失败，所选模型可能不支持图片输入、接口暂不可用或返回结果无效。本次不插入未经核对的图片，仍会生成文字笔记。"
                            code = getattr(exc, "status_code", None)
                            reason = f"HTTP {code}" if isinstance(code, int) else type(exc).__name__
                            raise RuntimeError(f"视觉核对失败（{reason}）") from exc
                    screenshots = list(screenshot_frames.values())
                    if len(screenshots) < len(screenshot_points):
                        warning = "部分截图时间点未能读取画面，笔记将使用成功提取的图片。"
                        task["logs"].append(warning)
                        task["advisory"] = warning
                task["logs"].append(f"{screenshot_stage}：{len(screenshot_points)} 个时间点，成功提取 {len(screenshots)} 张截图")
            except runtime.asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime.raise_if_cancel_requested(task)
                message = screenshot_stage + "未完成，本次继续生成文字笔记：" + runtime.redact_secrets(str(exc), api_key)
                task["logs"].append(message)
                task["advisory"] = message
                if not task.get("screenshot_warning"):
                    task["screenshot_warning"] = "截图未完成，本次继续生成文字笔记。请查看运行记录中的失败原因。"
                runtime.persist_task_runtime(task_id)
                screenshot_points, screenshot_frames, screenshots = [], {}, []

        transcript_characters = int(quality.get("characters") or 0)
        duration_seconds = float(info.get("duration") or 0)
        advisory = ""
        if (
            transcript_characters > runtime.LONG_TRANSCRIPT_CHARACTERS
            and runtime.NOTE_STYLES.get(request.summary_style, {}).get("base", request.summary_style) != "concise"
        ):
            advisory = (
                "这段内容很长，笔记会按时间顺序逐段完整记录（不做预先压缩），"
                "因此 Token 消耗与耗时都随时长增长，输出量与口播字数同量级。"
                "3 小时量级的课可能要几十分钟才出稿（限流的免费通道更慢），"
                "挂着等即可，任何时候都能取消。想省一些可以改用「精简摘要」。"
            )
        elif transcript_characters >= 9_000 or duration_seconds >= 1_800:
            advisory = (
                "这段内容较长，将通过多轮整理生成完整笔记，耗时和 Token 消耗会相应增加。"
                "上下文容量较大、指令理解能力较强的模型通常更稳定；免费或轻量模型可能出现遗漏或截断。"
            )
        if advisory:
            task["advisory"] = "\n".join(filter(None, [task.get("advisory"), advisory]))
            task["logs"].append(advisory)
        runtime.set_progress(task, 6, "生成笔记", 55, "正在规划笔记生成流程")

        async def report_llm_progress(progress: int, message: str) -> None:
            runtime.raise_if_cancel_requested(task)
            mapped = 55 + round(max(0, min(100, progress)) * 0.44)
            runtime.set_progress(task, 6, "生成笔记", mapped, message)

        checkpoint_sources = runtime.continuation_sources(resume_task_id, runtime.task_directory, runtime.read_task_manifest, runtime.tasks) if request.generation_action == "continue" else []
        summary = await summarizer.generate_summary(
            title,
            segments,
            {
                "owner": info.get("owner"),
                "published_at": runtime.format_publish_time(info),
                "duration_text": runtime.format_duration(info.get("duration", 0)),
                "view_count": info.get("view_count") or 0,
                "like_count": info.get("like_count") or 0,
                "transcript_source": transcript_result["source"],
                "description": (info.get("description") or "")[:600],
            },
            checkpoint_directory=runtime.task_directory(task_id) / "generation",
            checkpoint_sources=checkpoint_sources,
            style=request.summary_style,
            note_language=request.note_language,
            **({"ai_commentary": True} if request.ai_commentary else {}),
            **({"ai_commentary_position": request.ai_commentary_position} if request.ai_commentary_position is not None else {}),
            note_format=request.note_format,
            reasoning_effort=request.reasoning_effort,
            progress_callback=report_llm_progress,
            should_abort=should_abort,
            custom_prompt=request.custom_prompt,
            text_detail=request.text_detail,
            obsidian_tags=request.obsidian_tags,
            obsidian_callouts=request.obsidian_callouts,
            note_instructions="" if request.timed_screenshots else runtime.screenshot_instructions(screenshot_points, request.note_language),
            **({"obsidian_mode": True} if request.obsidian is not None and request.summary_style != "obsidian" else {}),
        )
        runtime.raise_if_cancel_requested(task)
        for warning in getattr(summarizer, "warnings", []):
            # 告警会把网关异常原文嵌进来，落盘前必须过一遍脱敏
            cleaned = runtime.redact_secrets(warning, api_key)
            if cleaned not in task["logs"]:
                task["logs"].append(cleaned)
        summary = runtime.add_note_header_metadata(summary, title, info)
        if screenshot_points:
            summary = runtime.insert_screenshots(summary, screenshot_points, screenshot_frames)
        summary = runtime.apply_timeline(summary, request.timeline_mode)
        summary = runtime.append_note_footer(
            summary,
            None,
            config.model_type,
            model,
            request.summary_style,
            include_source=False,
        )
        summary = runtime.apply_source_link(summary, source_url, request.source_link_position)
        if request.summary_style == "obsidian" or request.obsidian is not None:
            summary = runtime.add_obsidian_properties(summary, title, source_url)

        task_dir = runtime.task_directory(task_id)
        await runtime._write_atomically(task_dir / "notes.md", (summary,))
        exports = await archive_outputs(
            runtime, task_id, runtime.download_stem(title, info), summary,
            note_format=request.note_format, save_directory=request.note_save_directory,
            obsidian=request.obsidian.model_dump() if request.obsidian is not None else None,
            screenshots=screenshots, secret=used_api_key,
        )
        elapsed = runtime.finish_task_timing(task)
        task["result"] = {
            "title": title,
            "markdown": summary,
            "source": runtime.VideoSource.LOCAL.value if is_local else info.get("source", "other"),
            "duration": info.get("duration", 0),
            "input_type": "audio" if audio_only else "video",
            "screenshot_count": len(screenshots),
            "screenshot_warning": task.get("screenshot_warning"),
            "video_path": task.get("video_path"),
            "video_resolution": request.video_resolution,
            "video_encoding": request.video_encoding,
            "transcript_source": transcript_result["source"],
            "transcript_language": transcript_result["language"],
            "segment_count": len(segments),
            "transcript_quality": quality,
            "output_directory": str(task_dir),
            **exports,
            "processing_seconds": elapsed,
        }
        task.update(
            status="completed", step=7, step_name="完成", progress=100, error=None
        )
        task["logs"].append("视频笔记生成完成")
        runtime.notify_task("任务完成", f"视频笔记已生成：{title}")
        runtime.persist_task_runtime(task_id)
    except runtime.asyncio.CancelledError:
        runtime.finish_task_timing(task)
        task.update(status="cancelled", step_name="已取消", error=None)
        if not task["logs"] or "用户已取消任务" not in task["logs"][-1]:
            task["logs"].append("任务已取消；已生成的中间文件将保留")
        runtime.persist_task_runtime(task_id)
    except Exception as exc:
        runtime.finish_task_timing(task)
        raw = str(exc)
        # 失败文案有三条出口（error 字段、任务日志、系统通知），未知异常时
        # friendly_* 会原样返回底层文本，网关回显的明文 Key 就会跟着落进 task.json
        message = runtime.redact_secrets(
            runtime.friendly_task_error(
                raw, source=source_kind, step_name=task.get("step_name", "")
            ),
            used_api_key,
        )
        task.update(status="failed", error=message)
        task["logs"].append(f"处理失败：{message}")
        if message != raw:
            # 文案一旦被替换掉，真实的返回码就没人能看到了——这次误诊就是这么来的
            task["logs"].append(f"底层错误：{runtime.scrub_technical_error(raw, used_api_key)}")
        runtime.notify_task("任务失败", f"{title}\n{message}")
        runtime.persist_task_runtime(task_id)

