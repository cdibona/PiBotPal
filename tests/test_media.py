import io
import struct
import time
import pytest
from PIL import Image
from pibotpal.media import Media
from pibotpal.avi import MjpegAVI
from pibotpal.audio import effect_wave, SOUNDS


def test_default_mute_never_opens_capture(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    calls=[]
    monkeypatch.setattr('pibotpal.audio.PCM', lambda *a, **kw: calls.append(a))
    monkeypatch.setattr('pibotpal.audio.available', lambda: True)
    media=Media()
    assert media.status()['microphone']['muted']
    with pytest.raises(RuntimeError, match='muted'):
        media.audio_stream()
    media.microphone(True)
    assert calls == []
    media.microphone(False)
    assert media.status()['microphone']['muted']


def test_mute_terminates_capture_and_stops_frames(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    class Device:
        stopped=False
        closed=False
        def read(self, size): return b'\0' * size
        def stop(self): self.stopped=True
        def close(self): self.closed=True
    device=Device()
    monkeypatch.setattr('pibotpal.audio.PCM', lambda *a, **kw: device)
    monkeypatch.setattr('pibotpal.audio.available', lambda: True)
    media=Media()
    media.microphone(True)
    stream=media.audio_stream()
    assert next(stream).startswith(b'RIFF')
    assert media.status()['microphone']['listeners']==1
    media.microphone(False)
    assert device.stopped
    assert list(stream)==[]
    assert device.closed
    assert media.status()['microphone']['listeners']==0


def test_recording_duration_rejects_nonfinite_and_outside_limits(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    media=Media(simulate=True)
    for duration in [0,31,True,float('nan'),float('inf'),'5']:
        with pytest.raises(ValueError): media.record(duration)


def test_avi_preserves_jpeg_payload_and_indexes_frames(tmp_path):
    stream=io.BytesIO()
    Image.new('RGB',(640,480),(255,0,0)).save(stream,'JPEG')
    jpeg=stream.getvalue()
    path=tmp_path/'test.avi'
    writer=MjpegAVI(path)
    writer.write(jpeg)
    writer.write(jpeg)
    writer.close()
    data=path.read_bytes()
    assert data[:4]==b'RIFF'
    assert struct.unpack_from('<I',data,4)[0]==len(data)-8
    assert data.count(jpeg)==2
    idx=data.index(b'idx1')
    assert struct.unpack_from('<I',data,idx+4)[0]==32
    movi=data.index(b'movi')
    tag,flags,offset,length=struct.unpack_from('<4sIII',data,idx+8)
    assert tag==b'00dc' and length==len(jpeg)
    assert data[movi+offset+8:movi+offset+8+length]==jpeg


def test_color_chooses_connected_target_over_separate_speckles(tmp_path, monkeypatch):
    import numpy as np
    monkeypatch.setenv('PIBOTPAL_DATA',str(tmp_path))
    media=Media(simulate=True)
    frame=np.zeros((480,640,3),dtype=np.uint8)
    frame[100:130,200:230]=[0,0,255]
    frame[::20,::20]=[0,0,255]
    media._frame=frame
    media._jpeg=b'jpeg'
    media._frame_time=time.time()
    result=media.detect_color('red')
    assert result['detected']
    assert result['area']>=900
    assert abs(result['x']-215)<=1 and abs(result['y']-115)<=1
    assert not media.detect_color('green')['detected']


def test_effects_are_bounded_original_pcm():
    for name in SOUNDS:
        data,rate,channels=effect_wave(name+'.wav')
        assert rate==16000 and channels==1 and 0<len(data)<rate*2
    with pytest.raises(ValueError): effect_wave('../example.mp3')
