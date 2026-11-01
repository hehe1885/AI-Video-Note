import pytest
from backend.llm_summarizer import LLMSummarizer
from backend.main import SummarizeRequest, summary_style_label
from backend.note_styles import NOTE_STYLES
from backend.transcript import TranscriptSegment


@pytest.mark.asyncio
@pytest.mark.parametrize('style', NOTE_STYLES)
@pytest.mark.parametrize('long_video', [False, True])
@pytest.mark.parametrize('obsidian', [False, True])
async def test_style_instructions_reach_generation_and_do_not_leak(monkeypatch, style, long_video, obsidian):
    summarizer = LLMSummarizer('deepseek', 'fake-test-key')
    calls = []
    async def write(*args, **kwargs):
        request = summarizer._build_request('材料', 1000, 'off')
        calls.append((args[3], request['messages'][0]['content']))
        return '# 笔记\n\n[00:00-00:10] 内容'
    monkeypatch.setattr(summarizer, '_is_long_transcript', lambda _: long_video)
    monkeypatch.setattr(summarizer, '_write_condensed_notes', write)
    monkeypatch.setattr(summarizer, '_write_long_transcript_notes', write)
    result = await summarizer.generate_summary('标题', [TranscriptSegment(0, 10, '内容')],
        style=style, obsidian_mode=obsidian, custom_prompt='特别关注案例')
    assert result.startswith('# 笔记')
    assert calls[0][0] == NOTE_STYLES[style]['base']
    assert NOTE_STYLES[style]['guide'] in calls[0][1]
    assert '特别关注案例' in calls[0][1]
    assert ('请写一份Obsidian 笔记' in calls[0][1]) == obsidian
    await summarizer.generate_summary('标题', [TranscriptSegment(0, 10, '内容')], style='faithful')
    assert NOTE_STYLES[style]['guide'] not in calls[1][1]
    assert '特别关注案例' not in calls[1][1]
    assert '请写一份Obsidian 笔记' not in calls[1][1]


@pytest.mark.parametrize('style', NOTE_STYLES)
def test_new_style_schema_and_footer_label(style):
    request = SummarizeRequest(summary_style=style, llm_config={})
    assert request.summary_style == style
    assert summary_style_label(style) == NOTE_STYLES[style]['label']


@pytest.mark.asyncio
@pytest.mark.parametrize('format_name,label', [('markdown','Markdown 笔记'),('obsidian','Obsidian 笔记'),('docx','Word 笔记'),('pdf','PDF 笔记'),('enex','印象笔记 / Evernote 笔记'),('html','HTML 网页笔记'),('json','JSON 格式笔记'),('txt','纯文本笔记'),('png','图片形式的笔记')])
async def test_selected_final_format_reaches_prompt(monkeypatch, format_name, label):
    summarizer = LLMSummarizer('deepseek', 'fake-test-key')
    prompts = []
    async def write(*args, **kwargs):
        prompts.append(summarizer._build_request('材料', 1000, 'off')['messages'][0]['content'])
        return '# 笔记\n\n内容'
    monkeypatch.setattr(summarizer, '_write_condensed_notes', write)
    await summarizer.generate_summary('标题', [TranscriptSegment(0, 10, '内容')], note_format=format_name)
    assert prompts[0].endswith(f'请写一份{label}，按用户选择的风格、详略与自定义要求组织内容。')
    assert '内部传输与导出约定' not in prompts[0]
    assert '结构化中间正文' not in prompts[0]
    assert '不使用 Obsidian 专属语法' not in prompts[0]
    if format_name == 'docx':
        assert '字号按标题层级区分' in prompts[0]
        assert '图文详略遵循用户设置' in prompts[0]
    if format_name == 'obsidian':
        assert '除非用户明确要求，不主动添加双向链接、标签或提示块' in prompts[0]
    await summarizer.generate_summary('标题', [TranscriptSegment(0, 10, '内容')])
    assert prompts[1].endswith('请写一份Markdown 笔记，按用户选择的风格、详略与自定义要求组织内容。')


@pytest.mark.parametrize('tags', [False, True])
@pytest.mark.parametrize('callouts', [False, True])
def test_obsidian_feature_switches(tags, callouts):
    from backend.note_options import obsidian_feature_prompt
    from backend.main import add_obsidian_properties
    prompt = obsidian_feature_prompt(True, tags, callouts)
    assert ('用户已开启标签' in prompt) == tags
    assert ('用户已开启提示块' in prompt) == callouts
    assert obsidian_feature_prompt(False, tags, callouts) == ''
    assert 'tags:' not in add_obsidian_properties('# 笔记', '标题', None)
    request = SummarizeRequest(llm_config={})
    assert not request.obsidian_tags and not request.obsidian_callouts
