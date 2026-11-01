"""AMD/Intel GPU speech recognition using a pinned whisper.cpp Vulkan runtime."""
from __future__ import annotations
import asyncio
import base64
import json
import shutil
import tempfile
import ctypes as c
import hashlib
import os
import platform
import time
import zipfile
from contextlib import contextmanager
from filelock import FileLock, Timeout
from pathlib import Path

from .download import download_to_file
from .transcript import TranscriptSegment
from .whisper_asr import TranscriptionCancelledError
from .asr_policy import POLICY_VERSION, INDEPENDENT_TEXT_CONTEXT_MODELS, select_weight_precision, vulkan_decode_options

RUNTIME_VERSION = '1.9.1'
RUNTIME_SHA256 = '25fbea771d8aaffc4588a1d6aa1345a7bbc74912a8ada895d1695bbacfc1135a'
RUNTIME_URL = f'https://api.nuget.org/v3-flatcontainer/whisper.net.runtime.vulkan/{RUNTIME_VERSION}/whisper.net.runtime.vulkan.{RUNTIME_VERSION}.nupkg'
DLLS = ('ggml-base-whisper.dll', 'ggml-cpu-whisper.dll', 'ggml-vulkan-whisper.dll', 'ggml-whisper.dll', 'whisper.dll')
MODELS = {name: f'ggml-{name}.bin' for name in ('tiny', 'base', 'small', 'medium', 'large-v3')}
MODELS['turbo'] = 'ggml-large-v3-turbo.bin'
MODELS['belle-turbo-zh'] = 'ggml-model.bin'
# Use the author's GGML export, not an unrelated turbo model under Belle's name.
MODEL_SOURCES = {'belle-turbo-zh': ('BELLE-2/Belle-whisper-large-v3-turbo-zh-ggml', '0a7f573')}
@contextmanager
def installation_lock(root, cancel_event):
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root / '.install.lock'))
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelledError()
        try:
            lock.acquire(timeout=0.5)
            break
        except Timeout:
            continue
    try:
        yield
    finally:
        lock.release()


def model_directory(root: Path, model: str) -> Path:
    return root / 'vulkan' / model

def model_path(root: Path, model: str, precision: str = 'float16') -> Path:
    if precision not in {'float16','q8_0'}:
        raise ValueError('不支持的Vulkan权重精度')
    filename=MODELS[model]
    if precision=='q8_0':filename=filename.removesuffix('.bin')+'-q8_0.bin'
    return model_directory(root,model)/filename


def cache_status(root: Path, model: str, precision: str | None = None) -> str:
    if model not in MODELS:
        return 'unsupported'
    variants=('float16','q8_0') if precision is None else (precision,)
    for kind in variants:
        path=model_path(root,model,kind)
        if path.is_file() and path.stat().st_size>30*1024**2:
            with path.open('rb') as stream:
                header=stream.read(48)
            if header[:4]==b'lmgg':
                if kind=='float16':return 'cached'
                import struct
                if len(header)==48 and struct.unpack_from('<i',header,44)[0]%1000==7:
                    return 'cached'
    directory=model_directory(root,model)
    if precision is not None:
        # A cached sibling precision does not mean this variant is partial.
        filename=model_path(root,model,precision).name
        return 'incomplete' if directory.exists() and any(
            entry.name.startswith(filename) for entry in directory.iterdir()) else 'missing'
    return 'incomplete' if directory.exists() and any(directory.iterdir()) else 'missing'


def select_precision(model: str, device: dict | None) -> str:
    device=device or {}
    kind=select_weight_precision(model,device.get('memory_total'),device.get('memory_free'))
    return 'q8_0' if kind=='int8' else 'float16'


def checked_download(url, target, cancel_event):
    target.parent.mkdir(parents=True, exist_ok=True)
    ok, error = download_to_file(url, target, {'User-Agent': 'AI Video Note'}, cancel_event)
    if not ok:
        raise RuntimeError(f'下载失败：{error}')

def _runtime_valid(package: Path) -> bool:
    archive = package / f'whisper.net.runtime.vulkan.{RUNTIME_VERSION}.nupkg'
    directory = package / 'build' / 'win-x64'
    if not archive.is_file() or not all((directory / name).is_file() for name in DLLS):
        return False
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != RUNTIME_SHA256:
            return False
    try:
        with zipfile.ZipFile(archive) as contents:
            return all(hashlib.sha256((directory / name).read_bytes()).digest() ==
                       hashlib.sha256(contents.read(f'build/win-x64/{name}')).digest() for name in DLLS)
    except (OSError, KeyError, zipfile.BadZipFile):
        return False


