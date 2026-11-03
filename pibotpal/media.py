# SPDX-License-Identifier: MIT
"""Shared camera and explicitly enabled audio. No microphone opens at startup."""
from __future__ import annotations
import io
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

    def start(self):
        if not self.simulate:
            self.camera(True)

    def close(self):
        self.stop()
        self.camera(False)

    def status(self):
        with self._lock:
            recent = sorted(self.recordings.glob('*.avi'), reverse=True)[:20]
            return {
                'camera': {'enabled': bool(self._camera_thread and self._camera_thread.is_alive()),
                           'available': self._jpeg is not None, 'error': self._camera_error,
                           'timestamp': self._frame_time, 'width': 640, 'height': 480},
                'microphone': {'enabled': self._mic_enabled, 'muted': not self._mic_enabled,
                               'available': audio.available() and not self.simulate,
                               'error': self._mic_error, 'listeners': len(self._mic_processes)},
                'speaker': {'available': audio.available() and not self.simulate,
                            'busy': bool(self._audio_thread and self._audio_thread.is_alive()), 'error': self._speaker_error},
                'recording': {'active': bool(self._record_thread and self._record_thread.is_alive()),
                              'error': self._record_error},
                'recordings': [{'name': p.name, 'url': '/recordings/' + p.name, 'bytes': p.stat().st_size} for p in recent],
            }

    def camera(self, enabled: bool):
        with self._lock:
            if enabled:
                if self.simulate:
                    raise RuntimeError('Camera unavailable in simulation')
                if not self._camera_thread or not self._camera_thread.is_alive():
                    self._camera_stop.clear()
                    self._camera_error = None
                    self._camera_thread = threading.Thread(target=self._capture, daemon=True, name='camera')
                    self._camera_thread.start()
            else:
                self._camera_stop.set()
                thread = self._camera_thread
                if thread:
                    thread.join(timeout=4)
                self._frame = self._jpeg = None
        return self.status()['camera']

    def _capture(self):
        cam = None
        try:
            from PIL import Image
            from .camera import Camera as Picamera2
            cam = Picamera2()
            cam.configure(cam.create_video_configuration(main={'size': (640, 480), 'format': 'RGB888'},
                                                          controls={'FrameRate': 12}))
            cam.start()
            while not self._camera_stop.is_set():
                # Preserve BGR frame API; Pillow explicitly receives RGB bytes.
                frame = cam.capture_array('main')
                encoded = io.BytesIO()
                Image.fromarray(frame[:, :, ::-1]).save(encoded, format='JPEG', quality=78)
                self._frame, self._jpeg = frame, encoded.getvalue()
                self._frame_time = time.time()
        except Exception as exc:
            self._camera_error = str(exc)
        finally:
            if cam:
                try:
                    cam.stop()
                    cam.close()
                except Exception:
                    pass
            self._frame = self._jpeg = None

    def jpeg(self):
        if self._jpeg is None or time.time() - (self._frame_time or 0) > 3:
            raise RuntimeError(self._camera_error or 'Camera is off or starting')
        return self._jpeg

    def get_frame(self):
        self.jpeg()
        return self._frame.copy()

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
