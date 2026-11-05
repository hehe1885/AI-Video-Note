import json
from pathlib import Path
import pytest
from backend import main, agent_queue
from backend.note_options import strip_note_properties


@pytest.mark.parametrize('enabled',[True,False,None])
@pytest.mark.asyncio
async def test_agent_properties_setting_controls_actual_saved_files(tmp_path,monkeypatch,enabled):
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path/'workspace')
    monkeypatch.setattr(main,'tasks',{})
    vault=tmp_path/'vault';vault.mkdir()
    task_id='properties'
    request=main.SummarizeRequest(video_url='https://youtu.be/test',generation_mode='agent',
        screenshot_density='none',note_format='obsidian',llm_config={},source_link_position='start',
        obsidian={'vault':str(vault),'note_folder':'视频笔记'},
        **({'obsidian_properties':enabled} if enabled is not None else {}))
    directory=main.task_directory(task_id);directory.mkdir(parents=True)
    main.tasks[task_id]=main.new_task(status='processing',task_id=task_id)
    main.write_task_manifest(task_id,request,None,'web')
    assert main.read_task_manifest(task_id)['obsidian_properties']==(enabled is not False)
    (directory/'transcript.json').write_text(json.dumps({'segments':[{'start':0,'end':10,'text':'字幕素材'}]}),encoding='utf-8')
    await agent_queue.prepare(task_id,request,'测试标题',{},[],source_url=request.video_url)
    if enabled is None:
        packet=agent_queue.read_job(task_id);packet['settings'].pop('obsidian_properties');agent_queue.write_job(task_id,packet)
    token=(await agent_queue.claim_job(task_id,agent_queue.Claim()))['claim_token']
    await agent_queue.report_transcript_issues(task_id,agent_queue.TranscriptIssues(claim_token=token,issues=[]))
    # Even an unwanted model-authored front matter is omitted when explicitly disabled.
    body='# 正文\n\n正文保留。\n\n#主题标签'
    if enabled is False:body='---\ntitle: 模型生成的属性\nsource: model-source\n---\n\n'+body
    await agent_queue.finish(task_id,agent_queue.Result(claim_token=token,markdown=body))
    task=main.tasks[task_id]
    paths=[directory/'notes.md',Path(task['result']['obsidian_path']),Path(task['result']['archived_path'])]
    for path in paths:
        saved=path.read_text(encoding='utf-8')
        assert saved.startswith('---\ntitle:') is (enabled is not False)
        assert '正文保留。' in saved and '#主题标签' in saved and 'https://youtu.be/test' in saved
        if enabled is False:assert '模型生成的属性' not in saved and 'source:' not in saved


def test_properties_off_keeps_ordinary_markdown_and_code():
    for body in ('---\n\n正文\n\n---\n\n后文','```yaml\n---\ntitle: 示例\n---\n```','# 正文\n\n---\n\ntitle: 普通内容'):
        assert strip_note_properties(body)==body
    assert strip_note_properties('---\ntitle: 标题\nsource: ""\n---\n\n# 正文')=='# 正文'
    assert main.SummarizeRequest(llm_config={}).obsidian_properties is True
