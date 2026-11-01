import pytest
from backend.llm_summarizer import LLMSummarizer
from backend.main import SummarizeRequest
from backend.note_options import (
    resolve_commentary_position, place_commentary,
    reposition_agent_commentary, commentary_prompt,
)
from backend.transcript import TranscriptSegment


@pytest.mark.parametrize('position', ['none', 'start', 'end'])
@pytest.mark.asyncio
async def test_commentary_selection_controls_generation_and_order(monkeypatch, position):
    model = LLMSummarizer('deepseek', 'test-only-key')
    calls = []
    async def notes(*args, **kwargs):
        return '# Lecture\n\n## Concept\n\nSource content.'
    async def analysis(*args, **kwargs):
        calls.append(args[0])
        return '## AI Commentary\n\nModel analysis.'
    monkeypatch.setattr(model, '_write_condensed_notes', notes)
    monkeypatch.setattr(model, '_complete', analysis)
    # Explicit none must override both legacy switches and detailed style.
    result = await model.generate_summary('Lecture', [TranscriptSegment(0, 10, 'Material')],
        style='detailed', ai_commentary=True, ai_commentary_position=position)
    assert result.startswith('# Lecture\n')
    assert len(calls) == (0 if position == 'none' else 1)
    if position == 'none':
        assert 'Model analysis' not in result
    else:
        assert ('Model analysis' in result) and ('Source content.' in result)
        assert (result.index('AI Commentary') < result.index('## Concept')) == (position == 'start')


def test_legacy_commentary_and_explicit_disable():
    assert resolve_commentary_position(None, True) == 'end'
    assert resolve_commentary_position(None, False, 'detailed') == 'end'
    assert resolve_commentary_position(None, False, 'faithful') == 'none'
    assert resolve_commentary_position('none', True, 'detailed') == 'none'
    with pytest.raises(ValueError):
        resolve_commentary_position('middle')
    with pytest.raises(ValueError):
        SummarizeRequest(ai_commentary_position='middle')


@pytest.mark.parametrize('position', ['start', 'end'])
def test_agent_placement_preserves_body_and_nested_headings(position):
    note = '# Note\n\n## AI Commentary\n\nAnalysis.\n\n### Limits\n\nConditions.\n\n## Source\n\nDetails.'
    result = reposition_agent_commentary(note, position)
    assert result.startswith('# Note\n')
    assert result.count('AI Commentary') == 1
    for body in ['Analysis.', '### Limits', 'Conditions.', 'Details.']:
        assert body in result
    assert (result.index('AI Commentary') < result.index('## Source')) == (position == 'start')


def test_agent_placement_ignores_code_headings():
    code = '# Note\n\n```md\n## AI Commentary\nexample\n```\n\n## Topic\n\nContent.'
    assert reposition_agent_commentary(code, 'start') == code
    assert place_commentary('# Note\n\nBody', '# AI Commentary\nAnalysis', 'start').startswith('# Note\n\n## AI Commentary')
    assert '标题之后、正文之前' in commentary_prompt('start')
    assert '点评后不再追加正文' in commentary_prompt('end')
    assert '不添加 AI 点评' in commentary_prompt('none')


def test_agent_placement_ignores_indented_code():
    code = "# Note\n\n    ## AI Commentary\n    example\n\n## Topic\n\nContent."
    assert reposition_agent_commentary(code, "start") == code
