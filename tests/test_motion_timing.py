"""Motion timing regression checks with a virtual clock; no physical I/O."""
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
    clock=Clock()
    c=Controller(simulate=True,config_path=tmp_path/'calibration')
    c._angles=[0.]*12
    c._cancel=clock
    trace=[]
    original=c._write
    def write(angles):
        trace.append((clock.now,list(angles)))
        original(angles)
        clock.now += write_delay
    c._write=write
    monkeypatch.setattr('pibotpal.hardware.time.monotonic',clock.monotonic)
    return c,clock,trace


def test_unchanged_pose_has_no_dwell_or_writes(monkeypatch,tmp_path):
    c,clock,trace=setup_controller(monkeypatch,tmp_path)
    c._move([0.]*12,35)
    assert not trace and not clock.waits


def test_small_joint_changes_do_not_take_fixed_six_hundred_ms(monkeypatch,tmp_path):
    c,clock,trace=setup_controller(monkeypatch,tmp_path)
    c._move([1.]*12,35)
    assert .02 <= clock.now <= .04+1e-9
    assert c._angles == pytest.approx([1.]*12)
    for (t1,a),(t2,b) in zip(trace,trace[1:]):
        assert max(abs(x-y) for x,y in zip(a,b))/(t2-t1) <= 63+1e-6


def test_dense_waypoints_share_one_clock_without_per_sample_dwell(monkeypatch,tmp_path):
    c,clock,trace=setup_controller(monkeypatch,tmp_path)
    c._move_path([[5.]*12,[10.]*12],35)
    sparse_time=clock.now
    c._angles=[0.]*12;clock.now=0;trace.clear()
    c._move_path([[float(i)]*12 for i in range(11)],35)
    assert clock.now == pytest.approx(sparse_time,abs=.02)
    assert clock.now < .2
    assert all(b[0]-a[0]>=.01999 for a,b in zip(trace,trace[1:]))
    assert c._angles == pytest.approx([10.]*12)


def test_slow_writes_skip_expired_frames_without_catchup_bursts(monkeypatch,tmp_path):
    c,clock,trace=setup_controller(monkeypatch,tmp_path,write_delay=.075)
    c._move_path([[10.]*12,[20.]*12],35)
    assert all(b[0]-a[0]>=.07999 for a,b in zip(trace,trace[1:]))
    assert c._angles == pytest.approx([20.]*12)


def test_cancellation_stops_stream_before_final_target(monkeypatch,tmp_path):
    c,clock,trace=setup_controller(monkeypatch,tmp_path)
    clock.cancel_at=.05
    with pytest.raises(MotionCancelled): c._move_path([[10.]*12,[20.]*12],35)
    assert c._angles[0] < 20
    assert len(trace)<=4


def test_rounded_crawl_duration_is_not_multiplied_by_waypoint_count(monkeypatch,tmp_path):
    c,clock,trace=setup_controller(monkeypatch,tmp_path)
    c._angles=inverse([45,45,-50])*4
    poses=MotionPlan().forward
    c._move_path([c._pose_angles(p) for p in poses],35)
    assert clock.now < 4  # Old 14-point crawl at35% required about9seconds.
    assert c._angles == pytest.approx(c._pose_angles(poses[-1]))
