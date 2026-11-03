# SPDX-License-Identifier: GPL-3.0-only
# Adapted from SunFounder PiCrawler; see NOTICE and LICENSES/GPL-3.0.txt.
"""PiCrawler link geometry, derived from the installed SunFounder IK model.

Body coordinates: +x right, +y forward, +z up, millimetres. Servo ordering is
[knee beta, shoulder alpha, hip gamma], the order passed to servo_move by do_step.
These are commanded positions; ordinary PWM servos provide no position feedback.
"""
import math

A, B, C = 48.0, 78.0, 33.0
BODY_SIDE = 77.0
CHANNELS = [9, 10, 11, 3, 4, 5, 0, 1, 2, 6, 7, 8]
DIRECTIONS = [1, 1, -1, 1, 1, 1, 1, 1, -1, 1, 1, 1]
LEG_NAMES = ['Right front', 'Left front', 'Left rear', 'Right rear']
SIGNS = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
# The vendor limit_angle receives [beta, alpha, gamma] despite variable names.
LIMITS = [(-90, 90), (-10, 90), (-60, 60)]


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    return float(value)


def local_joints(angles):
    """Forward kinematics: hip, coxa end, knee, foot, in leg-local mm."""
    beta, alpha, gamma = map(math.radians, angles)
    yaw = math.pi / 4 - gamma
    shoulder = math.pi / 2 - alpha
    lower = shoulder - math.pi / 2 + beta
    def point(radius, height):
        return [radius * math.cos(yaw), radius * math.sin(yaw), height]
    r1 = C + A * math.cos(shoulder)
    z1 = A * math.sin(shoulder)
    return [[0.0, 0.0, 0.0], point(C, 0), point(r1, z1),
            point(r1 + B * math.cos(lower), z1 + B * math.sin(lower))]


def inverse(coord, strict=True):
    """Return servo-order angles. Non-strict matches vendor gait clamping."""
    if len(coord) != 3:
        raise ValueError('Each leg requires three coordinates')
    x, y, z = map(finite, coord)
    if max(abs(x), abs(y), abs(z)) > 200:
        raise ValueError('Leg coordinate outside supported workspace')
    w = math.hypot(x, y)
    v = w - C
    u = math.hypot(v, z)
    # Vendor intentionally restricts extension to this narrower range.
    if strict and not 30 <= u <= 91.58:
        raise ValueError('Foot target outside vendor workspace (30–91.58 mm from shoulder)')
    u = max(30, min(91.58, u))
    knee = math.acos(max(-1, min(1, (B*B + A*A - u*u) / (2*B*A))))
    shoulder = math.atan2(z, v) + math.acos(max(-1, min(1, (A*A + u*u - B*B) / (2*A*u))))
    result = [math.degrees(knee) - 90, 90 - math.degrees(shoulder), 45 - math.degrees(math.atan2(y, x))]
    if strict and any(not lo <= a <= hi for a, (lo, hi) in zip(result, LIMITS)):
        raise ValueError('Foot target requires a joint beyond vendor limits')
    return [max(lo, min(hi, a)) for a, (lo, hi) in zip(result, LIMITS)]


def leg_geometry(index, angles):
    if angles is None or any(a is None for a in angles):
        return {'index': index, 'name': LEG_NAMES[index], 'joints_mm': None, 'foot_local_mm': None}
    local = local_joints(angles)
    sx, sy = SIGNS[index]
    points = [[sx * (BODY_SIDE / 2 + x), sy * (BODY_SIDE / 2 + y), z] for x, y, z in local]
    return {'index': index, 'name': LEG_NAMES[index], 'joints_mm': points, 'foot_local_mm': local[-1]}
