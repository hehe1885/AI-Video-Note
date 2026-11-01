"""Agent Skill 脚本的凭据处理测试。

`skills/AI-Video-Note/scripts/video_note.py` 不在包里（没有可导入的包路径），
所以用 importlib 按文件加载——这个脚本是发给用户机器跑的，Key 的来路和出口
必须钉住：命令行明文要能换成环境变量/stdin，任何回显凭据的文本都要先脱敏。
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "AI-Video-Note"
    / "scripts"
    / "video_note.py"
)
spec = importlib.util.spec_from_file_location("video_note_skill", SCRIPT)
assert spec and spec.loader
video_note = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_note)


def test_skill_redacts_every_credential_shape() -> None:
    plain = "Vh7NqzL0mBxc8dke"
    video_note.remember_secret(plain)
    out = video_note.redact(
        'HTTP 401 upstream echoed {"api_key": "sk-abcdef12345", '
        '"authorization": Bearer Q29uZ3JldGVzczEyMzQ1Ng, "note": "' + plain + '"}'
    )
    assert "sk-abcdef12345" not in out
    assert "Q29uZ3JldGVzczEyMzQ1Ng" not in out
    assert plain not in out
    assert plain[:4] + "****" in out
    # 不能顺手把模型文件名单咬坏
    assert "tokenizer.json" in video_note.redact("missing tokenizer.json in snapshot")


def test_skill_reads_key_from_stdin_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env_name = video_note.API_KEY_ENV
    monkeypatch.setenv(env_name, "envkey-9f8e7d6c")
    from_argv = SimpleNamespace(api_key="argvkey-1a2b3c4d")
    from_env = SimpleNamespace(api_key="")
    from_stdin = SimpleNamespace(api_key="-")

    monkeypatch.setattr(video_note.sys, "stdin", io.StringIO("  piped-key-1a2b3c4d \n"))
    assert video_note.resolve_api_key(from_stdin) == "piped-key-1a2b3c4d"
    assert video_note.resolve_api_key(from_env) == "envkey-9f8e7d6c"
    assert video_note.resolve_api_key(from_argv) == "argvkey-1a2b3c4d"
    monkeypatch.delenv(env_name)
    assert video_note.resolve_api_key(from_env) == ""


def test_running_service_is_reused_without_launch(monkeypatch):
    monkeypatch.setattr(video_note, 'running_service', lambda: 'http://127.0.0.1:8765')
    monkeypatch.setattr(video_note.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must reuse'))
    assert video_note.find_service() == 'http://127.0.0.1:8765'


def test_exported_location_and_unicode_paths_are_argv_not_shell(tmp_path, monkeypatch):
    import json
    skill = tmp_path/'agent-skills'/'AI-Video-Note'
    script = skill/'scripts'/'video_note.py'
    script.parent.mkdir(parents=True)
    app = tmp_path/'软件 带空格'
    app.mkdir(); exe = app/'AI-Video-Note.exe';exe.touch()
    (skill/'startup.json').write_text(json.dumps({'application':str(exe)}), encoding='utf-8')
    monkeypatch.setattr(video_note,'__file__',str(script))
    monkeypatch.delenv('AI_VIDEO_NOTE_HOME',raising=False)
    assert video_note.startup_command() == ([str(exe),'--no-browser'],app)
    monkeypatch.setenv('AI_VIDEO_NOTE_HOME',str(tmp_path/'moved'))
    with pytest.raises(RuntimeError,match='重新导出'):
        video_note.startup_command()


def test_launch_waits_for_health_before_returning(monkeypatch,tmp_path):
    health = iter([None,None,'http://127.0.0.1:8766'])
    monkeypatch.setattr(video_note,'running_service',lambda:next(health))
    monkeypatch.setattr(video_note,'startup_command',lambda:(['app.exe','--no-browser'],tmp_path))
    monkeypatch.setattr(video_note.tempfile,'gettempdir',lambda:str(tmp_path))
    monkeypatch.setattr(video_note.time,'sleep',lambda seconds:None)
    captured=[]
    monkeypatch.setattr(video_note.subprocess,'Popen',lambda *a,**kw:captured.append((a,kw)))
    assert video_note.find_service()=='http://127.0.0.1:8766'
    assert len(captured)==1 and captured[0][1]['env']['AI_VIDEO_NOTE_NO_BROWSER']=='1'
    assert captured[0][1].get('shell',False) is False


def test_opt_out_and_timeout_never_submit_or_relaunch(monkeypatch,tmp_path):
    monkeypatch.setattr(video_note,'running_service',lambda:None)
    captured=[]
    monkeypatch.setattr(video_note.subprocess,'Popen',lambda *a,**kw:captured.append(a))
    with pytest.raises(SystemExit):video_note.find_service(auto_start=False)
    assert captured==[]
    monkeypatch.setattr(video_note,'startup_command',lambda:(['app.exe'],tmp_path))
    monkeypatch.setattr(video_note.tempfile,'gettempdir',lambda:str(tmp_path))
    with pytest.raises(SystemExit):video_note.find_service(startup_timeout=0)
    assert len(captured)==1


@pytest.mark.parametrize('agent_review', [False, True])
def test_transcript_submission_includes_api_or_agent_proofreading(monkeypatch, agent_review):
    monkeypatch.setattr(video_note.sys, 'argv', ['video_note.py', 'https://youtu.be/test', '--transcript-only'] + (['--review-agent'] if agent_review else []))
    monkeypatch.setattr(video_note, 'find_service', lambda **kw: 'http://127.0.0.1:8765')
    monkeypatch.setattr(video_note, 'pick_saved_channel', lambda base: {'model_type': 'custom', 'base_url': 'https://model.invalid/v1', 'model': 'proofreader'})
    monkeypatch.setattr(video_note, 'resolve_api_key', lambda args: '')
    monkeypatch.setattr(video_note, 'poll_task', lambda *args: {'status': 'awaiting_agent' if agent_review else 'completed', 'result': {}})
    calls = []
    def http(method, url, payload=None, **kwargs):
        calls.append((method, url, payload))
        return {'task_id': 'fixture'} if method == 'POST' else {'text': 'checked transcript'}
    monkeypatch.setattr(video_note, 'http_json', http)
    video_note.main()
    payload = calls[0][2]
    assert calls[0][1].endswith('/api/transcribe')
    assert payload['generation_mode'] == ('agent' if agent_review else 'api')
    if agent_review:
        assert payload['agent_execution'] == 'manual' and payload['llm_config'] == {}
        assert len(calls) == 1
    else:
        assert payload['llm_config']['model'] == 'proofreader'
        assert calls[-1][1].endswith('/transcript?output_format=markdown')
