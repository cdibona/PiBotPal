import pytest
from pibotpal.media import Media


def test_default_mute_never_opens_capture(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    calls=[]
    monkeypatch.setattr('pibotpal.media.subprocess.Popen', lambda *a, **kw: calls.append(a))
    media=Media()
    assert media.status()['microphone']['muted']
    with pytest.raises(RuntimeError, match='muted'):
        media.audio_stream()
    assert calls == []
    media.microphone(True)
    assert calls == []  # consent alone does not open the device
    media.microphone(False)
    assert media.status()['microphone']['muted']


def test_mute_terminates_capture_and_stops_frames(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    class Process:
        def __init__(self):
            self.returncode=None
            self.stdout=self
        def poll(self): return self.returncode
        def terminate(self): self.returncode=-15
        def wait(self, timeout): return self.returncode
        def read(self, size): return b'\0' * size
        def close(self): pass
    process=Process()
    monkeypatch.setattr('pibotpal.media.subprocess.Popen', lambda *a, **kw: process)
    media=Media()
    media.microphone(True)
    stream=media.audio_stream()
    assert next(stream).startswith(b'RIFF')
    assert media.status()['microphone']['listeners']==1
    media.microphone(False)
    assert process.poll()==-15
    assert list(stream)==[]
    assert media.status()['microphone']['listeners']==0


def test_recording_duration_rejects_nonfinite_and_outside_limits(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    media=Media(simulate=True)
    for duration in [0,31,float('nan'),float('inf'),'5']:
        with pytest.raises(ValueError): media.record(duration)
