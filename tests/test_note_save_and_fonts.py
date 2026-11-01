from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import json
import sys
import pytest
from PIL import Image
from docx import Document
from docx.oxml.ns import qn
from backend import main, agent_queue, agent_auto
from backend.task_cleanup import exported_files, unlink_exports
from backend.word_export import build_word_document

@pytest.mark.parametrize("format,extension",[("markdown","md"),("docx","docx"),("html","html"),("json","json"),("txt","txt"),("png","png")])
def test_custom_export_is_correct_format_and_preserves_images(tmp_path,monkeypatch,format,extension):
    workspace=tmp_path/"workspace";workspace.mkdir()
    monkeypatch.setattr(main,"WORKSPACE_DIR",workspace)
    task_id="12345678-1234-4321-abcd-123456789abc"
    task=workspace/task_id;(task/"frames").mkdir(parents=True)
    Image.new("RGB",(60,40),"blue").save(task/"frames/shot.jpg")
    text="# 中文标题\n\n正文 English text.\n\n![示例](frames/shot.jpg)"
    (task/"notes.md").write_text(text,encoding="utf-8")
    output=tmp_path/"exports";owned=[]
    note=main.archive_note("中文标题",text,task_id,note_format=format,save_directory=str(output),created_files=owned)
    assert note.parent==(output/"中文标题" if format=="markdown" else output) and note.suffix=="."+extension
    assert not (workspace/"notes").exists()
    assert (task/"notes.md").read_text(encoding="utf-8")==text
    if format=="docx":
        with ZipFile(note) as archive:assert any(name.startswith("word/media/") for name in archive.namelist())
    elif format=="html":
        assert 'src="data:image/jpeg;base64,' in note.read_text(encoding="utf-8")
    elif format=="png":
        with Image.open(note) as image:assert image.size[0]==1200 and image.height>100
    elif format in {"markdown","json"}:
        saved=json.loads(note.read_text(encoding="utf-8"))["content"] if format=="json" else note.read_text(encoding="utf-8")
        assert ("(shot.jpg)" if format=="markdown" else "-assets/shot.jpg") in saved
        if format!="markdown":
            assert all(path.name.isascii() for path in output.iterdir() if path.is_dir())
        assert any(path.name=="shot.jpg" for path in owned)
        (task/"frames/shot.jpg").unlink()
        assert next(path for path in owned if path.name=="shot.jpg").is_file()
    keep=output/"user-owned.txt";keep.write_text("keep")
    result={"archived_path":str(note),"note_export_files":[str(path) for path in owned]}
    paths=exported_files(workspace,task_id,{"note_save_directory":str(output)},result)
    assert paths==set(owned)
    unlink_exports(paths)
    assert not any(path.exists() for path in owned) and keep.is_file()

def test_title_folders_are_independent_for_repeated_exports(tmp_path):
    from backend.note_export import save_note_export
    task=tmp_path/'task123';(task/'frames').mkdir(parents=True)
    (task/'frames'/'shot.jpg').write_bytes(b'image')
    destination=tmp_path/'exports'
    content='![image](frames/shot.jpg)'
    first=save_note_export('中文标题',content,task,destination,'markdown')
    second=save_note_export('中文标题',content,task,destination,'markdown')
    assert first==destination/'中文标题'/'中文标题.md'
    assert second==destination/'中文标题-2'/'中文标题-2.md'
    for note in (first,second):
        assert (note.parent/'shot.jpg').read_bytes()==b'image'
        assert note.read_text(encoding='utf-8')=='![image](shot.jpg)'
    assert (task/'frames'/'shot.jpg').read_bytes()==b'image'


def test_deletion_still_accepts_legacy_chinese_assets(tmp_path):
    root=tmp_path/'workspace';notes=root/'notes';notes.mkdir(parents=True)
    task_id='task123'
    note=notes/'中文标题-task123.md';note.write_text('note',encoding='utf-8')
    assets=notes/(note.stem+'-assets');assets.mkdir()
    image=assets/'shot.jpg';image.write_bytes(b'image')
    paths=exported_files(root,task_id,{}, {'archived_path':str(note),'note_export_files':[str(note),str(image)]})
    assert paths=={note,image}
    unlink_exports(paths)
    assert not note.exists() and not assets.exists()

def test_word_fonts_and_theme_use_supported_chinese_fonts(tmp_path):
    content=build_word_document("# 标题\n\n正文 English\n\n## 小标题",tmp_path)
    document=Document(BytesIO(content))
    assert document.styles["Normal"].element.rPr.rFonts.get(qn("w:eastAsia"))=="宋体"
    assert document.styles["Title"].element.rPr.rFonts.get(qn("w:eastAsia"))=="黑体"
    assert document.styles["Normal"].font.name=="Times New Roman"
    with ZipFile(BytesIO(content)) as archive:
        theme=archive.read("word/theme/theme1.xml").decode()
        assert 'typeface="MS Gothic"' not in theme
        assert 'typeface="黑体"' in theme and 'typeface="宋体"' in theme
        styles=archive.read("word/styles.xml").decode()
        assert "eastAsiaTheme=" not in styles

@pytest.mark.asyncio
@pytest.mark.parametrize("path",["relative/folder","blocked"])
async def test_invalid_save_destination_rejected_before_task(tmp_path,monkeypatch,path):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{})
    if path=="blocked":
        destination=tmp_path/"file";destination.write_text("user file")
        path=str(destination)
    request=main.SummarizeRequest(video_url="https://youtu.be/test",generation_mode="agent",llm_config={},note_save_directory=path)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:await main.submit_video_task(request,"web")
    assert error.value.status_code==400 and main.tasks=={}

@pytest.mark.asyncio
async def test_model_logs_do_not_repeat_for_each_review_batch(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{"log-test":main.new_task(status="awaiting_agent",task_id="log-test")})
    (tmp_path/"log-test").mkdir()
    agent_queue.write_job("log-test",{"settings":{"agent_provider":"codex","agent_model":"gpt-test","agent_reasoning_effort":"medium"}})
    script='import sys;from pathlib import Path;sys.stderr.write("OpenAI Codex v1.0\\n--------\\nmodel: gpt-test\\nreasoning effort: medium\\n--------\\n");Path(sys.argv[1]).write_text("Notes")'
    monkeypatch.setattr(agent_auto,"invocation",lambda settings,directory,output,schema,images,prompt:([sys.executable,"-c",script,str(output)],b""))
    for _ in range(3):assert await agent_auto.run_cli("log-test","Fixture")=="Notes"
    logs=main.tasks["log-test"]["logs"]
    assert sum(" 请求模型：" in line for line in logs)==1
    assert sum(" 客户端报告模型：" in line for line in logs)==1
