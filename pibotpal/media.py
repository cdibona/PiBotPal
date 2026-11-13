# SPDX-License-Identifier: MIT
"""Shared camera and explicitly enabled audio. No microphone opens at startup."""
from __future__ import annotations
import io
import json
import tempfile
import math
import os
from pathlib import Path
import struct
import threading
import time
from datetime import datetime, timezone
from . import audio
from .avi import MjpegAVI


class Media:
    def __init__(self, simulate=False):
        self.simulate = simulate
        self.root = Path(os.environ.get('PIBOTPAL_DATA', '~/.local/share/pibotpal')).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.recordings = self.root / 'recordings'
        self.recordings.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._camera_stop = threading.Event()
        self._camera_thread = None
        self._frame = None
        self._jpeg = None
        self._frame_time = None
        self._camera_error = None
        self._camera_operations = threading.RLock()
        self._cameras = {}
        self._camera_inventory_loaded = False
        self._primary_camera_id = None
        self._camera_settings = self.root / 'cameras.json'
        if self._camera_settings.exists():
            try:
                settings = json.loads(self._camera_settings.read_text())
                primary = settings.get('primary_camera_id')
                if settings.get('version') != 1 or not isinstance(primary, str) or not primary.startswith('csi-') or len(primary) != 16:
                    raise ValueError('Invalid primary camera settings')
                self._primary_camera_id = primary
                os.chmod(self._camera_settings, 0o600)
            except Exception as exc:
                raise RuntimeError('Camera settings could not be read; repair cameras.json') from exc
        self._mic_enabled = False
        self._mic_error = None
        self._mic_processes = set()
        self._audio_thread = None
        self._audio_stop = threading.Event()
        self._output_pcm = None
        self._speaker_error = None
        self._record_stop = threading.Event()
        self._record_thread = None
        self._record_error = None
        self._mic_device = os.environ.get('PIBOTPAL_MIC_DEVICE', 'plughw:CARD=Device,DEV=0')
        self._speaker_device = os.environ.get('PIBOTPAL_SPEAKER_DEVICE', 'default')

    @property
    def primary_camera_id(self):
        return self._primary_camera_id

    def _discover_cameras(self):
        with self._camera_operations:
            if self._camera_inventory_loaded or self.simulate:
                return
            try:
                from .camera import list_cameras
                inventory = list_cameras()
                with self._lock:
                    for item in inventory:
                        if item['id'] not in self._cameras:
                            self._cameras[item['id']] = {**item, 'thread': None, 'stop': threading.Event(),
                                'frame': None, 'jpeg': None, 'timestamp': None, 'error': None,
                                'capture_metadata': {}, 'brightness': None}
                    if self._primary_camera_id not in self._cameras:
                        preferred = next((c['id'] for c in inventory if c['model'].lower() == 'ov5647'), None)
                        self._primary_camera_id = preferred or (inventory[0]['id'] if inventory else None)
                    self._camera_inventory_loaded = True
                    if not inventory:
                        self._camera_error = 'No CSI cameras detected'
            except Exception as exc:
                self._camera_error = str(exc)

    def _camera_status(self, slot):
        thread = slot['thread']
        stamp = slot['timestamp']
        return {key: slot[key] for key in ('id', 'hardware_id', 'index', 'model', 'sensor_rotation', 'connection')} | {
            'label': f"{slot['model'].upper()} · CSI {slot['index']}",
            'enabled': bool(thread and thread.is_alive() and not slot['stop'].is_set()),
            'available': slot['jpeg'] is not None and time.time() - (stamp or 0) <= 3,
            'error': slot['error'], 'timestamp': stamp, 'width': 640, 'height': 480,
            'capture_metadata': dict(slot.get('capture_metadata', {})),
            'brightness': slot.get('brightness'),
            'primary': slot['id'] == self._primary_camera_id,
            'video_url': f"/cameras/{slot['id']}/video.mjpg",
            'snapshot_url': f"/cameras/{slot['id']}/snapshot.jpg"}

    def list_cameras(self):
        self._discover_cameras()
        with self._lock:
            return [self._camera_status(slot) for slot in self._cameras.values()]

    def select_primary(self, camera_id):
        self._discover_cameras()
        with self._lock:
            if camera_id not in self._cameras:
                raise ValueError('Unknown CSI camera')
            fd, filename = tempfile.mkstemp(prefix='.cameras-', dir=self.root)
            try:
                with os.fdopen(fd, 'w') as out:
                    os.fchmod(out.fileno(), 0o600)
                    json.dump({'version': 1, 'primary_camera_id': camera_id}, out)
                    out.flush(); os.fsync(out.fileno())
                os.replace(filename, self._camera_settings)
            finally:
                if os.path.exists(filename): os.unlink(filename)
            self._primary_camera_id = camera_id
            self._sync_primary(self._cameras[camera_id])
            return self._camera_status(self._cameras[camera_id])

    def _sync_primary(self, slot):
        if slot['id'] == self._primary_camera_id:
            self._frame, self._jpeg = slot['frame'], slot['jpeg']
            self._frame_time, self._camera_error = slot['timestamp'], slot['error']
            self._camera_thread, self._camera_stop = slot['thread'], slot['stop']

    def start(self):
        if not self.simulate:
            self._discover_cameras()
            for camera_id in tuple(self._cameras):
                self.camera(True, camera_id)

    def close(self):
        self.stop()
        for camera_id in tuple(self._cameras):
            self.camera(False, camera_id)

    def status(self):
        cameras = self.list_cameras()
        with self._lock:
            recent = sorted(self.recordings.glob('*.avi'), reverse=True)[:20]
            primary = next((item for item in cameras if item['primary']), None)
            return {
                'camera': primary or {'enabled': False, 'available': self._jpeg is not None,
                           'error': self._camera_error, 'timestamp': self._frame_time,
                           'width': 640, 'height': 480},
                'cameras': cameras, 'primary_camera_id': self._primary_camera_id,
                'microphone': {'enabled': self._mic_enabled, 'muted': not self._mic_enabled,
                               'available': audio.available() and not self.simulate,
                               'error': self._mic_error, 'listeners': len(self._mic_processes)},
                'speaker': {'available': audio.available() and not self.simulate,
                            'busy': bool(self._audio_thread and self._audio_thread.is_alive()), 'error': self._speaker_error},
                'recording': {'active': bool(self._record_thread and self._record_thread.is_alive()),
                              'error': self._record_error},
                'recordings': [{'name': p.name, 'url': '/recordings/' + p.name, 'bytes': p.stat().st_size} for p in recent],
            }

    def camera(self, enabled: bool, camera_id=None):
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        self._discover_cameras()
        camera_id = camera_id or self._primary_camera_id
        if self.simulate and enabled:
            raise RuntimeError('Camera unavailable in simulation')
        if camera_id is None and not enabled:
            return self.status()['camera']
        with self._camera_operations:
            with self._lock:
                slot = self._cameras.get(camera_id)
                if slot is None:
                    raise ValueError('Unknown CSI camera')
                thread = slot['thread']
                if enabled:
                    if thread and thread.is_alive():
                        if slot['stop'].is_set():
                            raise RuntimeError('Camera is still stopping')
                        return self._camera_status(slot)
                    slot['stop'].clear()
                    slot['error'] = None
                    slot['thread'] = threading.Thread(target=self._capture, args=(slot,), daemon=True,
                                                      name='camera-' + camera_id)
                    self._sync_primary(slot)
                    slot['thread'].start()
                    return self._camera_status(slot)
                slot['stop'].set()
            if thread:
                thread.join(timeout=4)
            with self._lock:
                slot['frame'] = slot['jpeg'] = None
                self._sync_primary(slot)
                return self._camera_status(slot)

    def _capture(self, slot):
        cam = None
        try:
            from PIL import Image
            from .camera import Camera
            cam = Camera(slot['id'])
            cam.configure(cam.create_video_configuration(main={'size': (640, 480), 'format': 'RGB888'},
                                                          controls={'FrameRate': 12}))
            cam.start()
            while not slot['stop'].is_set():
                frame = cam.capture_array('main')
                encoded = io.BytesIO()
                Image.fromarray(frame[:, :, ::-1]).save(encoded, format='JPEG', quality=78)
                with self._lock:
                    slot['frame'], slot['jpeg'] = frame, encoded.getvalue()
                    slot['timestamp'] = time.time()
                    slot['capture_metadata'] = dict(getattr(cam, 'last_metadata', {}))
                    slot['brightness'] = round(float(frame[::16, ::16].mean()), 2)
                    self._sync_primary(slot)
        except Exception as exc:
            with self._lock:
                slot['error'] = str(exc)
        finally:
            if cam:
                try:
                    cam.close()
                except Exception as exc:
                    with self._lock:
                        slot['error'] = slot['error'] or str(exc)
            with self._lock:
                slot['frame'] = slot['jpeg'] = None
                self._sync_primary(slot)

    def jpeg(self, camera_id=None):
        with self._lock:
            if camera_id is None:
                jpeg, stamp, error = self._jpeg, self._frame_time, self._camera_error
            else:
                slot = self._cameras.get(camera_id)
                if slot is None:
                    raise ValueError('Unknown CSI camera')
                jpeg, stamp, error = slot['jpeg'], slot['timestamp'], slot['error']
            if jpeg is None or time.time() - (stamp or 0) > 3:
                raise RuntimeError(error or 'Camera is off or starting')
            return jpeg

    def get_frame(self, camera_id=None):
        with self._lock:
            self.jpeg(camera_id)
            return (self._frame if camera_id is None else self._cameras[camera_id]['frame']).copy()

    def detect_color(self, color='red'):
        from PIL import Image
        import numpy as np
        bounds = {'red': [(0, 14), (241, 255)], 'blue': [(135, 185)], 'green': [(50, 121)],
                  'yellow': [(28, 50)], 'orange': [(14, 28)], 'purple': [(185, 234)]}
        if color not in bounds:
            raise ValueError('Unknown color')
        frame = self.get_frame()
        hsv = np.asarray(Image.fromarray(frame[:, :, ::-1]).convert('HSV'))
        hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        mask = np.zeros(hue.shape, dtype=bool)
        for low, high in bounds[color]:
            mask |= (hue >= low) & (hue <= high)
        mask &= (saturation >= 100) & (value >= 60)
        # Connected runs union only adjacent rows: choose one physical blob,
        # so isolated speckles cannot combine into a false large target.
        parents, areas, boxes = [], [], []
        def root(label):
            while parents[label] != label:
                parents[label] = parents[parents[label]]
                label = parents[label]
            return label
        previous = []
        for y, row in enumerate(mask):
            changes = np.diff(np.r_[False, row, False].astype(np.int8))
            current = []
            for left, right in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
                label = len(parents)
                parents.append(label); areas.append(int(right-left)); boxes.append([int(left),y,int(right),y+1])
                for pl, pr, other in previous:
                    if pl <= right and pr >= left:
                        a, b = root(label), root(other)
                        if a != b:
                            parents[b] = a
                            areas[a] += areas[b]
                            boxes[a] = [min(boxes[a][0],boxes[b][0]),min(boxes[a][1],boxes[b][1]),
                                        max(boxes[a][2],boxes[b][2]),max(boxes[a][3],boxes[b][3])]
                current.append((left,right,label))
            previous = current
        candidates = [i for i in range(len(parents)) if root(i) == i]
        if not candidates:
            return {'detected': False, 'x': 0, 'y': 0, 'width': 0, 'height': 0, 'area': 0}
        largest = max(candidates,key=lambda i: areas[i])
        x,y,right,bottom = boxes[largest]
        return {'detected': areas[largest] > 300, 'x': (x+right)/2, 'y': (y+bottom)/2,
                'width': right-x, 'height': bottom-y, 'area': areas[largest],
                'frame_width': frame.shape[1], 'frame_height': frame.shape[0]}

    def record(self, seconds=10):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 30:
            raise ValueError('Recording duration must be 1–30 seconds')
        self.jpeg()
        with self._lock:
            if self._record_thread and self._record_thread.is_alive():
                raise RuntimeError('Already recording')
            if sum(p.stat().st_size for p in self.recordings.glob('*.avi')) > 1024**3:
                raise RuntimeError('Recording storage reached 1 GiB; remove old recordings locally')
            self._record_stop.clear()
            self._record_error = None
            name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ.avi')
            self._record_thread = threading.Thread(target=self._record, args=(name, seconds), daemon=True)
            self._record_thread.start()
        return {'name': name, 'url': '/recordings/' + name, 'seconds': seconds}

    def _record(self, name, seconds):
        writer = None
        try:
            writer = MjpegAVI(self.recordings / name)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not self._record_stop.is_set():
                writer.write(self.jpeg())
                self._record_stop.wait(0.1)
        except Exception as exc:
            self._record_error = str(exc)
        finally:
            if writer:
                writer.close()

    def microphone(self, enabled: bool):
        with self._lock:
            if enabled and (self.simulate or not audio.available()):
                raise RuntimeError('Microphone unavailable')
            self._mic_enabled = enabled
            if not enabled:
                for device in tuple(self._mic_processes):
                    device.stop()
        return self.status()['microphone']

    def audio_stream(self):
        with self._lock:
            if not self._mic_enabled:
                raise RuntimeError('Microphone is muted')
            if self._mic_processes:
                raise RuntimeError('Microphone already has a listener')
            device = audio.PCM(self._mic_device, capture=True)
            self._mic_processes.add(device)
        def chunks():
            try:
                yield struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 0x7fffffff, b'WAVE', b'fmt ', 16,
                                  1, 1, 16000, 32000, 2, 16, b'data', 0x7fffffdb)
                while self._mic_enabled:
                    chunk = device.read(1600)
                    if not chunk:
                        break
                    if self._mic_enabled:
                        yield chunk
            except Exception as exc:
                self._mic_error = str(exc)
            finally:
                device.close()
                with self._lock:
                    self._mic_processes.discard(device)
        return chunks()

    def _speaker_ready(self):
        if self.simulate:
            raise RuntimeError('Speaker unavailable in simulation')
        if self._audio_thread and self._audio_thread.is_alive():
            raise RuntimeError('Speaker is busy')
        self._speaker_error = None
        self._audio_stop.clear()

    def _play(self, generate):
        device = None
        try:
            data, rate, channels = generate()
            if self._audio_stop.is_set():
                return
            from .hat import enable_speaker
            enable_speaker()
            device = audio.PCM(self._speaker_device, rate=rate, channels=channels)
            with self._lock:
                self._output_pcm = device
                if self._audio_stop.is_set():
                    device.stop()
            device.write(data)
            # Allow the final buffered samples to play while retaining cancellation.
            self._audio_stop.wait(.15)
        except Exception as exc:
            self._speaker_error = str(exc)
        finally:
            if device:
                device.close()
            from .hat import disable_speaker
            disable_speaker()
            with self._lock:
                self._output_pcm = None

    def _start_audio(self, generate):
        self._speaker_ready()
        self._audio_thread = threading.Thread(target=self._play, args=(generate,), daemon=True, name='speaker')
        self._audio_thread.start()

    def speak(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise ValueError('Speech must contain 1–500 characters')
        with self._lock:
            self._start_audio(lambda: audio.speech_wave(text))
        return {'speaking': True, 'text': text}

    def play_sound(self, name):
        # Validate before starting the background worker.
        data = audio.effect_wave(name)
        with self._lock:
            self._start_audio(lambda: data)
        return {'playing': name, 'maximum_seconds': 30}

    def stop(self):
        self._record_stop.set()
        self.microphone(False)
        self._audio_stop.set()
        with self._lock:
            if self._output_pcm:
                self._output_pcm.stop()
