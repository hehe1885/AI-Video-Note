from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote
import re
import pytest
from backend import note_export
from backend.task_cleanup import exported_files, unlink_exports


def source(tmp_path):
    task=tmp_path/'tasks'/'task123'
    (task/'frames').mkdir(parents=True)
    (task/'frames'/'selected frame.jpg').write_bytes(b'selected')
    (task/'frames'/'unused.jpg').write_bytes(b'unused')
    return task


def test_export_copies_only_used_images_and_survives_source_removal(tmp_path):
    task=source(tmp_path)
    owned=[]
    note=note_export.save_markdown_folder('课程笔记','![图](./frames/selected%20frame.jpg)\n![重复](images/selected%20frame.jpg)',task,tmp_path/'notes',owned)
    assert note==tmp_path/'notes'/'课程笔记'/'课程笔记.md'
    assert {p.name for p in note.parent.iterdir()}=={'课程笔记.md','selected frame.jpg'}
    assert len(owned)==2
    for ref in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',note.read_text(encoding='utf-8')):
        assert (note.parent/unquote(ref)).read_bytes()==b'selected'
    assert (task/'frames'/'unused.jpg').read_bytes()==b'unused'
    (task/'frames'/'selected frame.jpg').unlink()
    assert (note.parent/'selected frame.jpg').read_bytes()==b'selected'


def test_concurrent_same_title_exports_never_overwrite(tmp_path):
    destination=tmp_path/'notes'
    with ThreadPoolExecutor(max_workers=4) as executor:
        notes=list(executor.map(lambda index:note_export.save_markdown_folder('同名',str(index),None,destination),range(8)))
    assert len(set(notes))==8
    assert {p.read_text() for p in notes}==set(map(str,range(8)))
    assert all(p.stem==p.parent.name for p in notes)


def test_failed_copy_removes_partial_export_but_preserves_all_sources(tmp_path,monkeypatch):
    task=source(tmp_path);destination=tmp_path/'notes'
    destination.mkdir();(destination/'keep.txt').write_text('keep')
    def broken_copy(src,target):
        Path(target).write_bytes(b'partial')
        raise OSError('copy failed')
    monkeypatch.setattr(note_export.shutil,'copy2',broken_copy)
    owned=[]
    with pytest.raises(OSError):
        note_export.save_markdown_folder('失败','![图](frames/selected%20frame.jpg)',task,destination,owned)
    assert owned==[]
    assert {p.name for p in destination.iterdir()}=={'keep.txt'}
    assert (task/'frames'/'selected frame.jpg').read_bytes()==b'selected'
    assert (task/'frames'/'unused.jpg').read_bytes()==b'unused'


@pytest.mark.parametrize('user_file',[False,True])
def test_cleanup_tracks_only_export_files_and_never_removes_user_additions(tmp_path,user_file):
    task=source(tmp_path);owned=[]
    note=note_export.save_markdown_folder('笔记','![图](frames/selected%20frame.jpg)',task,tmp_path/'notes',owned)
    extra=note.parent/'keep.jpg'
    if user_file:extra.write_bytes(b'user image')
    paths=exported_files(tmp_path,'task123',{},dict(archived_path=str(note),note_export_files=list(map(str,owned))))
    assert paths==set(owned)
    unlink_exports(paths)
    assert not any(p.exists() for p in owned)
    assert note.parent.exists()==user_file
    if user_file:assert extra.read_bytes()==b'user image'
    assert (task/'frames'/'selected frame.jpg').is_file()


@pytest.mark.parametrize('title',['../非法/标题?', 'CON', 'NUL.txt', '...'])
def test_titles_are_safe_and_directory_matches_markdown_name(tmp_path,title):
    note=note_export.save_markdown_folder(title,'note',None,tmp_path/'notes')
    assert note.parent.parent==tmp_path/'notes'
    assert note.stem==note.parent.name
    assert note.is_file()


def test_cleanup_ignores_unrecorded_title_folder_and_paths_outside_export_root(tmp_path):
    root=tmp_path/'notes';folder=root/'用户笔记';folder.mkdir(parents=True)
    note=folder/'用户笔记.md';note.write_text('user')
    assert not exported_files(tmp_path,'task123',{},dict(archived_path=str(note)))
    outside=tmp_path/'outside'/'outside.md';outside.parent.mkdir();outside.write_text('keep')
    assert not exported_files(tmp_path,'task123',{},dict(archived_path=str(outside),note_export_files=[str(outside)]))
