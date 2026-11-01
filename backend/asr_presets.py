"""CUDA adapter for the shared decoding and memory policy."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import os

from .asr_recovery import DEFAULT_DECODE_OPTIONS

from .asr_policy import cuda_decode_options, select_weight_precision


def decoding_options(model_name: str | None, device: str, compute_type: str | None) -> dict:
    # Precision changes memory use only; it must not change decoding intent.
    return cuda_decode_options(model_name) if device == 'cuda' else dict(DEFAULT_DECODE_OPTIONS)


@lru_cache(maxsize=1)
def cuda_device_memory_bytes() -> int | None:
    if os.name != 'nt':
        return None
    try:
        driver = ctypes.WinDLL('nvcuda.dll')
        driver.cuInit.argtypes = [ctypes.c_uint]
        driver.cuInit.restype = ctypes.c_int
        driver.cuDeviceTotalMem_v2.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.c_int]
        driver.cuDeviceTotalMem_v2.restype = ctypes.c_int
        memory = ctypes.c_size_t()
        if driver.cuInit(0) or driver.cuDeviceTotalMem_v2(ctypes.byref(memory), 0):
            return None
        return memory.value or None
    except (OSError, AttributeError):
        return None


def select_cuda_compute_type(model_name: str, memory_bytes: int | None, supported: set[str]) -> str:
    if select_weight_precision(model_name,memory_bytes) == 'int8' and 'int8_float16' in supported:
        return 'int8_float16'
    if 'float16' in supported:
        return 'float16'
    # Respect the actual CUDA capabilities instead of requesting unsupported FP16.
    return next((kind for kind in ('int8_float16','int8_float32','float32') if kind in supported),'float16')


def preferred_cuda_compute_type(model_name: str) -> str:
    memory = cuda_device_memory_bytes()
    try:
        from .gpu_runtime import activate_cuda_libraries
        activate_cuda_libraries()
        import ctranslate2
        supported = ctranslate2.get_supported_compute_types('cuda')
    except Exception:
        return 'float16'
    return select_cuda_compute_type(model_name, memory, supported)
