"""Presentation options applied after generation; transcript timing stays intact."""
import re

NOTE_WRITING_GUIDE = "正文写成可独立阅读的笔记，直接陈述概念、要点、步骤、依据和结论。不要反复使用‘讲者提到’‘作者提到’‘视频中指出’‘他认为’等转述开头，不要写成视频内容介绍。仅在必要引用、人物观点对比或需明确归属的个人观点、预测与争议判断处标明来源；保留不确定性与条件，不把观点或预测改写为已确认事实，不把来源经历写成读者经历。"
# Accept the annotation styles returned by different models, including malformed
# minute/second values: display=none must hide an annotation rather than validate it.
_TIME_VALUE = r'\d{1,3}[:：]\d{2}(?:[:：]\d{2})?(?:[.,]\d+)?'
_TIME_RANGE = _TIME_VALUE + r'(?:\s*[-–—~～至]\s*' + _TIME_VALUE + r')?'
TIME = re.compile('|'.join(re.escape(left) + r'\s*' + _TIME_RANGE + r'\s*' + re.escape(right)
                           for left, right in (('[', ']'), ('(', ')'), ('（', '）'), ('【', '】'), ('［', '］'))))
_LINK_DESTINATION = re.compile(r'(?<=\]\()(?:\\.|[^()\n]|\([^()\n]*\))*(?=\))')
_NAVIGATION_CODE = re.compile(r'(?m)^([ \t]*(?:[-*+]|\d+[.)])[ \t]+)`' + _TIME_RANGE + r'`[ \t]+')
_TIMED_TOC = re.compile(r'(?ms)(^#{1,6}[ \t]+(?:本片目录|视频目录|内容目录|目录|Table of [Cc]ontents|Contents)[ \t]*\n)(.*?)(?=^#{1,6}[ \t]|\Z)')


def strip_note_properties(markdown):
    """Omit leading YAML properties without removing ordinary thematic breaks or code."""
    text = markdown.lstrip()
    match = re.match(r'\A---[ \t]*\r?\n(?P<properties>.*?)^---[ \t]*(?:\r?\n|$)', text, re.M | re.S)
    if match and re.search(r'(?m)^[^\s:#][^:\n]*:(?:[ \t]|$)', match['properties']):
        return text[match.end():].lstrip('\r\n')
    return markdown


def note_option_prompt(detail, note_language="zh-CN"):
    from .prompt_rules import detail_instructions
    return detail_instructions(detail, note_language)

def apply_timeline(markdown, mode):
    if mode not in {'none', 'heading', 'paragraph_start', 'paragraph_end', 'all'}:
        raise ValueError('Invalid timeline mode')
    # Fenced code and math are kept verbatim.
    pieces = re.split(r'(^```[^\n]*\n.*?^```[^\n]*$|^~~~[^\n]*\n.*?^~~~[^\n]*$|^\$\$\s*\n.*?^\$\$\s*$)', markdown, flags=re.M | re.S)
    inline_code = []
    def protect(match):
        inline_code.append(match.group())
        return f'CODEPROTECTEDTOKEN{len(inline_code)-1}END'
    last_time = ''
    for part in range(0, len(pieces), 2):
        prose = pieces[part]
        if mode == 'none':
            # Long-note TOCs use a bare time range in inline code as a navigation
            # label. Remove that label while retaining real inline code examples.
            prose = _TIMED_TOC.sub(lambda match: match[1] + _NAVIGATION_CODE.sub(r'\1', match[2]), prose)
        protected = re.sub(r'(`+)([^`\n]+)\1', protect, prose)
        # Timestamps inside image paths and link destinations are data, not labels.
        protected = _LINK_DESTINATION.sub(protect, protected)
        blocks = re.split(r'\n\s*\n', protected)
        heading_times = {}
        for i, block in enumerate(blocks):
            if re.match(r'^#{1,6} ', block):
                own = TIME.search(block)
                following = next((TIME.search(b) for b in blocks[i+1:] if TIME.search(b)), None)
                heading_times[i] = (own or following).group() if own or following else ''
        for i, block in enumerate(blocks):
            if not block.strip(): continue
            # Standalone image references, tables and metadata are not prose paragraphs.
            if block.lstrip().startswith(('![', '|', '>')):
                if mode == 'none': blocks[i] = TIME.sub('', block)
                continue
            found = TIME.findall(block)
            cleaned = TIME.sub('', block).strip()
            is_heading = bool(re.match(r'^#{1,6} ', cleaned))
            stamp = (heading_times.get(i) if is_heading else (found[0] if found else last_time)) or ''
            if found: last_time = found[0]
            if is_heading:
                if mode in {'heading', 'all'} and stamp: cleaned += ' ' + stamp
                if stamp: last_time = stamp
            elif stamp and not cleaned.startswith(('# ', '---')):
                if mode in {'paragraph_start', 'all'}: cleaned = stamp + ' ' + cleaned
                if mode in {'paragraph_end', 'all'}: cleaned += ' ' + stamp
            blocks[i] = cleaned
        pieces[part] = '\n\n'.join(blocks)
    result = ''.join(pieces)
    for index, code in enumerate(inline_code):
        result = result.replace(f'CODEPROTECTEDTOKEN{index}END', code)
    return result


