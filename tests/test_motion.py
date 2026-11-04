import math

import pytest

from pibotpal.kinematics import A, B, C, inverse, local_joints
from pibotpal.motion import MotionPlan, validate_plan

ACTIONS = ('stand', 'sit', 'ready', 'forward', 'backward', 'turn_left', 'turn_right',
           'turn_left_angle', 'turn_right_angle', 'push_up', 'wave', 'look_left',
           'look_right', 'look_up', 'look_down', 'dance')


@pytest.mark.parametrize('action', ACTIONS)
def test_all_motion_endpoints_are_reachable_without_clipping(action):
    planner = MotionPlan()
    for parity in (0, 1):
        planner.stand_position = parity
        poses = validate_plan(getattr(planner, action))
        for pose in poses:
            for foot in pose:
                joints = local_joints(inverse(foot))
                assert joints[-1] == pytest.approx(foot, abs=1e-8)
                assert [math.dist(a, b) for a, b in zip(joints, joints[1:])] == pytest.approx([C, A, B])


@pytest.mark.parametrize('action', ('forward', 'backward', 'turn_left', 'turn_right'))
def test_crawl_lifts_only_one_foot_at_a_time_and_finishes_centered(action):
    poses = getattr(MotionPlan(), action)
    for pose in poses:
        assert sum(foot[2] > -50 for foot in pose) <= 1
    assert poses[-1] == poses[0]


def test_plans_are_fresh():
    planner = MotionPlan()
    planner.forward[0][0][0] = 999
    assert planner.forward[0][0][0] == 45


@pytest.mark.parametrize('action', ('forward', 'backward', 'turn_left', 'turn_right'))
def test_crawl_has_small_continuous_samples_and_no_duplicate_dwell_poses(action):
    for parity in (0, 1):
        planner = MotionPlan()
        planner.stand_position = parity
        poses = getattr(planner, action)
        distances = [max(math.dist(a, b) for a, b in zip(start, end))
                     for start, end in zip(poses, poses[1:])]
        # The old lift/slide/lower schedule jumped a full 10 mm at each corner.
        # Rounded swing samples keep each geometric increment below 5 mm.
        assert all(0 < distance < 5 for distance in distances)
        for offset in range(0, 32, 8):
            swing = poses[offset:offset + 9]
            raised = [index for index, foot in enumerate(swing[4]) if foot[2] > -50]
            assert len(raised) == 1
            foot = raised[0]
            assert swing[0][foot][2] == -50
            assert swing[4][foot][2] == -40
            assert swing[-1][foot][2] == -50
        # The final body transfer keeps all feet on their support plane.
        assert all(foot[2] == -50 for pose in poses[-9:] for foot in pose)
