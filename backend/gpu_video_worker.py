"""Isolate native video-driver failures; only the parent commits successful output."""
import asyncio
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

LOGGER=logging.getLogger(__name__)

def _decoder(source,hardware):
    import av
    from av.codec.hwaccel import HWAccel
    decoder=hardware.get('decoder')
    return av.open(str(source),hwaccel=HWAccel(decoder,device=hardware.get('decoder_device'),
        options=hardware.get('decoder_options') or {},allow_software_fallback=False)) if decoder else av.open(str(source))

def process_job(job):
    import av
    source=Path(job['source']);destination=Path(job['destination'])
    hardware=job['hardware'];operation=job['operation']
    if operation=='scale':
        with _decoder(source,hardware) as media:
            stream=media.streams.video[0]
            height=int(job['max_height']);height-=height%2
            width=max(2,int(stream.width*height/stream.height)//2*2)
            output_path=destination/'output.mp4'
            with av.open(str(output_path),'w') as output:
                encoder=output.add_stream(hardware['encoder'],rate=stream.average_rate or 25)
                encoder.width,encoder.height=width,height;encoder.pix_fmt='yuv420p'
                encoder.options=hardware.get('encoder_options') or {}
                encoder.bit_rate=max(300000,int(width*height*float(stream.average_rate or 25)*(.07 if job['codec']=='hevc' else .12)))
                audio={item.index:output.add_stream_from_template(item,opaque=True) for item in media.streams.audio}
                for packet in media.demux(stream,*media.streams.audio):
                    if packet.stream.type=='audio':
                        if packet.dts is not None:
                            packet.stream=audio[packet.stream.index];output.mux(packet)
                        continue
                    for frame in packet.decode():
                        resized=frame.reformat(width=width,height=height,format='yuv420p')
                        resized.pts,resized.time_base=frame.pts,frame.time_base
                        for item in encoder.encode(resized):output.mux(item)
                for item in encoder.encode():output.mux(item)
        # A driver returning an empty/corrupt output is also a failure.
        with av.open(str(output_path)) as media:
            assert media.streams.video[0].height==height
            assert media.streams.video[0].codec_context.codec.id==av.codec.Codec(job['codec'],'r').id
            assert next(media.decode(video=0),None) is not None
        return {'file':'output.mp4'}
    if operation=='screenshots':
        images=[]
        with _decoder(source,hardware) as media:
            stream=media.streams.video[0]
            if not stream.codec_context.is_hwaccel:raise RuntimeError('Hardware decoder is unavailable')
            duration=(media.duration or 0)/av.time_base
            times=sorted(set(job['timestamps'])) if job.get('timestamps') is not None else range(0,int(duration)+1,int(job['interval']))
            for stamp in times:
                if not math.isfinite(stamp) or stamp<0:continue
                media.seek(int(stamp*av.time_base),backward=True)
                for frame in media.decode(video=0):
                    if job.get('timestamps') is not None and (frame.time is None or frame.time<stamp):continue
                    name=f"ai_frame_{len(images):04d}_{round(stamp*1000)}ms.jpg" if job.get('timestamps') is not None else f"frame_{len(images):04d}_{stamp}s.jpg"
                    frame.to_image().save(destination/name,quality=85 if job.get('timestamps') is not None else 82)
                    images.append({'time':stamp,'file':name})
                    break
        return {'images':images}
    raise ValueError('Unsupported video operation')

def _run_process(job_file,should_abort,timeout):
    command=[sys.executable,'--gpu-video-worker',str(job_file)] if getattr(sys,'frozen',False) else [sys.executable,'-m','backend.gpu_video_worker',str(job_file)]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    started=time.monotonic()
    try:
        while True:
            if should_abort and should_abort():raise asyncio.CancelledError('任务已取消')
            if time.monotonic()-started>timeout:raise TimeoutError('GPU video worker timed out')
            try:
                output,_=process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:pass
        if process.returncode:raise RuntimeError('GPU video worker failed')
        result=json.loads(output.decode('utf-8'))
        if not result.get('ok'):raise RuntimeError('GPU video operation failed')
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

def try_operation(operation,source,*,max_height=None,frames_directory=None,timestamps=None,interval=None,should_abort=None,codec=None,notes=None):
    """Return None on hardware failure; cancellation is never swallowed."""
    import av
    from .video_codecs import processing_hardware
    source=Path(source)
    if should_abort and should_abort():raise asyncio.CancelledError('任务已取消')
    try:
        with av.open(str(source)) as media:
            if not media.streams.video:return None
            stream=media.streams.video[0]
            actual=stream.codec_context.codec.id
            source_codec='hevc' if actual==av.codec.Codec('hevc','r').id else 'h264' if actual==av.codec.Codec('h264','r').id else None
            duration=(media.duration or 0)/av.time_base
            if operation=='scale' and stream.height<=max_height:return source
        if source_codec is None:return None
        hardware=processing_hardware(codec or source_codec)
        if not hardware:return None
        if operation=='scale' and not hardware.get('encoder'):return None
        if operation=='screenshots' and not hardware.get('decoder'):return None
        root=Path(frames_directory) if operation=='screenshots' else source.parent
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='_video_gpu_',dir=root) as temporary:
            destination=Path(temporary)
            job={'operation':operation,'source':str(source.resolve()),'destination':str(destination.resolve()),
                 'hardware':hardware,'max_height':max_height,'timestamps':timestamps,'interval':interval,'codec':codec or source_codec}
            job_file=destination/'job.json';job_file.write_text(json.dumps(job),encoding='utf-8')
            result=_run_process(job_file,should_abort,max(90,min(7200,duration*3+60)))
            if should_abort and should_abort():raise asyncio.CancelledError('任务已取消')
            if operation=='scale':
                output=(destination/result['file']).resolve()
                if output.parent!=destination.resolve() or not output.is_file():raise ValueError('Invalid GPU output')
                target=source.with_name('preview_gpu_scaled-'+uuid.uuid4().hex[:12]+'.mp4')
                while target.exists() or target==source:
                    target=source.with_name('preview_gpu_scaled-'+uuid.uuid4().hex[:12]+'.mp4')
                output.replace(target)
                try:
                    source.unlink()
                except OSError:
                    target.unlink(missing_ok=True)
                    raise
                value=target
            else:
                paths={}
                for image in result['images']:
                    output=(destination/image['file']).resolve()
                    if output.parent!=destination.resolve() or output.suffix!='.jpg' or not output.is_file():raise ValueError('Invalid GPU screenshot')
                for image in result['images']:
                    output=destination/image['file'];target=root/output.name
                    output.replace(target);paths[float(image['time'])]=target
                value=paths if timestamps is not None else list(paths.values())
            message=f"视频{'压缩' if operation=='scale' else '截图'}已使用 GPU：{hardware.get('encoder') if operation=='scale' else hardware.get('decoder')}"
            if notes is not None:notes.append(message)
            LOGGER.info(message)
            return value
    except Exception as error:
        message=f"GPU 视频{'压缩' if operation=='scale' else '截图'}不可用，已回退 CPU（{type(error).__name__}）"
        if notes is not None:notes.append(message)
        LOGGER.info(message)
        return None

def worker_main(job_file):
    if os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0002|0x8000)
    if sys.stdout is None:sys.stdout=open(1,'w',encoding='utf-8',buffering=1,closefd=False)
    try:
        job=json.loads(Path(job_file).read_text(encoding='utf-8'))
        result={'ok':True,**process_job(job)}
    except Exception as error:
        result={'ok':False,'error':type(error).__name__}
    print(json.dumps(result),flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(worker_main(sys.argv[1]))
