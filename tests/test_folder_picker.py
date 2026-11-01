import sys
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend import folder_picker, main

def fake_tk(monkeypatch, response):
    events=[]
    class Root:
        def withdraw(self): events.append("withdraw")
        def attributes(self,*args): events.append(args)
        def destroy(self): events.append("destroy")
    def askdirectory(**kwargs):
        events.append(kwargs)
        if isinstance(response,Exception): raise response
        return response
    monkeypatch.setitem(sys.modules,"tkinter",SimpleNamespace(Tk=Root,filedialog=SimpleNamespace(askdirectory=askdirectory)))
    return events

def test_native_selection_and_cancel(tmp_path,monkeypatch):
    events=fake_tk(monkeypatch,str(tmp_path))
    assert folder_picker.choose_directory(str(tmp_path),language="en")==str(tmp_path.resolve())
    assert events[-1]=="destroy"
    options=events[-2]
    assert options["initialdir"]==str(tmp_path) and options["mustexist"] is True
    assert options["title"]=="Select note save folder"
    fake_tk(monkeypatch,"")
    assert folder_picker.choose_directory()==""

def test_failure_releases_dialog_lock(monkeypatch):
    events=fake_tk(monkeypatch,RuntimeError("dialog unavailable"))
    with pytest.raises(RuntimeError):folder_picker.choose_directory()
    assert events[-1]=="destroy" and not folder_picker._dialog_lock.locked()
    fake_tk(monkeypatch,"")
    assert folder_picker.choose_directory()==""

def test_busy_picker_does_not_open_another_window(monkeypatch):
    folder_picker._dialog_lock.acquire()
    try:
        with pytest.raises(folder_picker.PickerBusyError):folder_picker.choose_directory()
    finally:folder_picker._dialog_lock.release()

@pytest.mark.asyncio
async def test_selection_api_cancel_success_and_errors(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    for value in ["",str(tmp_path)]:
        monkeypatch.setattr(folder_picker,"choose_directory",lambda *a,**kw:value)
        result=await main.select_note_directory(main.NoteDirectorySelection())
        assert result=={"directory":value,"cancelled":not bool(value)}
    for error,status in [(folder_picker.PickerBusyError("busy"),409),(RuntimeError("broken"),503)]:
        def fail(*a,**kw):raise error
        monkeypatch.setattr(folder_picker,"choose_directory",fail)
        with pytest.raises(HTTPException) as info:await main.select_note_directory(main.NoteDirectorySelection())
        assert info.value.status_code==status
