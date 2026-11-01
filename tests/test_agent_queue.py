import asyncio
import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import main, agent_queue, mcp_server
from backend.transcript import TranscriptSegment
from backend.video_processor import SubtitleResult


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    task_id = "agent-test"
    directory = tmp_path / task_id; directory.mkdir()
    request = main.SummarizeRequest(video_url="https://youtu.be/example", generation_mode="agent", note_format="docx", custom_prompt="保留材料中的例子", timeline_mode="none", screenshot_density="none", llm_config={})
    main.tasks[task_id] = main.new_task(status="processing", task_id=task_id)
    main.write_task_manifest(task_id, request, None, "web")
    (directory / "transcript.json").write_text(json.dumps({"segments": [{"start": i, "end": i+1, "text": "材料"} for i in range(205)]}), encoding="utf-8")
    asyncio.run(agent_queue.prepare(task_id, request, "测试标题", {"duration": 300}, []))
    return task_id, directory, TestClient(main.app)


def test_queue_claim_packet_result_and_word_download(prepared):
    task_id, directory, client = prepared
    jobs = client.get("/api/agent/jobs").json()["jobs"]
    assert jobs[0]["task_id"] == task_id
    packet = client.get(f"/api/agent/jobs/{task_id}").json()
    assert len(packet["segments"]) == 100 and packet["next_offset"] == 100
    assert "Word" in packet["prompt"] and "保留材料中的例子" in packet["prompt"]
    assert "llm_config" not in packet["settings"]
    claim = client.post(f"/api/agent/jobs/{task_id}/claim", json={}).json()
    assert client.post(f"/api/agent/jobs/{task_id}/claim", json={}).status_code == 409
    body = {"claim_token": claim["claim_token"], "markdown": "# 测试笔记\n\n## 小节（15:30—47:48）\n\n[00:01-00:02] 这是正文。"}
    assert client.post(f"/api/agent/jobs/{task_id}/result", json={**body, "claim_token": "wrong"}).status_code == 409
    assert client.post(f"/api/agent/jobs/{task_id}/result", json=body).status_code == 409
    assert client.post(f"/api/agent/jobs/{task_id}/transcript-issues", json={"claim_token":claim["claim_token"],"issues":[]}).status_code == 200
    assert client.post(f"/api/agent/jobs/{task_id}/result", json=body).status_code == 200
    saved=(directory / "notes.md").read_text(encoding="utf-8")
    assert "00:01" not in saved and "15:30" not in saved
    assert "小节" in saved and "这是正文。" in saved
    assert client.get(f"/api/download/{task_id}?format=docx").content.startswith(b"PK")
    assert client.get("/api/agent/jobs").json()["jobs"] == []
    assert client.post(f"/api/agent/jobs/{task_id}/result", json=body).status_code == 409


def test_manual_import_overrides_claim_and_blank_rejected(prepared):
    task_id, _, client = prepared
    token = client.post(f"/api/agent/jobs/{task_id}/claim", json={}).json()["claim_token"]
    assert client.post(f"/api/agent/jobs/{task_id}/transcript-issues", json={"claim_token":token,"issues":[]}).status_code == 200
    assert client.post(f"/api/agent/jobs/{task_id}/import", json={"markdown":"   "}).status_code == 422
    assert client.post(f"/api/agent/jobs/{task_id}/import", json={"markdown":"# 手动笔记"}).status_code == 200
    assert client.post(f"/api/agent/jobs/{task_id}/result", json={"claim_token":token,"markdown":"overwrite"}).status_code == 409


def test_expired_lease_reclaim_release_and_cancel(prepared):
    task_id, _, client = prepared
    token = client.post(f"/api/agent/jobs/{task_id}/claim", json={}).json()["claim_token"]
    data = agent_queue.read_job(task_id); data["lease_until"] = time.time()-1; agent_queue.write_job(task_id,data)
    assert client.post(f"/api/agent/jobs/{task_id}/lease", json={"claim_token":token}).status_code == 409
    token = client.post(f"/api/agent/jobs/{task_id}/claim", json={}).json()["claim_token"]
    assert client.post(f"/api/agent/jobs/{task_id}/lease", json={"claim_token":token,"release":True}).status_code == 200
    assert client.get("/api/agent/jobs").json()["jobs"][0]["claimed"] is False
    assert client.post(f"/api/task/{task_id}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/agent/jobs/{task_id}/import", json={"markdown":"# note"}).status_code == 409


