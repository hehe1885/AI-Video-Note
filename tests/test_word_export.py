from io import BytesIO
from zipfile import ZipFile

from docx import Document
from backend.word_export import build_word_document


def test_word_content_and_embedded_image(tmp_path):
    from PIL import Image
    frames = tmp_path / 'frames'
    frames.mkdir()
    Image.new('RGB', (20, 10), 'blue').save(frames / 'shot.png')
    content = build_word_document('''# 中文标题

正文 **重点** 和 *斜体*，含 `代码` 和 [来源](https://example.com)。

- 第一条
- 第二条

| 概念 | 解释 |
| --- | --- |
| 测试 | 内容 |

![截图](./frames/shot.png)

```python
print("hello")
```
''', tmp_path)
    doc = Document(BytesIO(content))
    assert doc.paragraphs[0].text == '中文标题'
    assert doc.paragraphs[0].style.name == 'Title'
    assert any(run.bold and run.text == '重点' for p in doc.paragraphs for run in p.runs)
    assert any('https://example.com' in p.text for p in doc.paragraphs)
    assert doc.tables[0].cell(1, 1).text == '内容'
    assert len(doc.inline_shapes) == 1
    with ZipFile(BytesIO(content)) as archive:
        assert 'word/document.xml' in archive.namelist()
        assert any(name.startswith('word/media/') for name in archive.namelist())


def test_word_does_not_embed_outside_images(tmp_path):
    outside = tmp_path / 'private.png'
    outside.write_bytes(b'secret')
    doc = Document(BytesIO(build_word_document(f'![外部]({outside.as_uri()})', tmp_path)))
    assert len(doc.inline_shapes) == 0
    assert 'secret' not in doc.paragraphs[0].text



def test_word_download_api(tmp_path, monkeypatch):
    from backend import main
    from fastapi.testclient import TestClient
    task_id = 'word-download'
    folder = tmp_path / task_id
    folder.mkdir()
    (folder / 'notes.md').write_text('# Word test\n\nEditable content', encoding='utf-8')
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setitem(main.tasks, task_id, {'status': 'completed', 'result': {'title': 'Word test'}})
    response = TestClient(main.app).get(f'/api/download/{task_id}?format=docx')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert '.docx' in response.headers['content-disposition']
    assert Document(BytesIO(response.content)).paragraphs[0].text == 'Word test'
