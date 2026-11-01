from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image,ImageDraw
from reportlab.lib.enums import TA_CENTER

from backend.pdf_export import build_pdf_document
from backend.word_export import build_word_document
from backend.png_export import render_png


def fixture(tmp_path):
    (tmp_path/'frames').mkdir()
    Image.new('RGB',(240,100),'navy').save(tmp_path/'frames/shot.png')
    return '# 标题\n\n普通正文。\n\n![截图备注](frames/shot.png)\n\n图注：补充说明。\n\n后续正文。'


def test_word_picture_and_both_caption_forms_are_centered_without_indenting_prose(tmp_path):
    content=fixture(tmp_path)
    document=Document(BytesIO(build_word_document(content,tmp_path)))
    pictures=[paragraph for paragraph in document.paragraphs if paragraph._p.xpath('.//w:drawing')]
    assert len(pictures)==1
    assert pictures[0].alignment==WD_ALIGN_PARAGRAPH.CENTER
    captions=[p for p in document.paragraphs if p.text in {'截图备注','图注：补充说明。'}]
    assert len(captions)==2
    for paragraph in captions:
        assert paragraph.alignment==WD_ALIGN_PARAGRAPH.CENTER
        assert paragraph.style.name=='Caption'
        assert paragraph.paragraph_format.first_line_indent.pt==0
    for paragraph in document.paragraphs:
        if paragraph.text in {'普通正文。','后续正文。'}:
            assert paragraph.alignment!=WD_ALIGN_PARAGRAPH.CENTER
            assert paragraph.paragraph_format.first_line_indent.pt==24


def test_word_inline_screenshot_splits_prose_into_separate_paragraphs(tmp_path):
    fixture(tmp_path)
    document=Document(BytesIO(build_word_document('前文 ![说明](frames/shot.png) 后文',tmp_path)))
    assert [p.text.strip() for p in document.paragraphs]==['前文','','说明','后文']
    assert document.paragraphs[1].alignment==WD_ALIGN_PARAGRAPH.CENTER
    assert document.paragraphs[2].alignment==WD_ALIGN_PARAGRAPH.CENTER
    assert document.paragraphs[0].paragraph_format.first_line_indent.pt==24
    assert document.paragraphs[3].paragraph_format.first_line_indent.pt==24


def test_pdf_centered_picture_and_small_centered_captions_reach_renderer(tmp_path,monkeypatch):
    from backend import pdf_export
    content=fixture(tmp_path);pictures=[];captions=[]
    original_image=pdf_export.Image;original_paragraph=pdf_export.Paragraph
    def image(*args,**kwargs):
        result=original_image(*args,**kwargs);pictures.append(result);return result
    def paragraph(text,style,*args,**kwargs):
        result=original_paragraph(text,style,*args,**kwargs)
        if result.getPlainText() in {'截图备注','图注：补充说明。'}:captions.append(style)
        return result
    monkeypatch.setattr(pdf_export,'Image',image)
    monkeypatch.setattr(pdf_export,'Paragraph',paragraph)
    assert build_pdf_document(content,tmp_path).startswith(b'%PDF')
    assert len(pictures)==1 and pictures[0].hAlign=='CENTER'
    assert len(captions)==2
    assert all(style.alignment==TA_CENTER and style.firstLineIndent==0 and style.fontSize==9 for style in captions)


def test_png_caption_lines_are_centered_with_smaller_font(tmp_path,monkeypatch):
    content=fixture(tmp_path);recorded=[]
    original=ImageDraw.ImageDraw.text
    def capture(self,position,text,*args,**kwargs):
        recorded.append((position,text,kwargs['font']))
        return original(self,position,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    output=Image.open(BytesIO(render_png(content,tmp_path)))
    measure=ImageDraw.Draw(Image.new('RGB',(1,1)))
    for name in ['截图备注','图注：补充说明。']:
        position,text,font=next(item for item in recorded if item[1]==name)
        assert font.size==21
        assert abs(position[0]+measure.textlength(text,font=font)/2-output.width/2)<1
