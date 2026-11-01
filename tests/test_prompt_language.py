import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend import agent_queue, main
from backend.llm_summarizer import LLMSummarizer
from backend.note_options import note_option_prompt, note_format_prompt, obsidian_feature_prompt, commentary_prompt, note_language_prompt
from backend.prompt_language import agent_prompt
from backend.transcript import TranscriptSegment
from backend.ai_screenshots import plan_screenshots

SOURCE = "来源中文原文"
TITLE = "中文视频标题"
CUSTOM = "用户自定义原文"
SEGMENTS = [TranscriptSegment(0, 10, SOURCE)]

def no_generated_chinese(text):
    for value in (SOURCE, TITLE, CUSTOM):
        text = text.replace(value, "")
    assert not re.search(r"[\u4e00-\u9fff]", text), text

@pytest.mark.parametrize("stage,args,kwargs", [
    ("_chunk_prompt",(TITLE,1,1,SEGMENTS),{}),
    ("_merge_prompt",(TITLE,1,1,1,[SOURCE]),{}),
    ("_note_prompt",(TITLE,SOURCE,{"description":SOURCE}),{"coverage_end":"00:10"}),
    ("_note_prompt",(TITLE,SOURCE,{}),{"style":"concise"}),
    ("_section_note_prompt",(TITLE,1,1,SEGMENTS,SOURCE),{"retried":True,"brief":SOURCE}),
    ("_section_continuation_prompt",(TITLE,1,1,SOURCE,SEGMENTS,"00:05","00:10"),{}),
    ("_tail_patch_prompt",(TITLE,SOURCE,SEGMENTS,"00:05","00:10"),{}),
    ("_overview_prompt",(TITLE,SOURCE),{}),
    ("_analysis_prompt",(TITLE,SOURCE),{}),
    ("_context_brief",({"description":SOURCE},),{}),
])
def test_api_stage_instructions_are_english_without_translating_material(stage,args,kwargs):
    text = getattr(LLMSummarizer,stage)(*args,**kwargs,note_language="en")
    no_generated_chinese(text)
    assert SOURCE in text
    assert re.search(r"[\u4e00-\u9fff]",getattr(LLMSummarizer,stage)(*args,**kwargs))

@pytest.mark.parametrize("style",["faithful","concise","detailed","learning","tutorial","cards","outline","meeting","obsidian"])
@pytest.mark.parametrize("language",["zh-CN","en"])
def test_agent_prompt_language_and_custom_text(style,language):
    settings={"summary_style":style,"note_language":language,"custom_prompt":CUSTOM,"note_format":"obsidian",
              "obsidian_tags":True,"obsidian_callouts":True,"ai_commentary_position":"start","screenshot_density":"many",
              "vision_screenshots":True,"timed_screenshots":True}
    if language=="en":
        text=agent_prompt(settings)
        no_generated_chinese(text)
        assert "## AI Commentary" in text and "markdown_ref" in text
        assert CUSTOM in text
    else:
        assert "笔记语言" in note_language_prompt(language)

@pytest.mark.asyncio
@pytest.mark.parametrize("long_video",[False,True])
async def test_language_flows_into_api_system_and_generation(monkeypatch,long_video):
    summarizer=LLMSummarizer("deepseek","test-key")
    calls=[]
    async def generate(*args,**kwargs):
        text=summarizer._section_note_prompt(TITLE,1,1,SEGMENTS,"",note_language=summarizer._note_language)
        request=summarizer._build_request(text,1000,"off")
        calls.append(request)
        return "# English Notes\n\nContent."
    monkeypatch.setattr(summarizer,"_is_long_transcript",lambda _:long_video)
    monkeypatch.setattr(summarizer,"_write_condensed_notes",generate)
    monkeypatch.setattr(summarizer,"_write_long_transcript_notes",generate)
    for language in ("en","zh-CN"):
        await summarizer.generate_summary(TITLE,SEGMENTS,{},style="learning",note_language=language,
            custom_prompt=CUSTOM+note_language_prompt(language),reasoning_effort="off")
    for message in calls[0]["messages"]:
        no_generated_chinese(message["content"])
    assert SOURCE in calls[0]["messages"][1]["content"]
    assert CUSTOM in calls[0]["messages"][0]["content"]
    assert "你是视频笔记编辑" in calls[1]["messages"][0]["content"]

@pytest.mark.asyncio
async def test_english_screenshot_planning_and_retry_preserve_source():
    prompts=[]
    async def complete(prompt,**kwargs):
        prompts.append(prompt)
        return "invalid" if len(prompts)==1 else '{"shots":[]}'
    summarizer=SimpleNamespace(_note_language="en",_complete=complete)
    assert await plan_screenshots(summarizer,SEGMENTS,"medium")==[]
    assert len(prompts)==2
    for prompt in prompts:
        no_generated_chinese(prompt)
        assert SOURCE in prompt

@pytest.mark.asyncio
async def test_mcp_packet_uses_selected_language(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    monkeypatch.setattr(main,"tasks",{})
    directory=tmp_path/"language-test";directory.mkdir()
    (directory/"transcript.json").write_text(json.dumps({"segments":[{"start":0,"end":10,"text":SOURCE}]}),encoding="utf-8")
    main.tasks["language-test"]=main.new_task(status="awaiting_agent",task_id="language-test")
    settings={"note_language":"en","custom_prompt":CUSTOM,"summary_style":"tutorial","note_format":"docx"}
    agent_queue.write_job("language-test",{"title":TITLE,"settings":settings,"screenshots":[]})
    packet=await agent_queue.get_packet("language-test")
    no_generated_chinese(packet["prompt"])
    assert packet["segments"][0]["text"]==SOURCE
    assert packet["settings"]["note_language"]=="en"

def test_format_detail_and_commentary_english():
    for name in ("markdown","docx","obsidian","txt","html","json","png","pdf","enex"):
        no_generated_chinese(note_format_prompt(name,"en"))
    no_generated_chinese(note_option_prompt("brief","en"))
    no_generated_chinese(obsidian_feature_prompt(True,True,True,"en"))
    for position in ("none","start","end"):
        no_generated_chinese(commentary_prompt(position,"en"))