def test_pending_job_survives_restart(prepared, monkeypatch):
    task_id, _, client = prepared
    monkeypatch.setattr(main,"tasks",{})
    main.restore_tasks_from_workspace()
    assert main.tasks[task_id]["status"] == "awaiting_agent"
    assert client.get("/api/agent/jobs").json()["jobs"][0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_agent_pipeline_never_uses_model_api(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{})
    task_id="pipeline"; (tmp_path/task_id).mkdir()
    request=main.SummarizeRequest(video_url="https://www.youtube.com/watch?v=test",generation_mode="agent",screenshot_density="none",llm_config={})
    main.tasks[task_id]=main.new_task(task_id=task_id)
    main.write_task_manifest(task_id,request,None,"web")
    async def info(*args,**kwargs): return {"title":"视频标题","duration":10,"source":"youtube"}
    async def subtitles(*args,**kwargs): return SubtitleResult([TranscriptSegment(0,10,"这是一段足够长的测试字幕，包含视频的真实内容。")],"zh","platform_subtitle")
    def forbidden(*args,**kwargs): raise AssertionError("Agent mode must not access model API")
    monkeypatch.setattr(main.video_processor,"get_video_info",info)
    monkeypatch.setattr(main.video_processor,"fetch_subtitles",subtitles)
    monkeypatch.setattr(main,"resolve_llm_credentials",forbidden)
    monkeypatch.setattr(main,"LLMSummarizer",forbidden)
    await main.process_video_task(task_id,request)
    assert main.tasks[task_id]["status"] == "awaiting_agent", main.tasks[task_id].get("error")
    assert (tmp_path/task_id/"transcript.json").is_file()


@pytest.mark.asyncio
async def test_mcp_queue_tools_use_same_backend(prepared,monkeypatch):
    task_id, _, client=prepared
    monkeypatch.setattr(mcp_server,"_backend",mcp_server.InProcessBackend())
    assert (await mcp_server.list_agent_note_jobs())["jobs"][0]["task_id"]==task_id
    token=(await mcp_server.claim_agent_note_job(task_id))["claim_token"]
    await mcp_server.report_agent_transcript_issues(task_id, token, [])
    assert len((await mcp_server.get_agent_note_material(task_id,200,100))["segments"])==5
    assert (await mcp_server.submit_agent_note_result(task_id,token,"# MCP 笔记"))["status"]=="completed"


@pytest.mark.parametrize('position', ['none', 'start', 'end'])
def test_agent_commentary_position_reaches_prompt_and_export(prepared, position):
    task_id, directory, client = prepared
    job = agent_queue.read_job(task_id)
    job['settings'].update(ai_commentary=True, ai_commentary_position=position)
    agent_queue.write_job(task_id, job)
    packet = client.get(f'/api/agent/jobs/{task_id}').json()
    expected = {'none': '不添加 AI 点评', 'start': '标题之后、正文之前', 'end': '正文之后'}[position]
    assert expected in packet['prompt']
    assert packet['settings']['ai_commentary_position'] == position
    token = client.post(f'/api/agent/jobs/{task_id}/claim', json={}).json()['claim_token']
    assert client.post(f'/api/agent/jobs/{task_id}/transcript-issues', json={'claim_token':token,'issues':[]}).status_code == 200
    note = '# Note\n\n## Source\n\nOriginal content.'
    if position != 'none':
        note += '\n\n## AI Commentary\n\nAnalysis.'
    response = client.post(f'/api/agent/jobs/{task_id}/result', json={'claim_token': token, 'markdown': note})
    assert response.status_code == 200, response.text
    result = main.tasks[task_id]['result']['markdown']
    if position == 'none':
        assert 'AI Commentary' not in result
    else:
        assert (result.index('AI Commentary') < result.index('## Source')) == (position == 'start')
