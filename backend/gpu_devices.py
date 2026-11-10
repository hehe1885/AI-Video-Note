"""Read Vulkan device names without downloading models or the ASR runtime."""
from __future__ import annotations
import asyncio
import ctypes as c
import json
import os
import sys
import subprocess
import time
from pathlib import Path
from .whisper_vulkan import select_device
from . import asr_diagnostics

class ApplicationInfo(c.Structure):
    _fields_ = [('sType',c.c_uint32),('pNext',c.c_void_p),('pApplicationName',c.c_char_p),('applicationVersion',c.c_uint32),('pEngineName',c.c_char_p),('engineVersion',c.c_uint32),('apiVersion',c.c_uint32)]
class InstanceInfo(c.Structure):
    _fields_ = [('sType',c.c_uint32),('pNext',c.c_void_p),('flags',c.c_uint32),('pApplicationInfo',c.POINTER(ApplicationInfo)),('enabledLayerCount',c.c_uint32),('ppEnabledLayerNames',c.c_void_p),('enabledExtensionCount',c.c_uint32),('ppEnabledExtensionNames',c.c_void_p)]

class MemoryType(c.Structure):
    _fields_=[('propertyFlags',c.c_uint32),('heapIndex',c.c_uint32)]
class MemoryHeap(c.Structure):
    _fields_=[('size',c.c_uint64),('flags',c.c_uint32)]
class MemoryProperties(c.Structure):
    _fields_=[('memoryTypeCount',c.c_uint32),('memoryTypes',MemoryType*32),
              ('memoryHeapCount',c.c_uint32),('memoryHeaps',MemoryHeap*16)]

def device_local_memory(properties):
    return sum(properties.memoryHeaps[i].size for i in range(min(properties.memoryHeapCount,16))
               if properties.memoryHeaps[i].flags & 1) or None


def enumerate_devices():
    if os.name != 'nt':
        return []
    vk=c.WinDLL('vulkan-1.dll')
    vk.vkCreateInstance.argtypes=[c.POINTER(InstanceInfo),c.c_void_p,c.POINTER(c.c_void_p)]
    vk.vkCreateInstance.restype=c.c_int32
    vk.vkDestroyInstance.argtypes=[c.c_void_p,c.c_void_p]
    vk.vkDestroyInstance.restype=None
    vk.vkEnumeratePhysicalDevices.argtypes=[c.c_void_p,c.POINTER(c.c_uint32),c.POINTER(c.c_void_p)]
    vk.vkEnumeratePhysicalDevices.restype=c.c_int32
    vk.vkGetPhysicalDeviceProperties.argtypes=[c.c_void_p,c.c_void_p]
    vk.vkGetPhysicalDeviceProperties.restype=None
    vk.vkGetPhysicalDeviceMemoryProperties.argtypes=[c.c_void_p,c.POINTER(MemoryProperties)]
    vk.vkGetPhysicalDeviceMemoryProperties.restype=None
    app=ApplicationInfo(sType=0,pApplicationName=b'AI Video Note',apiVersion=1<<22)
    info=InstanceInfo(sType=1,pApplicationInfo=c.pointer(app))
    instance=c.c_void_p()
    if vk.vkCreateInstance(c.byref(info),None,c.byref(instance)) != 0:
        return []
    try:
        count=c.c_uint32()
        if vk.vkEnumeratePhysicalDevices(instance,c.byref(count),None) != 0 or not count.value:
            return []
        handles=(c.c_void_p*count.value)()
        if vk.vkEnumeratePhysicalDevices(instance,c.byref(count),handles) not in (0,5):
            return []
        devices=[]
        for i,handle in enumerate(handles[:count.value]):
            # Oversized, aligned storage for VkPhysicalDeviceProperties (incl. limits).
            properties=(c.c_uint64*1024)()
            vk.vkGetPhysicalDeviceProperties(handle,c.byref(properties))
            raw=bytes(properties)
            kind=int.from_bytes(raw[16:20],'little')
            if kind not in (1,2):
                continue
            memory=MemoryProperties()
            vk.vkGetPhysicalDeviceMemoryProperties(handle,c.byref(memory))
            devices.append({'memory_total':device_local_memory(memory),'index':i,'name':raw[20:276].split(b'\0',1)[0].decode('utf-8','replace'),'integrated':kind==1})
        return devices
    finally:
        vk.vkDestroyInstance(instance,None)


