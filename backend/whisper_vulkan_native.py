"""Pinned whisper.cpp 1.8.5 ABI. Loaded only inside the isolated ASR worker."""
import ctypes as c
import os
from pathlib import Path

class Heads(c.Structure):
    _fields_ = [('n_heads', c.c_size_t), ('heads', c.c_void_p)]
class Context(c.Structure):
    _fields_ = [('use_gpu', c.c_bool), ('flash_attn', c.c_bool), ('gpu_device', c.c_int), ('dtw_token_timestamps', c.c_bool), ('dtw_aheads_preset', c.c_int), ('dtw_n_top', c.c_int), ('dtw_aheads', Heads), ('dtw_mem_size', c.c_size_t)]
class Greedy(c.Structure):
    _fields_ = [('best_of', c.c_int)]
class Beam(c.Structure):
    _fields_ = [('beam_size', c.c_int), ('patience', c.c_float)]
class Vad(c.Structure):
    _fields_ = [('threshold', c.c_float), ('min_speech_duration_ms', c.c_int), ('min_silence_duration_ms', c.c_int), ('max_speech_duration_s', c.c_float), ('speech_pad_ms', c.c_int), ('samples_overlap', c.c_float)]
class Params(c.Structure):
    _fields_ = (
        [(n, c.c_int) for n in ['strategy', 'n_threads', 'n_max_text_ctx', 'offset_ms', 'duration_ms']]
        + [(n, c.c_bool) for n in ['translate', 'no_context', 'no_timestamps', 'single_segment', 'print_special', 'print_progress', 'print_realtime', 'print_timestamps', 'token_timestamps']]
        + [('thold_pt', c.c_float), ('thold_ptsum', c.c_float), ('max_len', c.c_int), ('split_on_word', c.c_bool), ('max_tokens', c.c_int), ('debug_mode', c.c_bool), ('audio_ctx', c.c_int), ('tdrz_enable', c.c_bool), ('suppress_regex', c.c_char_p), ('initial_prompt', c.c_char_p), ('carry_initial_prompt', c.c_bool), ('prompt_tokens', c.c_void_p), ('prompt_n_tokens', c.c_int), ('language', c.c_char_p), ('detect_language', c.c_bool), ('suppress_blank', c.c_bool), ('suppress_nst', c.c_bool)]
        + [(n, c.c_float) for n in ['temperature', 'max_initial_ts', 'length_penalty', 'temperature_inc', 'entropy_thold', 'logprob_thold', 'no_speech_thold']]
        + [('greedy', Greedy), ('beam_search', Beam)]
        + [(n, c.c_void_p) for n in ['new_segment_callback', 'new_segment_callback_user_data', 'progress_callback', 'progress_callback_user_data', 'encoder_begin_callback', 'encoder_begin_callback_user_data', 'abort_callback', 'abort_callback_user_data', 'logits_filter_callback', 'logits_filter_callback_user_data', 'grammar_rules']]
        + [('n_grammar_rules', c.c_size_t), ('i_start_rule', c.c_size_t), ('grammar_penalty', c.c_float), ('vad', c.c_bool), ('vad_model_path', c.c_char_p), ('vad_params', Vad)]
    )

class Native:
    def __init__(self, directory: Path):
        self.search = os.add_dll_directory(str(directory))
        self.ggml = c.CDLL(str(directory / 'ggml-whisper.dll'))
        self.base = c.CDLL(str(directory / 'ggml-base-whisper.dll'))
        self.lib = c.CDLL(str(directory / 'whisper.dll'))
        self.bind(self.lib, 'whisper_version', [], c.c_char_p)
        if self.lib.whisper_version() != b'1.8.5':
            raise RuntimeError('Vulkan 运行库版本不匹配，请删除运行库后重试')
        self.bind(self.ggml, 'ggml_backend_load_all_from_path', [c.c_char_p], None)
        self.ggml.ggml_backend_load_all_from_path(str(directory).encode('utf-8'))
        self.bind(self.ggml, 'ggml_backend_dev_count', [], c.c_size_t)
        self.bind(self.ggml, 'ggml_backend_dev_get', [c.c_size_t], c.c_void_p)
        for name in ['ggml_backend_dev_name', 'ggml_backend_dev_description']:
            self.bind(self.base, name, [c.c_void_p], c.c_char_p)
        self.bind(self.base, 'ggml_backend_dev_type', [c.c_void_p], c.c_int)
        self.bind(self.base, 'ggml_backend_dev_memory', [c.c_void_p,c.POINTER(c.c_size_t),c.POINTER(c.c_size_t)], None)
        for name, args, result in [
            ('whisper_context_default_params', [], Context),
            ('whisper_full_default_params', [c.c_int], Params),
            ('whisper_init_from_file_with_params', [c.c_char_p, Context], c.c_void_p),
            ('whisper_full', [c.c_void_p, Params, c.POINTER(c.c_float), c.c_int], c.c_int),
            ('whisper_free', [c.c_void_p], None),
            ('whisper_full_n_segments', [c.c_void_p], c.c_int),
            ('whisper_full_get_segment_text', [c.c_void_p, c.c_int], c.c_char_p),
            ('whisper_full_get_segment_t0', [c.c_void_p, c.c_int], c.c_int64),
            ('whisper_full_get_segment_t1', [c.c_void_p, c.c_int], c.c_int64),
            ('whisper_full_lang_id', [c.c_void_p], c.c_int),
            ('whisper_lang_str', [c.c_int], c.c_char_p),
        ]:
            self.bind(self.lib, name, args, result)

    @staticmethod
    def bind(lib, name, args, result):
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result

    def devices(self):
        result = []
        for i in range(self.ggml.ggml_backend_dev_count()):
            d = self.ggml.ggml_backend_dev_get(i)
            name = self.base.ggml_backend_dev_name(d).decode()
            if name.startswith('Vulkan') and self.base.ggml_backend_dev_type(d) in (1, 2):
                free,total=c.c_size_t(),c.c_size_t()
                self.base.ggml_backend_dev_memory(d,c.byref(free),c.byref(total))
                result.append({'memory_free':free.value if total.value else None,'memory_total':total.value or None,'index': int(name.removeprefix('Vulkan')), 'name': self.base.ggml_backend_dev_description(d).decode('utf-8'), 'integrated': self.base.ggml_backend_dev_type(d) == 2})
        return result
