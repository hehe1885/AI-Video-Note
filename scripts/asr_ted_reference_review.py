"""Compare independent audio ASR with TED's public transcript as a weak reference.

The transcript is never provided to recognition or used to train weights.
Editorial changes, uploads and normalization can all affect this disagreement.
"""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import urllib.request

ROOT = Path(__file__).resolve().parents[1]/'work/asr-seven-20261016-v2'
TALKS = {'BV1zj411K7zL': 'lydia_machova_the_secrets_of_learning_a_new_language',
         'BV1DXKS6rEYq': 'linus_torvalds_the_mind_behind_linux'}


def words(text):
    text = unicodedata.normalize('NFKC', text).lower().replace('’', "'")
    text = re.sub(r'[\[(](?:music|applause|laughter|laughs)[\])]', ' ', text)
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?:\.\d+)?", text)


def distance(a, b):
    previous = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(x!=y)))
        previous = current
    return previous[-1]


def timed_reference(ref):
    output = []
    for i, cue in enumerate(ref['cues']):
        tokens = words(re.sub(r'^(?:Chris Anderson|Linus Torvalds|CA|LT):\s*', '', cue['text']))
        start = cue['time']/1000
        end = ref['cues'][i+1]['time']/1000 if i+1 < len(ref['cues']) else start+3
        span = min(15, max(.1, end-start))
        output.extend((token,start+(j+.5)/len(tokens)*span) for j,token in enumerate(tokens))
    return output


def selected_words(ref, result):
    if 'selection_start' not in result:
        return [token for token,_ in timed_reference(ref)], words(result['text']), None
    # Use lexical matches only to estimate time offset; the answers remain TED's text.
    mapping = json.loads((ROOT/'references'/(result['video']+'-time-map.json')).read_text(encoding='utf-8'))
    offset = mapping['median_bilibili_minus_ted_seconds']
    if mapping['iqr_seconds'] > 2:
        raise ValueError('TED alignment is too uncertain for excerpt scoring')
    padding = 5
    lo, hi = result['selection_start']+padding-offset, result['selection_end']-padding-offset
    a = [token for token,stamp in timed_reference(ref) if lo <= stamp < hi]
    b = []
    for segment in result['segments']:
        tokens = words(segment['text'])
        for i, token in enumerate(tokens):
            stamp = segment['start']+(i+.5)/len(tokens)*(segment['end']-segment['start'])
            if padding <= stamp < result['duration']-padding:
                b.append(token)
    return a,b,{'source_start':result['selection_start'],'source_end':result['selection_end'],
                 'excluded_edge_seconds':padding,'estimated_bilibili_minus_ted_seconds':offset,
                 'alignment_iqr_seconds':mapping['iqr_seconds'],
                 'limit':'Word positions are interpolated within cues/segments; boundary scoring remains approximate.'}


def reference(video, slug):
    path = ROOT/'references'/(video+'-ted-reference.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    url = f'https://www.ted.com/talks/{slug}?language=en&view=transcript'
    request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode('utf-8')
    match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError('TED did not expose public transcript data')
    page = json.loads(match.group(1))['props']['pageProps']
    translation = page['transcriptData']['translation']
    if translation['language']['internalLanguageCode'] != 'en':
        raise ValueError('Reference is not English')
    cues = [cue for paragraph in translation['paragraphs'] for cue in paragraph['cues']]
    data = {'video':video,'url':url,'title':page['videoData']['title'],
            'ted_video_id':page['videoData']['id'],'duration':page['videoData']['duration'],
            'language':'en','text':' '.join(c['text'] for c in cues),'cues':cues,
            'reference_kind':'TED public editorial transcript; not human audio ground truth'}
    data['text_sha256'] = hashlib.sha256(data['text'].encode()).hexdigest()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def main():
    rows = []
    references = {video:reference(video,slug) for video,slug in TALKS.items()}
    for path in sorted((ROOT/'results').glob('*.json')):
        if '.heartbeat.' in path.name or '.previous-' in path.name:
            continue
        result = json.loads(path.read_text(encoding='utf-8'))
        if result.get('status') != 'completed' or result.get('video') not in references:
            continue
        ref = references[result['video']]
        a,b,selection = selected_words(ref,result)
        error = distance(a,b)
        matched = sum(block.size for block in difflib.SequenceMatcher(None,a,b,autojunk=False).get_matching_blocks())
        rows.append({'model':result['model'],'video':result['video'],'variant':result['variant'],
                     'reference_words':len(a),'asr_words':len(b),'word_edits':error,
                     'reference_disagreement_wer':error/len(a), 'matched_reference_fraction':matched/len(a),
                     'reference_sha256':ref['text_sha256'],'source_url':ref['url'],
                     'normalization':'unicode/case/punctuation/event tags; source cue speaker labels removed',
                     'selection':selection,
                     'device':result['device'],'seconds':result['transcribe_seconds']})
    (ROOT/'ted-reference-review.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    report = ['# TED 原站稿件独立对照', '',
              '这些参考在音频识别完成后独立获取，从未输入 ASR，不用于权重训练。', '',
              '指标为与 TED 公开稿件的词级编辑差异率，使用大小写/标点及非口述音乐掌声标签归一化；去除稿件各 cue 开头未被读出的说话人标签。保留数字形式与词形差异。稿件可能经过编辑、上传视频可能剪辑，因此不称为人工音频真值 WER 或实际准确率。', '',
              '先检查来源是否匹配、参考词数覆盖及局部差异，再比较同视频同模型的候选；不能把这个分数推广到中文视频。', '',
              '固定五分钟片段先用原全片的词匹配估计 TED/B站时间差；比较时排除片段首尾各五秒，以减少裁切边界影响。cue 内的词位置为插值估计，因此片段分数仍含边界误差，不能与全片旧分数直接比较。', '',
              '| 模型 | 视频 | 方案 | 参考词数 | 识别词数 | 参考差异率 | 匹配词覆盖 | GPU 秒数 |',
              '|---|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        report.append(f'| {row["model"]} | {row["video"]} | {row["variant"]} | {row["reference_words"]} | {row["asr_words"]} | {row["reference_disagreement_wer"]:.2%} | {row["matched_reference_fraction"]:.2%} | {row["seconds"]:.1f} |')
    report += ['', '参考来源：']
    report += [f'- [{ref["title"]}]({ref["url"]})' for ref in references.values()]
    (ROOT/'ted-reference-review.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    for row in rows:
        print(row['model'],row['video'],row['variant'],f'{row["reference_disagreement_wer"]:.2%}',row['reference_words'],row['asr_words'])


if __name__ == '__main__':
    main()
