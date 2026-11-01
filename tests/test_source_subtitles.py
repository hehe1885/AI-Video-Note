import json
from pathlib import Path
import pytest
from backend import main
from backend.video_processor import VideoProcessor, VideoSource


def track(language, ai_type=0):
    return {'lan':language, 'subtitle_url':'//example.com/sub.json', 'ai_type':ai_type}


@pytest.mark.parametrize('tracks,expected',[
    ([track('ai-en',1),track('ai-ja',1)],None),
    ([track('ai-en',1),track('ai-zh',0)],'ai-zh'),
    ([track('ai-zh',1),track('ai-en',0)],'ai-en'),
    ([{'lan':'ai-en','subtitle_url':'//example.com/sub.json'}],None),
    ([track('ai-ja',0)],'ai-ja'),
])
def test_bilibili_source_tracks_are_selected_without_translation_fallback(monkeypatch,tracks,expected):
    monkeypatch.setattr(VideoProcessor,'_bilibili_get_json',staticmethod(lambda *args:{'data':{'subtitle':{'subtitles':tracks}}}))
    selected=VideoProcessor._bilibili_subtitle_track('BV1miht6gERa',None,123,{})
    assert (selected['language'] if selected else None)==expected


@pytest.mark.asyncio
async def test_generic_bilibili_extractor_cannot_bypass_original_check(tmp_path):
    processor=VideoProcessor(tmp_path)
    notes=[]
    result=await processor.fetch_subtitles('https://www.bilibili.com/video/BV1miht6gERa/',
        {'subtitles':{'ai-en':[{'ext':'json','data':{'body':[{'from':0,'to':1,'content':'translated'}]}}]}},notes=notes)
    assert result is None and notes


def test_known_original_language_wins_and_translated_urls_are_filtered():
    info={'language':'en','automatic_captions':{
        'zh':[{'ext':'vtt','url':'https://example.com/caption?tlang=zh'}],
        'en':[{'ext':'vtt','url':'https://example.com/original'}]}}
    result=list(VideoProcessor._subtitle_candidates(info))
    assert result and {item[0] for item in result}=={'en'}
    info.pop('language')
    assert all('tlang=' not in item[1]['url'] for item in VideoProcessor._subtitle_candidates(info))


@pytest.mark.asyncio
async def test_translation_only_bilibili_page_falls_back_to_asr(tmp_path,monkeypatch):
    from backend.video_processor import BiliPage,BiliVideoInfo
    processor=VideoProcessor(tmp_path)
    async def pages(*args,**kwargs):return BiliVideoInfo('BV1miht6gERa',None,'中文视频',(BiliPage(1,'正文',123,10),))
    monkeypatch.setattr(processor,'bilibili_video_pages',pages)
    monkeypatch.setattr(VideoProcessor,'_bilibili_get_json',staticmethod(lambda *args:{'data':{'subtitle':{'subtitles':[track('ai-en',1)]}}}))
    outcome=await processor.fetch_bilibili_subtitles('https://www.bilibili.com/video/BV1miht6gERa/',{'sessdata':'fixture'})
    assert outcome.result is None and [page.page for page in outcome.pages_to_transcribe]==[1]


@pytest.mark.asyncio
async def test_subtitle_policy_survives_write_load_for_safe_reuse(tmp_path,monkeypatch):
    from backend.transcript import TranscriptSegment
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    task_dir=main.task_directory('source-policy');task_dir.mkdir(parents=True)
    await main.write_transcript_files('source-policy',[TranscriptSegment(0,1,'中文原文')],
        {'language':'ai-zh','source':'bilibili_ai_subtitle','subtitle_policy':'source-only-v1'})
    assert main.load_transcript_result(task_dir)['subtitle_policy']=='source-only-v1'


@pytest.mark.asyncio
async def test_old_translated_cache_is_not_reused_and_original_audio_is_transcribed(tmp_path,monkeypatch):
    from backend import transcript_review
    from backend.transcript import TranscriptSegment
    from backend.video_processor import BiliSubtitleOutcome
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    processor=VideoProcessor(tmp_path)
    old=main.task_directory('old');old.mkdir(parents=True)
    original=json.dumps({'language':'ai-en','source':'bilibili_ai_subtitle',
        'segments':[{'start':0,'end':10,'text':'English translation from old run'}]})
    (old/'transcript.json').write_text(original,encoding='utf-8')
    calls=[]
    async def info(*args,**kwargs):return {'title':'中文视频','source':'bilibili','duration':10}
    async def no_source(*args,**kwargs):return BiliSubtitleOutcome(None,'no_track')
    async def audio(*args,**kwargs):return tmp_path/'audio.wav'
    async def transcribe(*args,**kwargs):
        calls.append('asr')
        return {'segments':[TranscriptSegment(0,10,'这是一段来自原声的中文识别内容，保留原本的意思。')],
            'language':'zh','duration':10,'device':'cpu','model':'base','requested_model':'base'}
    async def review(runtime,task_id,request,segments,*args):return segments
    monkeypatch.setattr(processor,'get_video_info',info)
    monkeypatch.setattr(processor,'fetch_bilibili_subtitles',no_source)
    monkeypatch.setattr(processor,'download_audio',audio)
    monkeypatch.setattr(main,'video_processor',processor)
    monkeypatch.setattr(main,'run_transcription',transcribe)
    monkeypatch.setattr(transcript_review,'review_transcript_output',review)
    monkeypatch.setattr(main,'archive_note',lambda *args,**kwargs:None)
    monkeypatch.setattr(main,'tasks',{'new':main.new_task(task_id='new')})
    main.tasks['new']['resume_task_id']='old'
    main.task_directory('new').mkdir(parents=True)
    request=main.SummarizeRequest(video_url='https://www.bilibili.com/video/BV1miht6gERa/',output='transcript',llm_config={})
    await main.process_video_task('new',request)
    assert main.tasks['new']['status']=='completed',main.tasks['new'].get('error')
    assert calls==['asr']
    saved=json.loads((main.task_directory('new')/'transcript.json').read_text(encoding='utf-8'))
    assert saved['language']=='zh' and 'English translation' not in str(saved)
    assert (old/'transcript.json').read_text(encoding='utf-8')==original
