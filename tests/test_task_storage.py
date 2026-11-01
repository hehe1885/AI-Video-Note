import json
import uuid
import pytest
from backend import task_storage
from backend.note_review import find_media

def test_new_tasks_are_grouped_and_legacy_tasks_remain_readable(tmp_path):
    assert task_storage.directory(tmp_path,'new-task')==tmp_path/'tasks/new-task'
    (tmp_path/'legacy').mkdir()
    assert task_storage.directory(tmp_path,'legacy')==tmp_path/'legacy'
    for name in ['../outside','','..']:
        with pytest.raises(ValueError):task_storage.directory(tmp_path,name)

def test_history_discovers_grouped_and_legacy_tasks(tmp_path):
    old=tmp_path/'old';old.mkdir();(old/'task.json').write_text('{}')
    grouped=tmp_path/'tasks/new';grouped.mkdir(parents=True);(grouped/'task.json').write_text('{}')
    assert set(task_storage.directories(tmp_path))=={old,grouped}
    assert task_storage.directory(tmp_path,'old')==old
    assert task_storage.directory(tmp_path,'new')==grouped
