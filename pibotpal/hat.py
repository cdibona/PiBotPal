# SPDX-License-Identifier: MIT
"""Minimal Robot HAT transport, using Linux device interfaces.

Register addresses, GPIO wiring, clock and ADC scaling are hardware facts.
No upstream robot code is imported and no MCU reset is performed. Servo timer
configuration is deferred until an explicitly requested angle write.
"""
from __future__ import annotations
import fcntl
import math
import os
from pathlib import Path
import threading
import time


def header_chip():
    """Identify the 40-pin header by chip label, independent of Linux numbering."""
    override = os.getenv('PIBOTPAL_GPIO_CHIP')
    if override:
        return override
    for node in sorted(Path('/dev').glob('gpiochip*')):
        with node.open('rb', buffering=0) as handle:
            info = bytearray(68)  # linux/gpio.h gpiochip_info
            fcntl.ioctl(handle.fileno(), 0x8044B401, info, True)
        label = bytes(info[32:64]).split(b'\0')[0].decode()
        if label == 'pinctrl-rp1' or label.startswith('pinctrl-bcm'):
            return str(node)
    raise RuntimeError('Cannot identify Raspberry Pi header GPIO; set PIBOTPAL_GPIO_CHIP')


class Hat:
    def __init__(self, *, bus=None, address=None):
        from periphery import I2C
        self._bus = bus or I2C(os.getenv('PIBOTPAL_I2C_DEVICE', '/dev/i2c-1'))
        self._lock = threading.RLock()
        self._timers = set()
        self.address = address
        if self.address is None:
            configured = os.getenv('PIBOTPAL_HAT_ADDRESS')
            candidates = [int(configured, 0)] if configured else [0x14, 0x15, 0x16]
            for candidate in candidates:
                self.address = candidate
                try:
                    self.battery_voltage()
                    break
                except OSError:
                    self.address = None
            if self.address is None:
                self._bus.close()
                raise OSError('No Robot HAT ADC responded at configured I2C addresses')

    def _word(self, register, value):
        from periphery import I2C
        self._bus.transfer(self.address, [I2C.Message(bytes([register, value >> 8, value & 255]))])

    def battery_voltage(self):
        from periphery import I2C
        with self._lock:
            # A4 is register 0x10 | (7-4). ADC conversion and read use separate
            # transfers (STOP between them), as required by this MCU protocol.
            self._word(0x13, 0)
            response = I2C.Message(bytearray(2), read=True)
            self._bus.transfer(self.address, [response])
            raw = int.from_bytes(response.data, 'big')
            if raw > 4095:
                raise OSError('Robot HAT ADC returned an invalid 12-bit sample')
            return raw * (9.9 / 4095)

    def servo(self, channel):
        return Servo(self, channel)

    def angle(self, channel, angle):
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < 12:
            raise ValueError('PiCrawler servo channel must be 0–11')
        if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle) or not -90 <= angle <= 90:
            raise ValueError('Servo angle must be finite and within -90 to 90 degrees')
        # 72 MHz / 352 / 4095 = 49.95 Hz; 500–2500 us servo pulses.
        timer = channel // 4
        pulse = int((1500 + angle * (1000 / 90)) * 4095 / 20000)
        with self._lock:
            if timer not in self._timers:
                self._word(0x40 + timer, 351)
                self._word(0x44 + timer, 4095)
                self._timers.add(timer)
            self._word(0x20 + channel, pulse)

    def close(self):
        with self._lock:
            self._bus.close()


class Servo:
    def __init__(self, hat, channel):
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < 12:
            raise ValueError('PiCrawler servo channel must be 0–11')
        self._hat, self.channel = hat, channel

    def angle(self, angle):
        self._hat.angle(self.channel, angle)


class Sonar:
    def __init__(self, timeout=0.025):
        from periphery import GPIO
        self.timeout = timeout
        self._trigger = GPIO(header_chip(), 27, 'low', label='pibotpal-sonar-trigger')
        try:
            self._echo = GPIO(header_chip(), 22, 'in', edge='both', bias='pull_down', label='pibotpal-sonar-echo')
        except Exception:
            self._trigger.close()
            raise

    def read(self, times=1):
        for _ in range(times):
            # Discard queued edges from a previous measurement.
            while self._echo.poll(0):
                self._echo.read_event()
            self._trigger.write(True)
            time.sleep(0.00001)
            self._trigger.write(False)
            deadline = time.monotonic() + self.timeout
            rise = None
            while time.monotonic() < deadline:
                if not self._echo.poll(max(0, deadline - time.monotonic())):
                    break
                event = self._echo.read_event()
                if event.edge == 'rising':
                    rise = event.timestamp
                elif rise is not None:
                    # Kernel edge timestamps are nanoseconds, avoiding Python
                    # scheduling jitter in the measured pulse width.
                    return (event.timestamp - rise) * 34330 / 2e9
        return -1

    def close(self):
        self._trigger.write(False)
        self._trigger.close()
        self._echo.close()


_speaker = None
_speaker_lock = threading.Lock()


def enable_speaker():
    global _speaker
    from periphery import GPIO
    with _speaker_lock:
        if _speaker is None:
            v5 = any(path.read_bytes().rstrip(b'\0') == b'9daeea78-0000-076e-0032-582369ac3e02'
                     for path in Path('/proc/device-tree').glob('*hat*/uuid'))
            pin = int(os.getenv('PIBOTPAL_SPEAKER_GPIO', '12' if v5 else '20'))
            _speaker = GPIO(header_chip(), pin, 'low', label='pibotpal-speaker-enable')
        _speaker.write(True)


def disable_speaker():
    global _speaker
    with _speaker_lock:
        if _speaker is not None:
            _speaker.write(False)
            _speaker.close()
            _speaker = None
