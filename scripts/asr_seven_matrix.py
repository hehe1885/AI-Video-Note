"""Resumable full-audio RX 6600 experiment. Never silently runs inference on CPU.

Run with the project's Python. A STOP file pauses between cells; each cell has
its own process so native failures cannot corrupt another model's results.
Platform subtitle APIs are deliberately never called here.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
ROOT = PROJECT / 'work/asr-seven-20261016-v2'
CACHE = PROJECT / 'workspace/_model_cache'
GUIDE = PROJECT / '语音识别模型优化与扩展评测指南.md'
VIDEOS = ['BV18a411r7mt', 'BV1qsroBoEns', 'BV13sYreCEn2', 'BV1zj411K7zL',
          'BV1DXKS6rEYq', 'BV1PXX6BREaZ', 'BV1Wa411B73y']
MODELS = ['base', 'small', 'medium', 'turbo', 'large-v3', 'belle-turbo-zh']
VARIANTS = {'baseline': {}, 'independent': {'n_max_text_ctx': 0}, 'beam5': {'beam_size': 5}}
BEGIN = '<!-- ASR-SEVEN-20261016:START -->'
END = '<!-- ASR-SEVEN-20261016:END -->'


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def progress(state, **fields):
    state.update(fields, updated=now())
    save(ROOT / 'status.json', state)
    rows = []
    results = [read(p) for p in (ROOT / 'results').glob('*.json') if '.previous-' not in p.name and '.heartbeat.' not in p.name]
    for video in VIDEOS:
        pages = [p for p in state.get('pages', []) if p['video'] == video]
        counts = []
        for variant in VARIANTS:
            completed = sum(r.get('status') == 'completed' and r.get('video') == video
                            and r.get('variant') == variant for r in results)
            counts.append(f'{completed}/{6 * len(pages)}' if pages else '0/待确认')
        prepared = sum(bool(p.get('audio_sha256')) for p in pages)
        rows.append(f'| {video} | {prepared}/{len(pages)} P | ' + ' | '.join(counts) + ' |')
    completed = sum(r.get('status') == 'completed' for r in results)
    failed = sum(r.get('status') == 'failed' for r in results)
    text = f'''{BEGIN}
## 2026-10-16 新增七视频全片评测：{state.get('phase', '准备中')}

更新时间：{state['updated']}（北京时间）。本节为新实验；下方旧正文的 CUDA 和完成结论属于历史实验。

- 用户范围：六个模型 × 全部七个视频 × 所有分 P 的完整音频。
- 实际设备：AMD Radeon RX 6600，whisper.cpp 1.8.5 / Vulkan；模型使用 GGML 文件，不能把旧 CT2 权重视为同一格式。
- GPU 故障先定位修复；必要时 CPU 只作诊断，不替代 GPU 矩阵。实验工人禁止静默 CPU 回退。
- 三方案（修订版 v2）：baseline 为当前 greedy / n_max_text_ctx=16384；independent 只切换 n_max_text_ctx=0；beam5 只切换 beam search=5。no_context=True 在新建上下文时只清理调用前历史，不能代表窗口间关闭前文。应用默认值在审阅前保持原样。
- 不下载或读取平台字幕作为输入。音频结果独立保存；尚无人工逐字真值，不能宣称 CER/WER 或准确率提升比例。
- 模型：base、small、medium、turbo、large-v3、belle-turbo-zh。仅 CPU 的 Paraformer 不纳入本次六模型矩阵。
- 进度：完成 {completed} 项，失败 {failed} 项；计划 {18 * len(state.get('pages', []))} 项（分 P 清单尚未完成时为暂定）。
- 当前：{state.get('current', '初始化')}。
- 心跳：{state.get('heartbeat', '尚未开始推理')}。
- 数据与日志：`{ROOT}`；`status.json`、`manifest.json`、`results/`、`logs/`。
- 续跑：在项目目录执行 `.venv\\Scripts\\python.exe -X utf8 scripts\\asr_seven_matrix.py`；已完成且身份一致的结果复用。创建实验目录下的 `STOP` 文件在当前项结束后暂停；删除后可续跑。

| 视频 | 音频准备 | 基线（模型×P） | 独立窗口候选 | beam5 候选 |
|---|---|---|---|---|
{chr(10).join(rows)}

最近错误：{state.get('last_error', '无')}。

本轮尚未据此选择生产参数或训练权重。全部结果需结合音频逐例核对，置信度、字幕长度与循环诊断不等于准确率。
{END}'''
    original = GUIDE.read_text(encoding='utf-8')
    if BEGIN in original:
        start, rest = original.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        original = start + text + tail
    else:
        original = text + '\n\n' + original
    temporary = GUIDE.with_suffix('.md.tmp')
    temporary.write_text(original, encoding='utf-8')
    temporary.replace(GUIDE)


def configure_belle():
    from backend import whisper_vulkan as v
    v.MODELS['belle-turbo-zh'] = 'ggml-model.bin'


def worker(job_path):
    from backend import whisper_vulkan as v
    from backend.asr_recovery import has_long_loop
    configure_belle()
    job = read(job_path)
    output = Path(job['output'])
    heartbeat = output.with_suffix('.heartbeat.json')
    started = time.monotonic()
    result = dict(job)
    try:
        def beat(done, duration):
            save(heartbeat, {'done_seconds': done, 'duration': duration, 'updated': now()})
        data = v._transcribe(CACHE, Path(job['audio']), job['model'], 'amd', None, None, beat,
                             allow_cpu_fallback=False, decode_options=VARIANTS[job['variant']])
        if not data['device'].startswith('Vulkan') or data.get('fallback_note'):
            raise RuntimeError(f'GPU-only identity violation: {data["device"]}')
        data['segments'] = [dataclasses.asdict(s) for s in data['segments']]
        result.update(data, status='completed', loop_segments=sum(has_long_loop(s['text']) for s in data['segments']))
    except Exception:
        result.update(status='failed', error=traceback.format_exc())
    result.update(finished=now(), wall_seconds=round(time.monotonic()-started, 2))
    save(output, result)
    return 0 if result['status'] == 'completed' else 1


def prepare(state):
    import asyncio
    from backend.video_processor import VideoProcessor, bilibili_page_url
    from backend.whisper_vulkan import ensure_model, checked_download, model_path, cache_status
    from backend.download import report_downloads
    configure_belle()
    processor = VideoProcessor(ROOT / 'media')
    for video in VIDEOS:
        progress(state, phase='准备完整音频', current=video)
        try:
            info = asyncio.run(processor.bilibili_video_pages(f'https://www.bilibili.com/video/{video}/'))
            for page in info.pages:
                key = f'{video}-p{page.page}'
                existing = next((p for p in state.setdefault('pages', []) if p['key'] == key), None)
                if existing and Path(existing.get('audio', '')).is_file() and existing.get('audio_sha256'):
                    continue
                item = existing or {'video': video, 'key': key, 'page': page.page, 'title': info.title,
                                    'part': page.part, 'duration': page.duration}
                if not existing:
                    state['pages'].append(item)
                progress(state, current=f'下载完整音频 {key}')
                url = bilibili_page_url(f'https://www.bilibili.com/video/{video}/', page.page)
                target = processor._task_dir(key) / 'audio.m4a'
                # Direct media API requests only: no subtitle extraction or transcript reuse.
                if not target.is_file():
                    processor._download_bilibili_media_via_api(url, target, None, 'audio', None, [])
                import av
                with av.open(str(target)) as container:
                    if not container.streams.audio:
                        raise ValueError('下载素材没有音轨')
                    next(container.decode(audio=0))
                item.update(audio=str(target), audio_sha256=digest(target))
                progress(state)
        except Exception as error:
            state.setdefault('preparation_errors', {})[video] = str(error)
            progress(state, last_error=f'{video}: {error}')
    last_report = [0.]
    def download_progress(target, received, total):
        if time.monotonic() - last_report[0] > 10:
            last_report[0] = time.monotonic()
            progress(state, heartbeat=f'模型下载 {Path(target).name}: {received/2**20:.1f} / {(total or 0)/2**20:.1f} MiB')
    for model in MODELS:
        if (ROOT / 'STOP').exists():
            return False
        progress(state, phase='准备 Vulkan 模型', current=model)
        try:
            with report_downloads(download_progress):
                path = ensure_model(CACHE, model)
            state.setdefault('models', {})[model] = {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
            progress(state)
        except Exception as error:
            state.setdefault('preparation_errors', {})[model] = str(error)
            progress(state, last_error=f'{model}: {error}')
    return True


def main():
    from filelock import FileLock
    from backend.whisper_vulkan import RUNTIME_VERSION, RUNTIME_SHA256
    ROOT.mkdir(parents=True, exist_ok=True)
    for folder in ['results', 'logs', 'jobs']:
        (ROOT / folder).mkdir(exist_ok=True)
    with FileLock(str(ROOT / 'run.lock'), timeout=0):
        state = read(ROOT / 'status.json', {'started': now(), 'pages': [], 'models': {}})
        if not state['pages']:
            prior = read(PROJECT / 'work/asr-seven-20261016/status.json', {})
            state['pages'] = prior.get('pages', [])
            state['models'] = prior.get('models', {})
        state['pid'] = os.getpid()
        if not prepare(state):
            progress(state, phase='已暂停', current='STOP 文件存在')
            return
        manifest = {'videos': VIDEOS, 'models': state.get('models'), 'pages': state['pages'],
                    'variants': VARIANTS, 'runtime_version': RUNTIME_VERSION, 'runtime_sha256': RUNTIME_SHA256,
                    'sources': {p: digest(PROJECT / p) for p in ['backend/whisper_vulkan.py', 'backend/whisper_vulkan_native.py', 'scripts/asr_seven_matrix.py']}}
        save(ROOT / 'manifest.json', manifest)
        for model in MODELS:
            if model not in state.get('models', {}):
                continue
            for page in state['pages']:
                if not page.get('audio_sha256'):
                    continue
                for variant in VARIANTS:
                    if (ROOT / 'STOP').exists():
                        progress(state, phase='已暂停', current='STOP 文件存在')
                        return
                    key = f'{model}--{page["key"]}--{variant}'
                    output = ROOT / 'results' / (key + '.json')
                    job = {**page, 'model': model, 'variant': variant, 'options': VARIANTS[variant],
                           'model_sha256': state['models'][model]['sha256'], 'sources': manifest['sources'],
                           'runtime_sha256': RUNTIME_SHA256, 'output': str(output)}
                    identity = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
                    job['identity'] = identity
                    old = read(output, {})
                    if old.get('status') == 'completed' and old.get('identity') == identity:
                        continue
                    if output.exists():
                        output.rename(output.with_name(output.stem + f'.previous-{int(time.time())}.json'))
                    job_path = ROOT / 'jobs' / (key + '.json')
                    save(job_path, job)
                    progress(state, phase='完整音频 GPU 识别', current=key, heartbeat='启动隔离工人')
                    with (ROOT / 'logs' / (key + '.log')).open('w', encoding='utf-8') as log:
                        child = subprocess.Popen([sys.executable, '-X', 'utf8', str(Path(__file__)), '--worker', str(job_path)],
                                                 cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
                        state['worker_pid'] = child.pid
                        progress(state)
                        started = time.monotonic()
                        while child.poll() is None:
                            time.sleep(5)
                            beat = read(output.with_suffix('.heartbeat.json'), {})
                            progress(state, heartbeat=f'{beat.get("done_seconds", 0):.1f}/{beat.get("duration", page["duration"]):.1f} 秒；本项已运行 {time.monotonic()-started:.0f} 秒')
                            if time.monotonic()-started > 8*3600:
                                child.kill()
                                child.wait()
                                break
                    if not output.exists():
                        save(output, {**job, 'status': 'failed', 'error': f'Native process exit {child.returncode}; see logs/{key}.log'})
                    result = read(output)
                    if result['status'] != 'completed':
                        progress(state, last_error=f'{key}: {result.get("error", "失败")[-500:]}')
        results = [read(p) for p in (ROOT / 'results').glob('*.json')
                   if '.previous-' not in p.name and '.heartbeat.' not in p.name]
        completed = sum(r.get('status') == 'completed' for r in results)
        expected = len(MODELS) * len(VARIANTS) * len(state['pages'])
        finished = completed == expected and len({p['video'] for p in state['pages']}) == len(VIDEOS)
        progress(state, phase='矩阵完成，待音频核对与参数选择' if finished else '矩阵有未完成项，需要诊断修复',
                 current=f'{completed}/{expected} 项成功；检查逐例差异及失败日志')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(worker(args.worker))
    main()
