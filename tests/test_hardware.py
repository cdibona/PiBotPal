import math
import time

import pytest

from pibotpal.hardware import ACTIONS, CheckedPwmWrites, Controller, _vendor_moves
from pibotpal.kinematics import A, B, C, DIRECTIONS, inverse, leg_geometry, local_joints


def wait_idle(controller, timeout=3):
    deadline = time.monotonic() + timeout
    while controller.status()['busy'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert not controller.status()['busy']
    assert controller.status()['action_error'] is None


@pytest.fixture
def controller(tmp_path):
    c = Controller(simulate=True, config_path=tmp_path / 'calibration')
    c.start()
    yield c
    c.close()


@pytest.mark.parametrize('coord', [[60, 0, -30], [45, 45, -50], [70, 0, -50], [45, 0, -50], [45,45,-75], [50,50,-33]])
def test_kinematics_roundtrip(coord):
    angles = inverse(coord)
    points = local_joints(angles)
    assert points[-1] == pytest.approx(coord, abs=1e-8)
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    assert lengths == pytest.approx([C, A, B])


def test_geometry_leg_mirroring():
    angles = inverse([45,45,-50])
    expected = [[83.5,83.5,-50], [-83.5,83.5,-50], [-83.5,-83.5,-50], [83.5,-83.5,-50]]
    for i in range(4):
        assert leg_geometry(i, angles)['joints_mm'][-1] == pytest.approx(expected[i])


def test_start_arm_stop_never_write(tmp_path):
    created = []
    def factory(channel):
        created.append(channel)
        raise AssertionError('Unexpected servo construction')
    c = Controller(simulate=True, servo_factory=factory, config_path=tmp_path / 'calibration')
    c.start()
    assert c.status()['servo_angles'] == [None] * 12
    c.arm(True)
    c.stop()
    c.close()
    assert created == []


def test_disarmed_and_busy_commands_rejected(controller):
    with pytest.raises(RuntimeError, match='Arm'):
        controller.action('stand')
    controller.arm(True)
    controller.set_pose([[60,0,-30]] * 4)
    with pytest.raises(RuntimeError, match='busy'):
        controller.action('forward')
    wait_idle(controller)
    assert controller.status()['pose_source'] == 'commanded'


def test_stop_interrupts_interpolation_and_requires_rearm(controller):
    controller.arm(True)
    controller.set_pose([[60,0,-30]] * 4)
    wait_idle(controller)
    controller.set_angles([0,30,0]*4, speed=1)
    time.sleep(.08)
    controller.stop()
    stopped_angles = controller.status()['servo_angles']
    time.sleep(.1)
    assert controller.status()['servo_angles'] == stopped_angles
    assert controller.status()['estop']
    assert not controller.status()['armed']
    assert not controller.status()['busy']
    with pytest.raises(RuntimeError, match='Arm'):
        controller.action('forward')
    controller.arm(True)
    assert controller.status()['armed']


def test_pwm_directions_offsets_and_reported_angles(tmp_path):
    written = {}
    class FakeServo:
        def __init__(self, channel):
            self.channel = channel
        def angle(self, angle):
            written[self.channel] = angle
    c = Controller(simulate=True, servo_factory=FakeServo, config_path=tmp_path/'calibration')
    try:
        c.start()
        c.set_offsets([2.0]*12)
        c.arm(True)
        c.set_pose([[60,0,-30]]*4)
        wait_idle(c)
        status = c.status()
        for i, channel in enumerate(status['servo_channels']):
            assert written[channel] == pytest.approx(DIRECTIONS[i]*(status['servo_angles'][i]+2))
    finally:
        c.close()


def test_calibration_relabels_without_moving(controller):
    controller.arm(True)
    controller.set_angles([0]*12)
    wait_idle(controller)
    raw = controller.status()['servo_raw_angles']
    with pytest.raises(RuntimeError, match='Disarm'):
        controller.set_offsets([1]*12)
    controller.stop()
    controller.set_offsets([1]*12)
    assert controller.status()['servo_angles'] == [-1]*12
    assert controller.status()['servo_raw_angles'] == raw


@pytest.mark.parametrize('coords', [[[500,0,-30]]*4, [[0,0,-500]]*4, [[float('nan'),0,-30]]*4])
def test_invalid_coords_rejected(controller, coords):
    controller.arm(True)
    with pytest.raises(ValueError):
        controller.set_pose(coords)
    assert not controller.status()['busy']


def test_names_are_allowlisted(controller):
    controller.arm(True)
    with pytest.raises(ValueError, match='Unknown'):
        controller.action('__dict__')
    with pytest.raises(ValueError):
        controller.action('forward', steps=100)
    with pytest.raises(ValueError):
        controller.action('forward', speed=float('nan'))


def test_error_disarms_controller(tmp_path):
    class Broken:
        def __init__(self, channel):
            pass
        def angle(self, angle):
            raise OSError('I2C failure')
    c = Controller(simulate=True, servo_factory=Broken, config_path=tmp_path/'calibration')
    try:
        c.start()
        c.arm(True)
        c.set_angles([0]*12)
        time.sleep(.1)
        status = c.status()
        assert not status['armed']
        assert status['estop']
        assert 'I2C failure' in status['action_error']
    finally:
        c.close()


def test_vendor_gaits_load_without_constructing_robot():
    moves = _vendor_moves()
    assert len(moves.forward) > 1
    assert len(moves.wave) > 1


def test_invalid_calibration_cannot_arm(tmp_path):
    config = tmp_path / 'calibration'
    config.write_text('picrawler_servo_offset_list = [100]*12\n')
    c = Controller(simulate=True, config_path=config)
    try:
        c.start()
        with pytest.raises(RuntimeError, match='calibration'):
            c.arm(True)
        c.set_offsets([0]*12)
        c.arm(True)
    finally:
        c.close()


def test_all_vendor_action_coordinates_are_supported():
    moves = _vendor_moves()
    for name in ACTIONS:
        for pose in getattr(moves, name.replace(' ', '_')):
            for coord in pose:
                angles = inverse(coord, strict=False)
                assert all(math.isfinite(a) for a in angles)


def test_checked_pwm_preserves_vendor_byte_order_and_rejects_exhausted_retry():
    calls = []
    class Adapter(CheckedPwmWrites):
        result = None
        def _write_word_data(self, reg, value):
            calls.append((reg, value))
            return self.result
    adapter = Adapter()
    adapter._i2c_write(0x23, 0x1234)
    assert calls == [(0x23, 0x3412)]
    adapter.result = False
    with pytest.raises(OSError, match='I2C write failed'):
        adapter._i2c_write(0x23, 0x1234)


def test_exhausted_i2c_retry_does_not_report_new_angles(tmp_path):
    class ServoWithFailedWrite(CheckedPwmWrites):
        def __init__(self, channel):
            pass
        def _write_word_data(self, register, value):
            return False
        def angle(self, angle):
            self._i2c_write(0x20, 300)
    c = Controller(simulate=True, servo_factory=ServoWithFailedWrite, config_path=tmp_path/'calibration')
    try:
        c.start()
        c.arm(True)
        c.set_angles([0]*12)
        deadline = time.monotonic() + 2
        while c.status()['busy'] and time.monotonic() < deadline:
            time.sleep(.005)
        status = c.status()
        assert not status['armed']
        assert status['estop']
        assert 'I2C write failed' in status['action_error']
        assert status['servo_angles'] == [None]*12
    finally:
        c.close()
