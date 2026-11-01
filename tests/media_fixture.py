from fractions import Fraction
import av
import numpy as np
from tests.test_video_download_settings import make_video
from backend.video_processor import VideoProcessor
def make_audio(path):
    with av.open(str(path),'w') as output:
        stream=output.add_stream('aac',rate=16000);stream.layout='mono'
        for start in range(0,32768,1024):
            data=(.25*np.sin(2*np.pi*440*np.arange(start,start+1024)/16000)).astype('float32')[None,:]
            frame=av.AudioFrame.from_ndarray(data,format='fltp',layout='mono');frame.sample_rate=16000;frame.pts=start;frame.time_base=Fraction(1,16000)
            for packet in stream.encode(frame):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
def make_av_video(path,width=64,height=48):
    make_video(path,width,height);audio=path.with_name('sound.m4a');make_audio(audio)
    merged=VideoProcessor._merge_preview_audio(path,audio);merged.replace(path);audio.unlink()
