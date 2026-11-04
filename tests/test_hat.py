"""Transport tests use fake devices: never move physical servos."""
from collections import namedtuple

import pytest
from pibotpal.hat import Hat, Sonar


class Bus:
    def __init__(self, sample=3456):
        self.calls = []
        self.sample = sample
        self.closed = False
    def transfer(self, address, messages):
        for message in messages:
            if message.read:
                message.data[:] = self.sample.to_bytes(2, 'big')
            else:
                self.calls.append((address, bytes(message.data)))
    def close(self):
        self.closed = True


def test_servo_construction_does_not_write_and_timer_initializes_once():
    bus = Bus()
    hat = Hat(bus=bus, address=0x14)
    servo = hat.servo(9)
    assert bus.calls == []
    servo.angle(0)
    assert bus.calls == [(0x14, b'\x42\x00\x47'), (0x14, b'\x46\x4e\x20'), (0x14, b'\x29\x05\xdc')]
    hat.servo(10).angle(-90)
    assert bus.calls[-1] == (0x14, b'\x2a\x01\xf4')
    assert len(bus.calls) == 4
    hat.close()
    assert bus.closed


def test_adc_scaling_and_explicit_conversion_then_read():
    bus = Bus(sample=4095)
    hat = Hat(bus=bus, address=0x14)
    assert hat.battery_voltage() == pytest.approx(9.9)
    assert bus.calls == [(0x14, b'\x13\0\0')]
    bus.sample = 65535
    with pytest.raises(OSError, match='12-bit'):
        hat.battery_voltage()


def test_invalid_angle_never_reaches_transport():
    bus = Bus()
    servo = Hat(bus=bus, address=0x14).servo(0)
    for angle in (float('nan'), float('inf'), 91, True):
        with pytest.raises(ValueError):
            servo.angle(angle)
    assert bus.calls == []


def test_bus_failure_propagates():
    class FailedBus(Bus):
        def transfer(self, *args):
            raise OSError('disconnected')
    with pytest.raises(OSError, match='disconnected'):
        Hat(bus=FailedBus(), address=0x14).servo(0).angle(0)


def test_sonar_uses_kernel_timestamps(monkeypatch):
    Event = namedtuple('Event', ['edge', 'timestamp'])
    class Echo:
        events = [Event('rising', 1000000000), Event('falling', 1002000000)]
        def poll(self, timeout):
            return bool(timeout and self.events)
        def read_event(self):
            return self.events.pop(0)
    class Trigger:
        def write(self, value):
            pass
    sonar = Sonar.__new__(Sonar)
    sonar.timeout, sonar._echo, sonar._trigger = .025, Echo(), Trigger()
    assert sonar.read() == pytest.approx(34.33)


def test_servo_small_changes_have_one_microsecond_resolution_and_skip_unchanged():
    bus = Bus()
    servo = Hat(bus=bus, address=0x14).servo(0)
    servo.angle(0)
    before = len(bus.calls)
    servo.angle(0.001)
    assert len(bus.calls) == before
    servo.angle(.1)
    assert bus.calls[-1] == (0x14, b'\x20\x05\xdd')  # 1501 us
    servo.angle(90)
    assert int.from_bytes(bus.calls[-1][1][1:], 'big') == 2500
