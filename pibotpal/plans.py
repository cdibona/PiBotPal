# SPDX-License-Identifier: MIT
"""Durable, bounded robot plans. Drafting/saving never arms or moves the robot.

Pose values are commanded joint estimates, without physical feedback. Plans
snapshot poses so editing or deleting a library pose cannot alter a saved route.
Only explicit run() executes; nothing resumes after a process restart.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import uuid4

from .hardware import ACTIONS
from .kinematics import LIMITS

LIMITS_PUBLIC = {'max_steps': 64, 'max_run_seconds': 300, 'max_wait_seconds': 30,
                 'max_saved_poses': 256, 'max_saved_plans': 256,
                 'pose_source': 'commanded servo angles; no encoder feedback',
                 'supported_step_types': ['action', 'pose', 'wait']}


def _number(value, low, high, label, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or not low <= value <= high or
            (integer and int(value) != value)):
        raise ValueError(f'{label} must be {"an integer" if integer else "a number"} from {low} to {high}')
    return int(value) if integer else float(value)


def _text(value, label='Name', maximum=80, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or
            (not empty and not value.strip()) or any(ord(c) < 32 and c not in '\n\t' for c in value)):
        raise ValueError(f'Invalid {label.lower()}')
    return value.strip()


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Invalid saved item ID')
    return value


class PlanCancelled(Exception):
    pass


class PlanStore:
    def __init__(self, root, controller):
        self.controller = controller
        self.path = Path(root) / 'plans.json'
        self._lock = threading.RLock()
        self._dispatch = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._closed = False
        self._data = {'version': 1, 'poses': [], 'plans': []}
        self._state = {'running': False, 'plan_id': None, 'plan_name': None,
                       'phase': 'idle', 'step_index': None, 'step_count': 0,
                       'current_step': None, 'error': None, 'started_at': None,
                       'finished_at': None, 'timeout_seconds': None}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError('File too large')
                data = json.loads(self.path.read_text())
                if set(data) != {'version', 'poses', 'plans'} or type(data['version']) is not int or data['version'] != 1:
                    raise ValueError('Unknown plan schema')
                if not all(isinstance(data[k], list) and len(data[k]) <= 256 for k in ('poses', 'plans')):
                    raise ValueError('Invalid saved collections')
                self._data = data
                for kind in ('poses', 'plans'):
                    ids = set()
                    for item in data[kind]:
                        identifier = _identifier(item['id'])
                        if identifier in ids:
                            raise ValueError('Duplicate ID')
                        ids.add(identifier)
                        _text(item['name'])
                        if kind == 'poses':
                            self._angles(item['angles'], calibration=False)
                        else:
                            self.validate_steps(item['steps'], calibration=False)
                            _text(item.get('description', ''), 'Description', 2000, True)
                os.chmod(self.path, 0o600)
            except Exception as exc:
                raise RuntimeError('Saved plans could not be read; repair plans.json before starting') from exc

    def _write(self, data):
        encoded = json.dumps(data, indent=2, allow_nan=False) + '\n'
        if len(encoded.encode()) > 4 * 1024 * 1024:
            raise ValueError('Saved plans storage limit reached (4 MiB)')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.plans-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
            self._data = data
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def catalog(self):
        with self._lock:
            return {'poses': copy.deepcopy(self._data['poses']),
                    'plans': copy.deepcopy(self._data['plans']), 'limits': dict(LIMITS_PUBLIC)}

    def list_poses(self):
        return self.catalog()['poses']

    def list_plans(self):
        return self.catalog()['plans']

    def _get(self, kind, identifier):
        _identifier(identifier)
        with self._lock:
            item = next((row for row in self._data[kind] if row['id'] == identifier), None)
            if item is None:
                raise ValueError(f'Unknown saved {kind[:-1]}')
            return copy.deepcopy(item)

    def get_pose(self, pose_id):
        return self._get('poses', pose_id)

    def get_plan(self, plan_id):
        return self._get('plans', plan_id)

    def _angles(self, values, calibration=True):
        if not isinstance(values, (list, tuple)) or len(values) != 12:
            raise ValueError('Pose requires twelve known servo angles')
        offsets = self.controller.status().get('servo_offsets', [0] * 12) if calibration else [0] * 12
        result = []
        for index, value in enumerate(values):
            low, high = LIMITS[index % 3]
            value = _number(value, low, high, f'Servo {index}')
            if calibration and not -90 <= value + offsets[index] <= 90:
                raise ValueError(f'Servo {index} exceeds current calibrated limits')
            result.append(value)
        return result

    def _save(self, kind, item, identifier):
        with self._lock:
            data = copy.deepcopy(self._data)
            if identifier is not None:
                _identifier(identifier)
                if not any(row['id'] == identifier for row in data[kind]):
                    raise ValueError('Unknown saved item')
            elif len(data[kind]) >= 256:
                raise ValueError('Saved item limit reached')
            item['id'] = identifier or uuid4().hex
            item['updated_at'] = time.time()
            data[kind] = [row for row in data[kind] if row['id'] != item['id']] + [item]
            self._write(data)
            return copy.deepcopy(item)

    def save_pose(self, name, angles=None, pose_id=None):
        if angles is None:
            state = self.controller.status()
            if state.get('busy'):
                raise RuntimeError('Wait for movement to finish before capturing a pose')
            angles = state.get('servo_angles')
        return self._save('poses', {'name': _text(name), 'angles': self._angles(angles),
                                   'source': 'commanded'}, pose_id)

    def _delete(self, kind, identifier):
        with self._lock:
            self._get(kind, identifier)
            data = copy.deepcopy(self._data)
            data[kind] = [row for row in data[kind] if row['id'] != identifier]
            self._write(data)
        return {'deleted': identifier}

    def delete_pose(self, pose_id):
        return self._delete('poses', pose_id)

    def delete_plan(self, plan_id):
        return self._delete('plans', plan_id)

    def validate_steps(self, steps, calibration=True):
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            raise ValueError('Plan requires between 1 and 64 steps')
        result = []
        action_names = self.controller.actions.names() if hasattr(self.controller, 'actions') else ACTIONS
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError('Every plan step must be an object')
            kind = step.get('type')
            if kind == 'action':
                if set(step) - {'type', 'name', 'speed', 'steps'} or step.get('name') not in action_names:
                    raise ValueError('Unsupported plan action')
                result.append({'type': kind, 'name': step['name'],
                               'speed': _number(step.get('speed', 35), 1, 80, 'Speed', True),
                               'steps': _number(step.get('steps', 1), 1, 5, 'Action cycles', True)})
            elif kind == 'pose':
                if set(step) - {'type', 'pose_id', 'pose_name', 'angles', 'speed'}:
                    raise ValueError('Unknown pose step field')
                # An already saved step carries its immutable snapshot. Drafts
                # reference the current pose library entry before first saving.
                if 'angles' in step:
                    values = self._angles(step['angles'], calibration=calibration)
                    identifier = _identifier(step['pose_id']) if step.get('pose_id') else None
                    name = _text(step.get('pose_name', 'Servo pose'))
                else:
                    pose = self.get_pose(step.get('pose_id'))
                    values, identifier, name = self._angles(pose['angles'], calibration), pose['id'], pose['name']
                result.append({'type': kind, 'pose_id': identifier, 'pose_name': name,
                               'angles': values, 'speed': _number(step.get('speed', 30), 1, 80, 'Speed', True)})
            elif kind == 'wait':
                if set(step) - {'type', 'seconds'}:
                    raise ValueError('Unknown wait step field')
                result.append({'type': kind, 'seconds': _number(step.get('seconds', 1), 0, 30, 'Wait seconds')})
            else:
                raise ValueError('Supported plan steps are action, pose, and wait')
        if sum(step.get('seconds', 0) for step in result) > 300:
            raise ValueError('Total waiting exceeds the 300 second run limit')
        return result

    def save_plan(self, name, steps, plan_id=None, description=''):
        with self._lock:
            return self._save('plans', {'name': _text(name), 'steps': self.validate_steps(steps),
                                       'description': _text(description, 'Description', 2000, True)}, plan_id)

    def status(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def run(self, plan_id, timeout_seconds=300):
        # Serialize initial armed check and cancellation reset against stop.
        with self._dispatch:
            return self._start(plan_id, timeout_seconds)

    def _start(self, plan_id, timeout_seconds):
        timeout = _number(timeout_seconds, 1, 300, 'Plan timeout')
        with self._lock:
            if self._closed:
                raise RuntimeError('Plan runner is closed')
            if self._state['running']:
                raise RuntimeError('Another plan is running')
            state = self.controller.status()
            if not state.get('armed') or state.get('estop'):
                raise RuntimeError('Arm the robot before running a plan')
            if state.get('busy'):
                raise RuntimeError('Wait for current movement to finish')
            plan = self.get_plan(plan_id)
            plan['steps'] = self.validate_steps(plan['steps'])
            self._cancel.clear()
            self._state = {'running': True, 'plan_id': plan['id'], 'plan_name': plan['name'],
                           'phase': 'starting', 'step_index': None, 'step_count': len(plan['steps']),
                           'current_step': None, 'error': None, 'started_at': time.time(),
                           'finished_at': None, 'timeout_seconds': timeout}
            self._thread = threading.Thread(target=self._execute, args=(plan, timeout), name='pibotpal-plan', daemon=True)
            self._thread.start()
            return self.status()

    def _check(self, deadline):
        if self._cancel.is_set():
            raise PlanCancelled()
        if time.monotonic() >= deadline:
            raise RuntimeError('Plan exceeded its execution timeout')
        if not self.controller.status().get('armed'):
            raise PlanCancelled()

    def _execute(self, plan, timeout):
        deadline = time.monotonic() + timeout
        phase, error = 'complete', None
        try:
            for index, step in enumerate(plan['steps']):
                with self._dispatch:
                    self._check(deadline)
                    with self._lock:
                        self._state.update(phase='running', step_index=index, current_step=copy.deepcopy(step))
                    if step['type'] == 'action':
                        self.controller.action(step['name'], speed=step['speed'], steps=step['steps'])
                    elif step['type'] == 'pose':
                        self.controller.set_angles(step['angles'], speed=step['speed'])
                if step['type'] == 'wait':
                    end = time.monotonic() + step['seconds']
                    while time.monotonic() < end:
                        self._check(deadline)
                        self._cancel.wait(min(.05, max(0, end-time.monotonic())))
                else:
                    while self.controller.status().get('busy'):
                        self._check(deadline)
                        self._cancel.wait(.03)
                    state = self.controller.status()
                    if state.get('action_error'):
                        raise RuntimeError(state['action_error'])
                self._check(deadline)
        except PlanCancelled:
            phase = 'cancelled'
        except Exception as exc:
            phase, error = 'failed', str(exc)
        finally:
            # Keep the running flag until motion has stopped, so the caller
            # cannot start another owner during this cleanup barrier.
            with self._dispatch:
                self.controller.stop()
                with self._lock:
                    self._state.update(running=False, phase=phase, error=error, finished_at=time.time())

    def stop(self):
        self._cancel.set()
        # Event first, then dispatch barrier: once stop returns no queued plan
        # operation can be submitted, including a check/submit boundary race.
        with self._dispatch:
            # A run that held dispatch before us may have reset the event.
            self._cancel.set()
            self.controller.stop()
        return self.status()

    def close(self):
        with self._lock:
            self._closed = True
        self.stop()
        if self._thread:
            self._thread.join(timeout=3)


_WORDS = dict(zip(('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
                   'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen',
                   'eighteen', 'nineteen', 'twenty'), range(1, 21)))


def parse_plan(text, speed=35, action_names=None):
    """Parse a deliberately small command language; ambiguous input fails closed.

    Larger counts become several bounded action steps. Unsupported sensor
    conditions are rejected instead of being silently discarded or guessed.
    """
    original = _text(text, 'Plan description', 8000)
    speed = _number(speed, 1, 80, 'Speed', True)
    normalized = original.lower().replace('and then', ',').replace('then', ',')
    clauses = [part.strip() for part in re.split(r'[.,;\n]+', normalized) if part.strip()]
    steps = []
    names = sorted(ACTIONS if action_names is None else action_names, key=len, reverse=True)
    aliases = {'move forward': 'forward', 'walk forward': 'forward', 'move backward': 'backward',
               'walk backward': 'backward', 'left': 'turn left', 'right': 'turn right'}
    for clause in clauses:
        clause = re.sub(r'^and\s+', '', clause)
        if clause.startswith('wait') or clause.startswith('pause'):
            match = re.fullmatch(r'(?:wait|pause)(?: for)?\s+(\d+|[a-z]+)\s*(?:seconds?|s)?', clause)
            if not match:
                raise ValueError(f'Unrecognized plan instruction: {clause}')
            value = int(match[1]) if match[1].isdigit() else _WORDS.get(match[1])
            steps.append({'type': 'wait', 'seconds': _number(value, 0, 30, 'Wait seconds')})
            continue
        match = re.fullmatch(r'(.+?)(?:\s+(?:for\s+)?(\d+|[a-z]+)\s+(?:steps?|cycles?|times?))?', clause)
        name, count = match.groups()
        name = aliases.get(name, name)
        if name not in names:
            raise ValueError(f'Unrecognized plan instruction: {clause}')
        count = 1 if count is None else int(count) if count.isdigit() else _WORDS.get(count)
        count = _number(count, 1, 100, 'Action cycles', True)
        while count:
            batch = min(5, count)
            steps.append({'type': 'action', 'name': name, 'steps': batch, 'speed': speed})
            count -= batch
    if not 1 <= len(steps) <= 64:
        raise ValueError('Draft requires between 1 and 64 steps')
    if sum(step.get('seconds', 0) for step in steps) > 300:
        raise ValueError('Draft waits exceed the 300 second run limit')
    return {'steps': steps, 'source': 'parser', 'text': original,
            'requires_review': True, 'supports_conditions': False}
