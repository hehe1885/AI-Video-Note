"""Task-local ASR diagnostics shared by async tasks and inference threads."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from . import detailed_logs

_reporter = ContextVar('asr_diagnostic_reporter', default=None)


@contextmanager
def listen(reporter):
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def emit(message):
    detailed_logs.event('asr.message', message=message)
    reporter = _reporter.get()
    if reporter is None:
        logging.getLogger(__name__).info(message)
    else:
        reporter(message)


def model_ready(model, device, precision):
    detailed_logs.event('asr.model_ready', model=model, device=device, precision=precision)
    emit(f'语音识别模型已加载：模型 {model}；设备 {device}；精度 {precision}；开始转写')
