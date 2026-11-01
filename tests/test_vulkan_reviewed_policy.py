"""Reviewed native policy and silence handling must preserve real audio."""
from types import SimpleNamespace
import numpy as np
import pytest
from backend import whisper_vulkan as v
from backend import whisper_vulkan_native as native

@pytest.fixture
def native_run(monkeypatch,tmp_path):
    import faster_whisper.audio
    state={'samples':np.ones(16000,dtype=np.float32),'texts':[], 'calls':[], 'freed':[],'progress':[], 'models':[], 'initializations':[], 'init_results':[], 'devices':[{'index':0,'name':'AMD Radeon RX 6600'}]}
    def full(ctx,params,samples,count):
        state['calls'].append((params.n_max_text_ctx,params.strategy,count))
        return 0
    def initialize(path,params):
        state['initializations'].append(params.use_gpu)
        return state['init_results'].pop(0) if state['init_results'] else 1
    def model_file(*args,**kwargs):
        state['models'].append(kwargs.get('precision','float16'))
        return tmp_path/'model.bin'
    lib=SimpleNamespace(
        whisper_context_default_params=lambda:SimpleNamespace(),
        whisper_init_from_file_with_params=initialize,
        whisper_full_default_params=lambda strategy:native.Params(n_max_text_ctx=16384),
        whisper_full=full,whisper_free=lambda ctx:state['freed'].append(ctx),
        whisper_full_n_segments=lambda ctx:len(state['texts']),
        whisper_full_get_segment_text=lambda ctx,i:state['texts'][i].encode('utf-8'),
        whisper_full_get_segment_t0=lambda ctx,i:i*100,
        whisper_full_get_segment_t1=lambda ctx,i:(i+1)*100,
        whisper_full_lang_id=lambda ctx:0,whisper_lang_str=lambda i:b'zh')
    monkeypatch.setattr(v,'ensure_runtime',lambda *args:tmp_path)
    monkeypatch.setattr(v,'ensure_model',model_file)
    monkeypatch.setattr(native,'Native',lambda *args:SimpleNamespace(
        devices=lambda:state['devices'],lib=lib))
    monkeypatch.setattr(faster_whisper.audio,'decode_audio',lambda *args,**kwargs:state['samples'])
    def run(model='base',**kwargs):
        return v._transcribe(tmp_path,tmp_path/'audio.wav',model,'amd',None,None,
            lambda done,total:state['progress'].append((done,total)),allow_cpu_fallback=False,**kwargs)
    return state,run

@pytest.mark.parametrize('model,budget',[('base',16384),('small',16384),('medium',16384),
    ('turbo',0),('large-v3',0),('belle-turbo-zh',16384)])
def test_application_passes_reviewed_history_budget_to_native(native_run,model,budget):
    state,run=native_run
    result=run(model)
    assert state['calls']==[(budget,0,16000)]
    assert result['decode_options']['n_max_text_ctx']==budget
    assert result['decode_policy']=='reviewed_application'


def test_explicit_empty_mapping_still_reproduces_native_baseline(native_run):
    state,run=native_run
    result=run('large-v3',decode_options={})
    assert state['calls']==[(16384,0,16000)]
    assert result['decode_policy']=='explicit_override'


def test_digital_zero_cannot_generate_hallucinated_words(native_run):
    state,run=native_run
    state['samples']=np.zeros(16000,dtype=np.float32)
    state['texts']=['Thank you.']
    result=run()
    assert state['calls']==[]
    assert result['text']=='' and result['segments']==[]
    assert result['silence_guard']=='exact_digital_zero'
    assert result['device'].startswith('Vulkan')
    assert state['progress']==[(1.0,1.0)] and state['freed']==[1]


def test_quiet_nonzero_audio_and_real_repeated_words_are_preserved(native_run):
    state,run=native_run
    state['samples']=np.full(16000,1e-20,dtype=np.float32)
    state['texts']=['这个用法能让你最快入门']*6
    result=run('turbo')
    assert len(state['calls'])==1 and result['silence_guard'] is None
    assert len(result['segments'])==6
    assert result['text'].count('这个用法能让你最快入门')==6


def test_only_reserved_blank_marker_is_removed(native_run):
    state,run=native_run
    state['texts']=['[BLANK_AUDIO]','观众说 blank audio','[blank_audio]']
    result=run()
    assert [s.text for s in result['segments']]==['观众说 blank audio']


def test_low_free_memory_uses_q8_without_changing_decode_policy(native_run):
    state,run=native_run
    state['devices'][0].update(memory_total=8*1024**3,memory_free=1024**3)
    result=run('large-v3')
    assert state['models']==['q8_0'] and state['initializations']==[True]
    assert result['compute_type']=='q8_0' and result['decode_options']['n_max_text_ctx']==0
    assert result['gpu_memory_total']==8*1024**3


def test_failed_fp16_retries_q8_on_same_gpu(native_run):
    state,run=native_run
    state['init_results']=[None,1]
    result=run('large-v3')
    assert state['models']==['float16','q8_0']
    assert state['initializations']==[True,True]
    assert result['compute_type']=='q8_0' and result['fallback_note'] is None
    assert result['precision_reason']=='fp16_gpu_allocation_failed'
