import zipfile
from pathlib import Path
import pytest
from fastapi import HTTPException
from backend import main, skill_export, folder_picker


def test_export_contains_complete_skill_and_no_workspace_secrets(tmp_path):
    base = Path(__file__).resolve().parents[1]
    first = skill_export.export_skill(base, str(tmp_path))
    second = skill_export.export_skill(base, str(tmp_path))
    assert first != second and first.exists()
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {
            'AI-Video-Note/SKILL.md', 'AI-Video-Note/scripts/video_note.py',
            '安装说明-Installation.txt', 'AI-Video-Note/startup.json'}
        assert archive.read('AI-Video-Note/SKILL.md') == (base/'skills/AI-Video-Note/SKILL.md').read_bytes()
        assert '自动启动软件后台服务' in archive.read('安装说明-Installation.txt').decode()
    build = (base/'scripts/build_exe.ps1').read_text(encoding='utf-8')
    assert 'skills/AI-Video-Note/SKILL.md;skills/AI-Video-Note' in build
    assert 'skills/AI-Video-Note/scripts/video_note.py;skills/AI-Video-Note/scripts' in build
    assert 'skills/AI-Video-Note;skills/AI-Video-Note' not in build


def test_missing_packaged_skill_leaves_no_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        skill_export.export_skill(tmp_path, str(tmp_path))
    assert not list(tmp_path.glob('*.zip'))


@pytest.mark.asyncio
async def test_export_endpoint_selection_cancel_and_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(folder_picker, 'choose_directory', lambda *a, **kw: '')
    assert await main.export_agent_skill(main.NoteDirectorySelection()) == {'cancelled':True,'path':''}
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(folder_picker, 'choose_directory', lambda *a, **kw: str(tmp_path))
    result = await main.export_agent_skill(main.NoteDirectorySelection())
    assert not result['cancelled'] and Path(result['path']).is_file()
    def busy(*a, **kw):raise folder_picker.PickerBusyError('busy')
    monkeypatch.setattr(folder_picker, 'choose_directory', busy)
    with pytest.raises(HTTPException) as exc:
        await main.export_agent_skill(main.NoteDirectorySelection())
    assert exc.value.status_code == 409
