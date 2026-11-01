"""Bounded source-only context, deterministic across concurrent writes and resumes."""
from collections import Counter
import re
from .transcript import format_timestamp

GLOBAL_CONTEXT_LIMIT = 1600
NEIGHBOR_CONTEXT_LIMIT = 520


def _value(segment, key):
    return segment.get(key, '' if key == 'text' else 0) if isinstance(segment, dict) else getattr(segment, key)


def _excerpt(segment, limit=100):
    return f"[{format_timestamp(float(_value(segment, 'start')))}] " + re.sub(r'\s+', ' ', str(_value(segment, 'text'))).strip()[:limit]


def source_context(title, segments, language='zh-CN'):
    if not segments:
        return ''
    english = language == 'en'
    text = '\n'.join(str(_value(segment, 'text')) for segment in segments)
    # Keep literal recurring names, without inventing definitions or normalizing case.
    tokens = re.findall(r'\b[A-Za-z][A-Za-z0-9_+./-]{1,30}\b', text)
    tokens += re.findall(r'[“「《]([^”」》\n]{2,24})[”」》]', text)
    counts = Counter(tokens)
    # A literal term explicitly defined in the source can be reused even when
    # later mentions do not repeat the definition or quotation marks.
    defined = re.findall(r'(?:^|[，。；\n])\s*([\u4e00-\u9fff]{2,14})(?=是指|定义为|就是)', text)
    for term in defined:
        counts[term] = text.count(term)
    names = [token for token, count in counts.most_common()
             if count > 1 and (not token.isascii() or any(c.isupper() for c in token) or '_' in token)][:12]
    count = min(8, len(segments))
    positions = sorted({round(i * (len(segments) - 1) / max(1, count - 1)) for i in range(count)})
    guide = (
        'Whole-video context, for interpreting topics and references only. These are sampled source clues, not a complete outline or facts to copy into every section. Keep literal names consistent; do not repeat earlier explanations or add later material outside the current section.\n' if english else
        '全片衔接背景，仅用于理解主题与指代。以下是原字幕的抽样线索，不是完整大纲，不要直接复制到各段正文；保持术语名称一致，避免重复前文解释，也不要把后文写进当前段。\n')
    result = guide + ('Title: ' if english else '主题标题：') + str(title)[:120]
    if names:
        separator = ', ' if english else '、'
        selected = []
        for name in names:
            if len(separator.join(selected + [name])) > 180:
                break
            selected.append(name)
        result += ('\nRecurring source terms (literal spelling): ' if english else '\n原文重复出现的术语（保持原写法）：') + separator.join(selected)
    result += ('\nSource topic clues:\n' if english else '\n全片主题线索（原文）：\n') + '\n'.join(_excerpt(segments[i], 90) for i in positions)
    return result[:GLOBAL_CONTEXT_LIMIT]


def neighboring_context(sections, index, language='zh-CN'):
    english = language == 'en'
    lines = []
    if index > 0 and sections[index - 1]:
        previous = sections[index - 1]
        lines.append(('Previous interval topic clues, for reference only; do not repeat: ' if english else
                      '前一时间区间的主题线索，仅供衔接，不重复写入：') +
                     ' / '.join(_excerpt(previous[i], 90) for i in sorted({0, len(previous) // 2})))
    if index + 1 < len(sections) and sections[index + 1]:
        lines.append(('Next interval opening, for topic continuity only; do not write it early: ' if english else
                      '后一时间区间开头，仅供理解话题衔接，不提前写入：') + _excerpt(sections[index + 1][0], 90))
    return '\n'.join(lines)[:NEIGHBOR_CONTEXT_LIMIT]
