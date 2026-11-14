"""Controller-to-MCP application integration, entirely on simulated hardware."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pibotpal.app import create_app
from pibotpal.gamepad import EVENT
from pibotpal.gamepad_actions import MappingStore
from pibotpal.hardware import Controller
from pibotpal.media import Media


HEADERS = {'Accept': 'application/json, text/event-stream',
           'MCP-Protocol-Version': '2025-03-26'}
BUTTONS = ['South', 'East', 'West', 'North', 'Select', 'Start']
AXES = ['X', 'Y', 'Hat X', 'Hat Y']


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    monkeypatch.setenv('PIBOTPAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PIBOTPAL_SIMULATE', '1')
    robot = Controller(simulate=True, config_path=tmp_path/'calibration')
    media = Media(simulate=True)
    with TestClient(create_app(robot, media), base_url='http://127.0.0.1:8765') as result:
        yield result


def call(client, tool_name, **arguments):
    response = client.post('/mcp', headers=HEADERS, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': tool_name, 'arguments': arguments}})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    if not result.get('isError'):
        result['value'] = result.get('structuredContent', json.loads(result['content'][0]['text']))
    return result


def wait_for(predicate, timeout=2, details=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.005)
    raise AssertionError(f'Controller integration condition timed out: {details() if details else "no additional state"}')


def connect(client):
    pad = client.app.state.gamepad
    with pad._lock:
        pad._invalidate_input()
        pad._state.update(connected=True, attached=True, active_device_id='simulated-pad',
                          buttons=[False]*len(BUTTONS), raw_buttons=[False]*len(BUTTONS),
                          button_names=BUTTONS.copy(), axes=[0.0]*len(AXES),
                          raw_axes=[0.0]*len(AXES), axis_names=AXES.copy())
    pad._events(b''.join(EVENT.pack(0, 0, 0x81, i) for i in range(len(BUTTONS))))
    return pad


def button(pad, name, pressed):
    pad._events(EVENT.pack(1, int(pressed), 1, BUTTONS.index(name)))


def tap(pad, name):
    index = BUTTONS.index(name)
    # Both edges arrive in one read, before the 20 Hz action loop can poll.
    pad._events(EVENT.pack(1, 1, 1, index) + EVENT.pack(2, 0, 1, index))


def axis(pad, name, value):
    pad._events(EVENT.pack(1, value, 2, AXES.index(name)))


def ready(client):
    wait_for(lambda: client.app.state.gamepad_driver.state['state'] == 'ready')


def arm(client):
    assert call(client, 'arm', armed=True)['value']['armed']
    ready(client)


def short_actions(client, monkeypatch, seconds=.1):
    """Keep real submission IDs, busy state, worker cleanup and cancellation."""
    robot = client.app.state.controller
    actions = []

    def action(name, speed=40, steps=1):
        def perform():
            robot._cancel.wait(seconds)
            robot._check_cancel()
        result = robot._submit(name, perform)
        actions.append({'name': name, 'speed': speed, 'steps': steps,
                        'motion_id': result['motion_id']})
        return result

    monkeypatch.setattr(robot, 'action', action)
    return actions


def test_quick_press_runs_real_simulated_pose_and_release_halts_gait(client):
    pad = connect(client)
    robot = client.app.state.controller
    arm(client)
    tap(pad, 'South')
    wait_for(lambda: robot.status()['motion_id'] == 1)
    # Three joint phases split 3+1 at the default cap: six initial settles.
    wait_for(lambda: not robot.status()['busy'], timeout=8)
    assert all(angle is not None for angle in robot.status()['servo_angles'])
    assert robot.status()['action_error'] is None
    assert robot._hat is None and robot._servos == []
    # Let the controller observe the completed action and neutral inputs before
    # issuing the next direction; inputs during an action are not queued.
    ready(client)
    axis(pad, 'Hat Y', -32767)
    wait_for(lambda: robot.status()['motion_id'] == 2 and robot.status()['busy'],
             details=lambda: {'robot': robot.status(), 'input': pad.status(),
                              'control': client.app.state.gamepad_driver.state})
    axis(pad, 'Hat Y', 0)
    wait_for(lambda: not robot.status()['busy'])
    state = robot.status()
    assert state['armed'] and not state['estop']
    time.sleep(.08)
    assert robot.status()['servo_angles'] == state['servo_angles']
    assert robot.status()['motion_id'] == 2
    assert client.app.state.media.status()['microphone']['muted']


def test_held_direction_repeats_bounded_actions_until_release(client, monkeypatch):
    pad = connect(client)
    actions = short_actions(client, monkeypatch)
    robot = client.app.state.controller
    arm(client)
    axis(pad, 'Y', -32767)
    wait_for(lambda: len(actions) >= 3)
    axis(pad, 'Y', 0)
    wait_for(lambda: not robot.status()['busy'])
    count = len(actions)
    time.sleep(.15)
    assert len(actions) == count
    assert all(a['name'] == 'forward' and a['steps'] == 1 and a['speed'] == 40 for a in actions)
    assert [a['motion_id'] for a in actions] == list(range(1, count+1))
    assert robot.status()['armed'] and not robot.status()['estop']


def test_controller_arm_enables_motion_and_held_input_needs_neutral(client, monkeypatch):
    pad = connect(client)
    actions = short_actions(client, monkeypatch)
    wait_for(lambda: client.app.state.gamepad_driver.state['state'] == 'system_disarmed')
    tap(pad, 'South')
    time.sleep(.1)
    assert actions == []
    call(client, 'controller_set_enabled', enabled=False)
    wait_for(lambda: client.app.state.gamepad_driver.state['state'] == 'controller_disarmed')
    axis(pad, 'Y', -32767)
    time.sleep(.1)
    assert actions == []
    call(client, 'controller_set_enabled', enabled=True)
    assert client.app.state.controller.status()['armed']
    wait_for(lambda: client.app.state.gamepad_driver.state['state'] == 'waiting_neutral')
    assert actions == []
    axis(pad, 'Y', 0)
    ready(client)
    tap(pad, 'South')
    wait_for(lambda: len(actions) == 1)
    assert actions[0]['name'] == 'stand'


@pytest.mark.parametrize('disconnect', ['mcp_disarm', 'start_disarm', 'unplug'])
def test_disarm_or_unplug_halts_owned_motion_without_system_disarm(client, monkeypatch, disconnect):
    pad = connect(client)
    actions = short_actions(client, monkeypatch, seconds=3)
    robot = client.app.state.controller
    arm(client)
    axis(pad, 'Hat Y', -32767)
    wait_for(lambda: actions and client.app.state.gamepad_driver.owns(robot.status()))
    if disconnect == 'mcp_disarm':
        call(client, 'controller_set_enabled', enabled=False)
    elif disconnect == 'start_disarm':
        tap(pad, 'Start')
    else:
        pad._disconnect('Simulated unplug')
    wait_for(lambda: not robot.status()['busy'])
    assert robot.status()['armed'] and not robot.status()['estop']
    assert not pad.status()['armed']
    assert robot.status()['motion_id'] == 1
    time.sleep(.1)
    assert len(actions) == 1


@pytest.mark.parametrize('change', ['start_disarm', 'unplug', 'rearm_new_epoch', 'release_held_direction'])
def test_delayed_dispatch_rechecks_controller_state_and_held_input(client, monkeypatch, change):
    pad = connect(client)
    actions = short_actions(client, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    dispatch = pad.dispatch

    def delayed_dispatch(*args, **kwargs):
        # Hold execution before dispatch acquires the monitor's I/O guard,
        # reproducing an action queued to a worker just before Start/unplug.
        entered.set()
        assert release.wait(2)
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(pad, 'dispatch', delayed_dispatch)
    arm(client)
    axis(pad, 'Y', -32767)
    try:
        assert entered.wait(1)
        if change == 'release_held_direction':
            axis(pad, 'Y', 0)
            expected = 'ready'
        elif change == 'unplug':
            pad._disconnect('Simulated unplug before worker dispatch')
            expected = 'offline'
        else:
            tap(pad, 'Start')
            expected = 'controller_disarmed'
            if change == 'rearm_new_epoch':
                pad.set_enabled(True)
                expected = 'waiting_neutral'
    finally:
        release.set()
    wait_for(lambda: client.app.state.gamepad_driver.state['state'] == expected)
    assert actions == []
    assert client.app.state.controller.status()['motion_id'] == 0
    assert client.app.state.controller.status()['armed']
    assert client.app.state.controller.status()['servo_angles'] == [None]*12


def test_mapping_mcp_persists_validates_start_and_does_not_move(client, tmp_path):
    connect(client)
    bindings = [{'input': 'button:South', 'action': 'wave', 'mode': 'press'}]
    saved = call(client, 'controller_save_mapping', speed=27, deadzone=.5, bindings=bindings)
    assert not saved.get('isError')
    mapping = saved['value']['mapping']
    assert (mapping['speed'], mapping['deadzone'], mapping['bindings']) == (27, .5, bindings)
    path = tmp_path/'controller-mapping.json'
    stored = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert MappingStore(path).value == {'version': 1, 'speed': 27, 'deadzone': .5, 'bindings': bindings}
    rejected = call(client, 'controller_save_mapping', speed=27, deadzone=.5,
                    bindings=[{'input': 'button:Start', 'action': 'stand', 'mode': 'press'}])
    assert rejected['isError'] and 'reserved' in str(rejected)
    assert path.read_bytes() == stored
    state = client.app.state.controller.status()
    assert state['motion_id'] == 0 and state['servo_angles'] == [None]*12
    assert not state['armed']


def test_mapping_save_halts_owned_motion_then_requires_neutral(client, monkeypatch):
    pad = connect(client)
    actions = short_actions(client, monkeypatch, seconds=3)
    robot = client.app.state.controller
    arm(client)
    axis(pad, 'Y', -32767)
    wait_for(lambda: actions and client.app.state.gamepad_driver.owns(robot.status()))
    bindings = [{'input': 'axis:Y:-', 'action': 'backward', 'mode': 'hold'}]
    saved = call(client, 'controller_save_mapping', speed=23, deadzone=.5, bindings=bindings)
    assert not saved.get('isError')
    wait_for(lambda: not robot.status()['busy'])
    wait_for(lambda: client.app.state.gamepad_driver.state['state'] == 'waiting_neutral')
    assert len(actions) == 1 and robot.status()['armed']
    axis(pad, 'Y', 0)
    ready(client)
    axis(pad, 'Y', -32767)
    wait_for(lambda: len(actions) == 2)
    assert actions[-1]['name'] == 'backward' and actions[-1]['speed'] == 23


@pytest.mark.parametrize('owner', ['manual', 'plan'])
def test_controller_release_and_disarm_do_not_cancel_another_motion_owner(client, monkeypatch, owner):
    pad = connect(client)
    actions = short_actions(client, monkeypatch, seconds=.1)
    robot = client.app.state.controller
    arm(client)
    button(pad, 'South', True)
    wait_for(lambda: actions)
    wait_for(lambda: not robot.status()['busy'])
    short_actions(client, monkeypatch, seconds=3)
    if owner == 'manual':
        result = call(client, 'move', name='wave')
    else:
        saved = call(client, 'plan_save', name='External plan',
                     steps=[{'type': 'action', 'name': 'wave', 'speed': 40, 'steps': 1}])['value']
        result = call(client, 'plan_run', plan_id=saved['id'])
    assert not result.get('isError')
    wait_for(lambda: robot.status()['motion_id'] == 2 and robot.status()['busy'])
    button(pad, 'South', False)
    call(client, 'controller_set_enabled', enabled=False)
    time.sleep(.15)
    assert robot.status()['busy'] and robot.status()['motion_id'] == 2
    assert robot.status()['armed'] and not robot.status()['estop']
    if owner == 'plan':
        assert client.app.state.plans.status()['running']


def test_select_stop_disarms_and_cancels_an_external_busy_plan(client, monkeypatch):
    pad = connect(client)
    short_actions(client, monkeypatch, seconds=3)
    robot = client.app.state.controller
    arm(client)
    saved = call(client, 'plan_save', name='Stop target',
                 steps=[{'type': 'action', 'name': 'forward', 'speed': 40, 'steps': 1}])['value']
    assert not call(client, 'plan_run', plan_id=saved['id']).get('isError')
    wait_for(lambda: robot.status()['busy'])
    tap(pad, 'Select')
    wait_for(lambda: robot.status()['estop'] and not robot.status()['busy'])
    wait_for(lambda: not client.app.state.plans.status()['running'])
    assert not robot.status()['armed']
    assert client.app.state.plans.status()['phase'] == 'cancelled'
    assert client.app.state.media.status()['microphone']['muted']


def test_controller_event_stream_keeps_quick_press_history_while_disarmed_and_is_read_only(client):
    pad = connect(client)
    call(client, 'controller_set_enabled', enabled=False)
    endpoint = next(route.endpoint for route in client.app.routes
                    if getattr(route, 'path', None) == '/controllers/events')

    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    async def check():
        request = Request()
        response = await endpoint(request)
        assert response.media_type == 'text/event-stream'
        assert response.headers['X-Accel-Buffering'] == 'no'
        assert response.headers['Cache-Control'] == 'no-store'
        initial = await anext(response.body_iterator)
        assert not json.loads(initial.removeprefix('data: '))['armed']
        tap(pad, 'South')
        axis(pad, 'Y', -32767)
        message = await anext(response.body_iterator)
        state = json.loads(message.removeprefix('data: '))
        assert state['raw_axes'][1] == -1 and state['axes'][1] == 0
        assert not any(state['raw_buttons'])
        assert [e['value'] for e in state['recent_events'] if e['name'] == 'South'] == [True, False]
        request.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)

    asyncio.run(check())
    assert client.app.state.controller.status()['motion_id'] == 0
    assert client.app.state.controller.status()['servo_angles'] == [None]*12
    assert client.app.state.media.status()['microphone']['muted']
    assert client.get('/controllers/events', headers={'Origin': 'https://attacker.example'}).status_code == 403


def test_controller_event_stream_does_not_renew_system_motion_lease(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('pibotpal.app.time', SimpleNamespace(monotonic=lambda: now[0]))
    connect(client)
    arm(client)
    endpoint = next(route.endpoint for route in client.app.routes
                    if getattr(route, 'path', None) == '/controllers/events')

    class Request:
        async def is_disconnected(self):
            return False

    async def read_once():
        response = await endpoint(Request())
        await anext(response.body_iterator)
        await response.body_iterator.aclose()

    now[0] += 14
    asyncio.run(read_once())
    now[0] += 2
    wait_for(lambda: not client.app.state.controller.status()['armed'], timeout=2)
    assert client.app.state.controller.status()['estop']
    assert client.app.state.controller.status()['motion_id'] == 0


@pytest.mark.parametrize('source', ['start', 'mcp'])
@pytest.mark.parametrize('saved_enabled', [True, False])
def test_one_controller_arm_allows_mapped_action_without_system_arm(client, monkeypatch, source, saved_enabled):
    pad = connect(client)
    pad.set_enabled(saved_enabled, notify=False)
    robot = client.app.state.controller
    actions = short_actions(client, monkeypatch)
    # Merely attaching an enabled controller does not arm motion.
    time.sleep(.12)
    snapshot = call(client, 'controller_get')['value']
    assert not snapshot['armed'] and not snapshot['system_armed']
    if source == 'start':
        tap(pad, 'Start')
    else:
        result = call(client, 'controller_set_enabled', enabled=True)['value']
        assert result['armed'] and result['system_armed']
    ready(client)
    assert robot.status()['armed'] and robot.status()['servo_angles'] == [None]*12
    assert actions == []  # Arming itself must not issue a movement.
    tap(pad, 'South')
    wait_for(lambda: len(actions) == 1)
    assert actions[0]['name'] == 'stand'
    assert client.app.state.media.status()['microphone']['muted']


@pytest.mark.parametrize('source', ['start', 'mcp'])
@pytest.mark.parametrize('failure', ['undervoltage', 'calibration', 'active_plan', 'persistence'])
def test_controller_arm_preserves_system_guards(client, monkeypatch, source, failure):
    pad = connect(client)
    pad.set_enabled(False, notify=False)
    robot = client.app.state.controller
    spoken = []
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    if failure == 'undervoltage':
        monitor = client.app.state.power_monitor
        previous = monitor.status
        monkeypatch.setattr(monitor, 'status', lambda: {**previous(), 'under_voltage': True})
    elif failure == 'calibration':
        robot._errors.append('Calibration read failed: simulated invalid file')
    elif failure == 'active_plan':
        monkeypatch.setattr(client.app.state.plans, 'status', lambda: {'running': True})
    else:
        def fail_save(enabled):
            raise OSError('Simulated disk failure')
        monkeypatch.setattr(pad, '_save_enabled', fail_save)
    if source == 'start':
        tap(pad, 'Start')
        wait_for(lambda: pad.status()['error'])
    else:
        assert call(client, 'controller_set_enabled', enabled=True)['isError']
    assert not robot.status()['armed'] and robot.status()['motion_id'] == 0
    assert robot.status()['servo_angles'] == [None]*12
    assert not pad.status()['enabled']
    assert not spoken


@pytest.mark.parametrize('disarm', ['arm', 'stop'])
def test_system_disarm_discards_start_queued_before_it_finishes(client, monkeypatch, disarm):
    pad = connect(client)
    robot = client.app.state.controller
    # Inject a Start press during a disarm operation while the motion gate is
    # held. Its delayed request must not undo the operation on the next poll.
    if disarm == 'stop':
        original = client.app.state.media.stop
        def stop_with_queued_start():
            tap(pad, 'Start')
            return original()
        monkeypatch.setattr(client.app.state.media, 'stop', stop_with_queued_start)
        call(client, 'stop')
    else:
        original = robot.arm
        def disarm_with_queued_start(armed):
            result = original(armed)
            if not armed:
                tap(pad, 'Start')
            return result
        monkeypatch.setattr(robot, 'arm', disarm_with_queued_start)
        call(client, 'arm', armed=False)
    time.sleep(.15)
    assert not robot.status()['armed'] and robot.status()['motion_id'] == 0
    assert not pad.drain_notifications()
