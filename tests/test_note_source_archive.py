from pathlib import Path
from zipfile import ZipFile
from backend.note_options import apply_source_link
from backend import main

def test_source_link_positions():
    url = "https://example.com/video"
    assert apply_source_link("正文", url, "none") == "正文"
    assert apply_source_link("正文", url, "start").startswith("视频链接：")
    assert apply_source_link("正文", url, "end").rstrip().endswith(url)
    assert apply_source_link("正文", None, "start") == "正文"

def test_word_archive_is_real_docx(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    (tmp_path / "task").mkdir()
    result = main.archive_note("笔记", "# 标题\n\n正文", "task", note_format="docx")
    assert result.suffix == ".docx"
    with ZipFile(result) as document:
        assert "word/document.xml" in document.namelist()
    assert not list((tmp_path / "notes").glob("*.md"))
