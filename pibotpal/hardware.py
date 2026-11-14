# SPDX-License-Identifier: MIT
"""Single-owner, interruptible PiCrawler controller.

Neither start nor arm resets the MCU or commands servos. Servo construction is
lazy, on the first explicitly requested movement. Stop cancels future writes and
holds the last PWM position; it is a software stop, not a hardware power cutoff.
"""
from __future__ import annotations

import ast
import fcntl
import json
import tempfile
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


class MotionCancelled(Exception):
    pass


class Controller:
    def __init__(self, simulate=None, *, servo_factory=None, sonar_factory=None, config_path=None, moves_factory=None, budget_path=None):
        self.simulate = os.getenv('PIBOTPAL_SIMULATE', '0') == '1' if simulate is None else bool(simulate)
        self._servo_factory, self._sonar_factory = servo_factory, sonar_factory
        from .motion import MotionPlan
        self._moves_factory = moves_factory or MotionPlan
        self._config = Path(config_path or Path.home() / '.config/.picrawler.config')
        self._lock = threading.RLock()
        self._io_lock = threading.RLock()
        self._cancel, self._closed = threading.Event(), threading.Event()
        self._queue = queue.Queue(maxsize=1)
        self._worker = self._sensor = None
        self._ownership = None
        self._servos, self._sonar, self._moves = [], None, None
        self._hat = None
        self._armed = self._busy = self._estop = False
        self._interrupting = 0
        self._motion_id = 0
        self._action = self._action_error = None
        self._angles = [None] * 12
        self._offsets = [0.0] * 12
        self._errors = []
        self._range = {'distance_cm': None, 'valid': False, 'timestamp': None}
        self._battery = None
        self._battery_timestamp = None
        self._started = time.monotonic()
        self._budget_path = Path(budget_path or Path(os.getenv('PIBOTPAL_DATA', '~/.local/share/pibotpal')).expanduser() / 'servo-power.json')
        self._budget = {'max_active_servos': 3, 'per_servo_deg_s': 30.0, 'total_deg_s': 60.0, 'settle_ms': 80}
        self._budget_error = None
        self._budget_phase, self._active_servos = 'idle', []
        self._last_group, self._group_finished = None, None
        self._group_settle_until = 0.0
        self._read_offsets()
        self._read_budget()

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
        if self._hat:
            try:
                self._hat.close()
            except Exception as exc:
                self._error(f'HAT close failed: {exc}')
            self._hat = None
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
            if self._busy or self._interrupting:
                raise RuntimeError('Wait for the current action to stop before arming')
            if self._budget_error:
                raise RuntimeError('Repair servo budget before arming')
            if any(e.startswith('Calibration read failed:') for e in self._errors):
                raise RuntimeError('Repair calibration before arming')
            self._cancel.clear()
            self._armed, self._estop = True, False
        return self.status()

    def stop(self):
        return self._interrupt(disarm=True)

    def halt(self):
        """Cancel motion and finish active writes without changing arming.

        The canceled worker may still be cleaning up when this returns. New
        motion is accepted once busy clears; halt never clears errors or estop.
        """
        return self._interrupt(disarm=False)

    def _interrupt(self, *, disarm):
        # Setting the event first also interrupts an in-progress interpolation.
        self._cancel.set()
        with self._lock:
            self._interrupting += 1
            # A submit holding this lock may have cleared the early signal.
            self._cancel.set()
            if disarm:
                self._armed, self._estop = False, True
        # Barrier: after return, an active servo write batch has finished.
        try:
            with self._io_lock:
                pass
        finally:
            with self._lock:
                self._interrupting -= 1
        return self.status()

    def status(self):
        with self._lock:
            angles = list(self._angles)
            sensor = dict(self._range)
            if sensor['timestamp'] is not None and time.time() - sensor['timestamp'] > 2:
                sensor['valid'] = False
            return {
                'armed': self._armed, 'busy': self._busy, 'estop': self._estop,
                'motion_id': self._motion_id,
                'servo_budget': self.servo_budget(),
                'action': self._action, 'action_error': self._action_error,
                'simulate': self.simulate, 'servo_angles': angles,
                'servo_offsets': list(self._offsets), 'servo_directions': list(DIRECTIONS),
                'servo_channels': list(CHANNELS),
                'servo_raw_angles': [None if a is None else DIRECTIONS[i] * (a + self._offsets[i]) for i, a in enumerate(angles)],
                'legs': [leg_geometry(i, angles[i*3:i*3+3]) for i in range(4)],
                'ultrasonic': sensor, 'battery_voltage': self._battery,
                'power': {'powered': True, 'estimated_runtime_minutes': None, 'sleep_after_minutes': None, 'voltage': self._battery, 'timestamp': self._battery_timestamp,
                          'valid': self._battery is not None and self._battery_timestamp is not None and time.time()-self._battery_timestamp < 15,
                          'source': 'Robot HAT ADC', 'charging': None, 'external_power': None},
                'orientation': {'available': False, 'source': 'unavailable; no IMU', 'roll': None, 'pitch': None, 'yaw': None},
                'pose_source': 'commanded' if all(a is not None for a in angles) else 'unknown',
                'errors': list(self._errors), 'uptime_s': round(time.monotonic()-self._started, 1),
                'geometry': {'upper_mm': 48, 'lower_mm': 78, 'coxa_mm': 33, 'body_side_mm': 77,
                             'axes': 'x right, y forward, z up', 'body_dimensions_source': 'nominal linkage dimensions; not surveyed CAD'},
                'stop_behavior': 'Cancel motion and hold last PWM command; power remains on',
            }

    @staticmethod
    def _validate_budget(max_active_servos, per_servo_deg_s, total_deg_s, settle_ms):
        if isinstance(max_active_servos, bool) or not isinstance(max_active_servos, int) or not 1 <= max_active_servos <= 6:
            raise ValueError('max_active_servos must be an integer from 1 to 6')
        if isinstance(settle_ms, bool) or not isinstance(settle_ms, int) or not 40 <= settle_ms <= 500:
            raise ValueError('settle_ms must be an integer from 40 to 500')
        per_servo_deg_s, total_deg_s = finite(per_servo_deg_s), finite(total_deg_s)
        if not 5 <= per_servo_deg_s <= 60 or not 5 <= total_deg_s <= 180:
            raise ValueError('Servo rate must be 5–60 and total rate 5–180 degrees per second')
        return dict(max_active_servos=max_active_servos, per_servo_deg_s=per_servo_deg_s,
                    total_deg_s=total_deg_s, settle_ms=settle_ms)

    def _read_budget(self):
        try:
            if self._budget_path.exists():
                self._budget = self._validate_budget(**json.loads(self._budget_path.read_text()))
        except Exception as exc:
            self._budget_error = str(exc)
            self._error(f'Servo budget read failed: {exc}')
            self._estop = True

    def servo_budget(self):
        with self._lock:
            return {**self._budget, 'active_servos': list(self._active_servos),
                    'phase': self._budget_phase, 'unknown_position_settle_ms': max(1000, self._budget['settle_ms']),
                    'error': self._budget_error, 'units': 'commanded degrees/second',
                    'description': 'Commanded motion budget; no current feedback. Holding servos still draw power. Unknown initial positions cannot be rate limited.'}

    def set_servo_budget(self, max_active_servos, per_servo_deg_s, total_deg_s, settle_ms):
        values = self._validate_budget(max_active_servos, per_servo_deg_s, total_deg_s, settle_ms)
        with self._lock:
            if self._armed or self._busy or self._interrupting:
                raise RuntimeError('Disarm and wait for motion to stop before saving servo budget')
            self._budget_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=self._budget_path.parent, prefix='.servo-power-')
            try:
                with os.fdopen(descriptor, 'w') as handle:
                    json.dump(values, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self._budget_path)
            finally:
                Path(name).unlink(missing_ok=True)
            self._budget = values
            self._budget_error = None
            self._errors = [e for e in self._errors if not e.startswith('Servo budget read failed:')]
            return self.servo_budget()

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
            if self._busy or self._interrupting:
                raise RuntimeError('Robot is busy; wait or stop the current action')
            # A halted job retains busy until its worker has fully unwound.
            # Only a fresh accepted job may clear its cancellation signal.
            self._cancel.clear()
            self._motion_id += 1
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
            path = []
            for _ in range(steps):
                self._check_cancel()
                poses = getattr(self._moves, name.replace(' ', '_'))
                from .motion import validate_plan
                validate_plan(poses)
                path.extend(self._pose_angles(pose) for pose in poses)
                if name in ('forward', 'backward', 'turn left', 'turn right', 'turn left angle', 'turn right angle'):
                    self._moves.stand_position = (self._moves.stand_position + 1) & 1
            self._move_path(path, speed)
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

    def _get_hat(self):
        with self._io_lock:
            if self._hat is None:
                from .hat import Hat
                self._hat = Hat()
            return self._hat

    def _ensure_servos(self):
        if len(self._servos) == 12 or (self.simulate and self._servo_factory is None):
            return
        if self._servo_factory is None:
            factory = self._get_hat().servo
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
                if target is None:
                    continue
                # Record the calibrated command after applying the physical range.
                raw = max(-90, min(90, DIRECTIONS[i] * (target + self._offsets[i])))
                applied = DIRECTIONS[i] * raw - self._offsets[i]
                if self._angles[i] is not None and abs(applied - self._angles[i]) < 1e-9:
                    continue
                if self._servos:
                    self._servos[i].angle(raw)
                with self._lock:
                    self._angles[i] = DIRECTIONS[i] * raw - self._offsets[i]

    def _move(self, targets, speed):
        self._move_path([targets], speed)

    def _groups(self, indices):
        # Keep joints on the same leg together. A stricter configured limit
        # splits a leg; larger limits never force unrelated legs to move.
        limit = self._budget['max_active_servos']
        result = []
        for leg in range(4):
            joints = [i for i in indices if i // 3 == leg]
            result.extend(tuple(joints[n:n+limit]) for n in range(0, len(joints), limit))
        return tuple(result)

    def _group_begin(self, group, phase):
        self._check_cancel()
        now = time.monotonic()
        wait_until = self._group_settle_until
        if self._last_group is not None and tuple(group) != self._last_group:
            wait_until = max(wait_until, self._group_finished + self._budget['settle_ms'] / 1000)
        if wait_until > now:
            with self._lock:
                self._budget_phase, self._active_servos = 'settling', []
            if self._cancel.wait(wait_until - now):
                raise MotionCancelled()
            self._check_cancel()
        with self._lock:
            self._budget_phase, self._active_servos = phase, list(group)

    def _group_end(self, group):
        self._last_group, self._group_finished = tuple(group), time.monotonic()

    def _move_path(self, targets, speed):
        """Serialize leg trajectories under a commanded travel budget.

        Progress advances by at most one 20ms quantum per emitted sample.
        Scheduler stalls stretch time, never create catch-up angle jumps.
        A servo's initial physical position is unknowable without encoders:
        initialize only one group, then allow at least 1000ms to settle. This
        conservative delay does not confirm that the position was reached.
        """
        self._check_cancel()
        targets = [list(target) for target in targets]
        if not targets:
            return
        self._ensure_servos()
        try:
            # Establish unknown joints without pretending to interpolate from a
            # guessed position. Initialization remains staggered after a stop.
            unknown = [i for i, angle in enumerate(self._angles) if angle is None]
            for group in self._groups(unknown):
                self._group_begin(group, 'initializing unknown positions')
                sample = [None] * 12
                for index in group:
                    sample[index] = targets[0][index]
                delay = max(1.0, self._budget['settle_ms'] / 1000)
                try:
                    self._write(sample)
                finally:
                    # Cancellation or an I/O failure may interrupt a group
                    # after its first servo was already commanded.
                    self._group_end(group)
                    self._group_settle_until = time.monotonic() + delay
                with self._lock:
                    self._budget_phase, self._active_servos = 'settling unknown positions', []
                if self._cancel.wait(delay):
                    raise MotionCancelled()
                self._check_cancel()
            # Consecutive segments with the same changed joint groups share a
            # continuous trajectory. In a crawl this keeps a foot's complete
            # lift/swing/plant path together rather than pausing each waypoint.
            previous = list(self._angles)
            signature, run = None, []
            for target in targets:
                groups = self._groups([i for i, (a, b) in enumerate(zip(previous, target)) if abs(a-b) > 1e-9])
                if not groups:
                    previous = target
                    continue
                if signature is not None and groups != signature:
                    self._stream_groups(run, signature, speed)
                    run = []
                signature = groups
                run.append(target)
                previous = target
            if run:
                self._stream_groups(run, signature, speed)
        finally:
            # Even a canceled active group needs settling before a different
            # group may start in a subsequent accepted motion job.
            with self._lock:
                if self._active_servos:
                    self._group_end(self._active_servos)
                self._active_servos, self._budget_phase = [], 'idle'

    def _stream_groups(self, targets, groups, speed):
        rate = min(180.0 * speed / 100.0, self._budget['per_servo_deg_s'])
        aggregate = self._budget['total_deg_s']
        for group in groups:
            self._group_begin(group, 'moving')
            start = [self._angles[i] for i in group]
            points, durations = [start], []
            for target in targets:
                end = [target[i] for i in group]
                delta = [abs(a-b) for a, b in zip(points[-1], end)]
                duration = max(max(delta) / rate, sum(delta) / aggregate)
                if duration > 1e-12:
                    points.append(end)
                    durations.append(duration)
            elapsed, segment, consumed = 0.0, 0, 0.0
            total = sum(durations)
            deadline = time.monotonic() + .02
            while elapsed < total - 1e-12:
                self._check_cancel()
                if self._cancel.wait(max(0, deadline - time.monotonic())):
                    raise MotionCancelled()
                self._check_cancel()
                elapsed = min(total, elapsed + .02)
                while segment < len(durations)-1 and elapsed > consumed + durations[segment]:
                    consumed += durations[segment]
                    segment += 1
                weight = min(1, (elapsed - consumed) / durations[segment])
                sample = [None] * 12
                for n, index in enumerate(group):
                    sample[index] = points[segment][n] + (points[segment+1][n] - points[segment][n]) * weight
                self._write(sample)
                # An expired deadline gives no extra progression allowance.
                # Ensure a full control interval after slow device writes.
                deadline = max(deadline + .02, time.monotonic() + .02)
            self._group_end(group)

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
                from .hat import Sonar
                self._sonar = Sonar(timeout=0.025)
        except Exception as exc:
            self._error(f'Sensor setup failed: {exc}')
        try:
            battery = self._get_hat().battery_voltage
        except Exception as exc:
            self._error(f'Battery setup failed: {exc}')
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
                        self._battery_timestamp = time.time()
                except Exception as exc:
                    self._error(f'Battery read failed: {exc}')
            cycle += 1
            self._closed.wait(0.2)
