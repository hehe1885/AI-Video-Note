from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import asr_presets, whisper_asr


@pytest.mark.parametrize('model,history',[('base',True),('small',True),('medium',True),
    ('turbo',False),('large-v3',False),('belle-turbo-zh',True)])
def test_portable_policy_does_not_depend_on_precision(model,history):
    from backend.asr_policy import vulkan_decode_options
    for precision in ['float16','int8_float16','float32',None]:
        cuda=asr_presets.decoding_options(model,'cuda',precision)
        vk=vulkan_decode_options(model)
        assert cuda['condition_on_previous_text'] is history
        assert (vk['n_max_text_ctx']>0) is history
        assert cuda['beam_size']==vk['beam_size']==1
        assert cuda['best_of']==vk['best_of']==5
    options=asr_presets.decoding_options(model,'cuda','float16')
    options['no_repeat_ngram_size']=999
    assert asr_presets.decoding_options(model,'cuda','float16')['no_repeat_ngram_size']==0


def test_run_model_keeps_same_intent_after_precision_change_and_preserves_recovery():
    calls=[]
    class Model:
        model=SimpleNamespace(is_multilingual=True,compute_type='float16')
        def transcribe(self,path,**options):calls.append(options);return [],None
    model=Model()
    whisper_asr.WhisperTranscriber._run_model(model,Path('audio.wav'),None,'cuda',model_name='large-v3')
    model.model.compute_type='int8_float16'
    whisper_asr.WhisperTranscriber._run_model(model,Path('audio.wav'),None,'cuda',model_name='large-v3')
    whisper_asr.WhisperTranscriber._run_model(model,Path('audio.wav'),None,'cuda',model_name='base',recovery=True)
    assert [c['condition_on_previous_text'] for c in calls]==[False,False,False]
    assert [c['no_repeat_ngram_size'] for c in calls]==[0,0,10]
    assert all(c['beam_size']==1 and c['word_timestamps'] for c in calls)


def test_model_substitution_uses_loaded_identity_not_requested_identity(tmp_path,monkeypatch):
    calls=[]
    class Model:
        model=SimpleNamespace(is_multilingual=True,compute_type='int8_float32')
        def transcribe(self,path,**options):
            return [],SimpleNamespace(language='zh',language_probability=.9,duration=1.,duration_after_vad=1.)
    transcriber=whisper_asr.WhisperTranscriber(tmp_path)
    transcriber._models[('large-v3','cpu')]=Model()
    transcriber._actual_models[('large-v3','cpu')]='base'
    monkeypatch.setattr(whisper_asr,'decoding_options',lambda model,device,precision: calls.append((model,device,precision)) or {})
    result=transcriber._transcribe_sync(tmp_path/'audio.wav','large-v3',False,None)
    assert calls==[('base','cpu','int8_float32')]
    assert result['model']=='base' and result['requested_model']=='large-v3'


@pytest.mark.parametrize('model,memory,supported,wanted',[
    ('large-v3',4*1024**3,{'float16','int8_float16'},'int8_float16'),
    ('large-v3',6*1024**3,{'float16','int8_float16'},'float16'),
    ('large-v3',None,{'float16','int8_float16'},'float16'),
    ('large-v3',4*1024**3,{'float16'},'float16'),
    ('medium',4*1024**3,{'float16','int8_float16'},'float16'),
])
def test_low_memory_precision_requires_matching_model_and_capability(model,memory,supported,wanted):
    assert asr_presets.select_cuda_compute_type(model,memory,supported)==wanted


def test_unknown_hardware_information_does_not_guess_a_precision(monkeypatch):
    monkeypatch.setattr(asr_presets,'cuda_device_memory_bytes',lambda:None)
    assert asr_presets.preferred_cuda_compute_type('large-v3')=='float16'
