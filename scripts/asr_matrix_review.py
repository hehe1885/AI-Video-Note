"""Export auditable transcript differences; disagreement is not an error rate."""
from __future__ import annotations

import argparse
from collections import defaultdict
import difflib
import json
from pathlib import Path
import re
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1] / 'work/asr-seven-20261016-v2'


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())



def repeated_segment_runs(segments):
    """Flag adjacent identical normalized segments; review against actual audio."""
    runs = []
    current = None
    def flush():
        if current and current['count'] >= 3:
            runs.append(dict(current))
    for segment in segments:
        key = normalized(segment['text'])
        start, end = segment['start'], segment['end']
        if current and len(key) >= 4 and key == current['normalized_text'] and 0 <= start-current['end'] <= 2:
            current['count'] += 1
            current['end'] = end
        else:
            flush()
            current = {'normalized_text':key, 'text':segment['text'], 'count':1,
                       'start':start, 'end':end} if len(key) >= 4 else None
    flush()
    return runs


def generate():
    groups = defaultdict(dict)
    failures = []
    repeated = []
    paused = []
    for path in (ROOT / 'results').glob('*.json'):
        if '.heartbeat.' in path.name or '.previous-' in path.name:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('status') == 'paused':
            paused.append((path.stem, data.get('pause_reason', '用户暂停；下次重跑本项')))
            continue
        if data.get('status') != 'completed':
            failures.append((path.stem, data.get('error', '未完成')))
            continue
        runs = repeated_segment_runs(data.get('segments', []))
        if runs:
            repeated.append({'result':path.name, 'model':data['model'], 'variant':data['variant'], 'key':data['key'], 'runs':runs})
        groups[(data['model'], data['key'])][data['variant']] = data
    target = ROOT / 'review'
    target.mkdir(exist_ok=True)
    summary = ['# 七视频六模型音频参数对照', '',
               '本报告随已完成项更新。只对照同一模型、同一音频的结果，不将模型之间的一致性视为正确答案。', '',
               '识别不读取平台字幕；统一不带标题提示。独立稿件和画面参考另行保存。baseline 指引擎解码参数基线，并非完整网页任务含标题提示的结果。', '',
               '字符差异率是候选与基线的不一致程度，不是 CER/WER。仅统一 Unicode、大小写并去除标点空白，未统一简繁体；简繁差异会抬高该数值。数字变化、长循环、时间轴异常都需要回听。', '',
               '| 模型 | 视频 | 范围 | 方案 | GPU 推理秒数 | RTF | 字符数 | 循环段 | 与基线字符差异率 | 数字序列变化 |',
               '|---|---|---|---|---:|---:|---:|---:|---:|---|']
    for (model, key), variants in sorted(groups.items()):
        baseline = variants.get('baseline')
        base_text = normalized(baseline['text']) if baseline else None
        detail = [f'# {model} / {key}', '',
                  '来源范围见每项说明。以下结果保留原始识别文字，不自动统一术语、繁简体或数字。', '']
        for name in ['baseline', 'independent', 'beam5']:
            data = variants.get(name)
            if not data:
                continue
            text = normalized(data['text'])
            disagreement = 1-difflib.SequenceMatcher(None, base_text, text, autojunk=False).ratio() if base_text is not None else None
            numbers_changed = re.findall(r'\d+(?:\.\d+)?', baseline['text']) != re.findall(r'\d+(?:\.\d+)?', data['text']) if baseline else None
            ratio = f'{disagreement:.2%}' if disagreement is not None else '待基线'
            duration = data['duration']
            scope = f'{data["selection_start"]}–{data["selection_end"]}s' if 'selection_start' in data else '全片'
            summary.append(f'| {model} | {key} | {scope} | {name} | {data["transcribe_seconds"]:.1f} | {data["transcribe_seconds"]/duration:.3f} | {len(text)} | {data["loop_segments"]} | {ratio} | {numbers_changed} |')
            detail += [f'## {name}', '', f'实际设备：{data["device"]}；解码候选覆盖：{data["options"]}。', '',
                       f'输入范围：{scope}；时长 {duration:.2f} 秒。片段时间轴为片段本地时间，回到原片需加上 selection_start。', '',
                       f'输入音频 SHA-256：`{data["audio_sha256"]}`；GGML 模型 SHA-256：`{data["model_sha256"]}`。', '',
                       '| 开始秒 | 结束秒 | 原始识别文字 |', '|---:|---:|---|']
            for segment in data['segments']:
                detail.append(f'| {segment["start"]:.2f} | {segment["end"]:.2f} | {segment["text"].replace("|", "&#124;").replace(chr(10), " ")} |')
            detail.append('')
        (target / f'{model}--{key}.md').write_text('\n'.join(detail)+'\n', encoding='utf-8')
    summary += ['', '## 跨字幕段相同文本重复（需核对音频）', '',
                '原结果的循环段指标仅检查单段内部，不能覆盖跨段重复。下面是额外诊断，真实重复讲话也可能触发；不自动删除字幕。', '']
    for item in repeated:
        for run in item['runs']:
            summary.append(f"- {item['result']}: {run['start']:.2f}–{run['end']:.2f}s，连续{run['count']}段：{run['text']}")
    if not repeated:
        summary.append('暂无满足连续三段且规范化文本至少四字的重复。')
    report = {'metric':'adjacent identical normalized segment runs; diagnostic, not automatic error', 'results':repeated}
    report_tmp = ROOT/'cross-segment-repetition.json.tmp'
    report_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    report_tmp.replace(ROOT/'cross-segment-repetition.json')
    summary += ['', '## 失败项', '']
    summary += [f'- {key}: {error[-300:].replace(chr(10), " ")}' for key, error in failures] or ['暂无已记录的失败项；待执行项不计入成功。']
    if paused:
        summary += ['', '## 用户暂停项（不计为模型失败）', '']
        summary += [f'- {key}: {reason}' for key,reason in paused]
    summary += ['', '## 采用规则', '',
                '本工具不会自动选择生产参数。只有差异、耗时与启发式诊断，不能证明哪一版忠于音频；候选需要逐例音频或可靠参考核对。']
    temporary = ROOT / 'matrix-review.md.tmp'
    temporary.write_text('\n'.join(summary)+'\n', encoding='utf-8')
    temporary.replace(ROOT / 'matrix-review.md')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    while True:
        generate()
        if not args.watch:
            break
        state = json.loads((ROOT/'status.json').read_text(encoding='utf-8'))
        if state.get('phase', '').startswith(('矩阵执行结束', '矩阵完成', '矩阵有未完成', '已暂停')):
            break
        time.sleep(30)
