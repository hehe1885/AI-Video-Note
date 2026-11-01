from pathlib import Path
import hashlib,threading,re
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from .cache_index import index_path, read_entries, update_entries
_locks={};_guard=threading.Lock()
def cache_lock(path):
    with _guard:return _locks.setdefault(str(path),threading.Lock())
def cache_directory(root,url,height,encoding='compatibility'):
    p=urlsplit(url.strip());host=(p.hostname or '').lower();query=p.query
    if host=='bilibili.com' or host.endswith('.bilibili.com'):
        query=urlencode([(k,v) for k,v in parse_qsl(query) if k=='p' and v!='1'])
    canonical=urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/'),query,''))
    identity = f'{canonical}|{int(height)}' + ('|hevc-preferred' if encoding == 'size' else '')
    return Path(root).resolve()/'_cache'/hashlib.sha256(identity.encode()).hexdigest()[:32]
def inspect_video(path):
    import av
    with av.open(str(path)) as media:
        if not media.streams.video or next(media.decode(video=0),None) is None:raise ValueError('视频文件损坏或没有画面')
        return bool(media.streams.audio)
def read_index(root,workspace=None):
    return read_entries(root, 'video', workspace)
def indexed_path(directory,workspace=None):
    root=directory.parent.parent
    entry=read_index(root,workspace).get(directory.name) or {}
    filename=entry.get('filename')
    if not isinstance(filename,str):return None
    path=(root/filename).resolve()
    if path.parent!=root.resolve() or path.suffix.lower() not in {'.mp4','.mkv','.webm','.m4s','.mov','.avi'}:return None
    return path
def find_cached(directory,workspace=None,encoding=None):
    try:
        path=indexed_path(directory,workspace)
        if path is None or not path.is_file():return None
        if encoding:
            from .video_codecs import matches_policy
            if not matches_policy(path,encoding):return None
        entry=read_index(directory.parent.parent,workspace).get(directory.name) or {}
        if not inspect_video(path) and entry.get('audio_state')!='source_silent':return None
        return path
    except (OSError,ValueError):return None
def record_cache(directory,path,has_audio,*,source_url='',title='',height=720,storage_id='',workspace=None):
    root=directory.parent.parent;root.mkdir(parents=True,exist_ok=True)
    identifier=storage_id if re.fullmatch(r'[A-Za-z0-9_-]{1,128}',storage_id) else directory.name
    with cache_lock(index_path(root,'video',workspace)):
        target=root/(identifier+path.suffix.lower())
        if target.exists() and target!=path:
            target=root/(identifier+'-'+directory.name[:8]+path.suffix.lower())
        path.replace(target)
        host=(urlsplit(source_url).hostname or '').lower()
        platform='bilibili' if host=='bilibili.com' or host.endswith('.bilibili.com') else 'youtube' if host in {'youtube.com','www.youtube.com','m.youtube.com','youtu.be'} else 'douyin' if host.endswith('.douyin.com') or host=='douyin.com' else host or 'unknown'
        entry={'platform':platform,'source_url':source_url,'title':title,
            'resolution':int(height),'filename':target.name,'download_task_id':storage_id,
            'audio_state':'present' if has_audio else 'source_silent','saved_at':datetime.now(timezone.utc).isoformat()}
        try:
            import av
            from .video_codecs import video_codec
            identity = video_codec(target)
            entry['video_codec'] = 'h265' if identity == av.codec.Codec('hevc','r').id else 'h264' if identity == av.codec.Codec('h264','r').id else 'other'
        except (OSError,ValueError):
            pass
        update_entries(root,'video',{directory.name:entry},workspace)
    return target
def is_cache_path(path,workspace,manifest):
    root=manifest.get('video_download_directory') or Path(workspace)/'video'
    source=manifest.get('source_url')
    if not source or not Path(root).is_absolute():return False
    expected=indexed_path(cache_directory(root,source,manifest.get('video_resolution',720),manifest.get('video_encoding','compatibility')),workspace)
    return expected is not None and path.resolve()==expected
