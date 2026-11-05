"""Show independent visual-caption checks without pretending to measure audio CER."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'work/asr-seven-20261016-v2'


def main():
    plan = json.loads((ROOT/'references/visual-anchors-reviewed.json').read_text(encoding='utf-8'))
    results = []
    for path in (ROOT/'results').glob('*.json'):
        if '.heartbeat.' in path.name or '.previous-' in path.name:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('status') == 'completed':
            results.append(data)
    text = ['# 画面字幕弱参考逐例核对', '',
            '画面抽样在每个视频的 10%、30%、50%、70%、90% 位置。只录入清晰的口述字幕，其余跳过。', '',
            '下表不是人工音频真值，不计算 CER/WER；字幕可能有错、时间可能偏移。识别窗口会保留相邻句，不能因为多几个字就判错。所有模型均未读取这些参考。', '']
    for anchor in plan:
        if not anchor['reference_text']:
            continue
        video, stamp = anchor['video'], anchor['time']
        text += [f'## {video} / {stamp:.1f}s', '',
                 f'独立画面字幕：{anchor["reference_text"]}', '',
                 '| 模型 | 方案 | 附近识别原文（±3秒内相交的分段） |', '|---|---|---|']
        for result in sorted(results, key=lambda r: (r['model'], r['variant'])):
            if result['video'] != video:
                continue
            local_stamp = stamp-result.get('selection_start', 0)
            if 'selection_start' in result and not 0 <= local_stamp < result['duration']:
                continue
            window = ' '.join(s['text'] for s in result['segments']
                              if s['start'] < local_stamp+3 and s['end'] > local_stamp-3)
            window = window.replace('|', '&#124;').replace('\n', ' ')
            scope = f" ({result['selection_start']}–{result['selection_end']}s)" if 'selection_start' in result else ''
            text.append(f'| {result["model"]} | {result["variant"]}{scope} | {window} |')
        text.append('')
    (ROOT/'visual-reference-review.md').write_text('\n'.join(text)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
