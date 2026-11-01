from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from backend import main, obsidian_export
from backend.transcript import TranscriptSegment
from backend.video_processor import SubtitleResult, VideoProcessor


@pytest.mark.parametrize("mode,expected", [
    ("vault_root", ""),
    ("note_folder", "学习/视频"),
    ("note_subfolder", "学习/视频/pictures"),
    ("specified_folder", "pictures"),
])
def test_four_attachment_modes_create_independent_notes(tmp_path, mode, expected):
    vault = tmp_path / "仓库"
    vault.mkdir()
    source = tmp_path / "frame 1.png"
    source.write_bytes(b"image bytes")
    body = "---\ntags: [video-note]\n---\n# 笔记\n\n![截图](./frames/frame 1.png)"
    note = obsidian_export.export_note(body, "视频标题", [source], vault=str(vault),
        note_folder="学习/视频", attachment_mode=mode, attachment_folder="pictures")
    assert note.parent == vault / "学习" / "视频"
    content = note.read_text(encoding="utf-8")
    reference = content.split("![截图](", 1)[1].split(")", 1)[0]
    image = (note.parent / unquote(reference)).resolve()
    assert image.parent == (vault / expected).resolve()
    assert image.read_bytes() == b"image bytes"
    # Saved notes keep working after the task's original screenshot is removed.
    source.unlink()
    assert image.is_file()
    second = obsidian_export.export_note("# 第二份", "视频标题", [], vault=str(vault),
        note_folder="学习/视频", attachment_mode=mode, attachment_folder="pictures")
    assert second != note
    assert note.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("note_folder,mode,images", [
    ("../outside", "vault_root", ""),
    ("", "specified_folder", "../outside"),
    ("", "note_subfolder", ""),
    ("", "specified_folder", "D:/outside"),
    ("/absolute", "vault_root", ""),
])
def test_export_rejects_paths_outside_vault(tmp_path, note_folder, mode, images):
    with pytest.raises(ValueError):
        obsidian_export.resolve_folders(str(tmp_path), note_folder, mode, images)


def test_failed_export_cleans_up_new_attachments(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(FileNotFoundError):
        obsidian_export.export_note("# note", "title", [source, tmp_path / "missing.png"], vault=str(vault))
    assert not list(vault.rglob("*.png"))
    assert not list(vault.rglob("*.md"))


def test_invalid_obsidian_settings_rejected_before_task_creation(monkeypatch):
    monkeypatch.setattr(main, "tasks", {})
    client = TestClient(main.app)
    for settings in (None, {"vault": "relative/path"}):
        payload = {"video_url": "https://www.youtube.com/watch?v=abc", "summary_style": "obsidian", "llm_config": {}}
        if settings is not None:
            payload["obsidian"] = settings
        response = client.post("/api/summarize", json=payload)
        assert response.status_code == 422
        assert not main.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("attachment_mode", ["vault_root", "note_folder", "note_subfolder", "specified_folder"])
@pytest.mark.parametrize("style", ["obsidian", "learning"])
async def test_generated_obsidian_note_uses_custom_prompt_and_saves_to_vault(tmp_path, monkeypatch, attachment_mode, style):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    processor = VideoProcessor(workspace)
    received = {}
    frame = workspace / "test-obsidian" / "frames" / "frame.png"

    async def preview(*args, **kwargs):
        return workspace / "preview.mp4"

    async def frames(*args, **kwargs):
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"screenshot")
        return {2.0: frame}

    async def info(*args, **kwargs):
        return {"title": "示例：视频", "source": "youtube", "duration": 30, "owner": "作者"}

    async def subtitles(*args, **kwargs):
        return SubtitleResult([TranscriptSegment(0, 5, "内容")], "zh", "platform_subtitle")

    class Summarizer:
        def __init__(self, **kwargs):
            pass

        def describe_effort(self, *args):
            return "auto"

        async def _complete(self, *args, **kwargs):
            return '{"shots":[{"time":2,"caption":"截图","anchor":"内容"}]}'

        async def generate_summary(self, *args, **kwargs):
            received.update(kwargs)
            return "# 示例\n\n> [!note] 重点\n\n[[核心概念]]"

    monkeypatch.setattr(processor, "get_video_info", info)
    monkeypatch.setattr(processor, "fetch_subtitles", subtitles)
    monkeypatch.setattr(processor, "download_preview_video", preview)
    monkeypatch.setattr(processor, "extract_frames_at", frames)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(main, "LLMSummarizer", Summarizer)
    monkeypatch.setattr(main, "tasks", {"test-obsidian": main.new_task()})
    (workspace / "test-obsidian").mkdir()
    request = main.SummarizeRequest(video_url="https://www.youtube.com/watch?v=abc",
        summary_style=style, custom_prompt="添加复习问题", include_screenshots=True,
        obsidian={"vault": str(vault), "note_folder": "视频", "attachment_mode": attachment_mode},
        llm_config={"api_key": "fake-test-key"})
    await main.process_video_task("test-obsidian", request)
    task = main.tasks["test-obsidian"]
    assert task["status"] == "completed", task.get("error")
    assert received["custom_prompt"] == "添加复习问题"
    assert "[[VIDEO_SHOT_0]]" in received["note_instructions"]
    assert "[[VIDEO_SHOT_0]]" not in received["custom_prompt"]
    assert received["style"] == style
    if style != "obsidian":
        assert received["obsidian_mode"] is True
    path = Path(task["result"]["obsidian_path"])
    assert path.parent == vault / "视频"
    saved = path.read_text(encoding="utf-8")
    assert saved.startswith('---\ntitle: "示例：视频"')
    assert "[[核心概念]]" in saved
    assert "source:" in saved
    image_ref = saved.split("![截图 · 00:02](", 1)[1].split(")", 1)[0]
    copied = (path.parent / unquote(image_ref)).resolve()
    assert copied.is_relative_to(vault)
    assert copied.read_bytes() == b"screenshot"
    frame.unlink()
    assert copied.is_file()
