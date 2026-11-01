"""Verify HEVC hardware with real packets; system playback extensions are separate."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading

_cached = None
_cached_at = 0
_processing_cache = {}
_processing_lock = threading.Lock()
IS_WINDOWS = sys.platform == 'win32'

def encoder_candidates(devices, codec='hevc'):
    from .gpu_devices import automatic_device
    remaining=list(devices or [])
    vendors=[]
    choices=[]
    while remaining:
        device=automatic_device(remaining);remaining.remove(device)
        name=device.get('name','').lower()
        vendor='nvenc' if device.get('cuda') or 'nvidia' in name else 'amf' if 'amd' in name or 'radeon' in name else 'qsv' if 'intel' in name else None
        if vendor and vendor not in vendors:
            options={'gpu':str(device.get('cuda_index',0))} if vendor=='nvenc' else {}
            choices.append({'encoder':f'{codec}_{vendor}','options':options})
            vendors.append(vendor)
    # A missing Vulkan loader must not prevent AMF/QSV from being tested.
    for vendor in ('nvenc','amf','qsv'):
        if vendor not in vendors:choices.append({'encoder':f'{codec}_{vendor}','options':{}})
    return choices

def decoder_candidates(encoder, options):
    vendor=encoder.rsplit('_',1)[-1]
    identifier={'nvenc':'0x10de','amf':'0x1002','qsv':'0x8086'}[vendor]
    direct={'decoder':'d3d11va','decoder_options':{'vendor_id':identifier},'decoder_device':None}
    if vendor=='nvenc':return [{'decoder':'cuda','decoder_options':{},'decoder_device':options.get('gpu')},direct]
    if vendor=='qsv':return [direct,{'decoder':'qsv','decoder_options':{},'decoder_device':None}]
    return [direct]

def video_codec(path):
    import av
    with av.open(str(path)) as media:
        return media.streams.video[0].codec_context.codec.id

def matches_policy(path, policy):
    import av
    allowed = ['h264','hevc'] if policy == 'size' else ['h264']
    return video_codec(path) in {av.codec.Codec(name,'r').id for name in allowed}

def probe_encoder(encoder, options=None, decode_only=False):
    import av
    from av.codec.hwaccel import HWAccel
    options=options or {}
    with tempfile.TemporaryDirectory(prefix='ai-video-note-codec-') as temporary:
        path = Path(temporary)/'probe.mp4'
        with av.open(str(path),'w') as output:
            software='libx264' if encoder.startswith('h264') else 'libx265'
            stream = output.add_stream(software if decode_only else encoder,rate=25)
            stream.width = 1920
            stream.height = 1080
            stream.pix_fmt = 'yuv420p'
            stream.bit_rate = 200000
            stream.options = {'preset':'ultrafast'} if decode_only else options
            for index in range(4):
                frame = av.VideoFrame(1920,1080,'yuv420p')
                for plane in frame.planes:
                    plane.update(bytes([96 if plane == frame.planes[0] else 128])*plane.buffer_size)
                frame.pts = index
                for packet in stream.encode(frame): output.mux(packet)
            for packet in stream.encode(): output.mux(packet)
        # The same real sample must also decode in the application's software path.
        with av.open(str(path)) as media:
            assert next(media.decode(video=0),None) is not None
        errors = []
        for candidate in decoder_candidates(encoder,options):
            try:
                with av.open(str(path),hwaccel=HWAccel(candidate['decoder'],device=candidate['decoder_device'],options=candidate['decoder_options'],allow_software_fallback=False)) as media:
                    frame = next(media.decode(video=0),None)
                    if frame is not None and media.streams.video[0].codec_context.is_hwaccel:
                        assert frame.to_image().size==(1920,1080)
                        return {'available':not decode_only,'encoding_available':not decode_only,'encoder':encoder if not decode_only else None,'encoder_options':options,**candidate}
            except Exception as error:
                errors.append(type(error).__name__)
        return {'available':False,'encoding_available':not decode_only,'encoder':encoder if not decode_only else None,'encoder_options':options,'decoder':None,'reason':'hardware_decode_unverified'}

def extension_installed():
    if not IS_WINDOWS: return None
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            "[bool](Get-AppxPackage -Name '*HEVC*')"],capture_output=True,timeout=5,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode: return None
        value = result.stdout.decode('utf-8','replace').strip().lower()
        return True if value=='true' else False if value=='false' else None
    except (OSError,subprocess.TimeoutExpired): return None

def probe(devices=None, codec='hevc', check_extension=True):
    result = {'available':False,'encoding_available':False,'encoder':None,'decoder':None,'hevc_extension':extension_installed() if check_extension else None,
              'reason':'hardware_or_driver_unverified'}
    if not IS_WINDOWS: return result
    for candidate in encoder_candidates(devices,codec):
        argument=json.dumps(candidate)
        command = [sys.executable,'--video-codec-probe',argument] if getattr(sys,'frozen',False) else [sys.executable,'-m','backend.video_codecs',argument]
        try:
            process = subprocess.run(command,capture_output=True,timeout=8,
                cwd=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if process.returncode == 0:
                tested = json.loads(process.stdout.decode('utf-8'))
                if tested.get('available'):
                    result.update(tested)
                    result['reason'] = None
                    return result
                if tested.get('encoding_available') and not result.get('encoding_available'):
                    result.update(tested)
                elif tested.get('decoder') and not result.get('decoder') and not result.get('encoding_available'):
                    result.update(tested)
        except (OSError,ValueError,subprocess.TimeoutExpired): pass
    return result

def processing_hardware(codec):
    if codec not in {'h264','hevc'} or not IS_WINDOWS:return None
    with _processing_lock:
        cached=_processing_cache.get(codec)
        if cached and time.monotonic()-cached[0]<300:return dict(cached[1])
        if codec=='hevc' and _cached is not None and time.monotonic()-_cached_at<300:
            result=dict(_cached)
        else:
            from .gpu_devices import _cached_devices
            result=probe(_cached_devices,codec,check_extension=False)
        _processing_cache[codec]=(time.monotonic(),result)
        return dict(result)

async def capabilities(refresh=False):
    global _cached, _cached_at
    if refresh or _cached is None or time.monotonic()-_cached_at>300:
        from .gpu_devices import probe_devices
        devices=await probe_devices()
        _cached = await asyncio.to_thread(probe,devices)
        _cached_at = time.monotonic()
        _processing_cache['hevc']=(_cached_at,dict(_cached))
    return dict(_cached)

def child_main(encoder):
    if sys.stdout is None:
        sys.stdout = open(1,'w',encoding='utf-8',buffering=1,closefd=False)
    try:
        candidate=json.loads(encoder) if encoder.startswith('{') else {'encoder':encoder,'options':{}}
        result = probe_encoder(candidate['encoder'],candidate.get('options'))
    except Exception as error:
        try:
            result=probe_encoder(candidate['encoder'],candidate.get('options'),decode_only=True)
        except Exception:
            result = {'available':False,'reason':type(error).__name__}
    print(json.dumps(result),flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(child_main(sys.argv[1]))
