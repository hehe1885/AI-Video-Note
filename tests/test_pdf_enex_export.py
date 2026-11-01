import base64
from hashlib import md5
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image
from pypdf import PdfReader

from backend import main
from backend.enex_export import build_enex_document
from backend.pdf_export import build_pdf_document
from backend.task_cleanup import exported_files, unlink_exports


def material(root):
    task=root/'tasks'/'export123';frames=task/'frames';frames.mkdir(parents=True)
    Image.new('RGB',(640,360),'navy').save(frames/'used.jpg')
    Image.new('RGB',(40,40),'red').save(frames/'unused.png')
    content='# 中文课程笔记\n\n**重点**与 *解释*，含 [参考链接](https://example.com/path?q=1&x=2)。\n\n![课堂截图](frames/used.jpg)\n\n'
    return task,content


def test_enex_resources_match_enml_hashes_and_only_include_referenced_images(tmp_path):
    task,content=material(tmp_path)
    original=(task/'frames'/'used.jpg').read_bytes()
    document=build_enex_document(content+'\n![重复](./images/used.jpg)\n',task,'笔记 <&> 标题')
    root=etree.fromstring(document,etree.XMLParser(resolve_entities=False,no_network=True))
    assert root.tag=='en-export'
    assert root.findtext('note/title')=='笔记 <&> 标题'
    resources=root.findall('note/resource');assert len(resources)==1
    assert base64.b64decode(resources[0].findtext('data'))==original
    assert resources[0].findtext('mime')=='image/jpeg'
    assert resources[0].findtext('resource-attributes/file-name')=='used.jpg'
    enml=etree.fromstring(root.findtext('note/content').encode(),etree.XMLParser(resolve_entities=False,no_network=True))
    images=enml.findall('.//en-media');assert len(images)==2
    assert {node.get('hash') for node in images}=={md5(original).hexdigest()}
    assert enml.find('.//a').get('href')=='https://example.com/path?q=1&x=2'
    assert (task/'frames'/'used.jpg').read_bytes()==original
    assert (task/'frames'/'unused.png').is_file()


def test_enex_is_safe_xml_with_code_tables_and_literal_html(tmp_path):
    task,_=material(tmp_path)
    content='''# Test <&>

<script>alert(1)</script>

```python
print("x & y")
```

| 左 | 右 |
| :- | -: |
| a | b |

![external](https://example.com/image.png)
![missing](frames/missing.jpg)
'''
    root=etree.fromstring(build_enex_document(content,task),etree.XMLParser(resolve_entities=False,no_network=True))
    enml=etree.fromstring(root.findtext('note/content').encode(),etree.XMLParser(resolve_entities=False,no_network=True))
    assert not root.findall('.//resource')
    assert not enml.findall('.//script') and not enml.findall('.//img')
    assert not enml.findall('.//*[@class]')
    assert enml.find('.//table') is not None
    assert 'print("x & y")' in ''.join(enml.itertext())
    assert 'external' in ''.join(enml.itertext())


def test_pdf_has_searchable_chinese_embedded_image_links_and_multiple_pages(tmp_path):
    task,content=material(tmp_path)
    content+='| 项目 | 说明 |\n| --- | --- |\n| 表格内容 | 第一行 |\n\n'
    content+='\n\n'.join('## 第%d节\n\n这是一段用于检查中文分页的课程内容。'%n+'内容解释，保留关键步骤。'*8 for n in range(32))
    document=build_pdf_document(content,task,'中文课程笔记')
    reader=PdfReader(BytesIO(document))
    assert len(reader.pages)>2
    text=''.join(page.extract_text() for page in reader.pages)
    assert '中文课程笔记' in text and '表格内容' in text and '第31节' in text
    assert reader.metadata.title=='中文课程笔记'
    assert sum(len(page.images) for page in reader.pages)==1
    urls=[annotation.get_object()['/A'].get('/URI') for page in reader.pages for annotation in page.get('/Annots',[]) if '/A' in annotation.get_object()]
    assert 'https://example.com/path?q=1&x=2' in urls
    assert (task/'frames'/'used.jpg').is_file() and (task/'frames'/'unused.png').is_file()


def test_pdf_handles_long_table_cells_code_and_inline_images(tmp_path):
    task,_=material(tmp_path)
    content='**加粗前文 ![图片](frames/used.jpg) 后文**\n\n| 项目 | 内容 |\n|---|---|\n| 长行 | '+'长内容 '*1000+'|\n\n```python\n'+'very_long_line_'*60+'\n```'
    reader=PdfReader(BytesIO(build_pdf_document(content,task)))
    assert len(reader.pages)>1
    assert sum(len(page.images) for page in reader.pages)>=1
    assert 'very_long_line_' in ''.join(page.extract_text() for page in reader.pages)


@pytest.mark.parametrize('format,mime',[('pdf','application/pdf'),('enex','application/xml')])
@pytest.mark.parametrize('custom',[False,True])
def test_format_save_download_cleanup_and_source_preservation(tmp_path,monkeypatch,format,mime,custom):
    task,content=material(tmp_path)
    (task/'notes.md').write_text(content,encoding='utf-8')
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{'export123':dict(status='completed',result=dict(title='中文标题',markdown=content))})
    directory=tmp_path/'custom' if custom else tmp_path/'notes';owned=[]
    saved=main.archive_note('中文标题',content,'export123',note_format=format,
        save_directory=str(directory) if custom else '',created_files=owned)
    assert saved.parent==directory and saved.suffix=='.'+format
    if format=='pdf':assert saved.read_bytes().startswith(b'%PDF-')
    else:assert etree.fromstring(saved.read_bytes()).tag=='en-export'
    response=TestClient(main.app).get('/api/download/export123?format='+format)
    assert response.status_code==200 and response.headers['content-type'].startswith(mime)
    assert '.'+format in response.headers['content-disposition']
    if format=='pdf':assert PdfReader(BytesIO(response.content)).pages
    else:assert etree.fromstring(response.content).findtext('note/title')=='中文标题'
    result=dict(archived_path=str(saved),note_export_files=list(map(str,owned)))
    cleanup=exported_files(tmp_path,'export123',dict(note_save_directory=str(directory)) if custom else {},result)
    assert cleanup==set(owned)
    unlink_exports(cleanup)
    assert not saved.exists()
    assert (task/'frames'/'used.jpg').is_file() and (task/'frames'/'unused.png').is_file()


@pytest.mark.parametrize('format',['pdf','enex'])
def test_new_formats_accept_generation_request_and_reject_download_before_completion(tmp_path,monkeypatch,format):
    assert main.SummarizeRequest(llm_config={},note_format=format).note_format==format
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{'pending':dict(status='processing')})
    assert TestClient(main.app).get('/api/download/pending?format='+format).status_code==409
