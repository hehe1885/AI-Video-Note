import asyncio
import json
from pathlib import Path

import pytest

from backend.generation_store import GenerationStore, continuation_sources
from backend.llm_summarizer import LLMSummarizer
from backend.transcript import TranscriptSegment
from backend import llm_summarizer, main


def test_packaged_application_does_not_require_loose_python_sources(tmp_path, monkeypatch):
    from backend import generation_store, runtime_revision
    monkeypatch.setattr(generation_store.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_revision, "CODE_REVISION", "packaged-build")
    assert generation_store.policy_revision() == "packaged-build"
    store = GenerationStore(tmp_path, {"model": "one"})
    store.save("section-001", "packaged result")
    assert store.load("section-001") == "packaged result"


def test_receipts_reject_corruption_incomplete_and_degraded_work(tmp_path):
    first = GenerationStore(tmp_path / "old", {"model": "one"})
    first.save("section-001", "finished")
    first.save("section-002", "raw fallback", degraded=True)
    (first.directory / "section-003.md").write_text("partial")
    resumed = GenerationStore(tmp_path / "new", {"model": "one"}, [first.directory])
    assert resumed.load("section-001") == "finished"
    assert (resumed.directory / "section-001.md").read_text() == "finished"
    assert resumed.load("section-002") is None
    assert resumed.load("section-003") is None
    (resumed.directory / "section-001.md").write_text("corrupt")
    (first.directory / "section-001.md").write_text("corrupt")
    assert resumed.load("section-001") is None
    first.save("section-004", "finished")
    (first.directory / "section-004.json").write_text("[]")
    assert resumed.load("section-004") is None


def test_changed_identity_and_fresh_generation_never_load_old_prose(tmp_path):
    old = GenerationStore(tmp_path / "old", {"model": "one"})
    old.save("section-001", "previous version")
    assert GenerationStore(tmp_path / "changed", {"model": "two"}, [old.directory]).load("section-001") is None
    assert GenerationStore(tmp_path / "fresh", {"model": "one"}).load("section-001") is None
    assert old.load("section-001") == "previous version"


def test_continuation_chain_stops_at_completed_notes(tmp_path):
    manifests = {"retry": {"reused_task_id": "old"}, "old": {"runtime": {"status": "completed"}}}
    result = continuation_sources("retry", lambda identifier: tmp_path / identifier, manifests.get, {})
    assert result == [tmp_path / "retry/generation"]
    assert continuation_sources("old", lambda identifier: tmp_path / identifier, manifests.get, {}) == []
    manifests["old"] = {"reused_task_id": "retry"}
    assert len(continuation_sources("retry", lambda identifier: tmp_path / identifier, manifests.get, {})) == 2


def model():
    instance = LLMSummarizer.__new__(LLMSummarizer)
    instance.model_type = "custom"
    instance.model = "test"
    instance.base_url = "http://localhost:1234/v1"
    instance.warnings = []
    return instance


