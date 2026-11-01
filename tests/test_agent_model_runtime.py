import asyncio
import json
import sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from backend import agent_auto, agent_queue, main
from backend.agent_model_runtime import AgentModelSettings, CodexHeader, claude_report, codex_model_options

def test_explicit_model_and_effort_reach_codex_without_shell(tmp_path,monkeypatch):
    monkeypatch.setattr(agent_auto,'find_codex',lambda:'codex.exe')
    args,payload=agent_auto.invocation({'agent_provider':'codex','agent_model':'gpt-test','agent_reasoning_effort':'high'},tmp_path,tmp_path/'result.md',None,[],'source')
    assert args[args.index('--model')+1]=='gpt-test'
    assert args[args.index('--config')+1]=='model_reasoning_effort="high"'
    assert '--ignore-user-config' in args and '--sandbox' in args and args[-1]=='-'
    default,_=agent_auto.invocation({'agent_provider':'codex'},tmp_path,tmp_path/'result.md',None,[],'source')
    assert '--model' not in default and '--config' not in default
    with pytest.raises(ValidationError): AgentModelSettings(agent_model='--shell something')
    with pytest.raises(ValidationError): AgentModelSettings(agent_reasoning_effort='invalid')

def test_header_parser_never_accepts_prompt_or_generated_model_claim():
    parser=CodexHeader();report={}
    for line in [b'OpenAI Codex v1.0\n',b'--------\n',b'model: real-model\n',b'reasoning effort: high\n',b'--------\n',b'user\n',b'model: fake-model\n',b'reasoning effort: low\n']:
        report.update(parser.feed(line))
    assert report=={'reported_model':'real-model','reported_reasoning_effort':'high','report_source':'codex_header'}
    assert CodexHeader().feed(b'model: fabricated\n')=={}
    assert claude_report([{'type':'result','result':'model: fake'}])=={}
    assert claude_report([{'type':'assistant','message':{'model':'claude-test'} }])['reported_model']=='claude-test'

def test_cache_only_returns_public_model_fields(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'models_cache.json').write_text(json.dumps({'models':[{'slug':'gpt-test','display_name':'Test','instructions':'private','supported_reasoning_levels':[{'effort':'high'},{'effort':'bad'}]}]}),encoding='utf-8')
    assert codex_model_options()==[{'id':'gpt-test','label':'Test','reasoning_efforts':['high']}]
    (tmp_path/'models_cache.json').write_text('broken',encoding='utf-8')
    assert codex_model_options()==[]

@pytest.mark.asyncio
async def test_live_report_persisted_and_does_not_guess_requested_model(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{})
    task_id='model-test';directory=tmp_path/task_id;directory.mkdir()
    request=main.SummarizeRequest(llm_config={},agent_model='requested-model',agent_reasoning_effort='high')
    main.tasks[task_id]=main.new_task('awaiting_agent',task_id)
    main.write_task_manifest(task_id,request,None,'web')
    agent_queue.write_job(task_id,{'settings':request.model_dump(exclude={'llm_config','bilibili_cookie','douyin_cookie'}),'title':'test'})
    script="import sys,time;from pathlib import Path;sys.stdin.buffer.read();sys.stderr.write('OpenAI Codex v1\\n--------\\nmodel: actual-model\\nreasoning effort: high\\n--------\\nuser\\nmodel: fake-model\\n');sys.stderr.flush();time.sleep(.3);Path(sys.argv[1]).write_text('# Note',encoding='utf-8')"
    monkeypatch.setattr(agent_auto,'invocation',lambda settings,directory,output,*args: ([sys.executable,'-c',script,str(output)],b'material'))
    job=asyncio.create_task(agent_auto.run_cli(task_id,'material'))
    for _ in range(100):
        if main.tasks[task_id].get('agent_runtime',{}).get('reported_model'): break
        await asyncio.sleep(.02)
    report=main.tasks[task_id]['agent_runtime']
    assert report['reported_model']=='actual-model'
    assert report['requested_model']=='requested-model'
    assert await job=='# Note'
    assert main.read_task_manifest(task_id)['runtime']['agent_runtime']['reported_model']=='actual-model'
    main.tasks.clear();main.restore_tasks_from_workspace()
    assert main.tasks[task_id]['agent_runtime']['reported_model']=='actual-model'

@pytest.mark.asyncio
async def test_retry_preserves_model_settings(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{'retry':main.new_task('awaiting_agent','retry')})
    (tmp_path/'retry').mkdir()
    agent_queue.write_job('retry',{'settings':{},'lease_until':0})
    launched=[];monkeypatch.setattr(agent_auto,'launch',launched.append)
    await agent_queue.retry_auto('retry',agent_queue.AutoSettings(agent_model='gpt-test',agent_reasoning_effort='xhigh'))
    settings=agent_queue.read_job('retry')['settings']
    assert settings['agent_model']=='gpt-test' and settings['agent_reasoning_effort']=='xhigh'
    assert launched==['retry']


