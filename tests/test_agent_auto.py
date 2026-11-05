import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend import agent_auto, agent_queue, main


def test_provider_invocations_use_stdin_and_preserve_images(tmp_path, monkeypatch):
    image = tmp_path/'image.jpg'
    image.write_bytes(b'test-image')
    monkeypatch.setattr(agent_auto, 'find_codex', lambda: 'codex.exe')
    args, payload = agent_auto.invocation({'agent_provider':'codex'},tmp_path,tmp_path/'out.md',None,[image],'material')
    assert '--image' in args and '--sandbox' in args and 'read-only' in args
    assert 'material' not in args and payload==b'material'
    monkeypatch.setattr(agent_auto,'find_claude',lambda:['claude.exe'])
    args, payload = agent_auto.invocation({'agent_provider':'claude'},tmp_path,tmp_path/'out.md',None,[image],'material')
    assert '--tools' in args and args[args.index('--tools')+1]==''
    content=json.loads(payload)['message']['content']
    assert content[0]['text']=='material' and content[1]['type']=='image'
    args, payload = agent_auto.invocation({'agent_provider':'custom','agent_command':['python','agent.py','{output_file}']},tmp_path,tmp_path/'out.md',None,[],'material')
    assert args[-1]==str(tmp_path/'out.md') and payload==b'material'


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    async def reviewed(task_id, segments, *args):
        from backend.transcript import TranscriptSegment
        main.tasks[task_id]['transcript_review'] = {'status': 'clear', 'count': 0}
        return [TranscriptSegment(s['start'], s['end'], s['text']) for s in segments]
    monkeypatch.setattr(agent_auto, 'review_agent_transcript', reviewed)
    task_id='auto-test'
    directory=tmp_path/task_id
    directory.mkdir()
    request=main.SummarizeRequest(video_url='https://youtu.be/test',generation_mode='agent',llm_config={},screenshot_density='medium')
    main.tasks[task_id]=main.new_task(status='awaiting_agent',task_id=task_id)
    main.write_task_manifest(task_id,request,None,'web')
    (directory/'transcript.json').write_text(json.dumps({'segments':[{'start':0,'end':10,'text':'Fixture material'}]}),encoding='utf-8')
    agent_queue.write_job(task_id,{'settings':request.model_dump(exclude={'llm_config','bilibili_cookie','douyin_cookie'}),'title':'Fixture','info':{'duration':10},'video_path':str(directory/'preview.mp4'),'screenshots':[]})
    return task_id,directory


def test_automatic_worker_selects_frames_and_returns_note(job, monkeypatch):
    task_id, directory=job
    async def fake_cli(task_id,prompt,schema=None,images=()):
        if schema: return '{"times":[3]}'
        assert 'frames/frame.jpg' in prompt
        return '# Fixture\n\nText\n\n![Image](frames/frame.jpg)'
    async def extract(video,task_id,times,cancel):
        frames=directory/'frames'; frames.mkdir()
        image=frames/'frame.jpg'; image.write_bytes(b'fixture')
        return {3:image}
    monkeypatch.setattr(agent_auto,'run_cli',fake_cli)
    monkeypatch.setattr(main.video_processor,'extract_frames_at',extract)
    asyncio.run(agent_auto.work(task_id))
    assert main.tasks[task_id]['status']=='completed'
    assert main.tasks[task_id]['result']['screenshot_count']==1
    assert '![Image]' in (directory/'notes.md').read_text(encoding='utf-8')


def test_auto_failure_releases_claim_for_manual_fallback(job, monkeypatch):
    task_id,_=job
    async def fail(*args, **kwargs): raise RuntimeError('Fixture failure')
    monkeypatch.setattr(agent_auto,'run_cli',fail)
    asyncio.run(agent_auto.work(task_id))
    assert main.tasks[task_id]['status']=='awaiting_agent'
    assert agent_queue.read_job(task_id)['lease_until']==0
    assert 'Fixture failure' in main.tasks[task_id]['advisory']
    assert not main.tasks[task_id]['_agent_auto_running']


def test_english_worker_prompts_and_screenshot_plan(job, monkeypatch):
    task_id, directory = job
    data = agent_queue.read_job(task_id)
    data['settings']['note_language'] = 'en'
    agent_queue.write_job(task_id, data)
    prompts = []
    async def fake_cli(task_id, prompt, schema=None, images=()):
        prompts.append(prompt)
        assert not __import__('re').search(r'[\u4e00-\u9fff]', prompt)
        return '{"times":[]}' if schema else '# Fixture\n\nEnglish notes.'
    monkeypatch.setattr(agent_auto, 'run_cli', fake_cli)
    asyncio.run(agent_auto.work(task_id))
    assert main.tasks[task_id]['status'] == 'completed'
    assert len(prompts) == 2
    assert 'Output only JSON' in prompts[0]
    assert 'Return the complete note body' in prompts[1]


def test_qwen_and_custom_wrapper_language(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_auto, 'find_text_agent', lambda _: ['qwen.exe'])
    args, payload = agent_auto.invocation({'agent_provider':'qwen','note_language':'en'},
        tmp_path, tmp_path/'out.md', None, [], 'Original material')
    assert 'Generate the result from standard-input material' in args[args.index('-p')+1]
    assert payload == b'Original material'
    image = tmp_path/'image.jpg'
    image.write_bytes(b'fixture')
    args, payload = agent_auto.invocation({'agent_provider':'custom','agent_command':['agent.exe'],'note_language':'en'},
        tmp_path, tmp_path/'out.md', None, [image], 'Original material')
    assert b'Inspect these local images' in payload


def test_few_images_empty_plan_completes_text_note(job, monkeypatch):
    task_id, directory = job
    data = agent_queue.read_job(task_id)
    data['settings'].update(screenshot_density='few', vision_screenshots=True)
    agent_queue.write_job(task_id, data)
    async def fake_cli(task_id, prompt, schema=None, images=()):
        if schema:
            return '{"times":[]}'
        assert main.tasks[task_id]['step_name'] == '生成笔记'
        assert '文字笔记' in main.tasks[task_id]['logs'][-1]
        assert not images
        return '# 文字笔记\n\n保留视频内容。'
    monkeypatch.setattr(agent_auto, 'run_cli', fake_cli)
    asyncio.run(agent_auto.work(task_id))
    assert main.tasks[task_id]['status'] == 'completed'
    assert main.tasks[task_id]['result']['screenshot_count'] == 0
    assert main.tasks[task_id]['result']['screenshot_warning']


def test_few_images_all_rejected_completes_text_note(job, monkeypatch):
    task_id, directory = job
    data = agent_queue.read_job(task_id)
    data['settings'].update(screenshot_density='few', vision_screenshots=True)
    data['screenshots'] = [{'time':3, 'filename':'frame.jpg', 'markdown_ref':'frames/frame.jpg'}]
    agent_queue.write_job(task_id, data)
    async def reject(*args):
        current = agent_queue.read_job(task_id)
        current['screenshots'] = []
        agent_queue.write_job(task_id, current)
        main.tasks[task_id]['screenshot_warning'] = '未找到相关画面，生成文字笔记。'
        return current
    async def fake_cli(*args, **kwargs):
        assert not kwargs.get('images')
        return '# 文字笔记\n\n没有合适截图时继续正文。'
    monkeypatch.setattr(agent_auto, 'review_agent_screenshots', reject)
    monkeypatch.setattr(agent_auto, 'run_cli', fake_cli)
    asyncio.run(agent_auto.work(task_id))
    assert main.tasks[task_id]['status'] == 'completed'
    assert main.tasks[task_id]['result']['screenshot_count'] == 0
