# SPDX-License-Identifier: MIT
"""Shared, editable servo keyframes. Library operations never open hardware.

Built-in trajectories are converted to keyframes once, then use the same
definition and executor as user-created actions. Saved overrides are private,
bounded and atomic. A running action always owns an independent snapshot.
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

from .kinematics import LIMITS, inverse
from .motion import MotionPlan, validate_plan

BUILTIN_ACTIONS = ('stand', 'sit', 'ready', 'forward', 'backward', 'turn left', 'turn right',
                   'turn left angle', 'turn right angle', 'push up', 'wave', 'look left',
                   'look right', 'look up', 'look down', 'dance')
GAITS = frozenset(('forward', 'backward', 'turn left', 'turn right', 'turn left angle', 'turn right angle'))
PUBLIC_LIMITS = {'max_frames': 128, 'max_custom_actions': 128, 'max_run_seconds': 300,
                 'min_speed_deg_s': 1, 'max_speed_deg_s': 144, 'max_hold_seconds': 30,
                 'joint_limits': [list(pair) for pair in LIMITS],
                 'joint_names': ['End / knee', 'Shoulder', 'Angle / hip'],
                 'pose_source': 'commanded servo angles; no encoder feedback'}


def _number(value, low, high, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f'{label} must be a finite number from {low} to {high}')
    return float(value)


class ActionStore:
    def __init__(self, path, *, moves_factory=None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._defaults = self._seed(moves_factory)
        self._saved = {}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError('Action storage exceeds 4 MiB')
                data = json.loads(self.path.read_text())
                if (not isinstance(data, dict) or set(data) != {'version', 'actions'}
                        or type(data['version']) is not int or data['version'] != 1
                        or not isinstance(data['actions'], list)):
                    raise ValueError('Invalid action library schema')
                for item in data['actions']:
                    if not isinstance(item, dict) or set(item) != {'name', 'frames', 'description', 'playback', 'revision'}:
                        raise ValueError('Invalid saved action fields')
                    clean = self.validate(item['name'], item['frames'], item['description'], item['playback'])
                    revision = item['revision']
                    if type(revision) is not int or not 1 <= revision <= 2**53 - 1:
                        raise ValueError('Invalid action revision')
                    if clean['name'] in self._saved:
                        raise ValueError('Duplicate action name')
                    self._saved[clean['name']] = {**clean, 'revision': revision}
                self._check_count(self._saved)
                os.chmod(self.path, 0o600)
            except Exception as exc:
                raise RuntimeError('Saved actions could not be read; repair actions.json before starting') from exc

    @staticmethod
    def validate_name(name):
        if (not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9 _-]{0,63}', name)
                or name.strip() != name or '  ' in name or name in ('stop', 'arm', 'halt')):
            raise ValueError('Action name must use 1–64 lowercase letters, numbers, spaces, hyphens or underscores; stop, arm and halt are reserved')
        return name

    @staticmethod
    def validate_frames(frames):
        if not isinstance(frames, list) or not 1 <= len(frames) <= 128:
            raise ValueError('An action requires 1–128 keyframes')
        result = []
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict) or set(frame) - {'angles', 'speed_deg_s', 'hold_seconds'}:
                raise ValueError(f'Invalid keyframe {index + 1} fields')
            angles = frame.get('angles')
            if not isinstance(angles, (list, tuple)) or len(angles) != 12:
                raise ValueError(f'Keyframe {index + 1} requires twelve servo angles')
            angles = [_number(value, *LIMITS[joint % 3], f'Keyframe {index + 1} servo {joint}')
                      for joint, value in enumerate(angles)]
            result.append({'angles': angles,
                           'speed_deg_s': _number(frame.get('speed_deg_s', 30), 1, 144, 'Transition speed'),
                           'hold_seconds': _number(frame.get('hold_seconds', 0), 0, 30, 'Hold time')})
        if sum(frame['hold_seconds'] for frame in result) > 300:
            raise ValueError('Action hold time exceeds the 300 second run limit')
        return result

    @classmethod
    def validate(cls, name, frames, description='', playback='keyframes'):
        name = cls.validate_name(name)
        if not isinstance(description, str) or len(description) > 2000 or any(ord(c) < 32 and c not in '\n\t' for c in description):
            raise ValueError('Action description must contain at most 2000 characters')
        if playback not in ('keyframes', 'continuous'):
            raise ValueError('Playback must be keyframes or continuous')
        return {'name': name, 'description': description.strip(), 'playback': playback,
                'frames': cls.validate_frames(frames)}

    @classmethod
    def _seed(cls, moves_factory):
        generated, fallback = (moves_factory or MotionPlan)(), MotionPlan()
        result = {}
        for name in BUILTIN_ACTIONS:
            attribute = name.replace(' ', '_')
            poses = getattr(generated, attribute, None)
            if poses is None:
                poses = getattr(fallback, attribute)
            validate_plan(poses)
            frames = [{'angles': [angle for coord in pose for angle in inverse(coord)],
                       'speed_deg_s': 30, 'hold_seconds': 0} for pose in poses]
            result[name] = cls.validate(name, frames,
                'Editable shared ' + ('gait cycle.' if name in GAITS else 'keyframe action.'),
                'continuous' if name in GAITS else 'keyframes')
        return result

    def names(self):
        with self._lock:
            return list(BUILTIN_ACTIONS) + sorted(set(self._saved) - set(self._defaults))

    def _public(self, name):
        definition = self._saved.get(name) or {**self._defaults[name], 'revision': 0}
        builtin = name in self._defaults
        comparable = {key: definition[key] for key in ('name', 'frames', 'description', 'playback')}
        return copy.deepcopy({**definition, 'builtin': builtin,
                              'modified': builtin and comparable != self._defaults[name]})

    def get(self, name):
        try:
            self.validate_name(name)
        except ValueError:
            raise ValueError('Unknown action') from None
        with self._lock:
            if name not in self._saved and name not in self._defaults:
                raise ValueError('Unknown action')
            return self._public(name)

    def catalog(self):
        with self._lock:
            return {'actions': [self._public(name) for name in self.names()],
                    'limits': copy.deepcopy(PUBLIC_LIMITS)}

    def _check_count(self, saved):
        if len(set(saved) - set(self._defaults)) > 128:
            raise ValueError('Custom action limit reached (128)')

    def _write(self, saved):
        self._check_count(saved)
        encoded = json.dumps({'version': 1, 'actions': list(saved.values())}, indent=2, allow_nan=False) + '\n'
        if len(encoded.encode()) > 4 * 1024 * 1024:
            raise ValueError('Action storage exceeds 4 MiB')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.actions-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(encoded)
                out.flush(); os.fsync(out.fileno())
            os.replace(name, self.path)
            self._saved = saved
        finally:
            Path(name).unlink(missing_ok=True)

    def save(self, name, frames, description='', playback='keyframes'):
        clean = self.validate(name, frames, description, playback)
        with self._lock:
            previous = self._saved.get(name, {})
            clean['revision'] = previous.get('revision', 0) + 1
            if clean['revision'] > 2**53 - 1:
                raise ValueError('Action revision limit reached')
            self._write({**self._saved, name: clean})
            return self._public(name)

    def reset(self, name):
        self.validate_name(name)
        with self._lock:
            if name not in self._defaults:
                raise ValueError('Only built-in actions have a default to restore')
            return self.save(**self._defaults[name])

    def delete(self, name):
        self.validate_name(name)
        with self._lock:
            if name in self._defaults:
                raise ValueError('Built-in actions cannot be deleted; restore their defaults instead')
            if name not in self._saved:
                raise ValueError('Unknown action')
            self._write({key: value for key, value in self._saved.items() if key != name})
            return {'deleted': name}
