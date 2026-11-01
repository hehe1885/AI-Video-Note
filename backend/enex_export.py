"""Evernote ENEX export with self-contained ENML and referenced resources."""
import base64
from datetime import datetime, timezone
import hashlib
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lxml import etree
from markdown_it import MarkdownIt
from PIL import Image

from .note_export import referenced_images


def build_enex_document(content, task_dir, title='视频笔记'):
    from .math_render import prepare_math, formula_image
    content = prepare_math(content)
    sources = {source.name: source for source in referenced_images(content, Path(task_dir)).values()}
    resources = {}
    parser = MarkdownIt('commonmark', {'html': False}).enable('table')

    def image_rule(renderer, tokens, index, options, env):
        token = tokens[index]
        src = token.attrGet('src') or ''
        source = sources.get(Path(unquote(urlsplit(src).path)).name) if src.startswith(('frames/','./frames/','images/','./images/')) else None
        formula = formula_image(src)
        if source is None and formula is None:
            return escape(token.content)
        data = formula if formula is not None else source.read_bytes()
        with Image.open(BytesIO(data)) as image:
            mime = Image.MIME.get(image.format)
            width, height = image.size
        if not mime or not mime.startswith('image/'):
            return escape(token.content)
        digest = hashlib.md5(data).hexdigest()  # ENML resource identifier, not a security hash.
        resources[digest] = (source.name if source else 'formula-' + digest + '.png', data, mime, width, height)
        size = f' width="{max(1, round(width/2))}" height="{max(1, round(height/2))}"' if formula else ''
        return f'<en-media type="{escape(mime, quote=True)}" hash="{digest}"{size}/>'

    # ENML uses XHTML empty tags and does not allow code-fence language classes.
    parser.options['xhtmlOut'] = True
    parser.add_render_rule('image', image_rule)
    html = parser.render(content)
    body = etree.fromstring(('<en-note>' + html + '</en-note>').encode(),
                            etree.XMLParser(resolve_entities=False, no_network=True))
    for element in body.iter():
        # Markdown alignment attributes are not part of ENML's HTML subset.
        if 'class' in element.attrib:
            del element.attrib['class']
        if 'align' in element.attrib:
            element.set('style', f'text-align: {element.attrib.pop("align")}')
    enml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">\n'
    enml += etree.tostring(body, encoding='unicode')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root = etree.Element('en-export', {'export-date': stamp, 'application': 'AI-Video-Note', 'version': '1.0'})
    note = etree.SubElement(root, 'note')
    etree.SubElement(note, 'title').text = str(title)
    etree.SubElement(note, 'content').text = etree.CDATA(enml)
    etree.SubElement(note, 'created').text = stamp
    etree.SubElement(note, 'updated').text = stamp
    for filename, data, mime, width, height in resources.values():
        resource = etree.SubElement(note, 'resource')
        etree.SubElement(resource, 'data', encoding='base64').text = base64.b64encode(data).decode('ascii')
        etree.SubElement(resource, 'mime').text = mime
        etree.SubElement(resource, 'width').text = str(width)
        etree.SubElement(resource, 'height').text = str(height)
        etree.SubElement(etree.SubElement(resource, 'resource-attributes'), 'file-name').text = filename
    return etree.tostring(root, encoding='UTF-8', xml_declaration=True,
                          doctype='<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">')
