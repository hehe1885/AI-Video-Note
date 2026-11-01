import asyncio
from io import BytesIO
from zipfile import ZipFile
import pytest
from PIL import Image
from backend import agent_auto, agent_queue, main
from backend.word_export import build_word_document
from tests.test_agent_auto import job

def test_agent_compares_exactly_five_frames_before_note_generation(job,monkeypatch):
    task_id,directory=job
    data=agent_queue.read_job(task_id)
    data["settings"]["vision_screenshots"]=True
    data["settings"]["note_format"]="docx"
    agent_queue.write_job(task_id,data)
    captured=[];stages=[]
    async def extract(video,task_id,times,cancel):
        captured.append(times)
        folder=directory/"frames";folder.mkdir(exist_ok=True)
        result={}
        for i,stamp in enumerate(times):
            path=folder/f"frame_{i}_{stamp}.jpg"
            Image.new("RGB",(40,30),"blue").save(path)
            result[stamp]=path
        return result
    async def cli(task_id,prompt,schema=None,images=()):
        if schema and "times" in schema["properties"]:
            stages.append("plan")
            return '{"times":[3]}'
        if schema:
            stages.append("compare")
            assert len(images)==5
            assert all(path.is_file() for path in images)
            assert "等间隔提取5张" in prompt
            return '{"choices":[{"id":0,"candidate":4,"caption":"Chosen visible frame"}]}'
        stages.append("notes")
        assert len(images)==1
        current=agent_queue.read_job(task_id)
        assert len(current["screenshots"])==1
        return "# Notes\n\n![Chosen]("+current["screenshots"][0]["markdown_ref"]+")"
    monkeypatch.setattr(main.video_processor,"extract_frames_at",extract)
    monkeypatch.setattr(agent_auto,"run_cli",cli)
    asyncio.run(agent_auto.work(task_id))
    assert stages==["plan","compare","notes"]
    assert captured==[[0.0,1.5,3.0,4.5,6.0]]
    data=agent_queue.read_job(task_id)
    assert data["screenshots"][0]["time"]==6
    assert data["screenshots"][0]["caption"]=="Chosen visible frame"
    assert len(list((directory/"frames").glob("*.jpg")))==1
    assert main.tasks[task_id]["status"]=="completed"
    assert main.tasks[task_id]["result"]["screenshot_count"]==1
    with ZipFile(BytesIO(build_word_document((directory/"notes.md").read_text(encoding="utf-8"),directory))) as docx:
        assert len([name for name in docx.namelist() if name.startswith("word/media/")])==1

def test_mcp_capture_returns_five_candidate_group_and_counts_selected_frame(job,monkeypatch):
    task_id,directory=job
    data=agent_queue.read_job(task_id);data["settings"]["vision_screenshots"]=True
    agent_queue.write_job(task_id,data)
    async def extract(video,task_id,times,cancel):
        folder=directory/"frames";folder.mkdir(exist_ok=True)
        frames={}
        for index,time in enumerate(times):
            path=folder/f"frame_{index}.jpg";Image.new("RGB",(30,20),"white").save(path)
            frames[time]=path
        return frames
    monkeypatch.setattr(main.video_processor,"extract_frames_at",extract)
    claim=asyncio.run(agent_queue.claim_job(task_id,agent_queue.Claim()))
    result=asyncio.run(agent_queue.capture_job(task_id,agent_queue.Capture(claim_token=claim["claim_token"],times=[5])))
    assert len(result["screenshots"])==5
    assert result["candidate_groups"]==[{"time":5,"candidates":[2,3.5,5,6.5,8]}]
    packet=asyncio.run(agent_queue.get_packet(task_id))
    assert packet["candidate_groups"]==result["candidate_groups"]
    ref=result["screenshots"][2]["markdown_ref"]
    asyncio.run(agent_queue.finish(task_id,agent_queue.Result(claim_token=claim["claim_token"],markdown="# Notes\n\n![Selected]("+ref+")")))
    assert main.tasks[task_id]["result"]["screenshot_count"]==1
