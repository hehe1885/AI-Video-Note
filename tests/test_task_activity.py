import asyncio
from types import SimpleNamespace
import pytest
from backend.task_activity import activity, watch_pipeline


@pytest.fixture
def runtime():
    task = {'status':'processing', 'logs':[], 'progress_message':'原阶段', 'step_name':'准备音频'}
    return SimpleNamespace(tasks={'task':task}, persist_task_runtime=lambda task_id:None)


@pytest.mark.asyncio
async def test_stage_reports_wait_completion_and_stops_heartbeat(runtime):
    task = runtime.tasks['task']
    async with activity(runtime,'task','字幕校对',interval=.01) as report:
        await report('正在校对第 1/2 批')
        await asyncio.sleep(.025)
    assert task['logs'][0] == '字幕校对：开始'
    assert any('1/2' in line for line in task['logs'])
    assert any('等待模型回复' in line for line in task['logs'])
    assert '完成，用时' in task['logs'][-1]
    count = len(task['logs'])
    await asyncio.sleep(.025)
    assert len(task['logs']) == count
    assert task.get('_activity_token') is None


@pytest.mark.asyncio
async def test_failure_reports_no_secrets_and_terminal_state_is_not_overwritten(runtime):
    task = runtime.tasks['task']
    with pytest.raises(RuntimeError):
        async with activity(runtime,'task','视觉核对',interval=.01):
            raise RuntimeError('secret-url-and-credentials')
    assert '未完成' in task['logs'][-1]
    assert 'secret' not in '\n'.join(task['logs'])
    async with activity(runtime,'task','模型处理',interval=.01):
        task.update(status='cancelled',progress_message='已取消')
        count = len(task['logs'])
        await asyncio.sleep(.025)
    assert task['progress_message'] == '已取消'
    assert len(task['logs']) == count


@pytest.mark.asyncio
async def test_pipeline_logs_waiting_media_stage_but_stays_quiet_for_user_confirmation(runtime):
    task = runtime.tasks['task']
    async with watch_pipeline(runtime,'task',interval=.01):
        await asyncio.sleep(.03)
        assert any('准备音频：仍在处理' in line for line in task['logs'])
        task['transcript_review'] = {'status':'pending'}
        count = len(task['logs'])
        await asyncio.sleep(.03)
        assert len(task['logs']) == count
    await asyncio.sleep(.02)
    assert len(task['logs']) == count


@pytest.mark.asyncio
async def test_nested_phase_owns_heartbeat_and_restores_parent(runtime):
    task = runtime.tasks['task']
    async with activity(runtime,'task','音频核对',interval=.01):
        async with activity(runtime,'task','读取音频',waiting='正在下载',interval=.01):
            await asyncio.sleep(.025)
            assert '读取音频' in task['progress_message']
        assert task['progress_message'] == '音频核对：开始'
    assert task.get('_activity_token') is None
