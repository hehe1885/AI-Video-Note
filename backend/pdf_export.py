"""Paginated, searchable PDF notes with Chinese fonts and embedded local images."""
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from threading import RLock
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image as PILImage, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, LongTable, TableStyle, HRFlowable

from .note_export import referenced_images
from .note_layout import is_body_paragraph, is_caption_paragraph

_font_lock = RLock()


@lru_cache(maxsize=1)
def document_fonts():
    with _font_lock:
        candidates = [Path('C:/Windows/Fonts/simsun.ttc'), Path('C:/Windows/Fonts/msyh.ttc'),
                      Path('/usr/share/fonts/truetype/arphic/uming.ttc')]
        regular = next((path for path in candidates if path.is_file()), None)
        if regular:
            pdfmetrics.registerFont(TTFont('NoteText', str(regular), subfontIndex=0))
            bold = Path('C:/Windows/Fonts/simhei.ttf')
            pdfmetrics.registerFont(TTFont('NoteBold', str(bold if bold.is_file() else regular), subfontIndex=0))
            pdfmetrics.registerFontFamily('NoteText', normal='NoteText', bold='NoteBold', italic='NoteText', boldItalic='NoteBold')
            return 'NoteText', 'NoteBold'
        # Adobe's predefined CJK font is portable when a local CJK font is absent.
        if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        pdfmetrics.registerFontFamily('STSong-Light',normal='STSong-Light',bold='STSong-Light',italic='STSong-Light',boldItalic='STSong-Light')
        return 'STSong-Light', 'STSong-Light'


