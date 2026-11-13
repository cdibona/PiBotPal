"""Cross-camera routing and media lifecycle tests, without real device access."""
import queue
import time
from types import SimpleNamespace

import numpy as np
import pytest
from pibotpal.camera import RequestRouter, camera_identifier
from pibotpal.media import Media


IDS = [camera_identifier('/hardware/ov5647'), camera_identifier('/hardware/imx477')]
INVENTORY = [{'id': IDS[0], 'hardware_id': '/hardware/ov5647', 'index': 1, 'model': 'ov5647', 'sensor_rotation': 0, 'connection': 'csi'},
             {'id': IDS[1], 'hardware_id': '/hardware/imx477', 'index': 0, 'model': 'imx477', 'sensor_rotation': 180, 'connection': 'csi'}]


def test_dispatch_preserves_interleaved_completions_for_both_owners():
    router = RequestRouter(None)
    first, second = queue.SimpleQueue(), queue.SimpleQueue()
    a, b = router.register(first), router.register(second)
    requests = [SimpleNamespace(cookie=b, frame=2), SimpleNamespace(cookie=a, frame=1), SimpleNamespace(cookie=b, frame=3)]
    router.dispatch(requests)
    assert first.get_nowait() is requests[1]
    assert [second.get_nowait(), second.get_nowait()] == [requests[0], requests[2]]
    router.unregister(a)
    router.dispatch([SimpleNamespace(cookie=a, frame=4)])
    assert first.empty()


@pytest.fixture
def cameras(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    monkeypatch.setattr('pibotpal.camera.list_cameras', lambda: list(reversed(INVENTORY)))
    class FakeCamera:
        opened=[]
        closed=[]
        def __init__(self, camera_id): self.id=camera_id; self.opened.append(camera_id)
        def create_video_configuration(self, **kwargs): return kwargs
        def configure(self, options): pass
        def start(self): pass
        def capture_array(self, *args):
            time.sleep(.005)
            return np.full((480,640,3), 80 if self.id==IDS[0] else 160, dtype=np.uint8)
        def close(self): self.closed.append(self.id)
    monkeypatch.setattr('pibotpal.camera.Camera', FakeCamera)
    media=Media()
    yield media,FakeCamera
    media.close()


def wait_available(media, camera_id):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        try:
            media.jpeg(camera_id)
            return
        except RuntimeError: time.sleep(.01)
    pytest.fail('Fake camera did not produce a frame')


def test_start_captures_both_cameras_and_off_is_independent(cameras):
    media,fake=cameras
    media.start()
    for camera_id in IDS: wait_available(media,camera_id)
    assert set(fake.opened)==set(IDS)
    assert media.primary_camera_id==IDS[0]  # preserve previous front sensor, not index0
    assert media.get_frame(IDS[0])[0,0,0]==80
    assert media.get_frame(IDS[1])[0,0,0]==160
    media.camera(False,IDS[1])
    assert media.jpeg(IDS[0]).startswith(b'\xff\xd8')
    with pytest.raises(RuntimeError): media.jpeg(IDS[1])
    state=media.status()
    assert state['camera']['id']==IDS[0]
    assert len(state['cameras'])==2
    assert state['microphone']['muted']
    assert next(c for c in state['cameras'] if c['id']==IDS[1])['sensor_rotation']==180


def test_primary_selection_persists_stable_identity_without_reopening(cameras, tmp_path):
    media,fake=cameras
    media.start()
    for camera_id in IDS: wait_available(media,camera_id)
    opened=list(fake.opened)
    result=media.select_primary(IDS[1])
    assert result['primary']
    assert media.get_frame()[0,0,0]==160
    assert fake.opened==opened
    assert media._camera_settings.stat().st_mode & 0o777==0o600
    reloaded=Media()
    assert reloaded.primary_camera_id==IDS[1]
    assert reloaded.list_cameras()[0]['primary']
    with pytest.raises(ValueError): media.select_primary('../../outside')


def test_missing_camera_does_not_block_service_and_ids_are_not_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA',str(tmp_path))
    monkeypatch.setattr('pibotpal.camera.list_cameras',lambda:[])
    media=Media()
    media.start()
    assert media.list_cameras()==[]
    assert media.status()['camera']['error']=='No CSI cameras detected'
    assert '/' not in IDS[0] and IDS[0]==camera_identifier('/hardware/ov5647')
    with pytest.raises(ValueError): media.camera(True,'unknown')
    media.close()