@pytest.mark.asyncio
async def test_cancel_after_one_section_resume_only_missing_then_regenerate_all(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_summarizer, "LONG_TRANSCRIPT_CHARACTERS", 10)
    monkeypatch.setattr(llm_summarizer, "NOTE_SECTION_CHARACTERS", 500)
    monkeypatch.setattr(llm_summarizer, "SECTION_WRITE_CONCURRENCY", 1)
    segments = [TranscriptSegment(i * 60, (i + 1) * 60, "素材" * 150) for i in range(3)]
    first = model()
    first_calls = []
    cancel_requested = [False]
    async def interrupted(title, index, *args):
        first_calls.append(index)
        if index == 2:
            cancel_requested[0] = True
            raise asyncio.CancelledError()
        return f"## 第一版第 {index} 段\n正文"
    first._write_one_section = interrupted
    with pytest.raises(asyncio.CancelledError):
        await first.generate_summary("课程", segments, checkpoint_directory=tmp_path / "old", should_abort=lambda: cancel_requested[0])
    assert first_calls == [1, 2]
    assert (tmp_path / "old/section-001.md").is_file()
    assert not (tmp_path / "old/section-002.md").exists()

    resumed = model()
    calls, logs = [], []
    async def write_section(title, index, *args):
        calls.append(index)
        return f"## 第二版第 {index} 段\n正文"
    async def overview(*args): return "课程概览"
    resumed._write_one_section = write_section
    resumed._write_overview = overview
    note = await resumed.generate_summary("课程", segments, checkpoint_directory=tmp_path / "resumed", checkpoint_sources=[tmp_path / "old"], progress_callback=lambda _, message: logs.append(message))
    assert calls == [2, 3]
    assert "第一版第 1 段" in note and "第二版第 3 段" in note
    assert any("已复用 1/3" in message for message in logs)
    assert (tmp_path / "old/section-001.md").read_text(encoding="utf-8").startswith("## 第一版")

    fresh = model()
    calls.clear()
    fresh._write_one_section = write_section
    fresh._write_overview = overview
    note = await fresh.generate_summary("课程", segments, checkpoint_directory=tmp_path / "regenerated")
    assert calls == [1, 2, 3]
    assert "第一版第 1 段" not in note


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["note_language", "custom_prompt", "style", "reasoning_effort", "note_format", "text_detail", "note_instructions", "model", "base_url"])
async def test_changed_generation_options_invalidate_sections(tmp_path, monkeypatch, option):
    monkeypatch.setattr(llm_summarizer, "LONG_TRANSCRIPT_CHARACTERS", 10)
    segments = [TranscriptSegment(0, 60, "材料" * 40)]
    calls = []
    async def write_section(*args):
        calls.append(1)
        return "## 内容\n完整正文"
    async def overview(*args): return "概览"
    first = model()
    first._write_one_section, first._write_overview = write_section, overview
    await first.generate_summary("标题", segments, checkpoint_directory=tmp_path / "old")
    second = model()
    second._write_one_section, second._write_overview = write_section, overview
    kwargs = {"checkpoint_directory": tmp_path / "new", "checkpoint_sources": [tmp_path / "old"]}
    values = {"note_language": "en", "custom_prompt": "增加推导", "style": "faithful", "reasoning_effort": "off", "note_format": "html", "text_detail": "standard", "note_instructions": "[[VIDEO_SHOT_0]]", "model": "different", "base_url": "http://localhost:5678/v1"}
    if option in {"model", "base_url"}:
        setattr(second, option, values[option])
    else:
        kwargs[option] = values[option]
    await second.generate_summary("标题", segments, **kwargs)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_degraded_sections_are_preserved_but_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_summarizer, "LONG_TRANSCRIPT_CHARACTERS", 10)
    segments = [TranscriptSegment(0, 60, "材料" * 40)]
    first = model()
    async def degraded(title, index, *args):
        first._degraded_sections.add(index)
        first.warnings.append("使用原文兜底")
        return "原始转录兜底"
    async def overview(*args): return "概览"
    first._write_one_section, first._write_overview = degraded, overview
    await first.generate_summary("标题", segments, checkpoint_directory=tmp_path / "old")
    assert json.loads((tmp_path / "old/section-001.json").read_text())["status"] == "degraded"
    calls = []
    second = model()
    async def complete(*args):
        calls.append(1)
        return "## 重新整理\n完整正文"
    second._write_one_section, second._write_overview = complete, overview
    note = await second.generate_summary("标题", segments, checkpoint_directory=tmp_path / "new", checkpoint_sources=[tmp_path / "old"])
    assert calls == [1]
    assert "原始转录兜底" not in note


@pytest.mark.asyncio
async def test_short_generation_reuses_draft_after_commentary_cancellation(tmp_path):
    segments = [TranscriptSegment(0, 10, "短视频内容")]
    first = model()
    calls = []
    async def complete(prompt, **kwargs):
        calls.append(kwargs["stage"])
        if kwargs["stage"] == "正在补充点评与分析":
            raise asyncio.CancelledError()
        return "## 笔记\n[00:10] 完整正文"
    first._complete = complete
    with pytest.raises(asyncio.CancelledError):
        await first.generate_summary("短片", segments, ai_commentary=True, checkpoint_directory=tmp_path / "old")
    second = model()
    second._complete = complete
    calls.clear()
    async def commentary(prompt, **kwargs):
        calls.append(kwargs["stage"])
        return "点评"
    second._complete = commentary
    await second.generate_summary("短片", segments, ai_commentary=True, checkpoint_directory=tmp_path / "new", checkpoint_sources=[tmp_path / "old"])
    assert calls == ["正在补充点评与分析"]


