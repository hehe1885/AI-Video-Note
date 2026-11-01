"""Create GGML Q8_0 using the pinned, verified upstream quantizer.

This is an offline format conversion on CPU, never CPU speech recognition.
Original FP16 files are preserved. The executable lives apart from inference DLLs.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
import zipfile

VERSION='1.8.5'
ARCHIVE_SHA256='2a0e85915d0ff9e2a1d1b45b19973f05fbcdfb51d3af03557454acf6ccaa5e8a'
URL=f'https://github.com/ggml-org/whisper.cpp/releases/download/v{VERSION}/whisper-bin-x64.zip'


def _cancel(event):
    if event is not None and event.is_set():
        from .whisper_asr import TranscriptionCancelledError
        raise TranscriptionCancelledError()


def ensure_quantizer(root: Path, download, cancel_event=None) -> Path:
    folder=root/'vulkan'/'tools'/f'quantizer-{VERSION}'
    folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'upstream.zip'
    _cancel(cancel_event)
    if not archive.is_file():
        download(URL,archive,cancel_event)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=ARCHIVE_SHA256:
            raise RuntimeError('量化工具校验失败，请重新下载固定版本工具')
    executable=folder/'whisper-quantize.exe'
    with zipfile.ZipFile(archive) as contents:
        for entry in contents.infolist():
            relative=Path(entry.filename.replace('\\','/'))
            if relative.is_absolute() or '..' in relative.parts or ':' in entry.filename:
                raise RuntimeError('量化工具压缩包包含非法路径')
        selected=[entry for entry in contents.infolist() if not entry.is_dir() and
                  (Path(entry.filename).name=='whisper-quantize.exe' or entry.filename.endswith('.dll'))]
        if not any(Path(entry.filename).name=='whisper-quantize.exe' for entry in selected):
            raise RuntimeError('固定版本量化工具不完整')
        for entry in selected:
            _cancel(cancel_event)
            data=contents.read(entry)
            target=folder/Path(entry.filename).name
            if not target.is_file() or hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(data).digest():
                temporary=folder/(target.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    temporary.write_bytes(data);temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
    return executable


def quantize_model(root: Path, source: Path, target: Path, download, cancel_event=None):
    executable=ensure_quantizer(root,download,cancel_event)
    # Source/target basenames avoid passing Unicode model directory names to a
    # narrow-argv upstream tool; Windows sets the Unicode working directory.
    if source.parent != target.parent:
        raise ValueError('量化输入与输出必须位于同一模型目录')
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    process=None
    try:
        with tempfile.TemporaryFile() as log:
            process=subprocess.Popen([str(executable),source.name,temporary.name,'q8_0'],
                cwd=source.parent,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            started=time.monotonic()
            while process.poll() is None:
                _cancel(cancel_event)
                if time.monotonic()-started>1800:
                    raise RuntimeError('模型量化超时，请稍后重试')
                time.sleep(.1)
            if process.returncode:
                log.seek(0);message=log.read().decode('utf-8','replace')[-1200:]
                raise RuntimeError('Q8模型转换失败：'+message)
        _cancel(cancel_event)
        # hparams.ftype is the twelfth int32. 1000 encodes the quantizer version.
        import struct
        with temporary.open('rb') as stream:
            header=stream.read(48)
        if (len(header)!=48 or header[:4]!=b'lmgg' or
                struct.unpack_from('<i',header,44)[0]%1000!=7 or temporary.stat().st_size<30*1024**2):
            raise RuntimeError('Q8模型转换结果无效，未替换原模型')
        temporary.replace(target)
    finally:
        if process is not None and process.poll() is None:
            process.kill();process.wait()
        temporary.unlink(missing_ok=True)
