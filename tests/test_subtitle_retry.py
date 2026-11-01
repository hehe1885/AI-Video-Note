import asyncio
import urllib.error
import pytest
from backend.video_processor import VideoProcessor


@pytest.mark.asyncio
async def test_subtitles_retry_timeout_then_use_subtitles(tmp_path,monkeypatch):
    processor=VideoProcessor(tmp_path)
    calls=[]
    def download(*args):
        calls.append(1)
        if len(calls)==1: raise TimeoutError('signed-url-secret')
        return '1\n00:00:00,000 --> 00:00:01,000\nhello\n'
    monkeypatch.setattr(processor,'_download_text',download)
    notes=[]
    result=await processor.fetch_subtitles('https://youtube.com/watch?v=test',{'subtitles':{'zh':[{'url':'https://example.com/secret','ext':'srt'}]}},notes=notes)
    assert result and result.segments[0].text=='hello'
    assert len(calls)==2
    assert any('正在重试字幕' in note for note in notes)
    assert any('字幕读取成功' in note for note in notes)
    assert 'secret' not in '\n'.join(notes)


@pytest.mark.asyncio
async def test_subtitles_try_another_format_after_failure(tmp_path,monkeypatch):
    processor=VideoProcessor(tmp_path)
    calls=[]
    def download(url,*args):
        calls.append(url)
        if url.endswith('json3'): raise urllib.error.HTTPError(url,403,'expired-secret',{},None)
        return 'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n'
    monkeypatch.setattr(processor,'_download_text',download)
    notes=[]
    result=await processor.fetch_subtitles('https://youtube.com/watch?v=test',{'subtitles':{'zh':[{'url':'https://example.com/json3','ext':'json3'},{'url':'https://example.com/vtt','ext':'vtt'}]}},notes=notes)
    assert result and result.source=='platform_subtitle'
    assert len(calls)==2
    assert any('HTTP 403' in note for note in notes)


@pytest.mark.asyncio
async def test_subtitle_failures_are_not_silently_reported_as_no_subtitles(tmp_path,monkeypatch):
    processor=VideoProcessor(tmp_path)
    calls=[]
    def download(url,*args):
        calls.append(url)
        raise urllib.error.HTTPError(url,429,'server-secret',{},None)
    monkeypatch.setattr(processor,'_download_text',download)
    notes=[]
    result=await processor.fetch_subtitles('https://youtube.com/watch?v=test',{'subtitles':{'zh-hans':[{'url':'https://example.com/signed-secret','ext':'vtt'}]}},notes=notes)
    assert result is None
    # zh-hans matches both zh-hans and zh preferences, but is only downloaded once per attempt.
    assert len(calls)==2
    assert any('HTTP 429' in note for note in notes)
    assert '平台存在字幕轨' in notes[-1]
    assert 'secret' not in '\n'.join(notes)


@pytest.mark.asyncio
async def test_cancel_during_subtitle_retry_does_not_start_more_downloads(tmp_path,monkeypatch):
    processor=VideoProcessor(tmp_path)
    started=asyncio.Event()
    calls=[]
    def download(*args):
        calls.append(1)
        raise TimeoutError()
    async def pause(*args):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(processor,'_download_text',download)
    monkeypatch.setattr('backend.video_processor.asyncio.sleep',pause)
    task=asyncio.create_task(processor.fetch_subtitles('https://example.com/video',{'subtitles':{'en':[{'url':'https://example.com/sub','ext':'vtt'}]}}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert len(calls)==1

@pytest.mark.asyncio
async def test_available_platform_subtitles_skip_audio_and_asr(tmp_path,monkeypatch):
    from backend import main
    from backend import transcript_review
    async def checked(runtime, task_id, request, segments, *args): return segments
    monkeypatch.setattr(transcript_review, 'review_transcript_output', checked)
    processor=VideoProcessor(tmp_path)
    async def info(*args,**kwargs):
        return {'title':'subtitle regression','source':'youtube','duration':1,'subtitles':{'zh':[{'ext':'vtt','data':'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n字幕正文\n'}]}}
    async def forbidden(*args,**kwargs):
        raise AssertionError('Available platform subtitles must skip audio and ASR')
    monkeypatch.setattr(processor,'get_video_info',info)
    monkeypatch.setattr(processor,'download_audio',forbidden)
    monkeypatch.setattr(main,'run_transcription',forbidden)
    monkeypatch.setattr(main,'video_processor',processor)
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{})
    task_id='subtitle-regression'
    (tmp_path/task_id).mkdir()
    main.tasks[task_id]=main.new_task()
    main.tasks[task_id]['output']='transcript'
    request=main.SummarizeRequest(video_url='https://youtube.com/watch?v=test',output='transcript',llm_config=main.LLMConfig())
    await main.process_video_task(task_id,request)
    task=main.tasks[task_id]
    assert task['status']=='completed'
    assert task['result']['segment_count']==1
    assert any('已使用平台字幕' in line for line in task['logs'])
    assert not list((tmp_path/task_id).glob('audio.*'))
