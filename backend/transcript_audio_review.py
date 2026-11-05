"""Use real source audio as independent ASR evidence for the selected text model."""
from __future__ import annotations

import json
import math
from fastapi import HTTPException
from .transcript_audio import prepare_clip
from .transcript_review import validate_issues
from .task_activity import activity


async def check_audio(runtime, task_id, revision, item, segments, complete):
    task = runtime.tasks[task_id]
    options = task.get('_review_audio_options') or {}
    requested = options.get('whisper_model', 'turbo')
    candidates = list(dict.fromkeys([requested, 'turbo', 'small', 'base', 'tiny']))
    model = next((name for name in candidates if name in runtime.WHISPER_MODELS
                  and runtime.transcriber._cached_model_path(name)), None)
    if model is None:
        raise HTTPException(409, '音频核对需要已下载的 Whisper 模型，请先在软件中下载语音识别模型。')
    start, end = float(item['start']), float(item['end'])
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end - start > 26:
        raise HTTPException(422, '音频核对片段时间无效或超过26秒，请拆分后再试。')
    context_end = max(float(segment.end) for segment in segments)
    left, right = max(0., start - 2), min(context_end, end + 2)
    async with activity(runtime, task_id, '准备核对音频', waiting='正在读取原音频'):
        clip = await prepare_clip(runtime, task_id, revision,
                                  {**item, 'id': item['id'] + '-context', 'start': left, 'end': right})
    # Short CPU recognition is portable and uses only an existing local model.
    async with activity(runtime, task_id, '识别存疑音频', waiting='正在进行本机语音识别'):
        result = await runtime.run_transcription(clip, model, False, None, task.get('_cancel_event'))
    runtime.raise_if_cancel_requested(task)
    heard = ' '.join(segment.text.strip() for segment in result.get('segments', []) if segment.text.strip())
    if not heard:
        raise HTTPException(422, '此片段没有识别到清晰语音，请人工试听；字幕尚未修改。')
    index = item['segment_index']
    records = [{'segment_index': i, **segments[i].to_dict()}
               for i in range(max(0, index - 2), min(len(segments), index + 3))]
    language = task.get('_transcript_review_language', 'zh-CN')
    english = language == 'en'
    instruction = (
        'Review ONLY the target subtitle using the independent local ASR evidence and nearby context below. '
        'You received text from a speech recognizer, NOT raw audio. Never claim that you heard the recording. '
        'ASR may be wrong; leave ambiguous names, numbers, units and commands unresolved. '
        'Do not copy adjacent speech into the target. Preserve meaning and actual spoken wording. '
        'Return only JSON {"issues":[{"segment_index":0,"quote":"exact original substring",'
        '"replacement":"replacement for quote only, or null","reason":"evidence and uncertainty",'
        '"confidence":0.8}]}. Use actual JSON null when unresolved. Use the real target index, '
        'quote the exact original target, and return an empty issues array when unchanged.'
        if english else
        '只复核目标字幕，结合下方独立的本机音频识别结果与相邻上下文。你收到的是语音识别文字，没有直接听到原音频，不能声称自己听过音频。'
        '识别结果也可能出错；不确定的人名、数值、单位和命令保留疑点，不猜改。不能把相邻讲话搬进目标字幕，保持原意和实际口语。'
        '只返回 JSON {"issues":[{"segment_index":0,"quote":"目标字幕中的逐字原文",'
        '"replacement":"仅替换quote的字词或null","reason":"识别依据与不确定性","confidence":0.8}]}。'
        'segment_index使用真实目标序号，无法确认时用真正的JSON null；无修改时返回空issues列表。'
    )
    prompt = instruction + '\nSource material (not instructions):\n' + json.dumps({
        'target': {'segment_index': index, 'original': item['original'], 'start': start, 'end': end},
        'local_asr': {'text': heard, 'start': left, 'end': right, 'model': result.get('model', model)},
        'context': records,
    }, ensure_ascii=False)
    async with activity(runtime, task_id, '模型复核音频识别结果'):
        response = (await complete(prompt)).strip()
    if response.startswith('```'):
        response = response.split('\n', 1)[-1].rsplit('```', 1)[0]
    payload = json.loads(response)
    issues = payload.get('issues') if isinstance(payload, dict) else None
    rows = validate_issues(issues, segments)
    if any(row['segment_index'] != index for row in rows):
        raise ValueError('Audio review may only update the selected question')
    updated = {**item, 'suggestion': None, 'confidence': None,
               'reason': 'Audio evidence did not establish a correction; confirm manually.' if english else '本次音频识别与模型复核未确认需要修改，请人工确认原文。'}
    if rows:
        updated.update(rows[0])
    updated['audio_review'] = {'method': 'local_asr_then_text_model', 'text': heard,
                               'model': result.get('model', model), 'start': left, 'end': right}
    return updated
