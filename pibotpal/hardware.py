"""Single-owner, interruptible PiCrawler controller.

Neither start nor arm resets the MCU or commands servos. Servo construction is
lazy, on the first explicitly requested movement. Stop cancels future writes and
holds the last PWM position; it is a software stop, not a hardware power cutoff.
"""
from __future__ import annotations

import ast
import fcntl
import importlib.util
import math
import os
from pathlib import Path
import queue
import threading
import time

from .kinematics import CHANNELS, DIRECTIONS, LIMITS, finite, inverse, leg_geometry

ACTIONS = ('stand', 'sit', 'ready', 'forward', 'backward', 'turn left', 'turn right',
           'turn left angle', 'turn right angle', 'push up', 'wave', 'look left',
           'look right', 'look up', 'look down', 'dance')


class CheckedPwmWrites:
    """Keep robot_hat's retry policy, but make exhausted writes observable.

    PWM._i2c_write sends a 16-bit value high byte first. SMBus word writes
    send low byte first, hence the byte swap. The upstream I2C.write drops
    its helper's False result; overriding here prevents false pose telemetry.
    """
    def _i2c_write(self, register, value):
        swapped = ((value & 0xff) << 8) | ((value >> 8) & 0xff)
        if self._write_word_data(register, swapped) is False:
            raise OSError(f'Robot HAT I2C write failed at register 0x{register:02x}')


class MotionCancelled(Exception):
    pass


def _vendor_moves():
    """Load only the vendor's pure MoveList class, without importing GPIO/audio.

    This keeps simulation hardware independent. The file is a trusted installed
    dependency, never user-supplied request data; motion names are allowlisted.
    """
    source = Path(os.environ.get('PICRAWLER_HOME', os.environ.get('PIBOTPAL_PICRAWLER', str(Path.home() / 'picrawler')))) / 'picrawler' / 'picrawler.py'
    if not source.is_file():
        spec = importlib.util.find_spec('picrawler')
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError('SunFounder picrawler package/source is unavailable')
        source = Path(next(iter(spec.submodule_search_locations))) / 'picrawler.py'
    tree = ast.parse(source.read_text())
    outer = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Picrawler')
    cls = next(n for n in outer.body if isinstance(n, ast.ClassDef) and n.name == 'MoveList')
    namespace = {'math': math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(source), 'exec'), namespace)
    return namespace['MoveList']()


