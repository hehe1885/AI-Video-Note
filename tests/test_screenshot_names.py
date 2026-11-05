from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from backend import screenshot_files, gpu_video_worker, video_codecs
from backend.video_processor import VideoProcessor
from tests.test_video_download_settings import make_video


def test_repeat_and_concurrent_screenshots_never_share_names(tmp_path):
    image=Image.new('RGB',(32,24),'red')
    directory=tmp_path/'task-one'/'frames'
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths=list(pool.map(lambda _:screenshot_files.save_frame(image,directory,12.5),range(12)))
    assert len({path.name for path in paths})==12
    assert all('task-one_12500ms_' in path.name for path in paths)
    assert all(Image.open(path).size==(32,24) for path in paths)


def test_forced_uuid_collision_retries_without_overwriting_existing_image(tmp_path,monkeypatch):
    identifiers=iter(['a'*32,'a'*32,'b'*32])
    monkeypatch.setattr(screenshot_files.uuid,'uuid4',lambda:SimpleNamespace(hex=next(identifiers)))
    first=screenshot_files.save_frame(Image.new('RGB',(32,24),'red'),tmp_path,1)
    original=first.read_bytes()
    second=screenshot_files.save_frame(Image.new('RGB',(32,24),'blue'),tmp_path,1)
    assert first!=second and first.read_bytes()==original


def test_failed_image_write_removes_only_its_partial_output(tmp_path):
    existing=tmp_path/'keep.jpg';existing.write_bytes(b'keep')
    class Broken:
        def save(self,output,**kwargs):
            output.write(b'partial')
            raise OSError('failed write')
    with pytest.raises(OSError):screenshot_files.save_frame(Broken(),tmp_path,1)
    assert list(tmp_path.iterdir())==[existing]


def test_cpu_recapture_preserves_previous_frames(tmp_path):
    source=tmp_path/'video.mp4';make_video(source,64,48)
    processor=VideoProcessor(tmp_path/'workspace')
    first=processor._extract_frames_at_cpu(source,'task',[.5])[.5]
    original=first.read_bytes()
    second=processor._extract_frames_at_cpu(source,'task',[.5])[.5]
    assert first!=second and first.read_bytes()==original
    timed_one=processor._extract_frames_cpu(source,'task',1)
    timed_two=processor._extract_frames_cpu(source,'task',1)
    assert not ({p.name for p in timed_one}&{p.name for p in timed_two})


def test_gpu_commit_uses_unique_names_even_if_worker_reuses_a_temporary_name(tmp_path,monkeypatch):
    source=tmp_path/'video.mp4';make_video(source,64,48)
    monkeypatch.setattr(video_codecs,'processing_hardware',lambda codec:{'encoder':None,'decoder':'d3d11va'})
    def worker(job_file,*args):
        job=json.loads(Path(job_file).read_text(encoding='utf-8'))
        Image.new('RGB',(64,48),'red').save(Path(job['destination'])/'frame.jpg')
        return {'ok':True,'images':[{'time':.5,'file':'frame.jpg'}]}
    monkeypatch.setattr(gpu_video_worker,'_run_process',worker)
    frames=tmp_path/'task'/'frames';frames.mkdir(parents=True)
    old=frames/'frame.jpg';old.write_bytes(b'old attachment')
    first=gpu_video_worker.try_operation('screenshots',source,frames_directory=frames,timestamps=[.5])[.5]
    second=gpu_video_worker.try_operation('screenshots',source,frames_directory=frames,timestamps=[.5])[.5]
    assert first.name!=second.name and first.is_file() and second.is_file()
    assert old.read_bytes()==b'old attachment'