def note_format_prompt(format_name, note_language="zh-CN"):
    if note_language == "en":
        from . import prompt_language
        return prompt_language.format_prompt(format_name)
    requirements = {
        'markdown': ('Markdown 笔记', '使用清晰的标题、段落、列表和必要的表格。'),
        'obsidian': ('Obsidian 笔记', '适合在 Obsidian 中阅读和整理；除非用户明确要求，不主动添加双向链接、标签或提示块。属性区由程序按用户开关统一处理，不要自行输出 YAML 笔记属性。'),
        'docx': ('Word 笔记', '层级分明，标题与正文结构清晰，字号按标题层级区分（由导出程序排版）。文字解释充分，配图与对应内容紧密结合；图文详略遵循用户设置，未启用配图时以完整文字说明。'),
        'pdf': ('PDF 笔记', '使用清晰的标题、段落、列表和必要表格，适合分页阅读与打印；由导出程序完成分页和图片嵌入，不要自行生成 PDF 数据。'),
        'enex': ('印象笔记 / Evernote 笔记', '使用清晰的标题、段落、列表和必要表格，图片放在相关正文附近；由导出程序封装 ENEX 文件，不要自行生成 XML。'),
        'html': ('HTML 网页笔记', '适合网页阅读，按语义组织标题、正文、列表和必要的表格。'),
        'txt': ('纯文本笔记', '以文字段落和简洁条目为主，避免依赖表格、公式排版或专属笔记语法表达核心信息。'),
        'json': ('JSON 格式笔记', '生成清晰完整的笔记正文，程序会封装为 JSON 数据，不要自行生成 JSON 外壳。'),
        'png': ('图片形式的笔记', '适合图片阅读，使用易读的小标题和适度分段，避免过宽表格；不要省略重要内容来强行压成一张图。'),
    }
    if format_name not in requirements:
        raise ValueError('Unsupported note format')
    label, guide = requirements[format_name]
    return ('\n所选笔记格式要求（仅适用于笔记正文，辅助任务遵循各自要求）：'
            + guide + '\n只输出笔记正文，沿用正文模板中的标题、列表、表格和图片标记，不包裹整篇代码围栏。'
            + '\n请写一份' + label + '，按用户选择的风格、详略与自定义要求组织内容。')


def obsidian_feature_prompt(enabled, tags=False, callouts=False, note_language="zh-CN"):
    if note_language == "en":
        from . import prompt_language
        return prompt_language.obsidian_prompt(enabled, tags, callouts)
    if not enabled:
        return ''
    text = []
    if tags:
        text.append('用户已开启标签：在正文末尾添加少量与材料主题直接相关的 Obsidian 标签，使用 #标签 格式，标签不含空格，去重并避免泛化或无关标签。')
    if callouts:
        text.append('用户已开启提示块：重点总结、操作提示或注意事项适量使用 Obsidian 提示块（如 > [!summary]、> [!tip]、> [!warning]），内容必须有材料依据，不强行增加警告或用提示块替代完整正文。')
    return '\n' + '\n'.join(text) if text else ''


def apply_source_link(text, url, position):
    if not url or position == 'none':
        return text
    line = f'视频链接：{url}'
    if position == 'start':
        return line + '\n\n' + text.lstrip()
    return text.rstrip() + '\n\n' + line + '\n'


def note_language_prompt(language):
    if language == 'en':
        return '\nNote language: English. Write all headings, explanations, tables, captions and AI commentary in English, regardless of the source language. Preserve original code, URLs and proper nouns where appropriate. This language requirement overrides the language of the source and other default instructions.'
    return '\n笔记语言：简体中文。无论原视频使用什么语言，标题、正文、表格、图注和 AI 点评都使用简体中文；代码、网址和必要的专有名词保留原样。此语言要求优先于材料语言及默认模板语言。'


def resolve_commentary_position(position=None, enabled=False, style="faithful"):
    """Explicit placement wins; old checkbox/detailed clients keep end placement."""
    if position is not None:
        if position not in {"none", "start", "end"}:
            raise ValueError("Invalid AI commentary position")
        return position
    return "end" if enabled or style == "detailed" else "none"


def place_commentary(note, commentary, position):
    """Keep the document title first when placing commentary before the body."""
    if not commentary.strip() or position == "none":
        return note
    commentary = re.sub(r"^# (?=\S)", "## ", commentary.strip(), count=1)
    if position == "end":
        return note.rstrip() + "\n\n" + commentary
    if position != "start":
        raise ValueError("Invalid AI commentary position")
    title, separator, body = note.lstrip().partition("\n")
    if re.match(r"^#\s+", title):
        return title + "\n\n" + commentary + ("\n\n" + body.lstrip() if body.strip() else "")
    return commentary + "\n\n" + note.lstrip()


def commentary_prompt(position, note_language="zh-CN"):
    if note_language == "en":
        from . import prompt_language
        return prompt_language.commentary_prompt(position)
    if position == "none":
        return "\n不添加 AI 点评或模型分析小节，只整理材料中的内容。"
    placement = "标题之后、正文之前" if position == "start" else "正文之后"
    ending = "点评后继续笔记正文。" if position == "start" else "点评后不再追加正文。"
    return ("\n" + placement + "另设 ## AI 点评（英文笔记用 ## AI Commentary），分析观点、论据、适用条件与局限，不重复摘要；"
            "明确区分材料与模型分析，外部补充注明未联网核验，不编造事实。" + ending)


def reposition_agent_commentary(text, position):
    """Move a marked agent section without interpreting headings inside code fences."""
    if position == "none":
        return text
    lines = text.splitlines(keepends=True)
    begin = end = level = None
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip("\r\n"))
        if not heading:
            continue
        if begin is not None and len(heading[1]) <= level:
            end = index
            break
        if begin is None and re.fullmatch(r"AI\s*(?:点评|Commentary)", heading[2], re.I):
            begin, level = index, len(heading[1])
    if begin is None:
        return text
    end = len(lines) if end is None else end
    section = "".join(lines[begin:end])
    body = "".join(lines[:begin] + lines[end:]).strip()
    return place_commentary(body, section, position)
