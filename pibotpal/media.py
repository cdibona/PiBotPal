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
from collections import deque
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
        self._camera_preferences = {}
        self._camera_settings = self.root / 'cameras.json'
        if self._camera_settings.exists():
            try:
                settings = json.loads(self._camera_settings.read_text())
                primary = settings.get('primary_camera_id')
                if settings.get('version') != 1 or not isinstance(primary, str) or not primary.startswith('csi-') or len(primary) != 16:
                    raise ValueError('Invalid primary camera settings')
                self._primary_camera_id = primary
                preferences = settings.get('resolutions', {})
                if not isinstance(preferences, dict):
                    raise ValueError('Invalid camera resolutions')
                for identifier, size in preferences.items():
                    if (not isinstance(identifier, str) or not identifier.startswith('csi-') or len(identifier) != 16
                            or not isinstance(size, dict) or set(size) != {'width', 'height'}
                            or any(type(size[key]) is not int or not 1 <= size[key] <= 16384 for key in size)):
                        raise ValueError('Invalid camera resolution')
                self._camera_preferences = preferences
                os.chmod(self._camera_settings, 0o600)
            except Exception as exc:
                raise RuntimeError('Camera settings could not be read; repair cameras.json') from exc
        self._mic_enabled = False
        self._mic_error = None
        self._mic_processes = set()
        self._audio_thread = None
        self._audio_stop = threading.Event()
        self._audio_queue = deque()
        self._audio_active = None
        self._audio_closed = False
        self._output_pcm = None
        self._speaker_error = None
        self._record_stop = threading.Event()
        self._record_thread = None
        self._record_error = None
        self._record_camera_id = None
        self._mic_device = os.environ.get('PIBOTPAL_MIC_DEVICE', 'plughw:CARD=Device,DEV=0')
        self._speaker_device = os.environ.get('PIBOTPAL_SPEAKER_DEVICE', 'default')

    @property
    def primary_camera_id(self):
        return self._primary_camera_id

    def _discover_cameras(self):
        # Telemetry must remain responsive while another thread reconfigures a
        # camera (which holds _camera_operations while waiting for a frame).
        if self._camera_inventory_loaded or self.simulate:
            return
        with self._camera_operations:
            if self._camera_inventory_loaded or self.simulate:
                return
            try:
                from .camera import list_cameras
                inventory = list_cameras()
                with self._lock:
                    for item in inventory:
                        if item['id'] not in self._cameras:
                            from .camera import stream_frame_rate
                            resolutions = item.get('resolutions') or [{'width': 640, 'height': 480, 'frame_rate': 12}]
                            preferred = self._camera_preferences.get(item['id'], {'width': 640, 'height': 480})
                            mode = next((size for size in resolutions if (size['width'], size['height']) == (preferred['width'], preferred['height'])), None)
                            mode = mode or next((size for size in resolutions if (size['width'], size['height']) == (640, 480)), resolutions[0])
                            self._cameras[item['id']] = {**item, 'thread': None, 'stop': threading.Event(),
                                'frame': None, 'jpeg': None, 'timestamp': None, 'error': None,
                                'capture_metadata': {}, 'brightness': None, 'ready': threading.Event(),
                                'resolutions': resolutions, 'width': mode['width'], 'height': mode['height'],
                                'frame_rate': stream_frame_rate(mode['width'], mode['height'])}
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
            'error': slot['error'], 'timestamp': stamp,
            'width': slot.get('width', 640), 'height': slot.get('height', 480),
            'frame_rate': slot.get('frame_rate', 12),
            'resolutions': [dict(mode) for mode in slot.get('resolutions', [])],
            'sensor_resolution': dict(slot.get('sensor_resolution', {})),
            'capture_metadata': dict(slot.get('capture_metadata', {})),
            'brightness': slot.get('brightness'),
            'primary': slot['id'] == self._primary_camera_id,
            'video_url': f"/cameras/{slot['id']}/video.mjpg",
            'snapshot_url': f"/cameras/{slot['id']}/snapshot.jpg"}

    def list_cameras(self):
        self._discover_cameras()
        with self._lock:
            return [self._camera_status(slot) for slot in self._cameras.values()]

    def _save_camera_settings(self, primary, preferences):
        fd, filename = tempfile.mkstemp(prefix='.cameras-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as out:
                os.fchmod(out.fileno(), 0o600)
                json.dump({'version': 1, 'primary_camera_id': primary, 'resolutions': preferences}, out)
                out.flush(); os.fsync(out.fileno())
            os.replace(filename, self._camera_settings)
        finally:
            if os.path.exists(filename): os.unlink(filename)

    def select_primary(self, camera_id):
        self._discover_cameras()
        with self._camera_operations, self._lock:
            if camera_id not in self._cameras:
                raise ValueError('Unknown CSI camera')
            self._save_camera_settings(camera_id, self._camera_preferences)
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
        with self._lock:
            self._audio_closed = True
        self.stop()
        thread = self._audio_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
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
                            'busy': bool(self._audio_active or self._audio_queue),
                            'queued_announcements': sum(job[0] is not None for job in self._audio_queue),
                            'error': self._speaker_error},
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
                    slot.setdefault('ready', threading.Event()).clear()
                    slot['thread'] = threading.Thread(target=self._capture, args=(slot,), daemon=True,
                                                      name='camera-' + camera_id)
                    self._sync_primary(slot)
                    slot['thread'].start()
                    return self._camera_status(slot)
                slot['stop'].set()
            if thread:
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError('Camera is still stopping; try again after capture finishes')
            with self._lock:
                slot['frame'] = slot['jpeg'] = None
                self._sync_primary(slot)
                return self._camera_status(slot)

    def _wait_camera(self, slot):
        if not slot['ready'].wait(timeout=8):
            raise RuntimeError('Camera did not produce a frame in time')
        with self._lock:
            if slot['jpeg'] is None or slot['error']:
                raise RuntimeError(slot['error'] or 'Camera stopped before producing a frame')

    def configure_camera(self, camera_id, width, height):
        """Apply one advertised resolution, restoring the previous mode on failure."""
        if type(width) is not int or type(height) is not int:
            raise ValueError('Camera width and height must be integers')
        self._discover_cameras()
        with self._camera_operations:
            with self._lock:
                slot = self._cameras.get(camera_id)
                if slot is None:
                    raise ValueError('Unknown CSI camera')
                mode = next((size for size in slot.get('resolutions', []) if (size['width'], size['height']) == (width, height)), None)
                if mode is None:
                    raise ValueError('Resolution is not supported by this camera; use camera_list to select a supported size')
                if slot['thread'] and slot['thread'].is_alive() and slot['stop'].is_set():
                    raise RuntimeError('Camera is still stopping; try again after capture finishes')
                if self._record_thread and self._record_thread.is_alive() and self._record_camera_id == camera_id:
                    raise RuntimeError('Finish the current recording before changing its camera resolution')
                previous = {key: slot[key] for key in ('width', 'height', 'frame_rate')}
                enabled = bool(slot['thread'] and slot['thread'].is_alive() and not slot['stop'].is_set())
            # Joining happens outside the frame lock so capture can release its
            # buffers before the same sensor is acquired again.
            if enabled:
                self.camera(False, camera_id)
            try:
                from .camera import stream_frame_rate
                with self._lock:
                    slot.update(width=width, height=height, frame_rate=stream_frame_rate(width, height))
                if enabled:
                    self.camera(True, camera_id)
                    self._wait_camera(slot)
                preferences = {**self._camera_preferences, camera_id: {'width': width, 'height': height}}
                self._save_camera_settings(self._primary_camera_id, preferences)
                self._camera_preferences = preferences
            except Exception as exc:
                recovery_error = None
                try:
                    if enabled:
                        self.camera(False, camera_id)
                    with self._lock:
                        slot.update(previous)
                    if enabled:
                        self.camera(True, camera_id)
                        self._wait_camera(slot)
                except Exception as recovery:
                    recovery_error = str(recovery)
                detail = f' Previous mode could not restart: {recovery_error}' if recovery_error else ' Previous resolution restored.'
                raise RuntimeError(f'Camera resolution could not be applied: {exc}.{detail}') from exc
            with self._lock:
                return self._camera_status(slot)

    def _capture(self, slot):
        cam = None
        try:
            from PIL import Image
            from .camera import Camera
            cam = Camera(slot['id'])
            cam.configure(cam.create_video_configuration(main={'size': (slot['width'], slot['height']), 'format': 'RGB888'},
                                                          controls={'FrameRate': slot['frame_rate']}))
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
                    slot['width'], slot['height'] = frame.shape[1], frame.shape[0]
                    slot['ready'].set()
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
                slot['ready'].set()
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
        with self._camera_operations, self._lock:
            self.jpeg()
            if self._record_thread and self._record_thread.is_alive():
                raise RuntimeError('Already recording')
            if sum(p.stat().st_size for p in self.recordings.glob('*.avi')) > 1024**3:
                raise RuntimeError('Recording storage reached 1 GiB; remove old recordings locally')
            self._record_stop.clear()
            self._record_error = None
            self._record_camera_id = self._primary_camera_id
            slot = self._cameras.get(self._record_camera_id, {})
            width, height = slot.get('width', 640), slot.get('height', 480)
            fps = min(10, slot.get('frame_rate', 12))
            name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ.avi')
            self._record_thread = threading.Thread(target=self._record, args=(name, seconds, self._record_camera_id, width, height, fps), daemon=True)
            self._record_thread.start()
        return {'name': name, 'url': '/recordings/' + name, 'seconds': seconds}

    def _record(self, name, seconds, camera_id=None, width=640, height=480, fps=10):
        writer = None
        try:
            writer = MjpegAVI(self.recordings / name, width=width, height=height, fps=fps)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not self._record_stop.is_set():
                writer.write(self.jpeg(camera_id))
                self._record_stop.wait(1 / fps)
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
        if self._audio_closed:
            raise RuntimeError('Speaker is closed')
        if self._audio_active or self._audio_queue:
            raise RuntimeError('Speaker is busy')
        self._speaker_error = None

    def _play(self, generate, cancel):
        device = None
        amplifier = False
        try:
            data, rate, channels = generate()
            from .hat import enable_speaker
            with self._lock:
                if cancel.is_set():
                    return
                amplifier = True
                enable_speaker()
            device = audio.PCM(self._speaker_device, rate=rate, channels=channels)
            with self._lock:
                self._output_pcm = device
                if cancel.is_set():
                    device.stop()
            device.write(data)
            # Finish the actual ALSA/mixer buffer, rather than guessing its
            # latency. PCM.stop() can interrupt this nonblocking drain.
            device.drain()
        except Exception as exc:
            with self._lock:
                self._speaker_error = str(exc)
        finally:
            try:
                if device:
                    device.close()
            except Exception as exc:
                with self._lock:
                    self._speaker_error = str(exc)
            try:
                if amplifier:
                    from .hat import disable_speaker
                    disable_speaker()
            except Exception as exc:
                with self._lock:
                    self._speaker_error = str(exc)
            with self._lock:
                self._output_pcm = None

    def _audio_loop(self):
        while True:
            with self._lock:
                if not self._audio_queue or self._audio_closed:
                    self._audio_active = None
                    self._audio_thread = None
                    return
                self._audio_active = self._audio_queue.popleft()
                _, _, generate = self._audio_active
                # Each playback owns its cancellation token. A new announcement
                # queued after STOP cannot accidentally revive an older job.
                cancel = self._audio_stop = threading.Event()
            self._play(generate, cancel)
            with self._lock:
                self._audio_active = None

    def _ensure_audio_worker(self):
        if not self._audio_thread or not self._audio_thread.is_alive():
            self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True, name='speaker')
            self._audio_thread.start()

    def _start_audio(self, generate):
        self._speaker_ready()
        self._audio_queue.append((None, None, generate))
        self._ensure_audio_worker()

    def announce(self, text):
        """Queue one arming announcement without blocking robot operations.

        Only the newest pending state for each source is kept. Thus a burst of
        toggles cannot leave a long queue announcing obsolete arm states. User
        speech/effects retain their existing single-playback/busy behavior.
        """
        if text not in ('controller armed', 'controller disarmed', 'system armed', 'system disarmed'):
            raise ValueError('Unknown arming announcement')
        with self._lock:
            if self.simulate or self._audio_closed:
                return {'queued': False, 'text': text}
            key = text.split(' ', 1)[0]
            self._audio_queue = deque(job for job in self._audio_queue if job[0] != key)
            # If that state is already being announced, discard any pending
            # superseded state without repeating the same spoken phrase.
            if self._audio_active and self._audio_active[:2] == (key, text) and not self._audio_stop.is_set():
                return {'queued': False, 'text': text}
            self._speaker_error = None
            self._audio_queue.append((key, text, lambda: audio.speech_wave(text)))
            self._ensure_audio_worker()
            return {'queued': True, 'text': text}

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
        with self._lock:
            self._audio_stop.set()
            self._audio_queue.clear()
            if self._output_pcm:
                try:
                    self._output_pcm.stop()
                except Exception as exc:
                    self._speaker_error = str(exc)
        self.microphone(False)
