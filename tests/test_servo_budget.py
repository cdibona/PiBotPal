"""Budget guarantees across initial pose, path planning, and configuration."""
import json
import pytest
from pibotpal.hardware import Controller, MotionCancelled
from test_motion_timing import setup_controller


@pytest.mark.parametrize('limit', [1, 3, 4])
def test_body_pose_completes_end_joints_then_shoulders_then_angles(monkeypatch, tmp_path, limit):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.set_servo_budget(limit, 30, 60, 80, body_sequence='joints')
    c._move([3.]*12, 80)
    expected_groups = []
    for joint in range(3):
        indices = list(range(joint, 12, 3))
        expected_groups.extend(indices[n:n+limit] for n in range(0, 4, limit))
    groups, before, previous_group, previous_time = [], [0.]*12, None, None
    for timestamp, angles, changed in trace:
        assert len(changed) <= limit
        assert len({i % 3 for i in changed}) == 1
        if changed != previous_group:
            groups.append(changed)
            if previous_group is not None:
                assert timestamp - previous_time >= .1 - 1e-8
            joint = changed[0] % 3
            assert all(before[i] == pytest.approx(3) for i in range(12) if i % 3 < joint)
        delta = [abs(a-b) for a, b in zip(before, angles)]
        assert max(delta) <= 30*.02 + 1e-8
        assert sum(delta) <= 60*.02 + 1e-8
        before, previous_group, previous_time = angles, changed, timestamp
    assert groups == expected_groups
    assert c._angles == pytest.approx([3.]*12)


