"""Conservative Markdown fixes shared by note generation and export."""
from __future__ import annotations
import re
from urllib.parse import quote
from markdown_it import MarkdownIt

_BOLD = re.compile(r'(?<![\\*])\*\*(?![\s*])((?:(?!\*\*)[^\n])+?)(?<![\s*])\*\*(?!\*)')
_INLINE_CODE = re.compile(r'(`+)([\s\S]*?)(?<!`)\1(?!`)')
_MATH = re.compile(r'(?<!\\)\$\$[\s\S]*?\$\$|(?<![\\$])\$(?![\s$])(?:\\.|[^$\n\\])+?\$(?!\$)|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)')
_DESTINATION = re.compile(r'(?<=\]\()(?:\\.|[^()\n]|\([^()\n]*\))*?(?=\))')


def transform_prose(content, transform, protect_destinations=False):
    """Preserve code, HTML blocks, YAML properties and math byte for byte."""
    offsets = [0]
    for line in content.splitlines(keepends=True): offsets.append(offsets[-1] + len(line))
    spans = []
    for token in MarkdownIt('commonmark').parse(content):
        if token.type in {'fence', 'code_block', 'html_block'} and token.map:
            spans.append((offsets[token.map[0]], offsets[token.map[1]]))
    frontmatter = re.match(r'\A---[ \t]*\r?\n.*?^---[ \t]*(?:\r?\n|$)', content, re.M | re.S)
    if frontmatter: spans.append(frontmatter.span())
    destinations = [match.span() for match in _DESTINATION.finditer(content)]
    for pattern in (_INLINE_CODE, _MATH):
        spans.extend(match.span() for match in pattern.finditer(content)
                     if not any(left <= match.start() and match.end() <= right for left, right in destinations))
    if protect_destinations: spans.extend(destinations)
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]: merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else: merged.append((start, end))
    result, cursor = [], 0
    for start, end in merged:
        result.extend((transform(content[cursor:start]), content[start:end]))
        cursor = end
    result.append(transform(content[cursor:]))
    return ''.join(result)


def normalize_bold_spacing(content):
    def fix(prose):
        return _BOLD.sub(lambda match: match.group() + (
            ' ' if match.end() < len(prose) and prose[match.end()].isalnum() else ''), prose)
    return transform_prose(content, fix, protect_destinations=True)


def rewrite_frame_links(content, destinations):
    """Rewrite known image destinations, including ./, encoded names and titles.

    destinations maps an actual screenshot basename to its exported relative URL.
    Exact known filenames also repair legacy unescaped spaces in a destination.
    """
    variants = {}
    for name, destination in destinations.items():
        for folder in ('frames', 'images'):
            for prefix in ('', './'):
                raw = prefix + folder + '/' + name
                for source in (raw, quote(raw, safe='/'), raw.replace('(', r'\(').replace(')', r'\)')):
                    variants[source] = destination
    if not variants: return content
    sources = '|'.join(re.escape(source) for source in sorted(variants, key=len, reverse=True))
    alt = r'(?:\\.|[^\]\\]|\[(?:\\.|[^\]\\])*\])*'
    pattern = re.compile(r'(?P<opening>!\[' + alt + r'\]\([ \t]*<?|^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*<?)'
                         r'(?P<source>' + sources + r')(?=[>\s)]|$)', re.M)
    return transform_prose(content, lambda prose: pattern.sub(
        lambda match: match['opening'] + variants[match['source']], prose))
