"""Motion timing checks with virtual time and fake writes; no physical I/O."""
import pytest
from pibotpal.hardware import Controller, MotionCancelled
from pibotpal.kinematics import inverse
from pibotpal.motion import MotionPlan


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.cancel_at = None
    def monotonic(self): return self.now
    def is_set(self): return self.cancel_at is not None and self.now >= self.cancel_at
    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return self.is_set()


def setup_controller(monkeypatch, tmp_path, write_delay=0):
    clock = Clock()
    c = Controller(simulate=True, config_path=tmp_path/'calibration', budget_path=tmp_path/'budget.json')
    c._angles = [0.]*12
    c._cancel = clock
    trace = []
    original = c._write
    def write(angles):
        before = list(c._angles)
        original(angles)
        changed = [i for i, (a,b) in enumerate(zip(before,c._angles)) if a != b]
        trace.append((clock.now, list(c._angles), changed))
        clock.now += write_delay
    c._write = write
    monkeypatch.setattr('pibotpal.hardware.time.monotonic', clock.monotonic)
    return c, clock, trace


def test_unchanged_pose_has_no_dwell_or_writes(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._move([0.]*12,35)
    assert not trace and not clock.waits


def test_single_leg_small_change_is_rate_limited_without_fixed_pose_dwell(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._move([1.]*3+[0.]*9,35)
    assert clock.now == pytest.approx(.06)
    assert c._angles == pytest.approx([1.]*3+[0.]*9)
    assert all(set(changed) <= {0,1,2} for _,_,changed in trace)


def test_dense_waypoints_share_group_clock_without_per_sample_dwell(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._move_path([[5.]*12,[10.]*12],35)
    sparse_time = clock.now
    c._angles = [0.]*12; clock.now = 0; trace.clear(); c._last_group = None
    c._move_path([[float(i)]*12 for i in range(11)],35)
    assert clock.now == pytest.approx(sparse_time,abs=.02)
    assert clock.now < 3.1  # Three joint phases, each split 3 + 1 by the budget.
    assert all(b[0]-a[0] >= .01999 for a,b in zip(trace,trace[1:]))
    assert c._angles == pytest.approx([10.]*12)


def test_slow_writes_stretch_time_without_larger_angle_steps(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path,write_delay=.075)
    c._move_path([[10.]*12,[20.]*12],35)
    assert all(b[0]-a[0] >= .09499 for a,b in zip(trace,trace[1:]))
    before = [0.]*12
    for _, angles, _ in trace:
        assert sum(abs(a-b) for a,b in zip(before,angles)) <= 60*.02+1e-8
        assert max(abs(a-b) for a,b in zip(before,angles)) <= 30*.02+1e-8
        before = angles
    assert c._angles == pytest.approx([20.]*12)


def test_cancellation_stops_stream_before_final_target(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    clock.cancel_at = .05
    with pytest.raises(MotionCancelled): c._move_path([[10.]*12,[20.]*12],35)
    assert c._angles[0] < 20
    assert len(trace) <= 3
    assert c.servo_budget()['active_servos'] == []


def test_rounded_crawl_keeps_adjacent_single_leg_waypoints_continuous(monkeypatch,tmp_path):
    c,clock,trace = setup_controller(monkeypatch,tmp_path)
    c._angles = inverse([45,45,-50])*4
    poses = MotionPlan().forward
    c._move_path([c._pose_angles(p) for p in poses],35)
    assert c._angles == pytest.approx(c._pose_angles(poses[-1]))
    for previous,current in zip(trace,trace[1:]):
        if {i//3 for i in previous[2]} == {i//3 for i in current[2]}:
            assert current[0]-previous[0] == pytest.approx(.02)
