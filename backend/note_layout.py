"""Shared paragraph roles for PDF, Word and PNG note layout."""
import re

_SOURCE_LABEL = re.compile(r'^(?:视频链接|来源链接|原视频链接|视频来源|来源|Video(?:\s+(?:link|source|URL))?|Source(?:\s+(?:link|URL))?)\s*[:：]', re.I)
_CAPTION_LABEL = re.compile(r'^(?:图(?:片)?(?:注|说明|备注)|图\s*\d*|Fig(?:ure)?\.?\s*\d+|Caption|Image\s+(?:caption|note))\s*[:：.、]', re.I)
_METADATA_LABEL = re.compile(r'^(?:UP主|作者|时长|Author|Duration)\s*[:：]', re.I)


def is_body_paragraph(token):
    """Whether an inline Markdown token represents prose needing a first-line indent.

    Containers such as headings/lists/tables/quotes are handled by each renderer.
    Image alt text is rendered separately as a caption. Standalone image/math
    paragraphs keep their full width; inline math can still belong to body prose.
    """
    children = token.children or []
    if any(child.type == 'image' and child.attrGet('title') != 'note-formula-inline' for child in children):
        return False
    text = ''.join(child.content for child in children if child.type in {'text', 'code_inline'}).strip()
    if not text or _CAPTION_LABEL.match(text) or _METADATA_LABEL.match(text):
        return False
    links = [child for child in children if child.type == 'link_open']
    if _SOURCE_LABEL.match(text) and (links or re.search(r'https?://', text, re.I)):
        return False
    if re.fullmatch(r'https?://\S+', text, re.I):
        return False
    # A link on its own is navigation, even when the label hides the URL.
    depth = 0
    for child in children:
        if child.type == 'link_open':
            depth += 1
        elif child.type == 'link_close':
            depth -= 1
        elif child.type in {'text', 'code_inline'} and child.content.strip() and not depth:
            return True
    return not links


def is_caption_paragraph(token):
    text = ''.join(child.content for child in token.children or [] if child.type in {'text', 'code_inline'}).strip()
    return bool(_CAPTION_LABEL.match(text))
