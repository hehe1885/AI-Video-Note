"""GPU-only silence/real-repetition controls, serialized after the full matrix."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from scripts.asr_seven_matrix import ROOT, MODELS, VARIANTS, digest, read, save, progress


def prepare():
    import numpy as np
    from faster_whisper.audio import decode_audio
    directory = ROOT/'controls'
    directory.mkdir(exist_ok=True)
    state = read(ROOT/'status.json')
    source = state['pages'][0]
    # Repeating the actual waveform creates real repetition, not an ASR hallucination.
    samples = decode_audio(source['audio'], sampling_rate=16000)
    speech = samples[70*16000:80*16000]
    if len(speech) != 10*16000:
        raise ValueError('Missing source control interval')
    signals = {'silence30': np.zeros(30*16000, dtype=np.float32),
               'real-repeat60': np.tile(speech, 6)}
    controls = []
    for name, signal in signals.items():
        path = directory/(name+'.wav')
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes((signal.clip(-1,1)*32767).astype('<i2').tobytes())
        controls.append({'key': name, 'video': 'control-'+name, 'page': 1,
                         'audio': str(path), 'audio_sha256': digest(path), 'duration': len(signal)/16000,
                         'source_audio_sha256': source['audio_sha256'],
                         'construction': 'digital zero' if name=='silence30' else 'source 70–80s repeated six times'})
    save(directory/'plan.json', {'controls': controls, 'variants': VARIANTS, 'models': MODELS,
                                'purpose': 'Silence must not hallucinate; real repetition must not be automatically deleted.'})
    return controls


def main(wait):
    if wait:
        while True:
            state = read(ROOT/'status.json', {})
            phase = state.get('phase', '')
            if phase.startswith('矩阵完成'):
                break
            if phase.startswith(('矩阵有未完成', '已暂停')):
                raise RuntimeError('Matrix requires attention before controls can run')
            time.sleep(30)
    controls = prepare()
    manifest = read(ROOT/'manifest.json')
    directory = ROOT/'controls'
    failures = []
    state = read(ROOT/'status.json')
    for model in MODELS:
        for control in controls:
            for variant, options in VARIANTS.items():
                key = f'{model}--{control["key"]}--{variant}'
                output = directory/(key+'.json')
                job = {**control, 'model': model, 'variant': variant, 'options': options,
                       'model_sha256': manifest['models'][model]['sha256'],
                       'sources': manifest['sources'], 'output': str(output)}
                identity = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
                job['identity'] = identity
                old = read(output, {})
                if old.get('identity') == identity and old.get('status') == 'completed':
                    continue
                jobfile = directory/(key+'.job.json')
                save(jobfile, job)
                save(directory/'status.json', {'phase': 'running', 'current': key})
                progress(state, phase='静音与真实重复保护验证', current=key,
                         heartbeat='全片矩阵完成；追加保护对照不计入 126 项')
                with (directory/(key+'.log')).open('w', encoding='utf-8') as log:
                    child = subprocess.run([sys.executable,'-X','utf8',str(PROJECT/'scripts/asr_seven_matrix.py'),
                                            '--worker',str(jobfile)], cwd=PROJECT,
                                           stdout=log, stderr=subprocess.STDOUT, timeout=1800)
                if child.returncode:
                    failures.append(key)
                print(key, child.returncode, flush=True)
    save(directory/'status.json', {'phase': 'completed' if not failures else 'needs_attention', 'failures': failures})
    progress(state, phase='全片矩阵与保护对照执行结束，待逐例核对' if not failures else '保护对照出现失败，需要诊断修复',
             current=f'保护对照失败 {len(failures)} 项', heartbeat='识别执行结束；尚未自动更改生产参数')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait-for-matrix', action='store_true')
    main(parser.parse_args().wait_for_matrix)
