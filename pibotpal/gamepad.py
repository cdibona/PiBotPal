# SPDX-License-Identifier: MIT
"""USB joystick discovery, controller arming, and Linux input monitoring.

A USB receiver being present does not establish that its wireless handset is
paired. Input monitoring never grabs a device or changes its mode. The dispatch
guard serializes externally supplied action submissions against disarming.
"""
from __future__ import annotations

import copy
from collections import deque
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import threading
import time
import tempfile

EVENT = struct.Struct('=IhBB')
AXIS_CODES = {0:'X', 1:'Y', 2:'Z', 3:'RX', 4:'RY', 5:'RZ', 6:'Throttle',
              7:'Rudder', 8:'Wheel', 9:'Gas', 10:'Brake', 16:'Hat X', 17:'Hat Y'}
BUTTON_CODES = {0x130:'South', 0x131:'East', 0x132:'C', 0x133:'North',
                0x134:'West', 0x135:'Z', 0x136:'L1', 0x137:'R1',
                0x138:'L2', 0x139:'R2', 0x13a:'Select', 0x13b:'Start',
                0x13c:'Mode', 0x13d:'Left stick', 0x13e:'Right stick'}


def discover_gamepads(sys_root='/sys/class/input', dev_root='/dev/input'):
    """Identify joystick-class devices via sysfs; no device files are opened."""
    root, dev = Path(sys_root), Path(dev_root)
    links = []
    for subdir in ('by-id', 'by-path'):
        for link in sorted((dev / subdir).glob('*-joystick')):
            if '-event-' not in link.name:
                try: links.append((link.resolve(), str(link)))
                except OSError: pass
    devices = []
    for node in sorted(root.glob('js*')):
        if not node.name[2:].isdigit():
            continue
        device = node / 'device'
        def value(relative):
            try: return (device / relative).read_text().strip()
            except OSError: return ''
        name, vendor, product = value('name'), value('id/vendor'), value('id/product')
        path = dev / node.name
        stable = next((label for target,label in links if target == path.resolve()), None)
        identity = stable or '|'.join((vendor, product, value('phys'), node.name))
        known = vendor == '2563' and product == '0575'
        devices.append({'id':'gamepad-'+hashlib.sha256(identity.encode()).hexdigest()[:12],
                        'name':name or 'Linux joystick', 'vendor_id':vendor, 'product_id':product,
                        'path':str(path), 'stable_path':stable,
                        'readable':os.access(path, os.R_OK),
                        'compatibility':('USB PS2-style controller identity (ShanWan 2563:0575); manufacturer and handset pairing are not verified.'
                                         if known else 'Linux joystick device; manufacturer/layout not verified.')})
    return devices


def _ioctl_bytes(fd, request, count):
    result = bytearray(count)
    fcntl.ioctl(fd, request, result, True)
    return result


def capabilities(fd):
    """Read standardized Linux joystick capability ioctls without changing mode."""
    axes = _ioctl_bytes(fd, 0x80016a11, 1)[0]
    buttons = _ioctl_bytes(fd, 0x80016a12, 1)[0]
    if axes > 64 or buttons > 128:
        raise OSError('Unsupported joystick capability count')
    name = bytes(_ioctl_bytes(fd, 0x81006a13, 256)).split(b'\0')[0].decode('utf-8','replace')
    axis_map = _ioctl_bytes(fd, 0x80406a32, 64)
    # Linux KEY_MAX=0x2ff; BTN_MISC=0x100; map holds 512 unsigned shorts.
    button_map = _ioctl_bytes(fd, 0x84006a34, 1024)
    codes = struct.unpack('=512H', button_map)
    return {'name':name, 'axes':[0.0]*axes, 'buttons':[False]*buttons,
            'axis_names':[AXIS_CODES.get(code,f'Axis {i} (code {code})') for i,code in enumerate(axis_map[:axes])],
            'button_codes':list(codes[:buttons]),
            'button_names':[BUTTON_CODES.get(code,f'Button {i} (code {code})') for i,code in enumerate(codes[:buttons])]}


def _controller_layout(device, data):
    """Resolve receiver-specific physical labels without changing kernel input."""
    data = dict(data, button_mapping='linux')
    if (device.get('vendor_id') == '2563' and device.get('product_id') == '0575'
            and data.get('button_codes') == list(range(0x130, 0x13d))):
        # This receiver reports ordinal HID buttons, so generic Linux labels
        # incorrectly call physical Start (index 9) R2 and right stick Start.
        # Mapping facts: SDL_GameControllerDB's Linux GUID
        # 03000000632500007505000010010000, corroborated by the device driver:
        # https://github.com/mdqinc/SDL_GameControllerDB/blob/master/gamecontrollerdb.txt
        # https://github.com/hbiyik/hid-shanwan/blob/master/hid-shanwan.c
        # Only repair the original sequential map; corrected drivers retain
        # their own semantics. No external driver or mapping library is used.
        data['button_names'] = ['North', 'East', 'South', 'West', 'L1', 'R1',
                                'L2', 'R2', 'Select', 'Start', 'Left stick',
                                'Right stick', 'Mode']
        data['button_mapping'] = 'shanwan-2563:0575'
    return data


