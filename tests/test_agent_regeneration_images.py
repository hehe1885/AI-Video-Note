import asyncio
import json
from io import BytesIO
import shutil
from zipfile import ZipFile
from PIL import Image
import pytest
from backend import agent_queue, agent_auto, main
from backend.transcript import TranscriptSegment
from backend.word_export import build_word_document

@pytest.mark.asyncio
async def test_regenerated_word_keeps_matching_frames_without_replanning(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{})
    old_dir=tmp_path/"old";old_frames=old_dir/"frames";old_frames.mkdir(parents=True)
    new_dir=tmp_path/"new";new_dir.mkdir()
    image=old_frames/"frame.jpg"
    Image.new("RGB",(80,60),"blue").save(image)
    request=main.SummarizeRequest(video_url="https://youtu.be/test",generation_mode="agent",note_format="docx",screenshot_density="medium",llm_config={})
    settings=request.model_dump(exclude={"llm_config","bilibili_cookie","douyin_cookie"})
    main.tasks["old"]=main.new_task(status="completed",task_id="old")
    main.tasks["new"]=main.new_task(status="processing",task_id="new")
    main.tasks["new"]["resume_task_id"]="old"
    agent_queue.write_job("old",{"settings":settings,"title":"Fixture","source_url":request.video_url,
        "video_path":str(old_dir/"preview.mp4"),"screenshots":[{"time":3,"filename":"frame.jpg","markdown_ref":"frames/frame.jpg"}]})
    # A zero-image regeneration still leads back to the original image-bearing task.
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "task.json").write_text(json.dumps({"reused_task_id":"old"}), encoding="utf-8")
    main.tasks["bridge"] = main.new_task(status="completed", task_id="bridge")
    agent_queue.write_job("bridge", {"settings":settings, "source_url":request.video_url, "screenshots":[]})
    main.tasks["new"]["resume_task_id"] = "bridge"
    async def forbidden(*a,**k):raise AssertionError("Frames should not be extracted again")
    monkeypatch.setattr(main.video_processor,"extract_frames_at",forbidden)
    (new_dir/"transcript.json").write_text('{"segments":[{"start":0,"end":10,"text":"Material"}]}',encoding="utf-8")
    await agent_queue.prepare("new",request,"Fixture",{"duration":10},[TranscriptSegment(0,10,"Material")],
                              video_path=new_dir/"preview.mp4",source_url=request.video_url)
    assert len(agent_queue.read_job("new")["screenshots"])==1
    assert (new_dir/"frames/frame.jpg").read_bytes()==image.read_bytes()
    # Old history can be removed without breaking the new Word export.
    shutil.rmtree(old_frames)
    prompts=[]
    async def reviewed(task_id, segments, *args):
        main.tasks[task_id]['transcript_review'] = {'status': 'clear', 'count': 0}
        return [TranscriptSegment(s['start'], s['end'], s['text']) for s in segments]
    monkeypatch.setattr(agent_auto, 'review_agent_transcript', reviewed)
    async def cli(task_id,prompt,schema=None,images=()):
        assert schema is None, "Matching frames must bypass planning, even if another plan might return zero"
        prompts.append(prompt)
        return "# Notes\n\nRelated content.\n\n![Frame](frames/frame.jpg)"
    monkeypatch.setattr(agent_auto,"run_cli",cli)
    await agent_auto.work("new")
    assert len(prompts)==1 and main.tasks["new"]["status"]=="completed"
    assert main.tasks["new"]["result"]["screenshot_count"]==1
    note=(new_dir/"notes.md").read_text(encoding="utf-8")
    with ZipFile(BytesIO(build_word_document(note,new_dir))) as docx:
        assert any(path.startswith("word/media/") for path in docx.namelist())

@pytest.mark.parametrize("change",[
    {"screenshot_density":"few"},{"timed_screenshots":True},{"video_resolution":1080},{"bilibili_pages":[2]}])
def test_different_image_settings_do_not_reuse_frames(tmp_path,monkeypatch,change):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{"old":{},"new":{"resume_task_id":"old"}})
    (tmp_path/"old/frames").mkdir(parents=True);(tmp_path/"new").mkdir()
    (tmp_path/"old/frames/frame.jpg").write_bytes(b"frame")
    settings={"screenshot_density":"medium","video_resolution":720}
    agent_queue.write_job("old",{"settings":settings,"source_url":"https://youtu.be/test","screenshots":[{"time":3,"filename":"frame.jpg"}]})
    assert agent_queue.reuse_agent_screenshots("new",{**settings,**change},"https://youtu.be/test")==[]
    assert not (tmp_path/"new/frames").exists()

def test_missing_and_unsafe_frames_are_not_reused(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{"old":{},"new":{"resume_task_id":"old"}})
    (tmp_path/"old").mkdir();(tmp_path/"new").mkdir()
    settings={"screenshot_density":"medium"}
    agent_queue.write_job("old",{"settings":settings,"screenshots":[{"filename":"missing.jpg"},{"filename":"../outside.jpg"}]})
    assert agent_queue.reuse_agent_screenshots("new",settings,None)==[]
