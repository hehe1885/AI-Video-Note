"""Verify paragraph indent in actual PDF, Word and raster note exports."""
from io import BytesIO

from docx import Document
from markdown_it import MarkdownIt
from PIL import Image, ImageDraw
from pypdf import PdfReader
import pytest

from backend.pdf_export import build_pdf_document
from backend.png_export import render_png
from backend.word_export import build_word_document


@pytest.fixture
def note(tmp_path):
    (tmp_path/'frames').mkdir()
    Image.new('RGB',(120,60),'navy').save(tmp_path/'frames/shot.png')
    prose='正文首行应当缩进，后面的换行回到左侧正文边界。'+'继续解释主要概念和必要步骤。'*24
    content=f'''# 大标题

视频链接：https://example.com/video

## 小标题

{prose}

![图片备注保持左对齐](frames/shot.png)

图注：补充说明保持左对齐。

**另一段正文**也需要首行缩进。

- 列表内容

> 时长：04:17

| 项目 | 解释 |
| --- | --- |
| 表格内容 | 表格说明 |

```text
代码内容
```
'''
    return tmp_path,content


def test_word_body_indent_and_exceptions_are_encoded_in_document(note):
    task,content=note
    document=Document(BytesIO(build_word_document(content,task)))
    for paragraph in document.paragraphs:
        indent=paragraph.paragraph_format.first_line_indent
        if paragraph.text.startswith(('正文首行','另一段正文')):
            assert indent is not None and indent.pt==24
        else:
            assert indent is None or indent.pt==0,paragraph.text
    assert len(document.inline_shapes)==1
    for row in document.tables[0].rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                assert paragraph.paragraph_format.first_line_indent is None


def test_pdf_body_and_exception_styles_reach_the_real_renderer(note,monkeypatch):
    from backend import pdf_export
    task,content=note
    captured=[]
    original=pdf_export.Paragraph
    def capture(text,style,*args,**kwargs):
        paragraph=original(text,style,*args,**kwargs)
        captured.append((paragraph.getPlainText(),style.firstLineIndent,style.fontSize))
        return paragraph
    monkeypatch.setattr(pdf_export,'Paragraph',capture)
    reader=PdfReader(BytesIO(build_pdf_document(content,task)))
    assert reader.pages and sum(len(page.images) for page in reader.pages)==1
    for text,indent,size in captured:
        assert indent==(size*2 if text.startswith(('正文首行','另一段正文')) else 0),text
    assert any(text=='图片备注保持左对齐' for text,_,_ in captured)


def test_png_only_the_first_prose_line_moves_and_exceptions_stay_aligned(note,monkeypatch):
    task,content=note
    recorded=[]
    original=ImageDraw.ImageDraw.text
    def capture(self,position,text,*args,**kwargs):
        recorded.append((position,text,kwargs['font'].size))
        return original(self,position,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    render_png(content,task)
    start=next(i for i,(_,text,_) in enumerate(recorded) if text.startswith('正文首行'))
    assert recorded[start][0][0]==84+26*2
    assert recorded[start+1][0][0]==84,'Only the first line is indented'
    assert next(position[0] for position,text,_ in recorded if text.startswith('另一段正文'))==136
    for prefix in ('大标题','小标题','视频链接：'):
        assert next(position[0] for position,text,_ in recorded if text.startswith(prefix))==84,prefix
    assert next(position[0] for position,text,_ in recorded if text=='• 列表内容')==112
    for position,text,size in recorded:
        if text.startswith(('图片备注','图注：')):
            assert size==21 and position[0]>84


@pytest.mark.parametrize('source',[
    '视频链接：[点击查看](https://example.com/video)',
    'Video link: https://example.com/video',
    'Source: [Watch](https://example.com/video)',
    'https://example.com/video',
    '[观看视频](https://example.com/video)',
    '图1：图片说明。',
    'Caption: An illustration.',
])
def test_source_links_and_explicit_captions_do_not_get_word_body_indent(tmp_path,source):
    document=Document(BytesIO(build_word_document(source,tmp_path)))
    assert document.paragraphs[0].paragraph_format.first_line_indent.pt==0


def test_png_inline_math_keeps_first_line_indent_and_wraps(tmp_path,monkeypatch):
    from backend import math_render
    from markdown_it.token import Token
    buffer=BytesIO();Image.new('RGB',(50,35),'black').save(buffer,format='PNG')
    monkeypatch.setattr(math_render,'prepare_math',lambda text:text)
    monkeypatch.setattr(math_render,'formula_image',lambda src:buffer.getvalue() if src.startswith('data:') else None)
    tokens=MarkdownIt('commonmark').parse('正文公式 '+('继续说明。'*80))
    image=Token('image','img',0,attrs={'src':'data:image/png;base64,fixture','title':'note-formula-inline'})
    inline=next(token for token in tokens if token.type=='inline')
    inline.children.insert(1,image)
    from backend import png_export
    class Parser:
        def enable(self,*args):return self
        def parse(self,text):return tokens
    monkeypatch.setattr(png_export,'MarkdownIt',lambda *args:Parser())
    recorded=[]
    original=ImageDraw.ImageDraw.text
    def capture(self,position,text,*args,**kwargs):
        recorded.append((position,text))
        return original(self,position,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    render_png('fixture',tmp_path)
    assert recorded[0][0][0]==136
    assert any(position[0]==84 for position,_ in recorded[1:])
