import asyncio
import json
from pathlib import Path
from urllib.parse import unquote
import pytest
from markdown_it import MarkdownIt
from backend import main, obsidian_export, agent_queue, agent_auto
from backend.note_markdown import normalize_bold_spacing, rewrite_frame_links
from backend.transcript import TranscriptSegment
from backend.video_processor import SubtitleResult
from tests.test_agent_auto import job

PARSER = MarkdownIt('commonmark')


@pytest.mark.parametrize('label,body', [
    ('易拉罐回收。','材料以巴西为例。'), ('购车牌照费用。','普通车与豪华车原价不同。'),
    ('影院是否对号入座。','上座率高时提前选座。'),('冷冻室的灯。','如果冷藏室和冷冻室加灯的成本接近。'),
    ('24 小时便利店的门锁。','门平时很少上锁。'),('报纸贩卖机。','传统报纸机可以接触到整叠报纸。'),
])
def test_punctuation_ending_labels_close_before_normal_prose(label,body):
    source=f'- **{label}**{body}'
    assert '<strong>' not in PARSER.render(source), 'Reproduce the original delimiter failure'
    fixed=normalize_bold_spacing(source)
    assert fixed == f'- **{label}** {body}'
    html=PARSER.render(fixed)
    assert f'<strong>{label}</strong> {body}' in html
    assert normalize_bold_spacing(fixed)==fixed


def test_formatting_preserves_code_math_properties_destinations_and_existing_spacing():
    source='''---
title: "**属性。**保留"
---

```md
- **示例。**原样保留
```

    **缩进代码。**原样保留

`**行内代码。**保留` $**公式。**保留$ \\(**x**y\\)

![图](frames/**图片。**原名.jpg)

<div>
**HTML。**保留
</div>

- **正确。** 正文
- \\*\\*转义。\\*\\*原样
- ***三重。***原样
'''
    assert normalize_bold_spacing(source)==source


@pytest.mark.parametrize('reference',[
    'frames/frame 1(中文).jpg', './frames/frame 1(中文).jpg',
    'frames/frame%201%28%E4%B8%AD%E6%96%87%29.jpg',
    './frames/frame 1\\(中文\\).jpg',
])
def test_rewrite_known_frame_destinations_without_touching_examples(reference):
    body=f'![图 [细节]](<{reference}> "说明")\n\n`![图]({reference})`'
    fixed=rewrite_frame_links(body,{'frame 1(中文).jpg':'../picture/video-1.jpg'})
    assert fixed.startswith('![图 [细节]](<../picture/video-1.jpg> "说明")')
    assert fixed.endswith(f'`![图]({reference})`')


def test_image_reference_definitions_are_rewritten_but_plain_text_is_not():
    body='![图][figure]\n\n[figure]: frames/frame.jpg "caption"\n\n原文 frames/frame.jpg\n\n```md\n![图](frames/frame.jpg)\n```'
    fixed=rewrite_frame_links(body,{'frame.jpg':'../picture/video-1.jpg'})
    assert '[figure]: ../picture/video-1.jpg "caption"' in fixed
    assert '原文 frames/frame.jpg' in fixed
    assert '```md\n![图](frames/frame.jpg)\n```' in fixed


@pytest.mark.parametrize('mode', ['vault_root','note_folder','note_subfolder','specified_folder'])
@pytest.mark.parametrize('prefix',['','./'])
def test_all_obsidian_modes_resolve_bare_and_dot_frame_refs(tmp_path,mode,prefix):
    vault=tmp_path/'vault';vault.mkdir()
    image=tmp_path/'frame.jpg';image.write_bytes(b'original image')
    content=f'- **易拉罐回收。**正文\n\n![图]({prefix}frames/frame.jpg "说明")'
    note=obsidian_export.export_note(content,'note',[image],vault=str(vault),note_folder='视频笔记',
                                    attachment_mode=mode,attachment_folder='picture')
    saved=note.read_text(encoding='utf-8')
    children=[child for token in PARSER.parse(saved) for child in (token.children or [])]
    images=[child for child in children if child.type=='image']
    assert len(images)==1 and images[0].attrGet('title')=='说明'
    target=(note.parent/unquote(images[0].attrGet('src'))).resolve()
    assert target.is_relative_to(vault) and target.read_bytes()==b'original image'
    assert '<strong>易拉罐回收。</strong> 正文' in PARSER.render(saved)
    image.unlink()
    assert target.is_file(), 'Exported note is independent of task frames'


def test_agent_result_normalizes_body_and_exports_resolvable_vault_images(job,monkeypatch,tmp_path):
    task_id,directory=job
    vault=tmp_path/'vault';vault.mkdir()
    data=agent_queue.read_job(task_id)
    data['settings'].update(note_format='obsidian',obsidian={'vault':str(vault),'note_folder':'视频笔记',
        'attachment_mode':'specified_folder','attachment_folder':'picture'})
    agent_queue.write_job(task_id,data)
    async def cli(task_id,prompt,schema=None,images=()):
        return '{"times":[3]}' if schema else '- **易拉罐回收。**材料中的正文。\n\n![图](frames/frame.jpg)'
    async def extract(*args):
        frames=directory/'frames';frames.mkdir()
        image=frames/'frame.jpg';image.write_bytes(b'image bytes')
        return {3:image}
    monkeypatch.setattr(agent_auto,'run_cli',cli)
    monkeypatch.setattr(main.video_processor,'extract_frames_at',extract)
    asyncio.run(agent_auto.work(task_id))
    task=main.tasks[task_id]
    assert task['status']=='completed'
    assert '**易拉罐回收。** 材料中的正文。' in (directory/'notes.md').read_text(encoding='utf-8')
    exported=Path(task['result']['obsidian_path'])
    children=[child for token in PARSER.parse(exported.read_text(encoding='utf-8')) for child in token.children or []]
    image=next(child for child in children if child.type=='image')
    assert (exported.parent/unquote(image.attrGet('src'))).resolve().is_file()


@pytest.mark.asyncio
async def test_api_generation_also_normalizes_the_persisted_markdown(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'WORKSPACE_DIR',tmp_path)
    task_id='api-format'
    monkeypatch.setattr(main,'tasks',{task_id:main.new_task(task_id=task_id)})
    main.task_directory(task_id).mkdir(parents=True)
    request=main.SummarizeRequest(video_url='https://youtu.be/test',llm_config={},screenshot_density='none')
    main.write_task_manifest(task_id,request,None,'web')
    async def info(*args,**kwargs):return {'title':'Test','duration':10,'source':'youtube'}
    async def subtitle(*args,**kwargs):return SubtitleResult([TranscriptSegment(0,10,'素材。')],'zh','platform_subtitle')
    class Model:
        def __init__(self,**kwargs):pass
        def describe_effort(self,*args):return 'default'
        async def generate_summary(self,*args,**kwargs):return '- **冷冻室的灯。**后面是普通正文。'
    monkeypatch.setattr(main.video_processor,'get_video_info',info)
    monkeypatch.setattr(main.video_processor,'fetch_subtitles',subtitle)
    monkeypatch.setattr(main,'resolve_llm_credentials',lambda **kwargs:('fixture','test'))
    monkeypatch.setattr(main,'LLMSummarizer',Model)
    monkeypatch.setattr(main,'notify_task',lambda *args:None)
    await main.process_video_task(task_id,request)
    assert main.tasks[task_id]['status']=='completed',main.tasks[task_id].get('error')
    saved=(main.task_directory(task_id)/'notes.md').read_text(encoding='utf-8')
    assert '**冷冻室的灯。** 后面是普通正文。' in saved
