# SPDX-License-Identifier: MIT
"""Passive USB joystick discovery and a read-only Linux joystick event monitor.

A USB receiver being present does not establish that its wireless handset is
paired. This module never grabs a device, changes its mode, or commands motion.
"""
from __future__ import annotations

import copy
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
            'button_names':[BUTTON_CODES.get(code,f'Button {i} (code {code})') for i,code in enumerate(codes[:buttons])]}


class GamepadMonitor:
    def __init__(self, simulate=False, *, discover=None, opener=None, read=None,
                 close_fd=None, capability_reader=None, poll=None, settings_path=None):
        self.simulate = simulate
        self._discover = discover or discover_gamepads
        self._open = opener or (lambda path: os.open(path, os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC))
        self._read, self._close_fd = read or os.read, close_fd or os.close
        self._capabilities = capability_reader or capabilities
        self._poll = poll or (lambda fd,timeout: bool(select.select([fd],[],[],timeout)[0]))
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
        self._state = {'devices':[], 'active_device_id':None, 'connected':False,
                       'enabled':enabled, 'attached':False,
                       'axes':[], 'buttons':[], 'axis_names':[], 'button_names':[],
                       'last_event_at':None, 'events_seen':0, 'error':None,
                       'motion_enabled':False,
                       'note':'Read-only input monitor. Receiver presence does not verify wireless pairing. Move a stick or press a button to confirm live events; no robot movement is mapped.'}

    def start(self):
        with self._lock:
            if self._thread or self.simulate:
                return self.status()
            self._thread = threading.Thread(target=self._run,daemon=True,name='gamepad-monitor')
            self._thread.start()
        return self.status()

    def status(self):
        with self._lock:
            return copy.deepcopy(self._state)

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

    def set_enabled(self, enabled):
        """Enable USB input or close it immediately; never arms or moves the robot."""
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        with self._io_lock:
            if self._stop.is_set():
                raise RuntimeError('Controller input monitor is closed')
            self._save_enabled(enabled)
            with self._lock:
                self._state['enabled'] = enabled
            if not enabled:
                self._disconnect()
            elif not self.simulate:
                self._refresh()
            return self.status()

    def _disconnect(self, error=None):
        with self._io_lock:
            self._generation += 1
            if self._fd is not None:
                try: self._close_fd(self._fd)
                except OSError: pass
                self._fd = None
            self._buffer.clear()
            with self._lock:
                self._state.update(connected=False,active_device_id=None,axes=[],buttons=[],
                                   axis_names=[],button_names=[],last_event_at=None,events_seen=0,error=error)

    def _refresh(self):
        devices = self._discover()
        with self._io_lock:
            with self._lock:
                self._state.update(devices=devices, attached=bool(devices))
                active = self._state['active_device_id']
                enabled = self._state['enabled']
            if not enabled or self._stop.is_set():
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
                    data = self._capabilities(fd)
                    self._fd = fd
                    self._generation += 1
                    with self._lock:
                        self._state.update(connected=True,active_device_id=device['id'],error=None,
                                           axes=data['axes'],buttons=data['buttons'],
                                           axis_names=data['axis_names'],button_names=data['button_names'],
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
            if not self._state['enabled'] or self._stop.is_set():
                return
            self._buffer.extend(data)
            while len(self._buffer)>=EVENT.size:
                _, value, kind, number = EVENT.unpack(self._buffer[:EVENT.size])
                del self._buffer[:EVENT.size]
                initial = bool(kind & 0x80)
                kind &= ~0x80
                changed = False
                if kind == 1 and number < len(self._state['buttons']):
                    self._state['buttons'][number]=bool(value);changed=True
                elif kind == 2 and number < len(self._state['axes']):
                    self._state['axes'][number]=max(-1.0,min(1.0,value/32767));changed=True
                if changed and not initial:
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
