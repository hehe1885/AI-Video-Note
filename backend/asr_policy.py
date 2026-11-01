"""Portable ASR intent, independent of GPU vendor, engine and precision.

Adapters translate this policy; numerical outputs need not be bit-identical.
Memory estimates include working buffers and headroom, not weights alone.
"""
from __future__ import annotations

POLICY_VERSION = 'portable-balanced-v1'
INDEPENDENT_TEXT_CONTEXT_MODELS = frozenset({'turbo', 'large-v3'})
_FP16_MIB = {'tiny':75,'base':143,'small':466,'medium':1463,
             'large-v3':2952,'turbo':1550,'belle-turbo-zh':1550}
_BUFFER_MIB = {'tiny':256,'base':384,'small':512,'medium':768,
               'large-v3':1024,'turbo':768,'belle-turbo-zh':768}


def decode_policy(model_name: str | None) -> dict:
    return {'beam_size':1, 'best_of':5,
            'condition_on_previous_text':model_name not in INDEPENDENT_TEXT_CONTEXT_MODELS,
            'temperature':(0., .2, .4, .6, .8, 1.), 'no_speech_threshold':.6,
            'repetition_penalty':1., 'no_repeat_ngram_size':0}


def cuda_decode_options(model_name: str | None) -> dict:
    return decode_policy(model_name)


def vulkan_decode_options(model_name: str | None) -> dict:
    policy=decode_policy(model_name)
    return {'beam_size':policy['beam_size'], 'best_of':policy['best_of'],
            'n_max_text_ctx':16384 if policy['condition_on_previous_text'] else 0,
            'temperature':policy['temperature'][0],
            'temperature_inc':policy['temperature'][1]-policy['temperature'][0],
            'no_speech_thold':policy['no_speech_threshold']}


def select_weight_precision(model_name: str, total_bytes: int | None,
                            free_bytes: int | None = None) -> str:
    """Prefer FP16 if the estimated working set fits; otherwise try 8-bit.

    Missing hardware information never implies low memory. An unknown model is
    not guessed. Eight-bit is a memory choice, not a claim of faster execution.
    """
    if model_name not in _FP16_MIB or not isinstance(total_bytes,int) or isinstance(total_bytes,bool) or total_bytes <= 0:
        return 'float16'
    available=total_bytes
    if isinstance(free_bytes,int) and not isinstance(free_bytes,bool) and free_bytes >= 0:
        available=min(available,free_bytes)
    estimate=(_FP16_MIB[model_name]+_BUFFER_MIB[model_name]+512)*1024**2
    return 'float16' if available >= estimate else 'int8'