class GamepadMonitor:
    def __init__(self, simulate=False, *, discover=None, opener=None, read=None,
                 close_fd=None, capability_reader=None, poll=None, settings_path=None, monotonic=None,
                 system_armed=None):
        self.simulate = simulate
        self._discover = discover or discover_gamepads
        self._open = opener or (lambda path: os.open(path, os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC))
        self._read, self._close_fd = read or os.read, close_fd or os.close
        self._capabilities = capability_reader or capabilities
        self._poll = poll or (lambda fd,timeout: bool(select.select([fd],[],[],timeout)[0]))
        self._monotonic = monotonic or time.monotonic
        self._system_armed = system_armed
        self._lock = threading.RLock()
        # Descriptor changes and reads are serialized separately from status.
        # select() runs without this lock; its generation is checked before read.
        self._io_lock = threading.RLock()
        self._generation = 0
        self._settings_path = Path(settings_path) if settings_path is not None else None
        enabled = True
        if self._settings_path and self._settings_path.exists():
            try:
                settings = json.loads(self._settings_path.read_text())
                if (not isinstance(settings, dict) or set(settings) != {'version', 'enabled'}
                        or settings['version'] != 1 or type(settings['enabled']) is not bool):
                    raise ValueError('Invalid controller settings')
                enabled = settings['enabled']
                os.chmod(self._settings_path, 0o600)
            except (OSError, ValueError) as exc:
                raise RuntimeError('Controller settings could not be read; repair controller.json') from exc
        self._stop = threading.Event()
        self._thread = None
        self._fd = None
        self._buffer = bytearray()
        self._start_down = False
        self._start_ready = False
        self._last_start_toggle = None
        self._arming_error = None
        self._pending_system_arm = None
        self._notifications = deque(maxlen=64)
        self._input_events = deque(maxlen=512)
        self._state = {'devices':[], 'active_device_id':None, 'connected':False,
                       'enabled':enabled, 'attached':False,
                       'axes':[], 'buttons':[], 'axis_names':[], 'button_names':[],
                       'raw_axes':[], 'raw_buttons':[], 'input_revision':0,
                       'input_epoch':0, 'recent_events':[],
                       'button_codes':[], 'button_mapping':None,
                       'last_event_at':None, 'events_seen':0, 'error':None,
                       'motion_enabled':False,
                       'note':'Start toggles controller arming. Disarming keeps live input visible while blocking its actions. Receiver presence does not verify wireless pairing.'}

    def start(self):
        with self._lock:
            if self._thread or self.simulate:
                return self.status()
            self._thread = threading.Thread(target=self._run,daemon=True,name='gamepad-monitor')
            self._thread.start()
        return self.status()

    def status(self):
        with self._lock:
            state = copy.deepcopy(self._state)
            state['error'] = self._arming_error or state['error']
            state['armed'] = bool(state['enabled'] and state['connected'])
            state['start_listening'] = bool(state['connected'] and 'Start' in state['button_names'])
            return state

    def drain_notifications(self):
        """Return explicit arming transitions for the application's announcer."""
        with self._lock:
            events = list(self._notifications)
            self._notifications.clear()
            return events

    def drain_input_events(self):
        """Drain valid, non-initial input edges independently of UI history.

        Consumers must reject events whose epoch differs from the current
        status, and reset held inputs whenever that epoch changes.
        """
        with self._lock:
            events = list(self._input_events)
            self._input_events.clear()
            return events

    def read_input(self):
        """Take one coherent snapshot and its edges for the action dispatcher."""
        with self._lock:
            return self.status(), self.drain_input_events()

    def discard_pending_actions(self):
        """Barrier for system disarm/STOP: earlier Start requests cannot rearm."""
        with self._io_lock:
            with self._lock:
                self._invalidate_input()
                self._notifications.clear()

    def _invalidate_input(self):
        """Called under the state lock when queued actions are no longer valid."""
        self._state['input_epoch'] += 1
        self._input_events.clear()
        self._pending_system_arm = None

    def _save_enabled(self, enabled):
        if self._settings_path is None:
            return
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix='.controller-', dir=self._settings_path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                os.fchmod(out.fileno(), 0o600)
                json.dump({'version': 1, 'enabled': enabled}, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(filename, self._settings_path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def set_enabled(self, enabled, *, notify=True, request_system_arm=False, arm_system=None):
        """Arm normal controller input; Start remains listened to when disarmed.

        The historical enabled preference is retained on disk and over MCP.
        The app can arm motion under the same device lock. Start requests are
        checked by the app before granting system motion permission.
        """
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        with self._io_lock:
            if self._stop.is_set():
                raise RuntimeError('Controller input monitor is closed')
            previous = self.status()
            before = previous['armed']
            self._save_enabled(enabled)
            try:
                if enabled and previous['connected'] and arm_system is not None:
                    arm_system()
            except Exception:
                self._save_enabled(previous['enabled'])
                raise
            with self._lock:
                self._arming_error = None
                changed = self._state['enabled'] != enabled
                self._state['enabled'] = enabled
                if changed or request_system_arm:
                    self._invalidate_input()
                    self._state.update(axes=[0.0] * len(self._state['axes']),
                                       buttons=[False] * len(self._state['buttons']),
                                       last_event_at=None, events_seen=0)
            if self._fd is None and not self.simulate:
                self._refresh()
            state = self.status()
            if notify and (before != state['armed'] or request_system_arm):
                with self._lock:
                    if request_system_arm:
                        self._pending_system_arm = state['input_epoch']
                    self._notifications.append({'armed': state['armed'],
                                                'arm_system': request_system_arm,
                                                'input_epoch': state['input_epoch']})
            return state

    def apply_arm_request(self, input_epoch, operation):
        """Resolve a deliberate Start request only in its original input session.

        Stop/disconnect/new toggles invalidate queued requests. The device lock
        prevents a new disarm racing between validation and system arming.
        """
        with self._io_lock:
            state = self.status()
            if (self._stop.is_set() or not state['armed']
                    or state['input_epoch'] != input_epoch
                    or self._pending_system_arm != input_epoch):
                return False
            try:
                operation()
            except Exception as exc:
                self.set_enabled(False, notify=False)
                with self._lock:
                    self._arming_error = f'Controller arming rejected: {exc}'
                raise
            with self._lock:
                self._pending_system_arm = None
            return True

    def dispatch(self, operation, *args, input_epoch, held_input=None, threshold=.35, **kwargs):
        """Submit a bounded action only while this exact input session is armed.

        The operation must only enqueue work, never wait for physical motion.
        Sharing the device lock with Start/disconnect makes the final permission
        check atomic with submission, even when the app's worker starts late.
        """
        with self._io_lock:
            with self._lock:
                if (self._stop.is_set() or not self._state['enabled'] or not self._state['connected']
                        or self._state['input_epoch'] != input_epoch):
                    raise RuntimeError('Controller input changed before action submission')
                if held_input:
                    kind, name, *direction = held_input.split(':')
                    names = self._state['button_names' if kind == 'button' else 'axis_names']
                    raw = self._state['raw_buttons' if kind == 'button' else 'raw_axes']
                    value = raw[names.index(name)] if name in names else 0
                    if kind == 'axis' and direction == ['-']:
                        value = -value
                    if value < threshold:
                        raise RuntimeError('Controller direction was released before action submission')
            return operation(*args, **kwargs)

    def _disconnect(self, error=None):
        with self._io_lock:
            self._generation += 1
            if self._fd is not None:
                try: self._close_fd(self._fd)
                except OSError: pass
                self._fd = None
            self._buffer.clear()
            self._start_down = self._start_ready = False
            self._last_start_toggle = None
            with self._lock:
                self._invalidate_input()
                self._state.update(connected=False,active_device_id=None,axes=[],buttons=[],
                                   raw_axes=[],raw_buttons=[],recent_events=[],
                                   axis_names=[],button_names=[],button_codes=[],button_mapping=None,
                                   last_event_at=None,events_seen=0,error=error)

    def _refresh(self):
        devices = self._discover()
        with self._io_lock:
            with self._lock:
                self._state.update(devices=devices, attached=bool(devices))
                active = self._state['active_device_id']
            if self._stop.is_set():
                self._disconnect()
                return
            if self._fd is not None and not any(device['id']==active for device in devices):
                self._disconnect()
            if self._fd is not None:
                with self._lock: self._state['error'] = None
                return
            if not devices:
                return
            errors = []
            for device in devices:
                fd = None
                try:
                    fd = self._open(device['stable_path'] or device['path'])
                    data = _controller_layout(device, self._capabilities(fd))
                    self._fd = fd
                    self._generation += 1
                    self._start_down = self._start_ready = False
                    self._last_start_toggle = None
                    with self._lock:
                        self._invalidate_input()
                        self._state.update(connected=True,active_device_id=device['id'],error=None,
                                           axes=data['axes'],buttons=data['buttons'],
                                           raw_axes=list(data['axes']),raw_buttons=list(data['buttons']),
                                           recent_events=[],
                                           axis_names=data['axis_names'],button_names=data['button_names'],
                                           button_codes=data.get('button_codes', []),
                                           button_mapping=data['button_mapping'],
                                           events_seen=0,last_event_at=None)
                    return
                except PermissionError:
                    errors.append('Cannot read controller. The service user needs input-group access; restart the user service after permissions change.')
                except OSError as exc:
                    errors.append(f'Controller could not be opened: {exc}')
                finally:
                    if fd is not None and fd != self._fd:
                        try: self._close_fd(fd)
                        except OSError: pass
            with self._lock: self._state['error'] = errors[-1] if errors else None

    def _events(self, data):
        with self._io_lock, self._lock:
            if self._stop.is_set():
                return
            self._buffer.extend(data)
            while len(self._buffer)>=EVENT.size:
                _, value, kind, number = EVENT.unpack(self._buffer[:EVENT.size])
                del self._buffer[:EVENT.size]
                initial = bool(kind & 0x80)
                kind &= ~0x80
                if kind == 1 and number < len(self._state['buttons']):
                    event_kind, field, names = 'button', 'raw_buttons', 'button_names'
                    normalized = bool(value)
                    filtered = 'buttons'
                elif kind == 2 and number < len(self._state['axes']):
                    event_kind, field, names = 'axis', 'raw_axes', 'axis_names'
                    normalized = max(-1.0, min(1.0, value/32767))
                    filtered = 'axes'
                else:
                    continue
                # Keep independent arrays, including when tests/simulation
                # supply capabilities directly without opening a receiver.
                if len(self._state[field]) != len(self._state[filtered]):
                    self._state[field] = list(self._state[filtered])
                self._state[field][number] = normalized
                is_start = (kind == 1 and number < len(self._state['button_names'])
                            and self._state['button_names'][number] == 'Start')
                if is_start:
                    pressed, was_down = bool(value), self._start_down
                    self._start_down = pressed
                    if initial:
                        # A handset already holding Start when attached must
                        # release it before any press is considered deliberate.
                        self._start_ready = not pressed
                    elif not pressed:
                        self._start_ready = True
                    elif not was_down and self._start_ready:
                        now = self._monotonic()
                        if self._last_start_toggle is None or now - self._last_start_toggle >= .25:
                            self._last_start_toggle = now
                            try:
                                system_armed = self._system_armed() if self._system_armed is not None else True
                                pending_arm = self._pending_system_arm == self._state['input_epoch']
                                enabled = not (self._state['enabled'] and (system_armed or pending_arm))
                                self.set_enabled(enabled, request_system_arm=enabled and self._system_armed is not None)
                            except (OSError, RuntimeError) as exc:
                                # A failed save must neither change the latch
                                # nor kill the live input-monitor thread.
                                self._arming_error = f'Controller arming could not be changed: {exc}'
                    if not initial:
                        self._state['events_seen'] += 1
                        self._state['last_event_at'] = time.time()
                if not initial:
                    # Losing an edge could leave an action held. Overflow
                    # invalidates the consumer's holds instead of dropping
                    # just the oldest release event silently.
                    if len(self._input_events) == self._input_events.maxlen:
                        self._invalidate_input()
                    self._state['input_revision'] += 1
                    labels = self._state[names]
                    event = {'seq':self._state['input_revision'], 'time':time.time(),
                             'kind':event_kind, 'index':number,
                             'name':labels[number] if number < len(labels) else f'{event_kind.title()} {number}',
                             'value':normalized, 'epoch':self._state['input_epoch']}
                    # Separate records ensure draining/mutating the dispatch
                    # queue cannot alter the independent read-only UI history.
                    self._input_events.append(dict(event))
                    self._state['recent_events'].append(event)
                    del self._state['recent_events'][:-64]
                if not self._state['enabled']:
                    continue
                self._state[filtered][number] = normalized
                if not initial and not is_start:
                    self._state['events_seen']+=1
                    self._state['last_event_at']=time.time()

    def _run(self):
        next_scan = 0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now>=next_scan:
                    try: self._refresh()
                    except OSError as exc:
                        with self._lock: self._state['error']=f'Controller discovery failed: {exc}'
                    next_scan=now+1
                with self._io_lock:
                    fd, generation = self._fd, self._generation
                if fd is None:
                    self._stop.wait(.1)
                    continue
                try:
                    if not self._poll(fd,.1): continue
                    with self._io_lock:
                        if fd != self._fd or generation != self._generation:
                            continue
                        data = self._read(fd,4096)
                        if not data:
                            self._disconnect('Controller disconnected')
                        else:
                            self._events(data)
                except BlockingIOError:
                    pass
                except OSError as exc:
                    with self._io_lock:
                        if fd == self._fd and generation == self._generation:
                            self._disconnect('Controller disconnected' if exc.errno in (errno.ENODEV,errno.EIO,errno.EBADF) else f'Controller read failed: {exc}')
        finally:
            self._disconnect()

    def close(self):
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=2)
        else:
            self._disconnect()
