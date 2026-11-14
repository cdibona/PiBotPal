"""Budget guarantees across initial pose, path planning, and configuration."""
import json
import pytest
from pibotpal.hardware import Controller, MotionCancelled
from test_motion_timing import setup_controller


def test_body_pose_serializes_leg_groups_with_settle(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
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
    assert [sample[0] for sample in trace] == pytest.approx([0,1,2,3])
    assert [sample[2] for sample in trace] == [[0,1,2],[3,4,5],[6,7,8],[9,10,11]]
    assert clock.now >= 4
    assert c.servo_budget()['unknown_position_settle_ms'] == 1000


def test_cancel_during_initial_settle_leaves_other_groups_untouched(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._angles = [None]*12
    clock.cancel_at = .5
    with pytest.raises(MotionCancelled): c._move([0.]*12,80)
    assert len(trace) == 1
    assert c._angles == [0.]*3+[None]*9


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
    assert json.loads(path.read_text()) == {'max_active_servos':2,'per_servo_deg_s':20,'total_deg_s':40,'settle_ms':100}
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
