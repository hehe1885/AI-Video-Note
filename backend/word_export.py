"""Convert generated Markdown into an editable Word document."""
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.enum.text import WD_ALIGN_PARAGRAPH
from markdown_it import MarkdownIt

from .note_layout import is_body_paragraph, is_caption_paragraph


def build_word_document(markdown: str, task_dir: Path) -> bytes:
    from .math_render import prepare_math, formula_image
    markdown = prepare_math(markdown)
    document = Document()
    # Word themes can override explicit East Asian fonts through *Theme attributes.
    from lxml import etree
    for fonts in document.styles.element.iter(qn("w:rFonts")):
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme", "csTheme"):
            fonts.attrib.pop(qn("w:" + attribute), None)
    defaults = document.styles.element.find(qn("w:docDefaults"))
    if defaults is not None:
        for fonts in defaults.iter(qn("w:rFonts")):
            for script,font in (("eastAsia","宋体"),("ascii","Times New Roman"),("hAnsi","Times New Roman"),("cs","Times New Roman")):
                fonts.set(qn("w:"+script),font)
    theme_part = next((relation.target_part for relation in document.part.rels.values() if relation.reltype.endswith("/theme")),None)
    if theme_part is not None:
        theme = etree.fromstring(theme_part.blob)
        ns = {"a":"http://schemas.openxmlformats.org/drawingml/2006/main"}
        for group,chinese in (("majorFont","黑体"),("minorFont","宋体")):
            for node in theme.xpath("//a:"+group+"/a:latin",namespaces=ns):
                node.set("typeface","Times New Roman")
            for node in theme.xpath("//a:"+group+"/a:ea",namespaces=ns):
                node.set("typeface",chinese)
            for node in theme.xpath("//a:"+group+"/a:font",namespaces=ns):
                if node.get("script") in {"Hans","Hant"}:
                    node.set("typeface",chinese)
                elif "Gothic" in (node.get("typeface") or ""):
                    node.set("typeface","微软雅黑")
        theme_part._blob = etree.tostring(theme,encoding="UTF-8",xml_declaration=True,standalone=True)

    def set_style(name, chinese, english, size, bold=False):
        style = document.styles[name]
        style.font.name = english
        style.font.size = Pt(size)
        style.font.bold = bold
        style.font.color.rgb = RGBColor(0, 0, 0)
        fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        for key, value in [('eastAsia', chinese), ('ascii', english), ('hAnsi', english), ('cs', english)]:
            fonts.set(qn('w:' + key), value)
    set_style('Normal', '宋体', 'Times New Roman', 12)
    set_style('Title', '黑体', 'Times New Roman', 20, True)
    set_style('Heading 1', '黑体', 'Times New Roman', 16, True)
    set_style('Heading 2', '黑体', 'Times New Roman', 14, True)
    for level in range(3, 10):
        set_style(f'Heading {level}', '黑体', 'Times New Roman', 12, True)
    for name in ['List Bullet', 'List Number']:
        set_style(name, '宋体', 'Times New Roman', 12)
    for name in ['Caption', 'Header', 'Footer']:
        set_style(name, '宋体', 'Times New Roman', 10.5)
    title_added = False
    heading = False
    quote_depth = 0
    tokens = MarkdownIt('commonmark').enable('table').parse(markdown)
    paragraph = None
    lists = []
    table = None
    row = None
    cell = None
    bold = italic = False
    link_target = None

    def after(target, style=None):
        element = OxmlElement('w:p')
        target._p.addnext(element)
        paragraph = Paragraph(element, target._parent)
        if style: paragraph.style = style
        return paragraph

    def inline(target, children, body_context=False):
        nonlocal bold, italic, link_target
        after_picture = False
        original_style = target.style
        def text_target():
            nonlocal target, after_picture
            if after_picture:
                target = after(target, original_style)
                target.paragraph_format.first_line_indent = Pt(24 if body_context else 0)
                after_picture = False
            return target
        for token in children or []:
            if token.type in ('strong_open', 'strong_close'):
                bold = token.type == 'strong_open'
            elif token.type in ('em_open', 'em_close'):
                italic = token.type == 'em_open'
            elif token.type == 'image':
                formula = formula_image(token.attrGet('src') or '')
                if formula:
                    from PIL import Image
                    with Image.open(BytesIO(formula)) as image:
                        width = min(5.8, image.width/192)
                    run = text_target().add_run()
                    shape = run.add_picture(BytesIO(formula), width=Inches(width))
                    shape._inline.docPr.set('descr', token.content)
                    continue
                # Only embed this task's screenshots; never fetch remote URLs or read arbitrary paths.
                source = unquote(urlsplit(token.attrGet('src') or '').path)
                name = source.replace('\\', '/').rsplit('/', 1)[-1]
                image = (task_dir / 'frames' / name).resolve()
                frames = (task_dir / 'frames').resolve()
                if image.parent == frames and image.is_file():
                    try:
                        # Keep each screenshot and its caption in dedicated paragraphs,
                        # including when a model placed an image inside running prose.
                        if target.text.strip() or target._p.xpath('.//w:drawing') or after_picture:
                            if target.text.strip() and body_context and not after_picture:
                                target.paragraph_format.first_line_indent = Pt(24)
                            target = after(target)
                        target.paragraph_format.first_line_indent = Pt(0)
                        target.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        shape = target.add_run().add_picture(str(image), width=Inches(5.8))
                        shape._inline.docPr.set('descr', token.content or '')
                        if token.content.strip():
                            target.paragraph_format.keep_with_next = True
                            target = after(target, 'Caption')
                            target.paragraph_format.first_line_indent = Pt(0)
                            target.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            target.add_run(token.content)
                        after_picture = True
                        continue
                    except Exception:
                        # Unsupported/corrupt images must not prevent exporting the text.
                        pass
                text_target().add_run(token.content or '[图片]')
            elif token.type in ('softbreak', 'hardbreak'):
                text_target().add_run().add_break()
            elif token.type in ('text', 'code_inline'):
                run = text_target().add_run(token.content)
                run.bold = True if bold else None
                run.italic = italic
                if token.type == 'code_inline':
                    run.font.name = 'Consolas'
            elif token.type == 'link_open':
                link_target = token.attrGet('href')
            elif token.type == 'link_close':
                if link_target:
                    text_target().add_run(f' ({link_target})')
                link_target = None

    for index, token in enumerate(tokens):
        kind = token.type
        if kind in ('bullet_list_open', 'ordered_list_open'):
            lists.append('List Number' if kind == 'ordered_list_open' else 'List Bullet')
        elif kind in ('bullet_list_close', 'ordered_list_close'):
            lists.pop()
        elif kind == 'blockquote_open':
            quote_depth += 1
        elif kind == 'blockquote_close':
            quote_depth -= 1
        elif kind == 'heading_open':
            heading = True
            level = int(token.tag[1:])
            if level == 1 and not title_added:
                paragraph = document.add_paragraph(style='Title')
                title_added = True
            else:
                paragraph = document.add_heading(level=max(1, min(level - 1, 9)))
        elif kind == 'paragraph_open':
            paragraph = cell.paragraphs[0] if cell is not None else document.add_paragraph(style=lists[-1] if lists else None)
            if lists:
                paragraph.paragraph_format.left_indent = Inches(0.25 * len(lists))
        elif kind == 'inline':
            if paragraph is None:
                paragraph = document.add_paragraph()
            if not heading and not lists and cell is None and not quote_depth:
                # Apply only to prose; Normal remains unindented for tables, images and code.
                paragraph.paragraph_format.first_line_indent = Pt(24 if is_body_paragraph(token) else 0)
            if not heading and not lists and cell is None and not quote_depth and is_caption_paragraph(token):
                paragraph.style = 'Caption'
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            inline(paragraph, token.children, not heading and not lists and cell is None and not quote_depth and not is_caption_paragraph(token))
        elif kind in ('paragraph_close', 'heading_close'):
            paragraph = None
            if kind == 'heading_close': heading = False
        elif kind in ('fence', 'code_block'):
            paragraph = document.add_paragraph()
            run = paragraph.add_run(token.content.rstrip('\n'))
            run.font.name = 'Consolas'
            run.font.size = Pt(10)
            paragraph = None
        elif kind == 'table_open':
            columns = 0
            for item in tokens[index + 1:]:
                if item.type == 'tr_close':
                    break
                if item.type == 'th_open':
                    columns += 1
            table = document.add_table(rows=0, cols=max(columns, 1))
            table.style = 'Table Grid'
        elif kind == 'tr_open' and table is not None:
            row = iter(table.add_row().cells)
        elif kind in ('th_open', 'td_open') and row is not None:
            cell = next(row)
            paragraph = cell.paragraphs[0]
            bold = kind == 'th_open'
        elif kind in ('th_close', 'td_close'):
            cell = paragraph = None
            bold = False
        elif kind == 'table_close':
            table = row = cell = None
        elif kind == 'hr':
            document.add_paragraph('────────────────────')
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = 'Times New Roman'
                        run.font.size = Pt(10.5)
                        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), '宋体')
    output = BytesIO()
    document.save(output)
    return output.getvalue()
