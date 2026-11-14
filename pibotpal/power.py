# SPDX-License-Identifier: MIT
"""Cached Pi power telemetry. Reads never alter host power settings."""
import copy
from pathlib import Path
import re
import subprocess
import threading
import time


def decode_throttled(output):
    match = re.fullmatch(r'throttled=(0x[0-9a-fA-F]+)\s*', output)
    if not match:
        raise ValueError('Invalid Pi throttling response')
    bits = int(match[1], 16)
    return {'under_voltage': bool(bits & 1), 'under_voltage_occurred': bool(bits & (1 << 16)),
            'throttled': bool(bits & 4), 'throttled_occurred': bool(bits & (1 << 18)),
            'frequency_capped': bool(bits & 2), 'firmware_flags': hex(bits)}


class PowerMonitor:
    def __init__(self, simulate=False, *, cpu_root='/sys/devices/system/cpu/cpufreq',
                 thermal_file='/sys/class/thermal/thermal_zone0/temp', runner=None):
        self.simulate = simulate
        self.cpu_root, self.thermal_file = Path(cpu_root), Path(thermal_file)
        self._run = runner or subprocess.run
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._previous = None
        self._fault_generation = 0
        self._state = {'available': False, 'timestamp': None, 'error': None,
                       'cpu': {'current_mhz': None, 'max_mhz': None, 'min_mhz': None, 'governor': None},
                       'temperature_c': None, 'under_voltage': None, 'under_voltage_occurred': None,
                       'throttled': None, 'throttled_occurred': None, 'fault_generation': 0}

    def sample(self):
        if self.simulate:
            return self.status()
        errors, flags = [], {}
        cpu = {'current_mhz': None, 'max_mhz': None, 'min_mhz': None, 'governor': None}
        temperature = None
        try:
            policy = sorted(self.cpu_root.glob('policy*'))[0]
            for key, field in [('current_mhz', 'scaling_cur_freq'), ('max_mhz', 'scaling_max_freq'), ('min_mhz', 'scaling_min_freq')]:
                cpu[key] = int((policy / field).read_text()) / 1000
            cpu['governor'] = (policy / 'scaling_governor').read_text().strip()
        except (OSError, ValueError, IndexError) as exc:
            errors.append(f'CPU telemetry: {exc}')
        try:
            temperature = int(self.thermal_file.read_text()) / 1000
        except (OSError, ValueError):
            pass
        try:
            result = self._run(['/usr/bin/vcgencmd', 'get_throttled'], capture_output=True,
                               text=True, check=True, timeout=.5)
            flags = decode_throttled(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f'Pi voltage telemetry: {exc}')
        with self._lock:
            if flags:
                # A historical flag already set at startup is informational.
                # A fresh current/latched fault during this session is counted.
                if ((flags['under_voltage'] and (self._previous is None or not self._previous['under_voltage']))
                        or (self._previous is not None and flags['under_voltage_occurred']
                            and not self._previous['under_voltage_occurred'])):
                    self._fault_generation += 1
                self._previous = flags
            self._state = {'available': bool(flags), 'timestamp': time.time(), 'error': '; '.join(errors) or None,
                           'cpu': cpu, 'temperature_c': temperature,
                           'under_voltage': None, 'under_voltage_occurred': None,
                           'throttled': None, 'throttled_occurred': None,
                           **flags, 'fault_generation': self._fault_generation}
        return self.status()

    def status(self):
        with self._lock:
            value = copy.deepcopy(self._state)
        if value['timestamp'] is not None and time.time() - value['timestamp'] > 3:
            value.update(available=False, under_voltage=None, throttled=None, error='Pi power telemetry is stale')
        return value

    def start(self):
        if self.simulate or self._thread:
            return
        self.sample()
        def monitor():
            while not self._stop.wait(1):
                self.sample()
        self._thread = threading.Thread(target=monitor, daemon=True, name='pi-power-monitor')
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
