# SPDX-License-Identifier: MIT
"""Three-link robot geometry, calculated with planar two-link vector algebra.

Lengths and electrical channel mappings describe the physical assembly. Angles
are commands, not encoder measurements. Axes are right, forward and up (mm).
"""
import math

A, B, C = 48.0, 78.0, 33.0
BODY_SIDE = 77.0
CHANNELS = [9, 10, 11, 3, 4, 5, 0, 1, 2, 6, 7, 8]
DIRECTIONS = [1, 1, -1, 1, 1, 1, 1, 1, -1, 1, 1, 1]
LEG_NAMES = ['Right front', 'Left front', 'Left rear', 'Right rear']
SIGNS = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
LIMITS = [(-90, 90), (-10, 90), (-60, 60)]
MIN_REACH, MAX_REACH = 30.0, 91.58


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    return float(value)


def local_joints(angles):
    """Accumulate the coxa and two arm vectors, then rotate about the hip."""
    if len(angles) != 3:
        raise ValueError('Each leg requires three angles')
    knee, shoulder, hip = [math.radians(finite(v)) for v in angles]
    heading = complex(math.cos(math.pi / 4 - hip), math.sin(math.pi / 4 - hip))
    upper_heading = math.pi / 2 - shoulder
    lower_heading = upper_heading + knee - math.pi / 2
    planar = complex(C, 0)
    positions = [(0.0, 0.0), (C, 0.0)]
    for length, angle in ((A, upper_heading), (B, lower_heading)):
        planar += length * complex(math.cos(angle), math.sin(angle))
        positions.append((planar.real, planar.imag))
    return [[r * heading.real, r * heading.imag, z] for r, z in positions]


def inverse(coord, strict=True):
    """Solve the folded elbow branch using the endpoint triangle.

    The deliberately limited reach envelope is a mechanical safety policy, well
    inside full link extension. Non-strict callers retain bounded joint clipping.
    """
    if len(coord) != 3:
        raise ValueError('Each leg requires three coordinates')
    x, y, z = [finite(v) for v in coord]
    if any(abs(v) > 200 for v in (x, y, z)):
        raise ValueError('Leg coordinate outside supported workspace')
    radial = math.hypot(x, y) - C
    distance = math.hypot(radial, z)
    if strict and not MIN_REACH <= distance <= MAX_REACH:
        raise ValueError('Foot target outside supported workspace (30–91.58 mm from shoulder)')
    distance = min(MAX_REACH, max(MIN_REACH, distance))
    cosine = (distance ** 2 - A ** 2 - B ** 2) / (2 * A * B)
    elbow = -math.acos(min(1.0, max(-1.0, cosine)))
    upper = math.atan2(z, radial) - math.atan2(B * math.sin(elbow), A + B * math.cos(elbow))
    result = [90 + math.degrees(elbow), 90 - math.degrees(upper),
              45 - math.degrees(math.atan2(y, x))]
    if strict and any(value < low - 1e-9 or value > high + 1e-9
                      for value, (low, high) in zip(result, LIMITS)):
        raise ValueError('Foot target requires a joint beyond supported limits')
    return [min(high, max(low, value)) for value, (low, high) in zip(result, LIMITS)]


def leg_geometry(index, angles):
    if index not in range(4):
        raise ValueError('Leg index must be between 0 and 3')
    result = {'index': index, 'name': LEG_NAMES[index], 'joints_mm': None, 'foot_local_mm': None}
    if angles is None or any(a is None for a in angles):
        return result
    local = local_joints(angles)
    sx, sy = SIGNS[index]
    result.update(foot_local_mm=local[-1], joints_mm=[
        [sx * (x + BODY_SIDE / 2), sy * (y + BODY_SIDE / 2), z] for x, y, z in local])
    return result