def build_pdf_document(content, task_dir, title='视频笔记'):
    from .math_render import prepare_math, formula_image
    content = prepare_math(content)
    regular, bold = document_fonts()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=44, leftMargin=44,
                            topMargin=44, bottomMargin=44, title=str(title), author='AI-Video-Note')
    width = A4[0] - 88
    sources = {source.name: source for source in referenced_images(content, Path(task_dir)).values()}
    body = ParagraphStyle('body', fontName=regular, fontSize=10.5, leading=17, spaceAfter=8,
                          textColor=colors.HexColor('#263448'), wordWrap='CJK', alignment=TA_LEFT, autoLeading='max')
    caption = ParagraphStyle('caption', parent=body, fontSize=9, leading=14, alignment=TA_CENTER, textColor=colors.HexColor('#64748b'))
    code = ParagraphStyle('code', parent=body, fontSize=9, leading=14, backColor=colors.HexColor('#f1f5f9'),
                          borderPadding=8, spaceBefore=5, spaceAfter=12)
    story, lists, quote_depth = [], [], 0
    pending_prefix = ''

    def style(heading=0, indent_first_line=False):
        if heading:
            size = {1:21, 2:16, 3:13}.get(heading, 11.5)
            return ParagraphStyle('heading', parent=body, fontName=bold, fontSize=size, leading=size*1.45,
                                  spaceBefore=12, spaceAfter=9, keepWithNext=True)
        return ParagraphStyle('paragraph', parent=body, leftIndent=max(0,len(lists)-1)*14+quote_depth*14,
                              firstLineIndent=body.fontSize*2 if indent_first_line and not lists and not quote_depth else 0)

    def picture(source, available):
        with PILImage.open(source) as original:
            image = ImageOps.exif_transpose(original).convert('RGB')
            data = BytesIO(); image.save(data, format='PNG'); data.seek(0)
            ratio = min(available/image.width, (A4[1]-180)/image.height, 1.0)
            return Image(data, width=image.width*ratio, height=image.height*ratio, hAlign='CENTER')

    def inline(token, selected, available=width, prefix=''):
        result, chunks, active_tags = [], [escape(prefix)], []
        visible = bool(prefix)
        def flush():
            nonlocal visible
            if visible:
                result.append(Paragraph(''.join(chunks)+''.join(close for _,close in reversed(active_tags)), selected))
            chunks[:] = [start for start,_ in active_tags]
            visible = False
        for child in token.children or []:
            kind = child.type
            if kind in {'text','code_inline'}:
                chunks.append(escape(child.content))
                visible = visible or bool(child.content)
            elif kind in {'softbreak','hardbreak'}:chunks.append('<br/>');visible=True
            elif kind in {'strong_open','em_open'}:
                pair=('<b>','</b>') if kind=='strong_open' else ('<i>','</i>')
                active_tags.append(pair);chunks.append(pair[0])
            elif kind in {'strong_close','em_close'}:
                if active_tags:chunks.append(active_tags.pop()[1])
            elif kind=='link_open':
                href=child.attrGet('href') or ''
                allowed=urlsplit(href).scheme.lower() in {'http','https','mailto'}
                pair=(f'<link href="{escape(href,quote=True)}" color="#2563a6">','</link>') if allowed else ('','')
                chunks.append(pair[0]);active_tags.append(pair)
            elif kind=='link_close':
                if active_tags:chunks.append(active_tags.pop()[1])
            elif kind=='image':
                src=child.attrGet('src') or ''
                formula = formula_image(src)
                if formula:
                    with PILImage.open(BytesIO(formula)) as image:
                        ratio = min(.375, available / image.width, (A4[1]-180)/image.height)
                        fw, fh = image.width*ratio, image.height*ratio
                    if child.attrGet('title') == 'note-formula-inline':
                        chunks.append(f'<img src="{src}" width="{fw}" height="{fh}" valign="middle"/>')
                        visible = True
                    else:
                        flush()
                        result.append(Image(BytesIO(formula), width=fw, height=fh, hAlign='LEFT'))
                        result.append(Spacer(1, 3))
                    continue
                source=sources.get(Path(unquote(urlsplit(src).path)).name) if src.startswith(('frames/','./frames/','images/','./images/')) else None
                if source:
                    flush(); result.append(picture(source,available)); result.append(Spacer(1,7))
                    if child.content:result.append(Paragraph(escape(child.content),caption))
                else:chunks.append(escape(child.content));visible=visible or bool(child.content)
        flush()
        return result

    tokens = MarkdownIt('commonmark', {'html':False}).enable('table').parse(content)
    heading = 0; rows = None; row = None
    for token in tokens:
        kind=token.type
        if kind=='heading_open':heading=int(token.tag[1:])
        elif kind=='heading_close':heading=0
        elif kind=='blockquote_open':quote_depth+=1
        elif kind=='blockquote_close':quote_depth-=1
        elif kind in {'bullet_list_open','ordered_list_open'}:lists.append([kind=='ordered_list_open',int(token.attrGet('start') or 1)])
        elif kind in {'bullet_list_close','ordered_list_close'}:lists.pop();pending_prefix=''
        elif kind=='list_item_open':
            ordered,number=lists[-1];pending_prefix=f'{number}. ' if ordered else '• ';lists[-1][1]+=1
        elif kind=='list_item_close':pending_prefix=''
        elif kind=='table_open':rows=[]
        elif kind=='tr_open':row=[]
        elif kind=='tr_close':rows.append(row);row=None
        elif kind=='table_close':
            columns=max(map(len,rows),default=1)
            cells=[[inline(cell,body,width/columns-14) for cell in row] for row in rows]
            table=LongTable(cells,colWidths=[width/columns]*columns,repeatRows=1,splitByRow=1,splitInRow=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf0f6')),
                ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#cbd5e1')),('VALIGN',(0,0),(-1,-1),'TOP'),
                ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
                ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
            story.extend([table,Spacer(1,12)]);rows=None
        elif kind=='inline':
            if row is not None:
                row.append(token)
            else:
                story.extend(inline(token,caption if not heading and not lists and not quote_depth and is_caption_paragraph(token) else style(heading,is_body_paragraph(token)),prefix=pending_prefix));pending_prefix=''
        elif kind in {'fence','code_block'}:
            story.append(Paragraph(escape(token.content).replace(' ','&#160;').replace('\n','<br/>'),code))
        elif kind=='hr':story.extend([HRFlowable(width='100%',color=colors.HexColor('#cbd5e1')),Spacer(1,10)])
    if not story:story.append(Paragraph(' ',body))

    def footer(canvas, document):
        canvas.saveState();canvas.setFont(regular,8);canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawRightString(A4[0]-44,24,str(document.page));canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()
