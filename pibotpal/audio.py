# SPDX-License-Identifier: MIT
"""Small PCM adapter to the system ALSA library (LGPL-2.1-or-later).

No ALSA command line programs are executed. Devices open only on an explicit
capture/playback request. Nonblocking I/O permits prompt cancellation.
"""
import ctypes as C
import ctypes.util
import math
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path


def available():
    return bool(C.util.find_library('asound'))


class PCM:
    def __init__(self, device, capture=False, rate=16000, channels=1):
        self.lib = C.CDLL(C.util.find_library('asound') or 'libasound.so.2')
        self.lib.snd_pcm_open.argtypes = [C.POINTER(C.c_void_p), C.c_char_p, C.c_int, C.c_int]
        self.lib.snd_pcm_set_params.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_int, C.c_uint]
        for name in ('snd_pcm_readi', 'snd_pcm_writei'):
            getattr(self.lib, name).argtypes = [C.c_void_p, C.c_void_p, C.c_ulong]
            getattr(self.lib, name).restype = C.c_long
        for name in ('snd_pcm_close', 'snd_pcm_drop', 'snd_pcm_prepare'):
            getattr(self.lib, name).argtypes = [C.c_void_p]
        self.handle = C.c_void_p()
        self.cancel = threading.Event()
        self._lock = threading.RLock()
        self.capture, self.channels = capture, channels
        self._check(self.lib.snd_pcm_open(C.byref(self.handle), device.encode(), int(capture), 1))
        try:
            # S16_LE=2, RW_INTERLEAVED=3, software resampling on, 100 ms latency.
            self._check(self.lib.snd_pcm_set_params(self.handle, 2, 3, channels, rate, 1, 100000))
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(result):
        if result < 0:
            raise OSError(-result, 'ALSA PCM operation failed')
        return result

    def read(self, frames=1600):
        buf = C.create_string_buffer(frames * self.channels * 2)
        while not self.cancel.is_set():
            with self._lock:
                if self.cancel.is_set():
                    return b''
                n = self.lib.snd_pcm_readi(self.handle, buf, frames)
            if n == -11:  # EAGAIN
                self.cancel.wait(.01)
                continue
            if n == -32:  # EPIPE: recover an overrun without opening another device
                with self._lock:
                    if not self.cancel.is_set():
                        self._check(self.lib.snd_pcm_prepare(self.handle))
                continue
            self._check(n)
            return buf.raw[:n*self.channels*2]
        return b''

    def write(self, data):
        offset = 0
        while offset < len(data) and not self.cancel.is_set():
            block = data[offset:offset+4096*self.channels*2]
            buf = C.create_string_buffer(block)
            with self._lock:
                if self.cancel.is_set():
                    return
                n = self.lib.snd_pcm_writei(self.handle, buf, len(block)//(2*self.channels))
            if n == -11:
                self.cancel.wait(.01)
                continue
            if n == -32:
                with self._lock:
                    if not self.cancel.is_set():
                        self._check(self.lib.snd_pcm_prepare(self.handle))
                continue
            self._check(n)
            if n == 0:
                self.cancel.wait(.01)
            offset += n*self.channels*2

    def stop(self):
        self.cancel.set()
        with self._lock:
            if self.handle:
                self.lib.snd_pcm_drop(self.handle)

    def close(self):
        self.cancel.set()
        with self._lock:
            if self.handle:
                self.lib.snd_pcm_drop(self.handle)
                self.lib.snd_pcm_close(self.handle)
                self.handle = C.c_void_p()


def speech_wave(text):
    """Synthesize with CMU Flite's permissively licensed SLT voice, no playback."""
    core = C.CDLL('libflite.so.1', mode=C.RTLD_GLOBAL)
    voice_lib = C.CDLL('libflite_cmu_us_slt.so.1')
    core.flite_init()
    voice_lib.register_cmu_us_slt.argtypes = [C.c_char_p]
    voice_lib.register_cmu_us_slt.restype = C.c_void_p
    voice = voice_lib.register_cmu_us_slt(None)
    if not voice:
        raise RuntimeError('Flite voice could not initialize')
    core.flite_text_to_speech.argtypes = [C.c_char_p, C.c_void_p, C.c_char_p]
    core.flite_text_to_speech.restype = C.c_float
    with tempfile.TemporaryDirectory(prefix='pibotpal-speech-') as folder:
        path = Path(folder) / 'speech.wav'
        core.flite_text_to_speech(text.encode(), voice, str(path).encode())
        with wave.open(str(path), 'rb') as stream:
            if stream.getsampwidth() != 2:
                raise RuntimeError('Unsupported speech sample format')
            return stream.readframes(stream.getnframes()), stream.getframerate(), stream.getnchannels()


SOUNDS = ('talk1', 'talk2', 'talk3', 'sign', 'happy', 'happy2', 'error', 'depress',
          'depress2', 'battle', 'vigilance', 'warning', 'bell')


def effect_wave(name):
    """Original short synthesized cues; no vendor recordings or melodies."""
    name = name.removesuffix('.wav')
    if name not in SOUNDS:
        raise ValueError('Unknown sound; available: ' + ', '.join(SOUNDS))
    index = SOUNDS.index(name)
    rate = 16000
    data = bytearray()
    for note in range(3):
        frequency = 240 + index * 41 + note * (60 if index % 2 else -35)
        count = rate // 5
        for i in range(count):
            envelope = math.sin(math.pi*i/count)**2
            sample = int(6000*envelope*math.sin(2*math.pi*frequency*i/rate))
            data.extend(struct.pack('<h', sample))
    return bytes(data), rate, 1
