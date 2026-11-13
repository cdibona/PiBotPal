"""ALSA drain completion and cancellation without opening a sound device."""
import ctypes
import errno
from types import SimpleNamespace
import threading

import pytest

from pibotpal.audio import PCM


def pcm(drain, drop=lambda handle: 0):
    device = PCM.__new__(PCM)
    device.lib = SimpleNamespace(snd_pcm_drain=drain, snd_pcm_drop=drop)
    device.handle = ctypes.c_void_p(1)
    device.cancel = threading.Event()
    device._lock = threading.RLock()
    return device


def test_nonblocking_drain_retries_until_all_buffered_samples_finish():
    results = [-errno.EAGAIN, -errno.EINTR, -errno.EAGAIN, 0]
    calls = []
    def drain(handle):
        calls.append(handle.value)
        return results.pop(0)
    device = pcm(drain)
    device.drain()
    assert calls == [1, 1, 1, 1] and results == []
    assert not device.cancel.is_set()


def test_stop_remains_available_during_drain():
    draining = threading.Event()
    dropped = []
    def drain(handle):
        draining.set()
        return -errno.EAGAIN
    device = pcm(drain, lambda handle: dropped.append(handle.value))
    worker = threading.Thread(target=device.drain)
    worker.start()
    assert draining.wait(1)
    device.stop()
    worker.join(timeout=.25)
    assert not worker.is_alive() and dropped == [1]


def test_cancelled_drain_does_not_touch_alsa_and_other_errors_surface():
    calls = []
    device = pcm(lambda handle: calls.append(handle))
    device.cancel.set(); device.drain()
    assert calls == []
    failed = pcm(lambda handle: -errno.ENODEV)
    with pytest.raises(OSError) as error:
        failed.drain()
    assert error.value.errno == errno.ENODEV
