"""Cross-camera routing and media lifecycle tests, without real device access."""
import queue
import json
import struct
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from pibotpal.camera import RequestRouter, camera_identifier
from pibotpal.media import Media


IDS = [camera_identifier('/hardware/ov5647'), camera_identifier('/hardware/imx477')]
INVENTORY = [{'id': IDS[0], 'hardware_id': '/hardware/ov5647', 'index': 1, 'model': 'ov5647', 'sensor_rotation': 0, 'connection': 'csi'},
             {'id': IDS[1], 'hardware_id': '/hardware/imx477', 'index': 0, 'model': 'imx477', 'sensor_rotation': 180, 'connection': 'csi'}]
for sensor in INVENTORY:
    sensor['resolutions'] = [{'width': 320, 'height': 240, 'frame_rate': 12},
                             {'width': 640, 'height': 480, 'frame_rate': 12},
                             {'width': 1280, 'height': 720, 'frame_rate': 12}]
    sensor['sensor_resolution'] = {'width': 1280, 'height': 720}


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
        failed_sizes=set()
        def __init__(self, camera_id): self.id=camera_id; self.opened.append(camera_id)
        def create_video_configuration(self, **kwargs): return kwargs
        def configure(self, options):
            self.size=options['main']['size']
            if self.size in self.failed_sizes:
                raise RuntimeError('Synthetic capture configuration failure')
        def start(self): pass
        def capture_array(self, *args):
            time.sleep(.005)
            return np.full((self.size[1],self.size[0],3), 80 if self.id==IDS[0] else 160, dtype=np.uint8)
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


def test_capture_metadata_is_selected_numeric_and_json_safe():
    from pibotpal.camera import capture_metadata
    class Control:
        def __init__(self, name): self.name=name
    assert capture_metadata({Control('ExposureTime'): 25000,
                             Control('AnalogueGain'): 8.0,
                             Control('SensorTimestamp'): 123456789000,
                             Control('Lux'): float('nan'),
                             Control('UnknownControl'): 'private',
                             'DigitalGain': 1.5}) == {
          'ExposureTime': 25000, 'AnalogueGain': 8.0,
          'SensorTimestamp': 123456789000, 'DigitalGain': 1.5}


def test_camera_status_exposes_brightness_and_read_only_diagnostics(cameras):
    media, fake=cameras
    media.start()
    for camera_id in IDS: wait_available(media,camera_id)
    rows={row['id']:row for row in media.list_cameras()}
    assert rows[IDS[0]]['brightness']==80
    assert rows[IDS[1]]['brightness']==160
    assert rows[IDS[0]]['capture_metadata']=={}


def test_resolution_change_is_independent_and_persists_only_after_frame(cameras):
    from PIL import Image
    from io import BytesIO
    media, fake = cameras
    media.start()
    for camera_id in IDS: wait_available(media, camera_id)
    result = media.configure_camera(IDS[1], 1280, 720)
    assert (result['width'], result['height']) == (1280, 720)
    assert result['available'] and result['frame_rate'] == 12
    assert fake.opened.count(IDS[0]) == 1  # Other capture never restarts.
    assert fake.opened.count(IDS[1]) == 2
    assert Image.open(BytesIO(media.jpeg(IDS[1]))).size == (1280, 720)
    assert Image.open(BytesIO(media.jpeg(IDS[0]))).size == (640, 480)
    assert media._camera_settings.stat().st_mode & 0o777 == 0o600
    media.select_primary(IDS[1])
    reloaded = Media()
    selected = next(item for item in reloaded.list_cameras() if item['id'] == IDS[1])
    assert selected['primary'] and (selected['width'], selected['height']) == (1280, 720)
    assert not selected['enabled']


def test_resolution_failure_rolls_back_capture_and_saved_preferences(cameras):
    media, fake = cameras
    media.start()
    for camera_id in IDS: wait_available(media, camera_id)
    media.configure_camera(IDS[1], 320, 240)
    saved = media._camera_settings.read_bytes()
    fake.failed_sizes.add((1280, 720))
    with pytest.raises(RuntimeError, match='Previous resolution restored'):
        media.configure_camera(IDS[1], 1280, 720)
    result = next(item for item in media.list_cameras() if item['id'] == IDS[1])
    assert result['available'] and result['error'] is None
    assert (result['width'], result['height']) == (320, 240)
    assert media._camera_settings.read_bytes() == saved
    assert fake.opened.count(IDS[0]) == 1


