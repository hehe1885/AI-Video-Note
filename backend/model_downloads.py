"""One explicit model download at a time, sharing the ASR downloader and .part files."""
from __future__ import annotations
import asyncio
import threading
import time
from .download import report_downloads
from . import whisper_vulkan
from .paraformer_asr import MODEL_SPECS

ACTIVE = {'downloading','pausing'}

class ModelDownloads:
    def __init__(self):
        self.lock=threading.RLock()
        self.job=None
        self.cancel=None
        self.thread=None

    def snapshot(self):
        with self.lock:
            return dict(self.job) if self.job else {'status':'idle'}

    def active(self):
        return self.snapshot()['status'] in ACTIVE

    def start(self, model, backend, root, whisper, paraformer, *, precision=None):
        with self.lock:
            if self.active():
                raise ValueError('已有模型正在下载，请先暂停或等待完成')
            self.cancel=threading.Event()
            self.job={'model':model,'backend':backend,'precision':precision,'status':'downloading','file':'准备下载…','received':0,'total':None,'percent':None,'downloaded_bytes':0,'error':'','started_at':time.time()}
            cancel=self.cancel
            self.thread=threading.Thread(target=self._run,args=(model,backend,root,whisper,paraformer,cancel,precision),daemon=True)
            self.thread.start()
            return dict(self.job)

    def pause(self):
        with self.lock:
            if self.active():
                self.job['status']='pausing'
                self.cancel.set()
            return self.snapshot()

    def _run(self, model, backend, root, whisper, paraformer, cancel, precision=None):
        files={}
        def progress(path, received, total):
            from pathlib import Path
            with self.lock:
                files[path]=received
                self.job.update(file=Path(path).name,received=received,total=total,
                    percent=round(min(100,received/total*100),1) if total else None,
                    downloaded_bytes=sum(files.values()))
        try:
            with report_downloads(progress):
                if backend != 'cuda':
                    whisper_vulkan.ensure_runtime(root,cancel)
                    if precision=='q8_0':
                        whisper_vulkan.ensure_model(root,model,cancel,precision=precision)
                    else:
                        whisper_vulkan.ensure_model(root,model,cancel)
                elif model == 'paraformer-zh':
                    for component in MODEL_SPECS:
                        paraformer._download_component(component,cancel)
                else:
                    if not whisper._cached_model_path(model):
                        path=whisper._download_model_files(model,cancel)
                        if path is None:
                            raise RuntimeError(str(whisper._last_download_error or '模型下载失败，请检查网络后重试'))
            with self.lock:
                self.job.update(status='paused' if cancel.is_set() else 'completed',percent=100 if not cancel.is_set() else self.job['percent'])
        except (asyncio.CancelledError, Exception) as exc:
            with self.lock:
                self.job.update(status='paused' if cancel.is_set() or isinstance(exc,asyncio.CancelledError) else 'failed',error='' if cancel.is_set() else str(exc))
