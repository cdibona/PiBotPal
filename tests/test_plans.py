"""Plans are tested with fake controllers and simulation, never live hardware."""
import copy
import json
from pathlib import Path
import threading
import time

import pytest

from pibotpal.hardware import Controller
from pibotpal.plans import PlanStore, parse_plan


class FakeController:
    def __init__(self):
        self.state = {'armed': False, 'busy': False, 'estop': False, 'servo_angles': [0] * 12,
                      'servo_offsets': [0] * 12, 'action_error': None}
        self.calls = []

    def status(self):
        return copy.deepcopy(self.state)

    def action(self, name, **options):
        self.calls.append(('action', name, options))

    def set_angles(self, angles, **options):
        self.calls.append(('pose', list(angles), options))

    def stop(self):
        self.state.update(armed=False, busy=False)
        self.calls.append(('stop',))


@pytest.fixture
def store(tmp_path):
    return PlanStore(tmp_path, FakeController())


def wait(store, timeout=3):
    deadline = time.monotonic() + timeout
    while store.status()['running'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert not store.status()['running']
    return store.status()


def test_capture_requires_complete_idle_commanded_pose(store):
    store.controller.state['servo_angles'][0] = None
    with pytest.raises(ValueError):
        store.save_pose('Unknown')
    store.controller.state['servo_angles'] = [0] * 12
    store.controller.state['busy'] = True
    with pytest.raises(RuntimeError, match='finish'):
        store.save_pose('Moving')
    store.controller.state['busy'] = False
    pose = store.save_pose('Neutral')
    assert pose['angles'] == [0] * 12 and pose['source'] == 'commanded'
    assert store.controller.calls == []


def test_capture_rejects_joint_and_calibrated_limits(store):
    for values in ([0]*11, [None]*12, [float('nan')]*12, [True]*12, [91]*12):
        with pytest.raises(ValueError):
            store.save_pose('Bad', values)
    store.controller.state['servo_offsets'][0] = 2
    with pytest.raises(ValueError, match='calibrated'):
        store.save_pose('Too far', [90, 0, 0] + [0]*9)


def test_storage_persists_privately_without_resuming(tmp_path):
    controller = FakeController()
    first = PlanStore(tmp_path, controller)
    pose = first.save_pose('Neutral')
    plan = first.save_plan('Routine', [{'type': 'pose', 'pose_id': pose['id']}])
    assert first.path.stat().st_mode & 0o777 == 0o600
    second = PlanStore(tmp_path, controller)
    assert second.get_plan(plan['id']) == plan
    assert not second.status()['running']
    assert controller.calls == []


def test_plan_snapshots_survive_pose_edit_delete_and_restart(store):
    pose = store.save_pose('Neutral')
    plan = store.save_plan('Original', [{'type': 'pose', 'pose_id': pose['id']}])
    store.save_pose('New version', [5] * 12, pose_id=pose['id'])
    store.delete_pose(pose['id'])
    loaded = PlanStore(store.path.parent, store.controller)
    assert loaded.get_plan(plan['id'])['steps'][0]['angles'] == [0] * 12
    store.controller.state['armed'] = True
    loaded.run(plan['id'])
    assert wait(loaded)['phase'] == 'complete'
    assert store.controller.calls[0][1] == [0] * 12


def test_catalog_is_independent_and_deletion_checks_ids(store):
    pose = store.save_pose('Saved')
    catalog = store.catalog()
    catalog['poses'][0]['angles'][0] = 80
    assert store.get_pose(pose['id'])['angles'][0] == 0
    with pytest.raises(ValueError):
        store.delete_pose('../other-file')
    assert store.delete_pose(pose['id']) == {'deleted': pose['id']}
    with pytest.raises(ValueError):
        store.get_pose(pose['id'])


@pytest.mark.parametrize('step', [
    {'type': 'action', 'name': 'arm'}, {'type': 'action', 'name': 'forward', 'steps': 6},
    {'type': 'action', 'name': 'forward', 'steps': True},
    {'type': 'action', 'name': 'forward', 'speed': 0},
    {'type': 'action', 'name': 'stand', 'shell': 'echo surprise'},
    {'type': 'wait', 'seconds': float('inf')}, {'type': 'wait', 'seconds': 31},
    {'type': 'branch', 'condition': 'sonar clear'}, {'type': 'wait', 'seconds': 2, 'other': 1},
])
def test_steps_are_typed_and_bounded(store, step):
    with pytest.raises(ValueError):
        store.save_plan('Invalid', [step])
    assert not store.path.exists()


def test_whole_plan_size_wait_budget_and_names(store):
    for steps in ([], [{'type': 'wait', 'seconds': 0}] * 65, [{'type': 'wait', 'seconds': 30}] * 11):
        with pytest.raises(ValueError):
            store.save_plan('Invalid', steps)
    with pytest.raises(ValueError):
        store.save_plan('', [{'type': 'action', 'name': 'stand'}])


def test_run_requires_explicit_arm_and_waits_for_idle(store):
    plan = store.save_plan('Stand', [{'type': 'action', 'name': 'stand'}])
    with pytest.raises(RuntimeError, match='Arm'):
        store.run(plan['id'])
    store.controller.state.update(armed=True, busy=True)
    with pytest.raises(RuntimeError, match='finish'):
        store.run(plan['id'])
    assert store.controller.calls == []


def test_executes_in_order_and_disarms_after_completion(store):
    pose = store.save_pose('Neutral')
    plan = store.save_plan('Routine', [{'type': 'action', 'name': 'stand'},
                                      {'type': 'wait', 'seconds': .05},
                                      {'type': 'pose', 'pose_id': pose['id']},
                                      {'type': 'action', 'name': 'forward', 'steps': 4}])
    store.controller.state['armed'] = True
    store.run(plan['id'])
    state = wait(store)
    assert state['phase'] == 'complete' and state['step_index'] == 3
    assert [call[0] for call in store.controller.calls] == ['action', 'pose', 'action', 'stop']
    assert store.controller.calls[2][2]['steps'] == 4
    assert not store.controller.state['armed']


def test_stop_cancels_wait_and_prevents_following_motion(store):
    plan = store.save_plan('Waiting', [{'type': 'wait', 'seconds': 20},
                                      {'type': 'action', 'name': 'forward'}])
    store.controller.state['armed'] = True
    store.run(plan['id'])
    store.stop()
    assert wait(store)['phase'] == 'cancelled'
    assert not any(call[0] == 'action' for call in store.controller.calls)


def test_stop_barrier_during_dispatch_cannot_submit_another_step(store):
    entered, release = threading.Event(), threading.Event()
    def action(name, **options):
        store.controller.calls.append(('action', name, options))
        entered.set()
        release.wait(1)
    store.controller.action = action
    plan = store.save_plan('Race', [{'type': 'action', 'name': 'stand'},
                                   {'type': 'action', 'name': 'forward'}])
    store.controller.state['armed'] = True
    store.run(plan['id'])
    assert entered.wait(1)
    stopped = threading.Event()
    stopper = threading.Thread(target=lambda: (store.stop(), stopped.set()))
    stopper.start()
    assert not stopped.wait(.05)
    release.set()
    assert stopped.wait(1)
    stopper.join()
    assert wait(store)['phase'] == 'cancelled'
    assert [call[1] for call in store.controller.calls if call[0] == 'action'] == ['stand']


def test_timeout_stops_before_later_step(store):
    plan = store.save_plan('Timeout', [{'type': 'wait', 'seconds': 10},
                                     {'type': 'action', 'name': 'forward'}])
    store.controller.state['armed'] = True
    store.run(plan['id'], timeout_seconds=1)
    state = wait(store)
    assert state['phase'] == 'failed' and 'timeout' in state['error']
    assert not store.controller.state['armed']
    assert not any(call[0] == 'action' for call in store.controller.calls)


def test_controller_failure_stops_the_plan(store):
    def broken(*args, **kwargs):
        raise OSError('transport disconnected')
    store.controller.action = broken
    plan = store.save_plan('Failure', [{'type': 'action', 'name': 'stand'}])
    store.controller.state['armed'] = True
    store.run(plan['id'])
    assert wait(store)['phase'] == 'failed'
    assert not store.controller.state['armed']


def test_run_revalidates_snapshot_against_new_calibration(store):
    pose = store.save_pose('Edge', [90,0,0]+[0]*9)
    plan = store.save_plan('Old calibration', [{'type': 'pose', 'pose_id': pose['id']}])
    store.controller.state.update(armed=True)
    store.controller.state['servo_offsets'][0] = 2
    with pytest.raises(ValueError, match='calibrated'):
        store.run(plan['id'])
    assert store.controller.calls == []


def test_simulated_controller_executes_saved_pose(tmp_path):
    controller = Controller(simulate=True, config_path=tmp_path/'calibration', budget_path=tmp_path/'servo-power.json')
    store = PlanStore(tmp_path, controller)
    controller.start()
    try:
        pose = store.save_pose('Neutral', [0]*12)
        plan = store.save_plan('Pose', [{'type': 'pose', 'pose_id': pose['id'], 'speed': 80}])
        controller.arm(True)
        store.run(plan['id'])
        # Include the mandatory one-second settle for all four unknown groups.
        assert wait(store, timeout=6)['phase'] == 'complete'
        assert controller.status()['servo_angles'] == [0]*12
        assert not controller.status()['armed']
    finally:
        store.close()
        controller.close()


def test_natural_language_example_and_large_counts_are_bounded():
    draft = parse_plan('Stand. move forward 4 steps, turn left, move forward four steps.')
    assert [(step['name'], step['steps']) for step in draft['steps']] == [
        ('stand',1),('forward',4),('turn left',1),('forward',4)]
    assert draft['requires_review'] and not draft['supports_conditions']
    assert [s['steps'] for s in parse_plan('forward twelve steps')['steps']] == [5,5,2]
    assert parse_plan('wait three seconds')['steps'] == [{'type':'wait','seconds':3.0}]


@pytest.mark.parametrize('text', ['arm then forward', 'forward if sonar is clear',
                                 'forward 500 steps', 'delete all files', '',
                                 'stand and jump', 'wait forever'])
def test_parser_never_guesses_unknown_or_conditional_instructions(text):
    with pytest.raises(ValueError):
        parse_plan(text)


def test_corrupt_or_unknown_schema_cannot_be_silently_overwritten(tmp_path):
    (tmp_path/'plans.json').write_text(json.dumps({'version':99,'poses':[],'plans':[]}))
    with pytest.raises(RuntimeError, match='repair plans.json'):
        PlanStore(tmp_path, FakeController())
