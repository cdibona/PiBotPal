#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Apply a validated CPU frequency ceiling without changing voltage or governors."""
import argparse
from pathlib import Path


def apply_limit(max_khz, root=Path('/sys/devices/system/cpu/cpufreq')):
    policies = sorted(root.glob('policy*'))
    if not policies:
        raise RuntimeError('No CPU frequency policies are available')
    changes = []
    for policy in policies:
        minimum = int((policy / 'scaling_min_freq').read_text())
        maximum = int((policy / 'cpuinfo_max_freq').read_text())
        if type(max_khz) is not int or not minimum <= max_khz <= maximum:
            raise ValueError(f'{policy.name}: cap must be between {minimum} and {maximum} kHz')
        supported = policy / 'scaling_available_frequencies'
        if supported.exists() and max_khz not in [int(v) for v in supported.read_text().split()]:
            raise ValueError(f'{policy.name}: {max_khz} kHz is not a supported operating point')
        path = policy / 'scaling_max_freq'
        changes.append((path, path.read_text()))
    applied = []
    try:
        for path, previous in changes:
            applied.append((path, previous))
            path.write_text(str(max_khz) + '\n')
            if int(path.read_text()) != max_khz:
                raise RuntimeError(f'CPU frequency cap was not applied at {path}')
    except Exception:
        for path, previous in reversed(applied):
            path.write_text(previous)
        raise
    return len(changes)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-khz', type=int, default=1500000)
    args = parser.parse_args()
    count = apply_limit(args.max_khz)
    print(f'CPU ceiling set to {args.max_khz // 1000} MHz on {count} frequency policy/policies')