def test_off_camera_resolution_does_not_start_capture_and_invalid_input_does_not_stop(cameras):
    media, fake = cameras
    media.start()
    for camera_id in IDS: wait_available(media, camera_id)
    count = len(fake.opened)
    for width, height in [(999999, 240), (True, 480), (640.0, 480), (0, 0)]:
        with pytest.raises(ValueError):
            media.configure_camera(IDS[0], width, height)
    assert len(fake.opened) == count
    assert media.jpeg(IDS[0])
    media.camera(False, IDS[1])
    result = media.configure_camera(IDS[1], 320, 240)
    assert not result['enabled'] and not result['available']
    assert (result['width'], result['height']) == (320, 240)
    assert len(fake.opened) == count
    media.camera(True, IDS[1])
    wait_available(media, IDS[1])
    assert media.get_frame(IDS[1]).shape == (240, 320, 3)


def test_recording_pins_source_and_dimensions_and_blocks_reconfiguration(cameras):
    from PIL import Image
    from io import BytesIO
    media, fake = cameras
    media.start()
    for camera_id in IDS: wait_available(media, camera_id)
    media.configure_camera(IDS[0], 320, 240)
    recording = media.record(1)
    media.select_primary(IDS[1])
    with pytest.raises(RuntimeError, match='Finish the current recording'):
        media.configure_camera(IDS[0], 640, 480)
    media.configure_camera(IDS[1], 1280, 720)  # Unrelated camera remains configurable.
    media._record_thread.join(timeout=2)
    assert not media._record_thread.is_alive() and media._record_error is None
    data = (media.recordings / recording['name']).read_bytes()
    avih = data.index(b'avih') + 8
    assert struct.unpack_from('<II', data, avih + 32) == (320, 240)
    first = data.index(b'00dc') + 8
    jpeg_size = struct.unpack_from('<I', data, first - 4)[0]
    image = Image.open(BytesIO(data[first:first + jpeg_size]))
    assert image.size == (320, 240)
    assert image.getpixel((0, 0)) == (80, 80, 80)


def test_camera_preferences_accept_legacy_selection_and_reject_malformed_size(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    path = tmp_path / 'cameras.json'
    path.write_text(json.dumps({'version': 1, 'primary_camera_id': IDS[0]}))
    assert Media().primary_camera_id == IDS[0]
    path.write_text(json.dumps({'version': 1, 'primary_camera_id': IDS[0],
                              'resolutions': {IDS[0]: {'width': True, 'height': 480}}}))
    with pytest.raises(RuntimeError, match='Camera settings could not be read'):
        Media()


def test_telemetry_does_not_wait_for_camera_reconfiguration(cameras):
    media, fake = cameras
    media.list_cameras()  # Inventory is already available before configuring.
    locked, release, read = threading.Event(), threading.Event(), threading.Event()
    def configure_in_progress():
        with media._camera_operations:
            locked.set()
            release.wait(timeout=2)
    operation = threading.Thread(target=configure_in_progress)
    operation.start()
    assert locked.wait(timeout=1)
    status = threading.Thread(target=lambda: (media.status(), read.set()))
    status.start()
    try:
        assert read.wait(timeout=.5), 'Telemetry blocked behind camera configuration'
    finally:
        release.set()
        operation.join(timeout=2)
        status.join(timeout=2)


def test_capabilities_validate_output_without_acquiring_and_reject_upscaling():
    from pibotpal.camera import camera_capabilities
    class Size:
        def __init__(self, width, height): self.width, self.height = width, height
    class Formats:
        pixel_formats = ['RAW']
        def sizes(self, pixel_format): return [Size(640, 480), Size(9000, 9000)]
        def range(self, pixel_format):
            return SimpleNamespace(max=Size(2592, 1944), contains=lambda size: 32 <= size.width <= 2592 and 32 <= size.height <= 1944)
    class Configuration:
        def __init__(self): self.stream = SimpleNamespace(formats=Formats())
        def at(self, index): return self.stream
        def validate(self):
            if self.stream.size.width == 800:
                self.stream.size = Size(640, 480)  # libcamera can silently adjust.
            return 'valid'
    device = SimpleNamespace(generate_configuration=lambda roles: Configuration())
    api = SimpleNamespace(StreamRole=SimpleNamespace(Viewfinder='preview', Raw='raw'),
                          PixelFormat=lambda value: value, Size=Size,
                          CameraConfiguration=SimpleNamespace(Status=SimpleNamespace(Invalid='invalid')))
    result = camera_capabilities(device, api)
    sizes = {(row['width'], row['height']): row['frame_rate'] for row in result['resolutions']}
    assert sizes[(160, 120)] == 12 and sizes[(2592, 1944)] == 4
    assert (800, 600) not in sizes and (9000, 9000) not in sizes
    assert result['sensor_resolution'] == {'width': 2592, 'height': 1944}