def test_api_defaults_to_fresh_prose_and_records_explicit_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})
    for action in ["regenerate", "continue"]:
        request = main.SummarizeRequest(llm_config={}, generation_action=action)
        main.task_directory(action).mkdir(parents=True)
        main.write_task_manifest(action, request, "old", "web")
        assert main.read_task_manifest(action)["generation_action"] == action
    assert main.SummarizeRequest(llm_config={}).generation_action == "regenerate"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["continue", "regenerate"])
async def test_pipeline_restores_after_service_restart_or_rewrites_all(tmp_path, monkeypatch, action):
    """Use the real pipeline, task persistence, history restoration and note exports."""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(llm_summarizer, "LONG_TRANSCRIPT_CHARACTERS", 10)
    monkeypatch.setattr(llm_summarizer, "NOTE_SECTION_CHARACTERS", 500)
    monkeypatch.setattr(llm_summarizer, "SECTION_WRITE_CONCURRENCY", 1)
    monkeypatch.setattr(main, "resolve_llm_credentials", lambda **kwargs: ("dummy", "test"))
    material_dir = main.task_directory("materials")
    material_dir.mkdir(parents=True)
    segments = [TranscriptSegment(i * 60, (i + 1) * 60, "课程材料" * 75) for i in range(3)]
    (material_dir / "transcript.json").write_text(json.dumps({"language": "zh", "source": "platform_subtitle", "segments": [segment.to_dict() for segment in segments]}), encoding="utf-8")
    (material_dir / "task.json").write_text(json.dumps({"source_url": "https://youtube.com/watch?v=checkpoint", "title": "恢复测试", "source": "youtube", "duration": 180}), encoding="utf-8")
    calls, current = [], ["interrupted"]
    def factory(**kwargs):
        instance = model()
        async def reviewed(runtime, task_id, segments, *args, **kwargs): return segments
        instance.review_transcript = reviewed
        async def section(title, index, *args):
            calls.append(index)
            if current[0] == "interrupted" and index == 2:
                main.tasks["interrupted"]["_cancel_requested"] = True
                raise asyncio.CancelledError()
            return f"## [00:{index:02d}] {current[0]} 第 {index} 段\n笔记正文"
        async def overview(*args): return "课程概览"
        instance._write_one_section, instance._write_overview = section, overview
        return instance
    monkeypatch.setattr(main, "LLMSummarizer", factory)
    def request():
        return main.SummarizeRequest(video_url="https://youtube.com/watch?v=checkpoint", llm_config={"model_type": "custom", "model": "test", "base_url": "http://localhost:1234/v1"}, generation_action=action)
    def prepare(identifier, parent):
        main.task_directory(identifier).mkdir(parents=True)
        main.tasks[identifier] = main.new_task(task_id=identifier)
        main.tasks[identifier]["resume_task_id"] = parent
        main.write_task_manifest(identifier, request(), parent, "web")
    prepare("interrupted", "materials")
    await main.process_video_task("interrupted", request())
    assert main.tasks["interrupted"]["status"] == "cancelled"
    assert calls == [1, 2]
    saved = main.task_directory("interrupted") / "generation/section-001.md"
    original = saved.read_bytes()
    main.tasks.clear()
    main.restore_tasks_from_workspace()
    assert main.tasks["interrupted"]["status"] == "cancelled"
    current[0] = "new-attempt"
    calls.clear()
    prepare("new-attempt", "interrupted")
    await main.process_video_task("new-attempt", request())
    task = main.tasks["new-attempt"]
    assert task["status"] == "completed", task.get("error")
    assert calls == ([2, 3] if action == "continue" else [1, 2, 3])
    assert saved.read_bytes() == original
    assert ("interrupted 第 1 段" in task["result"]["markdown"]) == (action == "continue")
    assert Path(task["result"]["archived_path"]).is_file()


@pytest.mark.asyncio
async def test_mcp_carries_explicit_continuation_and_regeneration(monkeypatch):
    from backend import mcp_server
    captured = []
    class Backend:
        async def start_summarize(self, payload):
            captured.append(payload)
            return {"task_id": "new-task", "reused_task_id": "old-task"}
        async def get_bili_credentials(self): return {"saved": False}
    monkeypatch.setattr(mcp_server, "_get_backend", Backend)
    for action in ["continue", "regenerate"]:
        await mcp_server.summarize_video("https://youtube.com/watch?v=checkpoint", api_key="dummy", model_type="deepseek", generation_action=action, resume_task_id="old-task")
        assert captured[-1]["generation_action"] == action
        assert captured[-1]["resume_task_id"] == "old-task"
