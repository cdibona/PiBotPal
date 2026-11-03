# SPDX-License-Identifier: MIT
"""Original bounded foot trajectories for PiBotPal.

A crawl moves one raised foot at a time and returns to a centered stance after
one cycle. These small trajectories need validation on the assembled robot;
there is no claim of dynamic balance or measured world displacement.
"""
import math

from .kinematics import BODY_SIDE, SIGNS, inverse


def stance(height=-50):
    return [[45.0, 45.0, float(height)] for _ in range(4)]


def displaced(pose, dx=0, dy=0, dz=0):
    return [[x + sx * dx, y + sy * dy, z + dz]
            for (x, y, z), (sx, sy) in zip(pose, SIGNS)]


class MotionPlan:
    """Generate fresh pose lists so callers cannot mutate later actions."""
    def __init__(self):
        self.stand_position = 0

    @property
    def stand(self):
        return [stance()]

    @property
    def sit(self):
        return [stance(-35)]

    @property
    def ready(self):
        return self.stand

    def _crawl(self, travel=0, rotation=0):
        neutral = stance()
        pose = stance()
        poses = [stance()]
        # Alternating opposite corners reduces consecutive loading on one side.
        order = (0, 2, 1, 3) if self.stand_position % 2 == 0 else (3, 1, 2, 0)
        for index in order:
            sx, sy = SIGNS[index]
            x, y, z = neutral[index]
            world_x, world_y = sx * (x + BODY_SIDE / 2), sy * (y + BODY_SIDE / 2)
            c, s = math.cos(rotation), math.sin(rotation)
            target = [sx * (c * world_x - s * world_y) - BODY_SIDE / 2,
                      sy * (s * world_x + c * world_y + travel) - BODY_SIDE / 2, z]
            for foot in ([x, y, z + 10], [target[0], target[1], z + 10], target):
                pose = [p[:] for p in pose]
                pose[index] = foot
                poses.append(pose)
        poses.append(neutral)
        return poses

    @property
    def forward(self):
        return self._crawl(travel=10)

    @property
    def backward(self):
        return self._crawl(travel=-10)

    @property
    def turn_left(self):
        return self._crawl(rotation=math.radians(4))

    @property
    def turn_right(self):
        return self._crawl(rotation=math.radians(-4))

    @property
    def turn_left_angle(self):
        return self.turn_left

    @property
    def turn_right_angle(self):
        return self.turn_right

    @property
    def push_up(self):
        return [stance(), stance(-35), stance(-58), stance()]

    @property
    def wave(self):
        poses = [stance()]
        for y in (45, 51, 39, 45):
            pose = stance()
            pose[0] = [45, y, -35]
            poses.append(pose)
        return poses + [stance()]

    def _look(self, dx=0, dy=0, tilt=0):
        pose = displaced(stance(), dx, dy)
        for leg, (_, sy) in zip(pose, SIGNS):
            leg[2] += sy * tilt
        return [stance(), pose, stance()]

    @property
    def look_left(self):
        return self._look(dx=6)

    @property
    def look_right(self):
        return self._look(dx=-6)

    @property
    def look_up(self):
        return self._look(tilt=-6)

    @property
    def look_down(self):
        return self._look(tilt=6)

    @property
    def dance(self):
        return [stance(), displaced(stance(), dx=5), displaced(stance(), dy=5),
                displaced(stance(), dx=-5), displaced(stance(), dy=-5), stance()]


def validate_plan(poses):
    """Validate every endpoint with strict IK before submitting a trajectory."""
    for pose in poses:
        if len(pose) != 4:
            raise ValueError('A trajectory pose requires four feet')
        for foot in pose:
            inverse(foot)
    return poses