@pytest.mark.parametrize('provider', ['claude','qwen','antigravity','custom'])
def test_all_provider_model_arguments(provider,tmp_path,monkeypatch):
    monkeypatch.setattr(agent_auto,'find_claude',lambda:['claude'])
    monkeypatch.setattr(agent_auto,'find_text_agent',lambda provider:[provider])
    settings={'agent_provider':provider,'agent_model':'specific-model','agent_command':['python','adapter.py','--model','{model}','--metadata','{metadata_file}']}
    args,payload=agent_auto.invocation(settings,tmp_path,tmp_path/'out.md',None,[],'material')
    assert args[args.index('--model')+1]=='specific-model'
    assert b'material' in payload
    if provider=='qwen': assert args[args.index('--output-format')+1]=='json'
    if provider=='custom':
        assert args[-1]==str(tmp_path/'model-metadata.json')
        settings['agent_command']=['python','adapter.py']
        with pytest.raises(RuntimeError,match='model'): agent_auto.invocation(settings,tmp_path,tmp_path/'out.md',None,[],'material')


def test_model_catalogs_are_allowlisted_and_handle_missing_settings(tmp_path,monkeypatch):
    from backend.agent_model_runtime import local_model_catalogs
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'.claude'))
    (tmp_path/'.claude').mkdir(); (tmp_path/'.qwen').mkdir()
    (tmp_path/'.claude'/'settings.json').write_text(json.dumps({'model':'claude-specific','env':{'ANTHROPIC_API_KEY':'secret','ANTHROPIC_DEFAULT_SONNET_MODEL':'claude-other'}}))
    (tmp_path/'.qwen'/'settings.json').write_text(json.dumps({'model':{'name':'qwen-specific'},'modelProviders':{'openai':[{'id':'local-model','name':'Local model','apiKey':'secret'}]}}))
    data=local_model_catalogs()
    assert 'secret' not in json.dumps(data)
    assert any(m['id']=='claude-specific' for m in data['claude'])
    assert data['qwen']==[{'id':'qwen-specific','label':'qwen-specific'},{'id':'local-model','label':'Local model'}]
    (tmp_path/'.qwen'/'settings.json').write_text('invalid')
    assert local_model_catalogs()['qwen']==[]


@pytest.mark.parametrize('provider,events',[
    ('claude',[{'type':'assistant','message':{'model':'claude-real'}}]),
    ('qwen',[{'type':'system','subtype':'session_start','model':'qwen-real'}]),
    ('antigravity',[{'event':'init','init':{'model':'gemini-real'}}]),
])
def test_provider_metadata_never_uses_generated_text(provider,events):
    from backend.agent_model_runtime import structured_model_report
    assert structured_model_report(events,provider)['reported_model'].endswith('-real')
    assert structured_model_report([{'type':'result','result':'model: invented','model':'fake'},{'event':'step_update','step_update':{'text_delta':'model: fake'}}],provider)=={}


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['claude','qwen','antigravity','custom'])
async def test_provider_result_and_model_report_roundtrip(provider,tmp_path,monkeypatch):
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{})
    task_id='provider-run';(tmp_path/task_id).mkdir()
    main.tasks[task_id]=main.new_task('awaiting_agent',task_id)
    agent_queue.write_job(task_id,{'settings':{'agent_provider':provider,'agent_model':'requested-model'}})
    events=[{'type':'system','subtype':'init','model':'actual-model'},{'type':'result','result':'# Note'}]
    if provider=='antigravity': events=[{'event':'init','init':{'model':'actual-model'}},{'event':'result','result':{'status':'SUCCESS','response':'# Note'}}]
    stdout=json.dumps(events) if provider=='qwen' else '\n'.join(json.dumps(e) for e in events)
    script='import sys;sys.stdin.buffer.read();print('+repr(stdout)+')'
    if provider=='custom': script="import sys,json;from pathlib import Path;sys.stdin.buffer.read();Path('model-metadata.json').write_text(json.dumps({'model':'actual-model'}));print('# Note')"
    monkeypatch.setattr(agent_auto,'invocation',lambda *args:([sys.executable,'-c',script],b'material'))
    assert await agent_auto.run_cli(task_id,'material')=='# Note'
    assert main.tasks[task_id]['agent_runtime']['requested_model']=='requested-model'
    assert main.tasks[task_id]['agent_runtime']['reported_model']=='actual-model'



def test_claude_catalog_uses_resolved_names_without_prompt(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from backend.agent_model_runtime import claude_model_options
    def fake_run(args,**kwargs):
        request=json.loads(kwargs['input'])
        assert request['type']=='control_request' and request['request']['subtype']=='initialize'
        assert '--no-session-persistence' in args and '--strict-mcp-config' in args
        models=[{'value':'opus','resolvedModel':'actual-model[1m]'}, {'value':'sonnet','resolvedModel':'actual-model[1m]'}, {'value':'claude-concrete','resolvedModel':'claude-concrete'}]
        response={'type':'control_response','response':{'request_id':'model-discovery','subtype':'success','response':{'models':models,'account':{'private':'secret'}}}}
        return SimpleNamespace(returncode=0,stdout=json.dumps(response).encode())
    monkeypatch.setattr(subprocess,'run',fake_run)
    assert claude_model_options(['claude'])==[{'id':'actual-model[1m]','label':'actual-model[1m]'}, {'id':'claude-concrete','label':'claude-concrete'}]
    assert AgentModelSettings(agent_model='actual-model[1m]').agent_model=='actual-model[1m]'