def test_four_servo_budget_initializes_one_joint_type_at_a_time(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.set_servo_budget(4, 30, 60, 80)
    c._angles = [None]*12
    c._move([0.]*12, 80)
    assert [changed for _, _, changed in trace] == [[0,3,6,9], [1,4,7,10], [2,5,8,11]]
    assert [timestamp for timestamp, _, _ in trace] == pytest.approx([0, 1, 2])
    assert clock.now == pytest.approx(3)


def test_joint_sequence_keeps_single_leg_swing_together(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c._move_path([[v]*3 + [0.]*9 for v in (1., 2., 1., 0.)], 80)
    assert trace and all(changed == [0,1,2] for _, _, changed in trace)
    assert all(b[0] - a[0] == pytest.approx(.02) for a, b in zip(trace, trace[1:]))
    assert c._angles == pytest.approx([0.]*12)


def test_cancellation_during_end_phase_does_not_start_shoulders(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.set_servo_budget(4, 30, 60, 80)
    clock.cancel_at = .05
    with pytest.raises(MotionCancelled):
        c._move([10.]*12, 80)
    assert trace and all(set(changed) <= {0,3,6,9} for _, _, changed in trace)
    assert all(c._angles[i] == 0 for i in range(12) if i % 3)
    assert c.servo_budget()['active_servos'] == []


def test_gesture_completes_each_whole_body_keyframe_before_next(monkeypatch, tmp_path):
    from pibotpal.motion import MotionPlan
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.set_servo_budget(4, 30, 60, 80)
    keyframes = [c._pose_angles(pose) for pose in MotionPlan().push_up]
    c._angles = list(keyframes[0])
    monkeypatch.setattr(c, '_submit', lambda name, operation: operation())
    c.action('push up', speed=80)
    # Each full pose must exist in order. Streaming a whole gesture separately
    # for each joint group would never produce the intermediate full poses.
    after = 0
    for target in keyframes[1:]:
        reached = next(i for i in range(after, len(trace)) if trace[i][1] == pytest.approx(target))
        after = reached + 1
    assert c._angles == pytest.approx(keyframes[-1])


def test_legacy_budget_uses_joint_sequence_and_omitted_updates_preserve_selection(tmp_path):
    path = tmp_path/'budget.json'
    path.write_text(json.dumps({'max_active_servos': 3, 'per_servo_deg_s': 30,
                                'total_deg_s': 60, 'settle_ms': 80}))
    c = Controller(simulate=True, config_path=tmp_path/'calibration', budget_path=path)
    assert c.servo_budget()['body_sequence'] == 'joints'
    c.set_servo_budget(3, 30, 60, 80, body_sequence='legs')
    c.set_servo_budget(2, 20, 40, 100)
    assert c.servo_budget()['body_sequence'] == 'legs'
    assert json.loads(path.read_text())['body_sequence'] == 'legs'
    restored = Controller(simulate=True, config_path=tmp_path/'calibration', budget_path=path)
    assert restored.servo_budget()['body_sequence'] == 'legs'


@pytest.mark.parametrize('sequence', ['unknown', '', True, [], 1])
def test_invalid_sequence_cannot_overwrite_saved_budget(tmp_path, sequence):
    path = tmp_path/'budget.json'
    c = Controller(simulate=True, config_path=tmp_path/'calibration', budget_path=path)
    c.set_servo_budget(3, 30, 60, 80)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='body_sequence'):
        c.set_servo_budget(3, 30, 60, 80, body_sequence=sequence)
    assert path.read_bytes() == before


def test_invalid_saved_sequence_blocks_arming(tmp_path):
    path = tmp_path/'budget.json'
    path.write_text(json.dumps({'max_active_servos': 3, 'per_servo_deg_s': 30,
                                'total_deg_s': 60, 'settle_ms': 80, 'body_sequence': None}))
    c = Controller(simulate=True, config_path=tmp_path/'calibration', budget_path=path)
    with pytest.raises(RuntimeError, match='budget'):
        c.arm()


def test_body_pose_serializes_leg_groups_with_settle(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c.set_servo_budget(3,30,60,80,body_sequence='legs')
    c._move([3.]*12,80)
    last_leg, previous_time = None, 0
    before = [0.]*12
    legs_seen = []
    for timestamp,angles,changed in trace:
        assert len(changed) <= 3
        leg = changed[0]//3
        assert all(i//3 == leg for i in changed)
        if leg != last_leg:
            legs_seen.append(leg)
            if last_leg is not None:
                assert timestamp-previous_time >= .1-1e-8
        delta = [abs(a-b) for a,b in zip(before,angles)]
        assert max(delta) <= .6+1e-8
        assert sum(delta) <= 1.2+1e-8
        before, previous_time, last_leg = angles, timestamp, leg
    assert legs_seen == [0,1,2,3]
    assert c._angles == pytest.approx([3.]*12)


def test_unknown_first_pose_is_staggered_and_settled_including_last(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._angles = [None]*12
    c._move([0.]*12,80)
    assert [sample[0] for sample in trace] == pytest.approx([0,1,2,3,4,5])
    assert [sample[2] for sample in trace] == [[0,3,6],[9],[1,4,7],[10],[2,5,8],[11]]
    assert clock.now >= 6
    assert c.servo_budget()['unknown_position_settle_ms'] == 1000


def test_cancel_during_initial_settle_leaves_other_groups_untouched(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._angles = [None]*12
    clock.cancel_at = .5
    with pytest.raises(MotionCancelled): c._move([0.]*12,80)
    assert len(trace) == 1
    assert c._angles == [0.,None,None,0.,None,None,0.,None,None,None,None,None]


def test_one_servo_budget_and_aggregate_limit(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c.set_servo_budget(1,30,5,40)
    c._move([1.]*12,80)
    before = [0.]*12
    for timestamp,angles,changed in trace:
        assert len(changed) <= 1
        assert sum(abs(a-b) for a,b in zip(before,angles)) <= .1+1e-8
        before = angles
    assert c._angles == pytest.approx([1.]*12)


def test_budget_persists_atomically_and_requires_disarmed_idle(tmp_path):
    path = tmp_path/'servo-power.json'
    c = Controller(simulate=True, budget_path=path, config_path=tmp_path/'calibration')
    result = c.set_servo_budget(2,20,40,100)
    assert result['max_active_servos'] == 2
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {'max_active_servos':2,'per_servo_deg_s':20,'total_deg_s':40,'settle_ms':100,'body_sequence':'joints'}
    other = Controller(simulate=True, budget_path=path, config_path=tmp_path/'calibration')
    assert other.servo_budget()['per_servo_deg_s'] == 20
    c.arm()
    with pytest.raises(RuntimeError,match='Disarm'): c.set_servo_budget(3,30,60,80)
    c.stop(); c._busy = True
    with pytest.raises(RuntimeError,match='Disarm'): c.set_servo_budget(3,30,60,80)


@pytest.mark.parametrize('values', [(True,30,60,80),(7,30,60,80),(3,float('nan'),60,80),(3,30,181,80),(3,30,60,39),(3,30,60,80.5)])
def test_budget_rejects_invalid_values_without_saving(tmp_path,values):
    path = tmp_path/'budget.json'
    c = Controller(simulate=True,budget_path=path)
    with pytest.raises(ValueError): c.set_servo_budget(*values)
    assert not path.exists()


def test_corrupt_budget_blocks_arm_until_repaired(tmp_path):
    path = tmp_path/'budget.json'; path.write_text('{"max_active_servos":12}')
    c = Controller(simulate=True,budget_path=path)
    with pytest.raises(RuntimeError,match='budget'): c.arm()
    c.set_servo_budget(3,30,60,80)
    c.arm()
    assert c.status()['armed']


def test_interrupted_unknown_settle_is_respected_by_next_job(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._angles = [None]*12
    class EarlyCancel:
        canceled = False
        def is_set(self): return self.canceled
        def wait(self, seconds):
            clock.now += min(seconds,.1)
            self.canceled = True
            return True
    c._cancel = EarlyCancel()
    with pytest.raises(MotionCancelled): c._move([0.]*12,80)
    assert clock.now == pytest.approx(.1)
    c._cancel = clock
    c._move([0.]*12,80)
    assert trace[1][0] >= 1


def test_partial_unknown_write_also_preserves_settle_deadline(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._angles = [None]*12
    def interrupted(sample):
        c._angles[0] = sample[0]
        raise MotionCancelled()
    original = c._write
    c._write = interrupted
    with pytest.raises(MotionCancelled): c._move([0.]*12,80)
    c._write = original
    c._move([0.]*12,80)
    assert trace[0][0] >= 1


def test_stationary_servos_receive_no_repeated_pwm_commands(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    writes = []
    class Servo:
        def __init__(self,index): self.index = index
        def angle(self,value): writes.append((self.index,value))
    c._servos = [Servo(i) for i in range(12)]
    c._move([0.,1.,0.]+[0.]*9,80)
    assert writes and {index for index,_ in writes} == {1}
    count = len(writes)
    c._write([0.,1.,0.]+[0.]*9)
    assert len(writes) == count


def test_failed_budget_save_preserves_disk_and_active_limits(monkeypatch,tmp_path):
    path = tmp_path/'budget.json'
    c = Controller(simulate=True, budget_path=path, config_path=tmp_path/'calibration')
    c.set_servo_budget(2,20,40,100)
    before = path.read_bytes()
    def failed_replace(*args):
        raise OSError('Storage unavailable')
    monkeypatch.setattr('pibotpal.hardware.os.replace', failed_replace)
    with pytest.raises(OSError, match='Storage unavailable'):
        c.set_servo_budget(1,10,20,200)
    assert path.read_bytes() == before
    assert c.servo_budget()['max_active_servos'] == 2
    assert c.servo_budget()['total_deg_s'] == 40
    assert list(tmp_path.glob('.servo-power-*')) == []


def test_canceled_known_motion_settles_before_a_different_leg(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    clock.cancel_at = .05
    with pytest.raises(MotionCancelled):
        c._move([10.]*3+[0.]*9,80)
    last_write = trace[-1][0]
    old_trace_length = len(trace)
    clock.cancel_at = None
    next_pose = list(c._angles)
    next_pose[3:6] = [1.]*3
    c._move(next_pose,80)
    assert trace[old_trace_length][0] - last_write >= .08
    assert all(set(changed) <= {3,4,5} for _,_,changed in trace[old_trace_length:])