def ensure_runtime(root: Path, cancel_event=None, archive_source: Path | None = None) -> Path:
    # root still controls models only. Native packages use NuGet's standard cache.
    from .gpu_runtime import vulkan_package
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('当前 AMD/Intel Vulkan 加速支持 Windows 64 位系统')
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelledError()
    package = vulkan_package(RUNTIME_VERSION)
    with installation_lock(package.parent / '.ai-video-note-install', cancel_event):
        if _runtime_valid(package):
            return package / 'build' / 'win-x64'
        if package.exists():
            raise RuntimeError(f'NuGet 识别组件目录不完整或校验失败，请修复此版本后重试：{package}')
        archive = package.parent / f'whisper.net.runtime.vulkan.{RUNTIME_VERSION}.download.nupkg'
        if not archive.is_file():
            if archive_source is not None:
                with archive_source.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != RUNTIME_SHA256:
                        raise RuntimeError('手动下载的 Vulkan 安装包校验失败，请重新下载固定版本')
                shutil.copyfile(archive_source, archive)
            else:
                checked_download(RUNTIME_URL, archive, cancel_event)
        with archive.open('rb') as stream:
            archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        if archive_hash != RUNTIME_SHA256:
            archive.unlink(missing_ok=True)
            raise RuntimeError('Vulkan 运行库校验失败，请重试下载')
        with tempfile.TemporaryDirectory(prefix='.install-', dir=package.parent) as temporary:
            prepared = Path(temporary) / RUNTIME_VERSION
            prepared.mkdir()
            with zipfile.ZipFile(archive) as contents:
                # Validate the entire archive before writing any package entry.
                for entry in contents.infolist():
                    relative = Path(entry.filename.replace('\\', '/'))
                    if relative.is_absolute() or '..' in relative.parts or ':' in entry.filename:
                        raise RuntimeError('Vulkan 安装包包含非法路径')
                for entry in contents.infolist():
                    if cancel_event is not None and cancel_event.is_set():
                        raise TranscriptionCancelledError()
                    destination = prepared / entry.filename.replace('\\', '/')
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with contents.open(entry) as source, destination.open('wb') as output:
                            shutil.copyfileobj(source, output)
            filename = f'whisper.net.runtime.vulkan.{RUNTIME_VERSION}.nupkg'
            shutil.copyfile(archive, prepared / filename)
            with archive.open('rb') as stream:
                digest = base64.b64encode(hashlib.file_digest(stream, 'sha512').digest()).decode('ascii')
            (prepared / (filename + '.sha512')).write_text(digest, encoding='utf-8')
            if not _runtime_valid(prepared):
                raise RuntimeError('Vulkan 运行库校验失败，安装未完成')
            (prepared / '.nupkg.metadata').write_text(json.dumps({'version': 2, 'contentHash': digest,
                'source': 'https://api.nuget.org/v3/index.json'}), encoding='utf-8')
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptionCancelledError()
            # Never replace a package created concurrently by another package manager.
            if package.exists():
                if not _runtime_valid(package):
                    raise RuntimeError('NuGet 包目录正在被其他程序修改，请稍后重试')
            else:
                prepared.rename(package)
        archive.unlink(missing_ok=True)
    return package / 'build' / 'win-x64'


def ensure_model(root: Path, model: str, cancel_event=None, *, precision: str = 'float16') -> Path:
    with installation_lock(root / "vulkan", cancel_event):
        return _ensure_model(root, model, cancel_event, precision=precision)

def _ensure_model(root: Path, model: str, cancel_event=None, *, precision: str = 'float16') -> Path:
    if model not in MODELS:
        raise ValueError('此模型不支持 AMD/Intel Vulkan 加速，请选择支持的 Whisper 模型')
    path = model_path(root, model, precision)
    if precision=='q8_0':
        if cache_status(root,model,precision)!='cached':
            source=_ensure_model(root,model,cancel_event,precision='float16')
            from .vulkan_quantization import quantize_model
            quantize_model(root,source,path,checked_download,cancel_event)
        return path
    if cache_status(root, model, precision) != 'cached':
        endpoints = [os.getenv('HF_ENDPOINT', 'https://huggingface.co'), 'https://hf-mirror.com']
        endpoints = list(dict.fromkeys(endpoints))
        repository, revision = MODEL_SOURCES.get(model, ('ggerganov/whisper.cpp', 'main'))
        for endpoint in endpoints:
            try:
                checked_download(f'{endpoint.rstrip("/")}/{repository}/resolve/{revision}/{MODELS[model]}', path, cancel_event)
                if cache_status(root, model, precision) != 'cached':
                    path.unlink(missing_ok=True)
                    raise RuntimeError('模型文件不完整或格式错误')
                break
            except Exception:
                if endpoint == endpoints[-1]:
                    raise
    return path

