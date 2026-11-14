# SPDX-License-Identifier: MIT
"""Persisted controller bindings and bounded, neutral-gated input decisions.

No device writes happen here. The application executes decisions under its
shared motion gate, using the same operations as the MCP motion tools.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from .hardware import ACTIONS

DRIVING = {'forward', 'backward', 'turn left', 'turn right'}
ACTION_IDS = [name for name in ACTIONS if not name.endswith(' angle')] + ['stop']
FACE_LABELS = {'South': 'A / × (South)', 'East': 'B / ○ (East)',
               'West': 'X / □ (West)', 'North': 'Y / △ (North)'}


def defaults():
    pairs = [('axis:Hat Y:-', 'forward'), ('axis:Hat Y:+', 'backward'),
             ('axis:Hat X:-', 'turn left'), ('axis:Hat X:+', 'turn right'),
             ('axis:Y:-', 'forward'), ('axis:Y:+', 'backward'),
             ('axis:X:-', 'turn left'), ('axis:X:+', 'turn right'),
             ('button:South', 'stand'), ('button:East', 'sit'),
             ('button:West', 'wave'), ('button:North', 'ready'),
             ('button:Select', 'stop')]
    return {'version': 1, 'speed': 40, 'deadzone': .35,
            'bindings': [{'input': key, 'action': action,
                          'mode': 'hold' if action in DRIVING else 'press'} for key, action in pairs]}


def validate_mapping(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'speed', 'deadzone', 'bindings'}
            or type(value['version']) is not int or value['version'] != 1):
        raise ValueError('Invalid controller mapping version or fields')
    speed, deadzone, bindings = value['speed'], value['deadzone'], value['bindings']
    if type(speed) is not int or not 1 <= speed <= 80:
        raise ValueError('Controller speed must be an integer from 1 to 80')
    if type(deadzone) not in (int, float) or not .15 <= deadzone <= .8 or not math.isfinite(deadzone):
        raise ValueError('Stick dead zone must be between 0.15 and 0.8')
    if not isinstance(bindings, list) or len(bindings) > 64:
        raise ValueError('Provide at most 64 controller bindings')
    seen = set()
    for item in bindings:
        if not isinstance(item, dict) or set(item) != {'input', 'action', 'mode'}:
            raise ValueError('Each binding needs input, action, and mode')
        key = item['input']
        if not isinstance(key, str) or not re.fullmatch(r'(button:[^:\x00-\x1f]{1,64}|axis:[^:\x00-\x1f]{1,64}:[+-])', key):
            raise ValueError('Invalid controller input')
        if key == 'button:Start':
            raise ValueError('Start is reserved for controller arming')
        if key in seen:
            raise ValueError('An input can have only one action')
        seen.add(key)
        if (not isinstance(item['action'], str) or item['action'] not in ACTION_IDS
                or not isinstance(item['mode'], str) or item['mode'] not in ('press', 'hold')):
            raise ValueError('Unknown controller action or mode')
        if item['mode'] == 'hold' and item['action'] not in DRIVING:
            raise ValueError('Only walking and turning actions can repeat while held')
    return copy.deepcopy(value)


class MappingStore:
    def __init__(self, path):
        self.path = Path(path)
        self.value = defaults()
        if self.path.exists():
            try:
                self.value = validate_mapping(json.loads(self.path.read_text()))
                os.chmod(self.path, 0o600)
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError('Controller mappings could not be read; repair controller-mapping.json') from exc

    def save(self, speed, deadzone, bindings):
        value = validate_mapping({'version': 1, 'speed': speed, 'deadzone': deadzone, 'bindings': bindings})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.controller-mapping-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                os.fchmod(out.fileno(), 0o600)
                json.dump(value, out)
                out.flush(); os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.value = value

    def public(self, state):
        value = copy.deepcopy(self.value)
        inputs = []
        buttons = state.get('button_names') or ['South', 'East', 'West', 'North', 'Select', 'Start', 'L1', 'R1', 'L2', 'R2', 'Left stick', 'Right stick', 'Mode']
        axes = state.get('axis_names') or ['X', 'Y', 'Hat X', 'Hat Y']
        axis_labels = {'X': ('Left stick left', 'Left stick right'), 'Y': ('Left stick forward', 'Left stick back'),
                       'RX': ('Right stick left', 'Right stick right'), 'RY': ('Right stick up', 'Right stick down'),
                       'Hat X': ('D-pad left', 'D-pad right'), 'Hat Y': ('D-pad forward', 'D-pad back')}
        if state.get('button_mapping') == 'shanwan-2563:0575':
            axis_labels.update(Z=axis_labels['RX'], RZ=axis_labels['RY'])
        for name in buttons:
            inputs.append({'id': 'button:' + name, 'label': FACE_LABELS.get(name, name), 'kind': 'button'})
        for name in axes:
            for sign in ('-', '+'):
                label = axis_labels.get(name)
                inputs.append({'id': f'axis:{name}:{sign}', 'label': label[sign == '+'] if label else f'{name} {sign}', 'kind': 'axis'})
        known = {item['id'] for item in inputs}
        for binding in value['bindings']:
            if binding['input'] not in known:
                inputs.append({'id': binding['input'], 'label': binding['input'] + ' (not attached)',
                               'kind': binding['input'].split(':')[0]})
        value.update(inputs=inputs, actions=[{'id': name, 'label': 'Stop & disarm' if name == 'stop' else name.capitalize(),
                                             'hold': name in DRIVING} for name in ACTION_IDS], reserved=['button:Start'])
        return value


def input_values(state):
    values = {'button:' + name: bool(value) for name, value in
              zip(state.get('button_names', []), state.get('raw_buttons', state.get('buttons', [])))}
    for name, value in zip(state.get('axis_names', []), state.get('raw_axes', state.get('axes', []))):
        values[f'axis:{name}:-'] = max(0, -value)
        values[f'axis:{name}:+'] = max(0, value)
    return values


class GamepadDriver:
    HOLD_LIMIT = 10

    def __init__(self, mappings):
        self.mappings = mappings
        self._epoch = None
        self._neutral = True
        self._previous = set()
        self._event_active = set()
        self._held_inputs = set()
        self._owned = None
        self._hold_action = None
        self._hold_since = None
        self._active_input = None
        self._last_seq = 0
        self.state = {'state': 'offline', 'message': 'Connect a controller.', 'action': None, 'error': None}

    def reset(self):
        self._neutral = True
        self._previous.clear()
        self._held_inputs.clear()
        self._hold_action = self._hold_since = None
        self.state = {'state': 'waiting_neutral', 'message': 'Release the sticks and buttons to continue.',
                      'action': None, 'error': None}

    def owns(self, robot):
        return self._owned is not None and robot.get('busy') and robot.get('motion_id') == self._owned

    def started(self, decision, robot):
        self._owned = robot['motion_id']
        self._active_input = decision['input']
        self._hold_action = decision['action'] if decision['mode'] == 'hold' else None
        self.state.update(state='running', action=decision['action'], message=decision['action'].capitalize(), error=None)

    def halted(self):
        self._owned = self._hold_action = self._active_input = None

    def failed(self, exc):
        self.reset()
        self.state.update(state='blocked', error=str(exc), message=str(exc))

    def update(self, pad, robot, events, *, blocked=False, now=None):
        now = time.monotonic() if now is None else now
        timestamp = time.time()
        epoch = pad.get('input_epoch', 0)
        fresh = [event for event in events if event['seq'] > self._last_seq
                 and event.get('epoch', epoch) == epoch and 0 <= timestamp - event['time'] <= .5]
        if events:
            self._last_seq = max(self._last_seq, max(e['seq'] for e in events))
        bindings = {b['input']: b for b in self.mappings.value['bindings']}
        values = input_values(pad)
        deadzone = self.mappings.value['deadzone']
        # Hysteresis prevents a noisy stick from repeatedly crossing the edge.
        active = {key for key in bindings if values.get(key, 0) >=
                  (deadzone * .7 if key.startswith('axis:') and key in (self._previous | self._event_active) else deadzone)}
        # Derive edges from the full event stream, not just the latest snapshot:
        # a quick press/release must survive between polls, while a repeated
        # "pressed" report must not start another action. Axis presses use the
        # same dead zone and hysteresis as held movement, including Stop.
        presses = set()
        for event in fresh:
            name, value = event['name'], event['value']
            event_values = ({'button:' + name: bool(value)} if event['kind'] == 'button'
                            else {f'axis:{name}:-': max(0, -value),
                                  f'axis:{name}:+': max(0, value)})
            for key, value in event_values.items():
                if key not in bindings:
                    continue
                threshold = deadzone * .7 if key in self._event_active and key.startswith('axis:') else deadzone
                if value >= threshold:
                    if key not in self._event_active:
                        presses.add(key)
                    self._event_active.add(key)
                else:
                    self._event_active.discard(key)
        # Reconcile silent initialization and reconnect snapshots as well.
        self._event_active = set(active)
        owned = self.owns(robot)
        if self._owned is not None and not owned:
            self._owned = None
        changed_epoch = self._epoch != epoch
        if changed_epoch:
            self._epoch = epoch
            self.reset()
        # STOP remains a one-press cancellation when controller input is armed,
        # even if the robot is busy with a plan or system motion is disarmed.
        if pad.get('armed') and not changed_epoch:
            if any(bindings[key]['action'] == 'stop' for key in presses):
                self.reset()
                return {'kind': 'stop'}
        reason = ('offline' if not pad.get('connected') else 'controller_disarmed' if not pad.get('armed')
                  else 'system_disarmed' if not robot.get('armed') or robot.get('estop')
                  else 'blocked' if blocked else 'busy' if robot.get('busy') and not owned else None)
        if reason:
            self.reset()
            messages = {'offline': 'Connect a controller.', 'controller_disarmed': 'Controller actions are off; input monitoring remains live.',
                        'system_disarmed': 'Press Start or the controller icon to arm controller actions.',
                        'blocked': 'A plan or test is running. Stop it before using controller actions.',
                        'busy': 'Robot is busy. Release controls and try again when it finishes.'}
            self.state.update(state=reason, message=messages[reason])
            return {'kind': 'halt'} if owned else None
        if changed_epoch and owned:
            return {'kind': 'halt'}
        if owned and self._hold_action is None:
            # A one-shot action runs to completion. Inputs arriving during it
            # are consumed, and a held stick must not become a queued walk
            # as soon as the one-shot finishes.
            self._neutral = True
            self._previous = active
            return None
        if self._neutral:
            self._previous = active
            if not active:
                self._neutral = False
                self._previous.clear()
                self.state.update(state='ready', message='Ready for controller input.', action=None, error=None)
            else:
                self.state.update(state='waiting_neutral', message='Release the sticks and buttons to continue.')
            return None
        self._previous = active
        hold_bindings = [b for key, b in bindings.items() if key in active and b['mode'] == 'hold']
        hold_actions = {b['action'] for b in hold_bindings}
        if len(hold_actions) > 1:
            self.reset()
            self.state.update(message='Conflicting directions. Release the controls to continue.')
            return {'kind': 'halt'} if owned else None
        # A snapshot alone must not initiate motion after a delayed event or
        # lost queue. Fresh edges authorize each hold; snapshots then sustain
        # its bounded repeat until release, gating, or the hold timeout.
        self._held_inputs.intersection_update(active)
        self._held_inputs.update(key for key in presses if key in active and bindings[key]['mode'] == 'hold')
        if hold_bindings and not any(b['input'] in self._held_inputs for b in hold_bindings):
            self.reset()
            return {'kind': 'halt'} if owned else None
        hold_bindings = [b for b in hold_bindings if b['input'] in self._held_inputs]
        hold = next(iter(hold_actions), None)
        if hold:
            if self._hold_since is None:
                self._hold_since = now
            if now - self._hold_since >= self.HOLD_LIMIT:
                self.reset()
                self.state.update(message='Hold limit reached. Release the controls to continue.')
                return {'kind': 'halt'} if owned else None
        else:
            self._hold_since = None
        if owned:
            if self._hold_action and hold != self._hold_action:
                self.state.update(state='ready', action=None, message='Direction released; motion halted.')
                return {'kind': 'halt'}
            return None
        candidates = [bindings[key] for key in presses if key in bindings and bindings[key]['mode'] == 'press'
                      and bindings[key]['action'] != 'stop']
        if len(candidates) > 1 or (candidates and hold):
            self.reset()
            self.state.update(message='Press one action at a time; release controls to continue.')
            return None
        choice = candidates[0] if candidates else hold_bindings[0] if hold_bindings else None
        if choice:
            return {'kind': 'move', **choice, 'speed': self.mappings.value['speed'], 'steps': 1}
        self._hold_action = None
        self.state.update(state='ready', action=None, message='Ready for controller input.')
        return None
