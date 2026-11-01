import json
import pytest
from backend.note_options import apply_timeline, note_option_prompt
from backend import main
from backend.video_processor import VideoProcessor
from backend.transcript import TranscriptSegment

@pytest.mark.parametrize('mode', ['none', 'heading', 'paragraph_start', 'paragraph_end', 'all'])
def test_timeline_positions_and_code_preservation(mode):
    code = '```python\nclock = "[01:02]"\n```'
    text = '# Title\n\n## Topic [00:10-00:20]\n\n[00:10-00:15] Explanation.\n\n' + code
    result = apply_timeline(text, mode)
    assert code in result
    heading = next(line for line in result.splitlines() if line.startswith('## Topic'))
    paragraph = next(line for line in result.splitlines() if 'Explanation.' in line)
    assert ('[00:10-00:20]' in heading) == (mode in {'heading', 'all'})
    assert paragraph.startswith('[00:10-00:15]') == (mode in {'paragraph_start', 'all'})
    assert paragraph.endswith('[00:10-00:15]') == (mode in {'paragraph_end', 'all'})
    assert 'Explanation.' in result


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", ["brief", "standard", "detailed"])
async def test_audio_note_skips_images_and_preserves_subtitle_timing(tmp_path, monkeypatch, detail):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    processor = VideoProcessor(tmp_path)
    async def forbidden(*args, **kwargs): raise AssertionError('Audio must not download or extract video')
    monkeypatch.setattr(processor, 'download_preview_video', forbidden)
    monkeypatch.setattr(processor, 'extract_frames_at', forbidden)
    monkeypatch.setattr(main, 'video_processor', processor)
    task_id = 'audio-note'
    directory = tmp_path / task_id
    directory.mkdir()
    audio = directory / 'input.wav'
    audio.write_bytes(b'test-audio')
    main.tasks[task_id] = main.new_task(task_id=task_id)
    main.tasks[task_id].update(uploaded_file_path=str(audio), uploaded_filename='Lecture.wav')
    async def transcribe(path, *args, **kwargs):
        assert path == audio
        return dict(segments=[TranscriptSegment(0, 30, 'Explanation of concepts and useful examples. ' * 20)],
                    language='en', duration=30, device='cpu', model='base', requested_model='base')
    monkeypatch.setattr(main, 'run_transcription', transcribe)
    captured = []
    class Model:
        warnings = []
        def __init__(self, **kwargs): pass
        def describe_effort(self, *args): return 'test'
        async def generate_summary(self, title, *args, **kwargs):
            assert title == 'Lecture'
            captured.append(kwargs)
            return '# Lecture\n\n## Topic（00:00—00:30）\n\n[00:00-00:30] Explanation.'
    monkeypatch.setattr(main, 'LLMSummarizer', Model)
    monkeypatch.setattr(main, 'resolve_llm_credentials', lambda **kwargs: ('fake', 'test'))
    monkeypatch.setattr(main, 'archive_note', lambda *args, **kwargs: None)
    request = main.SummarizeRequest(llm_config={}, include_screenshots=True, timed_screenshots=True,
                                   timeline_mode='none', text_detail=detail)
    await main.process_video_task(task_id, request)
    task = main.tasks[task_id]
    assert task['status'] == 'completed', task.get('error')
    assert task['result']['screenshot_count'] == 0
    assert '[00:00-00:30]' not in task['result']['markdown']
    assert captured[0]['text_detail'] == detail
    assert captured[0]['custom_prompt'] == ''
    assert captured[0]['note_instructions'] == ''
    subtitles = await main.get_task_transcript(task_id)
    assert subtitles['segments'][0]['start'] == 0
    assert subtitles['segments'][0]['end'] == 30


@pytest.mark.asyncio
async def test_pending_history_uses_source_until_title_available(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {'pending': main.new_task(task_id='pending')})
    (tmp_path / 'pending').mkdir()
    (tmp_path / 'pending' / 'task.json').write_text(json.dumps({'source_url': 'https://youtube.com/watch?v=test'}))
    assert (await main.list_recent_tasks())['tasks'][0]['title'] == 'https://youtube.com/watch?v=test'
    main.update_task_manifest('pending', {'title': 'Video title'})
    assert (await main.list_recent_tasks())['tasks'][0]['title'] == 'Video title'


def test_none_preserves_inline_code_and_image_paths():
    note = '## Topic [01:00]\n\n[01:01] Use `[01:02]` in code.\n\n![Example](./frames/shot.jpg)'
    result = apply_timeline(note, 'none')
    assert '[01:00]' not in result
    assert '[01:01]' not in result
    assert '`[01:02]`' in result
    assert '![Example](./frames/shot.jpg)' in result


@pytest.mark.parametrize('stamp', [
    '[00:10-00:20]', '(00:10–00:20)', '（15:30—47:48）', '（89:65—140:06）',
    '（224:62—249:98）', '【01:20】', '［01:20］', '(01:02:03.500 ~ 01:02:08.750)',
    '（ 00：10 至 00：20 ）',
])
def test_none_removes_model_annotation_formats_without_losing_content(stamp):
    note=f'## 应用介绍{stamp}\n\n{stamp} 完整解释。\n\n> 引用 {stamp}\n\n| 内容 |\n| --- |\n| 说明 {stamp} |'
    result=apply_timeline(note,'none')
    assert stamp not in result
    for text in ('应用介绍','完整解释。','引用','说明'):
        assert text in result


def test_none_preserves_literal_times_metadata_code_math_and_destinations():
    literals=[
        '```text\nclock = "（00:10—00:20）"\n```',
        '~~~text\n[00:10]\n~~~',
        '`[01:02]`',
        '- `00:00-00:30` is a literal range example outside the navigation list.',
        '$$\n[01:02]\n$$',
        '![Example](frames/shot(00:10).jpg)',
        '[Reference](https://example.test/(00:10))',
        '> 时长：04:17',
        '会议开始于 09:30，持续 20 分钟。',
    ]
    result=apply_timeline('## Topic（00:10—00:20）\n\n'+'\n\n'.join(literals),'none')
    assert 'Topic（' not in result
    for literal in literals:
        assert literal in result


def test_none_removes_generated_long_note_toc_times_but_keeps_inline_examples():
    from backend.llm_summarizer import LLMSummarizer
    parts=[[TranscriptSegment(0,30,'first')],[TranscriptSegment(30,60,'last')]]
    note=LLMSummarizer._assemble_sections('Lecture',parts,
        ['## First [00:00-00:30]\n\nExplanation.', '## Last (00:30–01:00)\n\nUse `[01:02]` in code.'],
        ['First','Last'],'Overview')
    result=apply_timeline(note,'none')
    assert '- First' in result and '- Last' in result
    assert '00:00' not in result and '00:30' not in result and '01:00' not in result
    assert '`[01:02]`' in result


@pytest.mark.parametrize('mode', ['heading','paragraph_start','paragraph_end','all'])
def test_parenthesized_timestamps_follow_the_selected_position(mode):
    result=apply_timeline('## Topic（00:10—00:20）\n\n（00:12） Explanation.',mode)
    heading=next(line for line in result.splitlines() if line.startswith('## Topic'))
    paragraph=next(line for line in result.splitlines() if 'Explanation.' in line)
    assert ('（00:10—00:20）' in heading)==(mode in {'heading','all'})
    assert paragraph.startswith('（00:12）')==(mode in {'paragraph_start','all'})
    assert paragraph.endswith('（00:12）')==(mode in {'paragraph_end','all'})