def select_device(devices, backend):
    words = {'intel': ('intel',), 'amd': ('amd', 'radeon'), 'nvidia': ('nvidia',), 'vulkan': ()}[backend]
    candidates = [d for d in devices if not words or any(word in d['name'].lower() for word in words)]
    # Use the driver's device type, not vendor names or enumeration order.
    candidates.sort(key=lambda d: bool(d.get('integrated', False)))
    return candidates[0] if candidates else None

async def transcribe(root, media_path, model_name, backend, title=None, cancel_event=None, progress_callback=None):
    try:
        return await asyncio.to_thread(_transcribe, root, media_path, model_name, backend, title, cancel_event, progress_callback)
    except TranscriptionCancelledError as exc:
        raise asyncio.CancelledError('转写已取消') from exc

def _transcribe(root, media_path, model_name, backend, title, cancel_event, progress_callback,
                *, allow_cpu_fallback=True, decode_options=None):
    from faster_whisper.audio import decode_audio
    from .whisper_vulkan_native import Native
    started = time.monotonic()
    def cancelled():
        return cancel_event is not None and cancel_event.is_set()
    if cancelled():
        raise TranscriptionCancelledError()
    runtime = ensure_runtime(root, cancel_event)
    try:
        native = Native(runtime)
    except OSError as exc:
        raise RuntimeError("无法启动 Vulkan 加速，请更新显卡驱动并安装 Microsoft Visual C++ 2015–2022 x64 运行库，或关闭显卡加速") from exc
    selected = select_device(native.devices(), backend)
    fallback = None
    if selected is None:
        if not allow_cpu_fallback:
            raise RuntimeError('GPU-only 转写未检测到可用 Vulkan 显卡，禁止 CPU 回退')
        fallback = f'未检测到可用的 {backend.upper()} Vulkan 显卡，已使用 CPU 转写。请检查显卡驱动。'
    precision=select_precision(model_name,selected)
    path = ensure_model(root, model_name, cancel_event) if precision=='float16' else ensure_model(root,model_name,cancel_event,precision=precision)
    precision_reason='memory_budget'
    params = native.lib.whisper_context_default_params()
    params.use_gpu = selected is not None
    params.flash_attn = False
    params.gpu_device = selected['index'] if selected else 0
    ctx = native.lib.whisper_init_from_file_with_params(str(path).encode('utf-8'), params)
    if not ctx and selected and precision=='float16':
        # Budget estimates can become stale while other apps allocate VRAM.
        # Retry smaller weights on the same GPU before considering any failure.
        path=ensure_model(root,model_name,cancel_event,precision='q8_0')
        precision='q8_0';precision_reason='fp16_gpu_allocation_failed'
        ctx=native.lib.whisper_init_from_file_with_params(str(path).encode('utf-8'),params)
    if not ctx and selected:
        raise RuntimeError('GPU模型加载失败，Q8精度仍无法载入；禁止 CPU 回退，请释放显存或选择较小模型')
    if not ctx:
        raise RuntimeError('语音识别模型无法加载，请检查模型完整性、内存及显卡驱动')
    try:
        samples = decode_audio(str(media_path), sampling_rate=16000)
        duration = len(samples) / 16000
        if not len(samples):
            raise ValueError('媒体中没有可识别的音频')
        opts = native.lib.whisper_full_default_params(0)
        opts.n_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
        opts.translate = False
        opts.no_context = True
        # A single portable policy feeds CUDA and Vulkan. Explicit overrides
        # still request the old native baseline for reproducible experiments.
        if decode_options is None:
            policy=vulkan_decode_options(model_name)
            opts.n_max_text_ctx=policy['n_max_text_ctx']
            opts.greedy.best_of=policy['best_of']
            opts.temperature=policy['temperature'];opts.temperature_inc=policy['temperature_inc']
            opts.no_speech_thold=policy['no_speech_thold']
        overrides=dict(decode_options or {})
        unknown = set(overrides) - {'no_context', 'n_max_text_ctx', 'beam_size', 'language'}
        if unknown:
            raise ValueError(f'不支持的 Vulkan 解码参数：{sorted(unknown)}')
        if 'no_context' in overrides:
            opts.no_context = bool(overrides['no_context'])
        if 'n_max_text_ctx' in overrides:
            budget = overrides['n_max_text_ctx']
            if not isinstance(budget, int) or isinstance(budget, bool) or not 0 <= budget <= 16384:
                raise ValueError('n_max_text_ctx 必须为 0–16384 的整数')
            opts.n_max_text_ctx = budget
        if 'beam_size' in overrides:
            beam = overrides['beam_size']
            if not isinstance(beam, int) or isinstance(beam, bool) or not 1 <= beam <= 10:
                raise ValueError('beam_size 必须为 1–10 的整数')
            if beam > 1:
                opts.strategy = 1
                opts.beam_search.beam_size = beam
        opts.print_progress = opts.print_realtime = opts.print_timestamps = opts.print_special = False
        language = b'en' if model_name.endswith('.en') else b'auto'
        if 'language' in overrides:
            language = overrides['language'].encode('ascii')
        prompt = (title or '').encode('utf-8')
        opts.language, opts.initial_prompt = language, prompt
        effective_options = {
            'strategy': int(opts.strategy), 'greedy_best_of': int(opts.greedy.best_of),
            'beam_size': int(opts.beam_search.beam_size),
            'no_context': bool(opts.no_context), 'n_max_text_ctx': int(opts.n_max_text_ctx),
            'language': language.decode('ascii'), 'temperature': float(opts.temperature),
            'temperature_inc': float(opts.temperature_inc),
            'no_speech_thold': float(opts.no_speech_thold),
        }
        abort_type = c.CFUNCTYPE(c.c_bool, c.c_void_p)
        progress_type = c.CFUNCTYPE(None, c.c_void_p, c.c_void_p, c.c_int, c.c_void_p)
        abort = abort_type(lambda _: cancelled())
        def report(_ctx, _state, percent, _user):
            if progress_callback:
                try:
                    progress_callback(duration * percent / 100, duration)
                except Exception:
                    pass
        progress = progress_type(report)
        opts.abort_callback = c.cast(abort, c.c_void_p)
        opts.progress_callback = c.cast(progress, c.c_void_p)
        if cancelled():
            raise TranscriptionCancelledError()
        load_seconds = time.monotonic() - started
        # Only exact digital zero: never threshold away quiet speech or music.
        digital_silence = not samples.any()
        if digital_silence:
            code = 0
            report(None, None, 100, None)
        else:
            code = native.lib.whisper_full(ctx, opts, samples.ctypes.data_as(c.POINTER(c.c_float)), len(samples))
        if cancelled():
            raise TranscriptionCancelledError()
        if code:
            raise RuntimeError(f'Vulkan 识别失败（{code}），请改用较小模型或关闭 GPU 加速重试')
        segments = []
        for i in range(0 if digital_silence else native.lib.whisper_full_n_segments(ctx)):
            text = native.lib.whisper_full_get_segment_text(ctx, i).decode('utf-8').strip()
            if text and text.casefold() != '[blank_audio]':
                segments.append(TranscriptSegment(native.lib.whisper_full_get_segment_t0(ctx, i) / 100, native.lib.whisper_full_get_segment_t1(ctx, i) / 100, text))
        return {'text': ' '.join(s.text for s in segments), 'segments': segments,
                'language': native.lib.whisper_lang_str(native.lib.whisper_full_lang_id(ctx)).decode(),
                'language_probability': None, 'device': f'Vulkan · {selected["name"]} ({precision.upper()})' if selected else 'cpu',
                'model': model_name, 'requested_model': model_name, 'duration': duration, 'duration_after_vad': duration,
                'fallback_note': fallback, 'model_load_seconds': round(load_seconds, 1),
                'transcribe_seconds': round(time.monotonic() - started - load_seconds, 1), 'engine': 'whisper_vulkan',
                'decode_options': effective_options,
                'decode_policy': 'reviewed_application' if decode_options is None else 'explicit_override',
                'decode_policy_version': POLICY_VERSION if decode_options is None else None,
                'compute_type': precision, 'model_file': str(path),
                'precision_reason': precision_reason,
                'gpu_memory_total': selected.get('memory_total') if selected else None,
                'gpu_memory_free': selected.get('memory_free') if selected else None,
                'silence_guard': 'exact_digital_zero' if digital_silence else None}
    finally:
        native.lib.whisper_free(ctx)
