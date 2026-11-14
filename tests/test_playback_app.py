"""Editor arm-and-play requests use MCP and simulated hardware only."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from tests.test_controller_app import client, call, wait_for


def request(client, kind):
    if kind == 'move':
        return kind, {'name': 'stand'}
    plan = call(client, 'plan_save', name='Editor playback', steps=[
        {'type': 'action', 'name': 'stand'}, {'type': 'wait', 'seconds': .1}])['value']
    return 'plan_run', {'plan_id': plan['id']}


def permission(client):
    state = client.app.state.controller.status()
    return {key: state[key] for key in ('armed', 'estop', 'motion_id', 'servo_angles')}


@pytest.mark.parametrize('kind', ['move', 'plan_run'])
def test_saved_playback_requires_arm_or_explicit_arm_and_play(client, monkeypatch, kind):
    robot = client.app.state.controller
    paths, spoken = [], []
    monkeypatch.setattr(robot, '_move_path', lambda path, speed: paths.append(path))
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    tool, arguments = request(client, kind)
    before = permission(client)
    assert call(client, tool, **arguments)['isError']
    assert permission(client) == before and not paths and not spoken
    assert not call(client, tool, **arguments, arm_motion=True).get('isError')
    wait_for(lambda: len(paths) == 1 and not robot.status()['busy'])
    if kind == 'plan_run':
        wait_for(lambda: not client.app.state.plans.status()['running'])
        assert client.app.state.plans.status()['phase'] == 'complete'
        assert not robot.status()['armed']
    else:
        assert robot.status()['armed']
        # A second explicit play while armed does not re-arm or repeat speech.
        assert not call(client, tool, **arguments, arm_motion=True).get('isError')
        wait_for(lambda: len(paths) == 2 and not robot.status()['busy'])
    assert spoken == ['system armed']
    assert robot._hat is None and robot._servos == []


@pytest.mark.parametrize('arguments', [
    {'name': 'missing'}, {'name': 'stand', 'speed': 0},
    {'name': 'stand', 'speed': 81}, {'name': 'stand', 'steps': 0},
    {'name': 'stand', 'steps': 6},
])
def test_bad_action_request_never_grants_permission(client, monkeypatch, arguments):
    spoken = []
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    before = permission(client)
    assert call(client, 'move', **arguments, arm_motion=True)['isError']
    assert permission(client) == before and not spoken


@pytest.mark.parametrize('kind', ['move', 'plan_run'])
@pytest.mark.parametrize('failure', ['busy', 'example_owner', 'plan_owner', 'undervoltage', 'bad_calibration'])
def test_arm_and_play_preserves_motion_guards(client, monkeypatch, kind, failure):
    tool, arguments = request(client, kind)
    robot = client.app.state.controller
    spoken = []
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    before = permission(client)
    if failure == 'busy':
        monkeypatch.setattr(robot, '_busy', True)
    elif failure == 'example_owner':
        monkeypatch.setattr('pibotpal.app.ExampleRunner.status', lambda self: {'running': True, 'permits_manual_motion': False})
    elif failure == 'plan_owner':
        monkeypatch.setattr(client.app.state.plans, 'status', lambda: {'running': True})
    elif failure == 'undervoltage':
        power = client.app.state.power_monitor
        original = power.status
        monkeypatch.setattr(power, 'status', lambda: {**original(), 'under_voltage': True})
    else:
        robot._errors.append('Calibration read failed: simulated invalid file')
    assert call(client, tool, **arguments, arm_motion=True)['isError']
    assert permission(client) == before and not spoken


@pytest.mark.parametrize('kind', ['move', 'plan_run'])
def test_playback_checks_action_frame_calibration_before_arming(client, monkeypatch, kind):
    robot = client.app.state.controller
    # A valid saved logical angle can become invalid under a later calibration.
    frames = [{'angles': [90, 30, 0] + [0, 30, 0] * 3, 'speed_deg_s': 30}]
    assert not call(client, 'action_save', name='edge', frames=frames).get('isError')
    if kind == 'move':
        arguments = {'name': 'edge'}
    else:
        plan = call(client, 'plan_save', name='Late invalid action', steps=[
            {'type': 'wait', 'seconds': 1}, {'type': 'action', 'name': 'edge'}])['value']
        arguments = {'plan_id': plan['id']}
    robot._offsets[0] = 2
    spoken = []
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    before = permission(client)
    result = call(client, kind, **arguments, arm_motion=True)
    assert result['isError'] and 'calibrated' in str(result)
    assert permission(client) == before and not spoken
    assert not client.app.state.plans.status()['running']


@pytest.mark.parametrize('failure', ['missing', 'late_pose', 'missing_action', 'invalid_step', 'closed'])
def test_invalid_saved_routine_does_not_arm(client, monkeypatch, failure):
    robot, plans = client.app.state.controller, client.app.state.plans
    pose = call(client, 'pose_save', name='Edge', angles=[90, 0, 0] + [0] * 9)['value']
    plan = call(client, 'plan_save', name='Check every step', steps=[
        {'type': 'wait', 'seconds': 1}, {'type': 'pose', 'pose_id': pose['id']}])['value']
    plan_id = plan['id']
    if failure == 'missing':
        plan_id = '0' * 32
    elif failure == 'late_pose':
        robot._offsets[0] = 2
    elif failure == 'missing_action':
        plans._data['plans'][0]['steps'][1] = {'type': 'action', 'name': 'missing'}
    elif failure == 'invalid_step':
        plans._data['plans'][0]['steps'][1] = {'type': 'wait', 'seconds': 900}
    else:
        monkeypatch.setattr(plans, '_closed', True)
    spoken = []
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    before = permission(client)
    assert call(client, 'plan_run', plan_id=plan_id, arm_motion=True)['isError']
    assert permission(client) == before and not spoken
    assert not plans.status()['running']


@pytest.mark.parametrize('kind', ['move', 'plan_run'])
def test_dispatch_failure_revokes_only_permission_granted_by_this_play(client, monkeypatch, kind):
    tool, arguments = request(client, kind)
    target, name = ((client.app.state.controller, 'action') if kind == 'move'
                    else (client.app.state.plans, 'run'))
    def reject(*args, **kwargs):
        raise RuntimeError('Simulated dispatch failure')
    monkeypatch.setattr(target, name, reject)
    assert call(client, tool, **arguments, arm_motion=True)['isError']
    assert not client.app.state.controller.status()['armed']
    call(client, 'arm', armed=True)
    assert call(client, tool, **arguments, arm_motion=True)['isError']
    assert client.app.state.controller.status()['armed']
    assert client.app.state.controller.status()['motion_id'] == 0


@pytest.mark.parametrize('kind', ['move', 'plan_run'])
def test_stop_cannot_interleave_between_arm_and_play_dispatch(client, monkeypatch, kind):
    tool, arguments = request(client, kind)
    robot = client.app.state.controller
    entered, release, stop_entered = threading.Event(), threading.Event(), threading.Event()
    events = []
    def announce(text):
        if text == 'system armed':
            events.append('armed')
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(client.app.state.media, 'announce', announce)
    # Keep action execution cancellable and long enough to observe STOP.
    monkeypatch.setattr(robot, '_move_path', lambda path, speed: robot._cancel.wait(2))
    target, name = ((robot, 'action') if kind == 'move' else (client.app.state.plans, 'run'))
    dispatch, stop = getattr(target, name), robot.stop
    def observed_dispatch(*args, **kwargs):
        events.append('dispatch')
        return dispatch(*args, **kwargs)
    def observed_stop():
        events.append('stop')
        stop_entered.set()
        return stop()
    monkeypatch.setattr(target, name, observed_dispatch)
    monkeypatch.setattr(robot, 'stop', observed_stop)
    with ThreadPoolExecutor(max_workers=2) as pool:
        playing = pool.submit(call, client, tool, **arguments, arm_motion=True)
        try:
            assert entered.wait(5)
            stopping = pool.submit(call, client, 'stop')
            assert not stop_entered.wait(.2), 'STOP bypassed the arm-and-dispatch gate'
        finally:
            release.set()
        assert not playing.result(timeout=5).get('isError')
        assert not stopping.result(timeout=5).get('isError')
    wait_for(lambda: not robot.status()['busy'] and not client.app.state.plans.status()['running'])
    assert events.index('armed') < events.index('dispatch') < events.index('stop')
    assert events.count('dispatch') == 1
    assert not robot.status()['armed']


def test_routine_delete_waits_for_arm_and_dispatch(client, monkeypatch):
    tool, arguments = request(client, 'plan_run')
    entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
    def announce(text):
        if text == 'system armed':
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(client.app.state.media, 'announce', announce)
    monkeypatch.setattr(client.app.state.controller, '_move_path', lambda path, speed: None)
    plans = client.app.state.plans
    original = plans.delete_plan
    def observed_delete(plan_id):
        deleted.set()
        return original(plan_id)
    monkeypatch.setattr(plans, 'delete_plan', observed_delete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        playing = pool.submit(call, client, tool, **arguments, arm_motion=True)
        try:
            assert entered.wait(5)
            deletion = pool.submit(call, client, 'plan_delete', **arguments)
            assert not deleted.wait(.2)
        finally:
            release.set()
        assert not playing.result(timeout=5).get('isError')
        assert not deletion.result(timeout=5).get('isError')
    wait_for(lambda: not plans.status()['running'])
    assert plans.status()['phase'] == 'complete'