class Controller:
    def __init__(self, simulate=None, *, servo_factory=None, sonar_factory=None, config_path=None, moves_factory=None):
        self.simulate = os.getenv('PIBOTPAL_SIMULATE', '0') == '1' if simulate is None else bool(simulate)
        self._servo_factory, self._sonar_factory = servo_factory, sonar_factory
        self._moves_factory = moves_factory or _vendor_moves
        self._config = Path(config_path or Path.home() / '.config/.picrawler.config')
        self._lock = threading.RLock()
        self._io_lock = threading.RLock()
        self._cancel, self._closed = threading.Event(), threading.Event()
        self._queue = queue.Queue(maxsize=1)
        self._worker = self._sensor = None
        self._ownership = None
        self._servos, self._sonar, self._moves = [], None, None
        self._armed = self._busy = self._estop = False
        self._action = self._action_error = None
        self._angles = [None] * 12
        self._offsets = [0.0] * 12
        self._errors = []
        self._range = {'distance_cm': None, 'valid': False, 'timestamp': None}
        self._battery = None
        self._started = time.monotonic()
        self._read_offsets()

    def _error(self, message):
        with self._lock:
            if not self._errors or self._errors[-1] != message:
                self._errors.append(message)
                self._errors = self._errors[-10:]

    def _read_offsets(self):
        if not self._config.exists():
            return
        try:
            for line in self._config.read_text().splitlines():
                key, sep, value = line.partition('=')
                if sep and key.strip() == 'picrawler_servo_offset_list':
                    offsets = ast.literal_eval(value.strip())
                    self._offsets = self._validate_offsets(offsets)
        except Exception as exc:
            self._error(f'Calibration read failed: {exc}')
            # Invalid calibration must not silently enable movement.
            self._estop = True

    def start(self):
        with self._lock:
            if self._worker:
                return self.status()
            if self._closed.is_set():
                raise RuntimeError('Controller is closed')
            if not self.simulate:
                lockpath = Path(os.getenv('PIBOTPAL_STATE_DIR', str(Path.home() / '.local/state/pibotpal')))
                lockpath.mkdir(parents=True, exist_ok=True)
                self._ownership = (lockpath / 'hardware.lock').open('a')
                try:
                    fcntl.flock(self._ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self._ownership.close()
                    self._ownership = None
                    raise RuntimeError('Another PiBotPal process owns the hardware')
            self._worker = threading.Thread(target=self._run, daemon=True, name='picrawler-motion')
            self._worker.start()
            self._sensor = threading.Thread(target=self._poll_sensors, daemon=True, name='picrawler-sensors')
            self._sensor.start()
        return self.status()

    def close(self):
        self.stop()
        self._closed.set()
        if self._worker:
            self._worker.join(timeout=3)
        if self._sensor:
            self._sensor.join(timeout=3)
        if self._sonar:
            try:
                self._sonar.close()
            except Exception:
                pass
        if self._ownership:
            self._ownership.close()
            self._ownership = None

    def arm(self, armed=True):
        if not isinstance(armed, bool):
            raise ValueError('armed must be a boolean')
        if not armed:
            return self.stop()
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('Controller is closed')
            if self._busy:
                raise RuntimeError('Wait for the current action to stop before arming')
            if any(e.startswith('Calibration read failed:') for e in self._errors):
                raise RuntimeError('Repair calibration before arming')
            self._cancel.clear()
            self._armed, self._estop = True, False
        return self.status()

    def stop(self):
        # Setting the event first also interrupts an in-progress interpolation.
        self._cancel.set()
        with self._lock:
            self._armed, self._estop = False, True
        # Barrier: after return, an active servo write batch has finished.
        with self._io_lock:
            pass
        return self.status()

    def status(self):
        with self._lock:
            angles = list(self._angles)
            sensor = dict(self._range)
            if sensor['timestamp'] is not None and time.time() - sensor['timestamp'] > 2:
                sensor['valid'] = False
            return {
                'armed': self._armed, 'busy': self._busy, 'estop': self._estop,
                'action': self._action, 'action_error': self._action_error,
                'simulate': self.simulate, 'servo_angles': angles,
                'servo_offsets': list(self._offsets), 'servo_directions': list(DIRECTIONS),
                'servo_channels': list(CHANNELS),
                'servo_raw_angles': [None if a is None else DIRECTIONS[i] * (a + self._offsets[i]) for i, a in enumerate(angles)],
                'legs': [leg_geometry(i, angles[i*3:i*3+3]) for i in range(4)],
                'ultrasonic': sensor, 'battery_voltage': self._battery,
                'orientation': {'available': False, 'source': 'unavailable; no IMU', 'roll': None, 'pitch': None, 'yaw': None},
                'pose_source': 'commanded' if all(a is not None for a in angles) else 'unknown',
                'errors': list(self._errors), 'uptime_s': round(time.monotonic()-self._started, 1),
                'geometry': {'upper_mm': 48, 'lower_mm': 78, 'coxa_mm': 33, 'body_side_mm': 77,
                             'axes': 'x right, y forward, z up', 'body_dimensions_source': 'vendor gait model; not surveyed CAD'},
                'stop_behavior': 'Cancel motion and hold last PWM command; power remains on',
            }

    @staticmethod
    def _speed(speed):
        speed = finite(speed)
        if not 1 <= speed <= 80:
            raise ValueError('Speed must be between 1 and 80')
        return speed

    def _submit(self, name, operation):
        with self._lock:
            if not self._worker or self._closed.is_set():
                raise RuntimeError('Controller is not running')
            if not self._armed or self._estop:
                raise RuntimeError('Arm the robot before moving')
            if self._busy:
                raise RuntimeError('Robot is busy; wait or stop the current action')
            self._busy, self._action, self._action_error = True, name, None
            self._queue.put_nowait(operation)
        return self.status()

    def action(self, name, speed=40, steps=1):
        if name not in ACTIONS:
            raise ValueError('Unknown action')
        speed = self._speed(speed)
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 5:
            raise ValueError('steps must be an integer from 1 to 5')
        def perform():
            if self._moves is None:
                self._moves = self._moves_factory()
            for _ in range(steps):
                self._check_cancel()
                # getattr avoids the vendor dict implementation's eval.
                poses = getattr(self._moves, name.replace(' ', '_'))
                for pose in poses:
                    self._move(self._pose_angles(pose, strict=False), speed)
                if name in ('forward', 'backward', 'turn left', 'turn right', 'turn left angle', 'turn right angle'):
                    self._moves.stand_position = (self._moves.stand_position + 1) & 1
        return self._submit(name, perform)

    def _pose_angles(self, coords, strict=True):
        if not isinstance(coords, (list, tuple)) or len(coords) != 4:
            raise ValueError('Pose requires four leg coordinates')
        return [a for coord in coords for a in inverse(coord, strict=strict)]

    def set_pose(self, coords, speed=30):
        angles, speed = self._pose_angles(coords), self._speed(speed)
        return self._submit('custom pose', lambda: self._move(angles, speed))

    def set_leg(self, leg, x, y, z, speed=30):
        self._index(leg, 4)
        target, speed = inverse([x, y, z]), self._speed(speed)
        with self._lock:
            if any(a is None for a in self._angles):
                raise RuntimeError('Command a full pose before moving one leg')
            angles = list(self._angles)
            angles[leg*3:leg*3+3] = target
        return self._submit(f'leg {leg+1}', lambda: self._move(angles, speed))

    @staticmethod
    def _index(index, count):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            raise ValueError(f'Index must be an integer from 0 to {count-1}')

    def set_servo(self, index, angle, speed=30):
        self._index(index, 12)
        with self._lock:
            if any(a is None for a in self._angles):
                raise RuntimeError('Command a full pose before moving one servo')
            angles = list(self._angles)
            angles[index] = angle
        return self.set_angles(angles, speed)

    def set_angles(self, angles, speed=30):
        if not isinstance(angles, (list, tuple)) or len(angles) != 12:
            raise ValueError('Exactly twelve servo angles required')
        angles, speed = [finite(a) for a in angles], self._speed(speed)
        for i, a in enumerate(angles):
            lo, hi = LIMITS[i % 3]
            if not lo <= a <= hi or not -90 <= a + self._offsets[i] <= 90:
                raise ValueError(f'Servo {i} exceeds calibrated joint limits')
        return self._submit('servo pose', lambda: self._move(angles, speed))

    @staticmethod
    def _validate_offsets(offsets):
        if not isinstance(offsets, (list, tuple)) or len(offsets) != 12:
            raise ValueError('Exactly twelve calibration offsets required')
        result = [finite(v) for v in offsets]
        if any(abs(v) > 20 for v in result):
            raise ValueError('Calibration offsets must be within ±20 degrees')
        return result

    def set_offsets(self, offsets):
        values = self._validate_offsets(offsets)
        with self._lock:
            if self._busy or self._armed:
                raise RuntimeError('Disarm before saving calibration offsets')
            if not self.simulate:
                self._config.parent.mkdir(parents=True, exist_ok=True)
                old = self._config.read_text() if self._config.exists() else '# robot-hat config\n'
                lines = [line for line in old.splitlines() if line.partition('=')[0].strip() != 'picrawler_servo_offset_list']
                self._config.with_suffix('.config.bak').write_text(old)
                tmp = self._config.with_suffix('.config.tmp')
                tmp.write_text('\n'.join(lines) + '\npicrawler_servo_offset_list = ' + repr(values) + '\n\n')
                tmp.replace(self._config)
            # Raw PWM outputs have not changed. Re-express the last commands.
            self._angles = [None if a is None else a+self._offsets[i]-values[i] for i,a in enumerate(self._angles)]
            self._offsets = values
            self._errors = [e for e in self._errors if not e.startswith('Calibration read failed:')]
        return self.status()

    def _check_cancel(self):
        if self._cancel.is_set() or self._closed.is_set():
            raise MotionCancelled()

    def _ensure_servos(self):
        if len(self._servos) == 12 or (self.simulate and self._servo_factory is None):
            return
        if self._servo_factory is None:
            from robot_hat import Servo
            class CheckedServo(CheckedPwmWrites, Servo):
                pass
            factory = CheckedServo
        else:
            factory = self._servo_factory
        for channel in CHANNELS[len(self._servos):]:
            self._check_cancel()
            with self._io_lock:
                self._check_cancel()
                self._servos.append(factory(channel))

    def _write(self, angles):
        with self._io_lock:
            self._check_cancel()
            for i, target in enumerate(angles):
                self._check_cancel()
                # Vendor Servo.angle clamps raw PWM angles. Record that actual
                # commanded value instead of reporting an unreachable target.
                raw = max(-90, min(90, DIRECTIONS[i] * (target + self._offsets[i])))
                if self._servos:
                    self._servos[i].angle(raw)
                with self._lock:
                    self._angles[i] = DIRECTIONS[i] * raw - self._offsets[i]

    def _move(self, targets, speed):
        self._check_cancel()
        self._ensure_servos()
        with self._lock:
            start = list(self._angles)
        if any(a is None for a in start):
            # No encoders: interpolating from a guessed position is misleading.
            # The first full-pose command initializes the PWM targets.
            self._write(targets)
            if self._cancel.wait(0.25):
                raise MotionCancelled()
            return
        duration = max((1000 - 9.9 * speed)/1000, max(abs(a-b) for a,b in zip(start,targets))/428)
        count = max(1, math.ceil(duration / 0.02))
        for step in range(1, count+1):
            begin = time.monotonic()
            self._write([a+(b-a)*step/count for a,b in zip(start, targets)])
            if self._cancel.wait(max(0, duration/count - (time.monotonic()-begin))):
                raise MotionCancelled()

    def _run(self):
        while not self._closed.is_set():
            try:
                operation = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._check_cancel()
                operation()
            except MotionCancelled:
                # Gait planner state may have advanced past the interrupted pose.
                self._moves = None
            except Exception as exc:
                self._action_error = str(exc)
                self._error(f'Motion failed: {exc}')
                self._cancel.set()
                with self._lock:
                    self._armed, self._estop = False, True
                self._moves = None
            finally:
                with self._lock:
                    self._busy, self._action = False, None
                self._queue.task_done()

    def _poll_sensors(self):
        if self.simulate:
            # Simulated sensors stay unknown rather than inventing live returns.
            return
        battery = None
        try:
            if self._sonar_factory:
                self._sonar = self._sonar_factory()
            else:
                from robot_hat import Pin, Ultrasonic
                self._sonar = Ultrasonic(Pin('D2'), Pin('D3'), timeout=0.025)
            from robot_hat.device import get_battery_voltage
            battery = get_battery_voltage
        except Exception as exc:
            self._error(f'Sensor setup failed: {exc}')
        cycle = 0
        while not self._closed.is_set():
            if self._sonar:
                try:
                    value = self._sonar.read(times=1)
                    valid = isinstance(value, (int, float)) and math.isfinite(value) and 2 <= value <= 400
                    with self._lock:
                        self._range = {'distance_cm': round(value, 2) if valid else None, 'valid': valid, 'timestamp': time.time(),
                                       'raw_cm': value if isinstance(value, (int, float)) and math.isfinite(value) else None,
                                       'error': None if valid else ('Echo timed out on D2/D3' if value == -1 else 'Incomplete echo on D2/D3' if value == -2 else 'Return outside 2–400 cm range')}
                except Exception as exc:
                    with self._lock:
                        self._range = {'distance_cm': None, 'valid': False, 'timestamp': time.time()}
                    self._error(f'Ultrasonic read failed: {exc}')
            if battery and cycle % 25 == 0:
                try:
                    with self._io_lock:
                        value = battery()
                    with self._lock:
                        self._battery = round(value, 2) if math.isfinite(value) and value > 0 else None
                except Exception as exc:
                    self._error(f'Battery read failed: {exc}')
            cycle += 1
            self._closed.wait(0.2)
