"""Shared camera and explicitly enabled audio. No microphone opens at startup."""
from __future__ import annotations
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime, timezone


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
        self._audio_processes = []
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
                               'available': shutil.which('arecord') is not None and not self.simulate,
                               'error': self._mic_error, 'listeners': len(self._mic_processes)},
                'speaker': {'available': shutil.which('aplay') is not None and not self.simulate,
                            'busy': any(p.poll() is None for p in self._audio_processes), 'error': self._speaker_error},
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
            import cv2
            from picamera2 import Picamera2
            cam = Picamera2()
            cam.configure(cam.create_video_configuration(main={'size': (640, 480), 'format': 'RGB888'},
                                                          controls={'FrameRate': 12}))
            cam.start()
            while not self._camera_stop.is_set():
                # Picamera2 RGB888 memory order is BGR, directly usable by OpenCV.
                frame = cam.capture_array('main')
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
                if ok:
                    self._frame, self._jpeg = frame, encoded.tobytes()
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
        import cv2
        import numpy as np
        bounds = {'red': [(0, 10), (170, 180)], 'blue': [(95, 130)], 'green': [(35, 85)],
                  'yellow': [(20, 35)], 'orange': [(10, 20)], 'purple': [(130, 165)]}
        if color not in bounds:
            raise ValueError('Unknown color')
        frame = self.get_frame()
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for low, high in bounds[color]:
            mask |= cv2.inRange(hsv, (low, 100, 60), (high, 255, 255))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {'detected': False, 'x': 0, 'y': 0, 'width': 0, 'height': 0, 'area': 0}
        contour = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(contour)
        area = float(cv2.contourArea(contour))
        return {'detected': area > 300, 'x': x + w / 2, 'y': y + h / 2,
                'width': w, 'height': h, 'area': area, 'frame_width': 640, 'frame_height': 480}

    def record(self, seconds=10):
        if not isinstance(seconds, (int, float)) or not 1 <= seconds <= 30:
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
            import cv2
            writer = cv2.VideoWriter(str(self.recordings / name), cv2.VideoWriter_fourcc(*'MJPG'), 10, (640, 480))
            if not writer.isOpened():
                raise RuntimeError('Video encoder could not start')
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not self._record_stop.is_set():
                writer.write(self.get_frame())
                self._record_stop.wait(0.1)
        except Exception as exc:
            self._record_error = str(exc)
        finally:
            if writer:
                writer.release()

    def microphone(self, enabled: bool):
        with self._lock:
            if enabled and (self.simulate or not shutil.which('arecord')):
                raise RuntimeError('Microphone unavailable')
            self._mic_enabled = enabled
            if not enabled:
                for process in tuple(self._mic_processes):
                    self._terminate(process)
        return self.status()['microphone']

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)

    def audio_stream(self):
        with self._lock:
            if not self._mic_enabled:
                raise RuntimeError('Microphone is muted')
            if self._mic_processes:
                raise RuntimeError('Microphone already has a listener')
            process = subprocess.Popen(['arecord', '-q', '-D', self._mic_device, '-t', 'raw', '-f', 'S16_LE',
                                        '-r', '16000', '-c', '1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._mic_processes.add(process)
        def chunks():
            try:
                # A streaming PCM WAV with an unknown length (browser consumes until mute/close).
                yield struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 0x7fffffff, b'WAVE', b'fmt ', 16,
                                  1, 1, 16000, 32000, 2, 16, b'data', 0x7fffffdb)
                while self._mic_enabled and process.poll() is None:
                    chunk = process.stdout.read(3200)
                    if not chunk:
                        break
                    if self._mic_enabled:
                        yield chunk
                if process.poll() not in (None, 0, -15):
                    self._mic_error = 'Microphone capture failed; check ALSA device'
            finally:
                self._terminate(process)
                process.stdout.close()
                with self._lock:
                    self._mic_processes.discard(process)
        return chunks()

    def _speaker_ready(self):
        if self.simulate:
            raise RuntimeError('Speaker unavailable in simulation')
        if any(p.poll() is None for p in self._audio_processes):
            raise RuntimeError('Speaker is busy')
        from robot_hat.utils import enable_speaker
        enable_speaker()
        self._speaker_error = None

    def speak(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise ValueError('Speech must contain 1–500 characters')
        with self._lock:
            self._speaker_ready()
            source = subprocess.Popen(['espeak', '--stdout', '--', text], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            output = subprocess.Popen(['aplay', '-q', '-D', self._speaker_device], stdin=source.stdout,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            source.stdout.close()
            self._audio_processes = [source, output]
        return {'speaking': True, 'text': text}

    def play_sound(self, name):
        base = Path(os.environ.get('PICRAWLER_HOME', '~/picrawler')).expanduser() / 'examples'
        files = {p.name: p for sub in ('sounds', 'musics') for p in (base / sub).glob('*')
                 if p.suffix in ('.wav', '.mp3')}
        if name not in files:
            raise ValueError('Unknown sound; available: ' + ', '.join(sorted(files)))
        with self._lock:
            self._speaker_ready()
            source = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(files[name]), '-t', '30', '-f', 'wav', '-'],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            output = subprocess.Popen(['aplay', '-q', '-D', self._speaker_device], stdin=source.stdout,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            source.stdout.close()
            self._audio_processes = [source, output]
        return {'playing': name, 'maximum_seconds': 30}

    def stop(self):
        self._record_stop.set()
        self.microphone(False)
        with self._lock:
            for process in self._audio_processes:
                self._terminate(process)
