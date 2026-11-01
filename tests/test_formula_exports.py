"""Exercise real local KaTeX output across rich formats and protect source notation."""
import base64
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image
from pypdf import PdfReader
import pytest

from backend import main
from backend.math_render import math_segments, prepare_math, browser_path
from backend.note_export import save_note_export
from backend.note_styles import NOTE_STYLES


def test_formula_scanner_preserves_code_escaped_dollars_and_alternate_delimiters():
    content = r'''Literal \$5 and `price $x$`.

~~~latex
$not_math$
~~~

```tex
$$not_math$$
```

$x_1$ and \(v=at\), then \[E=mc^2\] and $$\frac{a}{b}$$.
'''
    found = [(source, display) for _, source, display in math_segments(content)]
    assert found == [('x_1', False), ('v=at', False), ('E=mc^2', True), (r'\frac{a}{b}', True)]


@pytest.fixture(scope='module')
def content():
    try:
        browser_path()
    except RuntimeError:
        pytest.skip('Real formula integration requires installed Edge or Chrome')
    return r'''# 公式笔记

速度 $v=at$，单位 $\mathrm{m\,s^{-1}}$。

$$\frac{-b\pm\sqrt{b^2-4ac}}{2a}$$

$$\int_0^1 x^2\,dx=\frac{1}{3}$$

$$\begin{pmatrix}1&2\\3&4\end{pmatrix}$$

$$\ce{2H2 + O2 -> 2H2O}$$

| 名称 | 公式 |
| --- | --- |
| 能量 | $E=mc^2$ |

```latex
$literal_code$
```
'''


def test_retired_style_migrates_and_is_absent_from_schema():
    for language in ['zh-CN', 'en']:
        request = main.SummarizeRequest(llm_config={}, summary_style='science', note_language=language)
        assert request.summary_style == 'faithful'
    assert 'science' not in NOTE_STYLES
    assert 'science' not in main.SummarizeRequest.model_json_schema()['properties']['summary_style']['enum']


def test_all_rich_exports_embed_formulas_and_leave_task_unchanged(tmp_path, content):
    task = tmp_path/'tasks'/'math123'
    task.mkdir(parents=True)
    outputs = tmp_path/'exports'
    for format in ['pdf', 'enex', 'docx', 'png', 'html']:
        path = save_note_export('理科公式', content, task, outputs, format)
        data = path.read_bytes()
        if format == 'pdf':
            reader = PdfReader(BytesIO(data))
            assert sum(len(page.images) for page in reader.pages) >= 7
            assert 'literal_code' in ''.join(page.extract_text() for page in reader.pages)
        elif format == 'enex':
            root = etree.fromstring(data)
            assert len(root.findall('note/resource')) >= 7
            body = etree.fromstring(root.findtext('note/content').encode(), etree.XMLParser(no_network=True))
            assert len(body.findall('.//en-media')) >= 7
        elif format == 'docx':
            with ZipFile(BytesIO(data)) as document:
                assert len([name for name in document.namelist() if name.startswith('word/media/')]) >= 7
                assert b'literal_code' in document.read('word/document.xml')
        elif format == 'html':
            assert data.count(b'data:image/png;base64,') >= 7
            assert b'$literal_code$' in data
        else:
            with Image.open(BytesIO(data)) as image:
                assert image.width == 1200 and image.height > 700
                image.verify()
    assert list(task.iterdir()) == []


def test_source_formats_keep_formula_source(tmp_path):
    task=tmp_path/'task'; task.mkdir()
    content=r'# Formula' + '\n\n' + r'$x_1^2=\frac{a}{b}$'
    for format in ['markdown', 'obsidian', 'json', 'txt']:
        path=save_note_export('Formula', content, task, tmp_path/'exports', format)
        saved=path.read_text(encoding='utf-8')
        if format=='json':
            saved=json.loads(saved)['content']
        assert r'$x_1^2=\frac{a}{b}$' in saved


def test_html_download_embeds_formulas(tmp_path, monkeypatch, content):
    task=tmp_path/'tasks'/'math123'; task.mkdir(parents=True)
    (task/'notes.md').write_text(content,encoding='utf-8')
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{'math123':{'status':'completed','result':{'title':'理科笔记'}}})
    response=TestClient(main.app).get('/api/download/math123?format=html')
    assert response.status_code==200
    assert 'text/html' in response.headers['content-type']
    assert response.content.count(b'data:image/png;base64,')>=7
    assert '.html' in response.headers['content-disposition']


@pytest.mark.parametrize('format', ['pdf', 'docx', 'enex', 'html'])
def test_render_errors_reach_download_ui(tmp_path, monkeypatch, format):
    from backend import math_render
    def unavailable(_):
        raise RuntimeError('公式导出需要本机安装 Microsoft Edge 或 Chrome')
    monkeypatch.setattr(math_render, 'prepare_math', unavailable)
    task=tmp_path/'tasks'/'math123'; task.mkdir(parents=True)
    (task/'notes.md').write_text('$x$',encoding='utf-8')
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    monkeypatch.setattr(main,'tasks',{'math123':{'status':'completed','result':{'title':'笔记'}}})
    response=TestClient(main.app).get('/api/download/math123?format='+format)
    assert response.status_code==503
    assert 'Edge' in response.json()['detail']
