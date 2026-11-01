import asyncio
from types import SimpleNamespace
import pytest
from backend import llm_summarizer, main
from backend.llm_summarizer import LLMSummarizer
from backend.prompt_rules import build_instructions, build_agent_prompt, planning_instructions, detail_instructions
from backend.note_context import source_context, neighboring_context, GLOBAL_CONTEXT_LIMIT, NEIGHBOR_CONTEXT_LIMIT
from backend.transcript import TranscriptSegment


CUSTOM = 'USER_CUSTOM_QUIZ_REQUEST'
IMAGE_RULE = '[[VIDEO_SHOT_0]]'


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
@pytest.mark.parametrize('detail', ['brief', 'standard', 'detailed'])
def test_three_levels_share_identical_api_and_agent_base_rules(language, detail):
    settings = dict(note_language=language, text_detail=detail, summary_style='tutorial',
                    custom_prompt=CUSTOM, note_format='docx', obsidian_tags=False)
    model = LLMSummarizer('deepseek', 'test')
    model._note_language, model._text_detail = language, detail
    model._selected_style, model._custom_prompt, model._note_format = 'tutorial', CUSTOM, 'docx'
    system = model._build_request('source', 1000, 'off')['messages'][0]['content']
    assert system == build_instructions(settings)
    assert build_agent_prompt(settings).startswith(system)
    assert detail_instructions(detail, language) in system
    assert CUSTOM in system
    assert main.SummarizeRequest(text_detail=detail, llm_config={}).text_detail == detail


@pytest.mark.parametrize('phase', ['material', 'overview', 'commentary', 'planning', 'vision', 'transcript_review'])
@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_auxiliary_requests_ignore_user_structure_style_detail_and_image_markers(phase, language):
    model = LLMSummarizer('deepseek', 'test')
    model._note_language = language
    model._selected_style = 'learning'
    model._custom_prompt = CUSTOM
    model._text_detail = 'detailed'
    model._note_instructions = IMAGE_RULE
    request = model._build_request('SOURCE_UNCHANGED', 1000, 'off', prompt_phase=phase)
    system = request['messages'][0]['content']
    for value in (CUSTOM, IMAGE_RULE, '复习问题', 'review questions', 'Detailed text:', '文字详略：'):
        assert value not in system
    assert request['messages'][1]['content'] == 'SOURCE_UNCHANGED'


def test_agent_screenshot_planning_ignores_full_note_requirements():
    settings = dict(custom_prompt=CUSTOM, summary_style='learning', note_format='docx', screenshot_density='many')
    assert CUSTOM not in planning_instructions(settings)
    assert '复习问题' not in planning_instructions(settings)
    assert '较多' in planning_instructions(settings)


@pytest.mark.asyncio
async def test_provider_parameter_retry_preserves_auxiliary_phase():
    model = LLMSummarizer('deepseek', 'test')
    model._custom_prompt = CUSTOM
    attempts = []
    class Rejected(Exception):
        status_code = 400
        body = {'error': {'message': 'unsupported parameter', 'param': 'reasoning_effort'}}
    class Stream:
        def __aiter__(self): return self
        async def __anext__(self):
            if getattr(self, 'sent', False): raise StopAsyncIteration
            self.sent = True
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"shots":[]}', reasoning_content=None), finish_reason='stop')])
        async def close(self): pass
    async def create(**request):
        attempts.append(request)
        if len(attempts) == 1: raise Rejected('reasoning_effort is not supported')
        return Stream()
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert await model._complete('Return only JSON', 1000, effort='high', prompt_phase='planning') == '{"shots":[]}'
    assert len(attempts) == 2
    assert all(CUSTOM not in request['messages'][0]['content'] for request in attempts)


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_bounded_context_preserves_source_terms_and_video_tail(language):
    segments = [TranscriptSegment(i * 60, (i + 1) * 60,
                ('CUDA uses HEVC. ' + ' '.join(f'TERM{j}' for j in range(20)) + ' “显卡加速” ' + 'detail ' * 60)) for i in range(30)]
    context = source_context('Very long title ' * 100, segments, language)
    assert len(context) <= GLOBAL_CONTEXT_LIMIT
    assert 'CUDA' in context and 'HEVC' in context
    assert '[29:00]' in context
    assert context == source_context('Very long title ' * 100, segments, language)
    sections = [segments[:10], segments[10:20], segments[20:]]
    neighboring = neighboring_context(sections, 1, language)
    assert len(neighboring) <= NEIGHBOR_CONTEXT_LIMIT
    assert '[00:00]' in neighboring and '[20:00]' in neighboring
    assert not neighboring_context([segments], 0, language)


@pytest.mark.asyncio
async def test_long_sections_share_context_without_extra_model_calls(monkeypatch):
    monkeypatch.setattr(llm_summarizer, 'LONG_TRANSCRIPT_CHARACTERS', 10)
    monkeypatch.setattr(llm_summarizer, 'NOTE_SECTION_CHARACTERS', 500)
    segments = [TranscriptSegment(i * 60, (i + 1) * 60, 'CUDA and HEVC. ' * 40) for i in range(3)]
    model = LLMSummarizer('deepseek', 'test')
    received = []
    async def section(title, index, total, part, tail, *args):
        received.append((index, args[-1]))
        return f'## Section {index}\n[00:00-03:00] Text'
    async def overview(*args): return ''
    model._write_one_section, model._write_overview = section, overview
    await model.generate_summary('GPU lesson', segments, text_detail='standard', style='faithful')
    assert len(received) >= 2
    assert all('CUDA' in context and '全片衔接背景' in context for _, context in received)
    assert any('前一时间区间' in context for index, context in received if index > 1)


@pytest.mark.parametrize('language', ['zh-CN','en'])
def test_agent_none_explicitly_disables_final_timestamps_without_changing_api_drafts(language):
    settings={'timeline_mode':'none','note_language':language}
    phrase='Timestamp display is disabled by the user' if language=='en' else '用户选择不添加时间线'
    prompt=build_agent_prompt(settings)
    assert phrase in prompt
    assert phrase not in build_instructions(settings)
    assert phrase not in build_agent_prompt({**settings,'timeline_mode':'heading'})