def enumerate_cuda_devices():
    """NVIDIA detection also works when its Vulkan loader is absent."""
    if os.name != 'nt':
        return []
    driver = c.WinDLL('nvcuda.dll')
    driver.cuInit.argtypes = [c.c_uint]
    driver.cuInit.restype = c.c_int
    driver.cuDeviceGetCount.argtypes = [c.POINTER(c.c_int)]
    driver.cuDeviceGetCount.restype = c.c_int
    driver.cuDeviceGetName.argtypes = [c.c_char_p,c.c_int,c.c_int]
    driver.cuDeviceGetName.restype = c.c_int
    count = c.c_int()
    if driver.cuInit(0) or driver.cuDeviceGetCount(c.byref(count)):
        return []
    devices = []
    for index in range(count.value):
        name = c.create_string_buffer(256)
        if not driver.cuDeviceGetName(name, len(name), index):
            devices.append({'index':index,'name':name.value.decode('utf-8','replace'),'integrated':False,'cuda':True})
    return devices


_cached_devices = None
_cached_at = 0.0
_pending_probe = None

async def probe_devices():
    global _pending_probe
    if _cached_devices is not None and time.monotonic() - _cached_at < 15:
        return list(_cached_devices)
    loop = asyncio.get_running_loop()
    if _pending_probe is None or _pending_probe.done() or _pending_probe.get_loop() is not loop:
        _pending_probe = loop.create_task(_probe_devices())
    return await asyncio.shield(_pending_probe)

async def _query_devices(cuda_only=False):
    command = [sys.executable, '--gpu-probe'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'backend.gpu_devices']
    if cuda_only:
        command.append('--cuda-only')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent,
        **options)
    try:
        output, errors = await asyncio.wait_for(process.communicate(), timeout=10)
        if process.returncode:
            detail = errors.decode('utf-8', 'replace').strip()[-300:]
            raise RuntimeError(f'探测进程退出码 {process.returncode}：{detail}')
        devices = json.loads(output.decode('utf-8'))
        if not isinstance(devices, list) or any(not isinstance(d, dict) or not isinstance(d.get('name'), str) for d in devices):
            raise ValueError('显卡探测返回格式无效')
        return devices
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _probe_devices():
    global _cached_devices, _cached_at
    if _cached_devices is not None and time.monotonic() - _cached_at < 15:
        return list(_cached_devices)
    try:
        devices = await _query_devices()
    except (asyncio.TimeoutError, ValueError, OSError, RuntimeError) as error:
        asr_diagnostics.emit(f'完整显卡探测未完成（{type(error).__name__}: {error}），正在单独复核 CUDA')
        devices = []
    if not devices and os.name == 'nt':
        try:
            devices = await _query_devices(cuda_only=True)
            if devices:
                asr_diagnostics.emit('独立 CUDA 探测已识别显卡：' + '、'.join(d['name'] for d in devices))
        except (asyncio.TimeoutError, ValueError, OSError, RuntimeError) as error:
            asr_diagnostics.emit(f'独立 CUDA 探测失败（{type(error).__name__}: {error}）')
    # An empty/failed probe is transient, not proof that this computer has no GPU.
    if devices:
        _cached_devices, _cached_at = devices, time.monotonic()
    return list(devices)


def automatic_device(devices):
    # A discrete AMD/Intel GPU still wins over an integrated GPU. Within the
    # same class prefer NVIDIA so automatic mode can use the CUDA engine.
    return next(iter(sorted(devices,key=lambda item:(bool(item.get('integrated')),not ('nvidia' in item['name'].lower() or item.get('cuda',False))))),None)


async def detect(backend):
    devices=await probe_devices()
    if backend == 'auto':
        selected = automatic_device(devices)
    elif backend == 'cuda':
        selected = automatic_device([item for item in devices if item.get('cuda') or 'nvidia' in item['name'].lower()])
    else:
        selected = select_device(devices, backend)
    effective = 'cpu' if selected is None else ('cuda' if backend=='auto' and ('nvidia' in selected['name'].lower() or selected.get('cuda')) else ('vulkan' if backend=='auto' else backend))
    return {'detected':selected is not None,'name':selected['name'] if selected else None,'backend':effective}


async def preferred_vulkan_precision(model,backend):
    from .whisper_vulkan import select_precision
    selected=select_device(await probe_devices(),backend)
    return select_precision(model,selected)


async def resolve_backend(backend, model=None):
    if backend != 'auto':
        return backend
    selected = await detect('auto')
    effective = selected['backend']
    if model == 'paraformer-zh':
        return 'cpu'
    return effective


def child_main():
    if sys.stdout is None:
        sys.stdout = open(1,'w',encoding='utf-8',buffering=1,closefd=False)
    try:
        devices=[] if '--cuda-only' in sys.argv else enumerate_devices()
    except (OSError,ValueError):
        devices=[]
    try:
        cuda=enumerate_cuda_devices()
        for item in cuda:
            existing=next((device for device in devices if device['name']==item['name']),None)
            if existing is not None:
                existing['cuda']=True
                existing['cuda_index']=item['index']
            else:
                item['cuda_index']=item['index']
                devices.append(item)
    except (OSError,ValueError):
        pass
    print(json.dumps(devices,ensure_ascii=True),flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(child_main())
