"""Shared action integration uses simulated controllers; never physical servos."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from tests.test_controller_app import client, call, connect, tap, ready, wait_for


def frame(angles=None, speed=24, hold=0):
    return {'angles': angles or [0, 30, 0]*4, 'speed_deg_s': speed, 'hold_seconds': hold}


def test_action_catalog_and_new_action_are_available_in_planner_without_moving(client, tmp_path):
    catalog = call(client, 'action_list')['value']
    assert {'stand', 'sit', 'wave', 'forward'} <= {a['name'] for a in catalog['actions']}
    assert all(a['frames'] and a['builtin'] for a in catalog['actions'])
    saved = call(client, 'action_save', name='greeting', frames=[frame()])['value']
    assert saved['name'] == 'greeting' and not saved['builtin']
    assert saved['frames'][0]['speed_deg_s'] == 24
    assert 'greeting' in {a['name'] for a in call(client, 'plan_list')['value']['actions']}
    assert 'greeting' in {a['id'] for a in call(client, 'controller_get')['value']['mapping']['actions']}
    path = tmp_path / 'actions.json'
    assert path.stat().st_mode & 0o777 == 0o600
    assert any(a['name'] == 'greeting' for a in json.loads(path.read_text())['actions'])
    state = client.app.state.controller.status()
    assert not state['armed'] and state['servo_angles'] == [None]*12 and state['motion_id'] == 0


def test_edited_builtin_is_used_by_move_controller_routine_and_testing(client, monkeypatch):
    robot = client.app.state.controller
    target = [5, 35, 2]*4
    call(client, 'action_save', name='stand', frames=[frame(target, speed=12)])
    received = []
    def capture(path, speed):
        for angles in path:
            received.append((list(angles), speed))
            robot._angles = list(angles)
    monkeypatch.setattr(robot, '_move_path', capture)
    call(client, 'arm', armed=True)
    assert not call(client, 'move', name='stand').get('isError')
    wait_for(lambda: not robot.status()['busy'])
    assert received[-1][0] == target
    assert received[-1][1] * 1.8 == 12
    pad = connect(client)
    ready(client)
    tap(pad, 'South')
    wait_for(lambda: len(received) == 2)
    wait_for(lambda: not robot.status()['busy'])
    assert received[-1][0] == target
    plan = call(client, 'plan_save', name='Shared stand', steps=[{'type':'action', 'name':'stand'}])['value']
    assert not call(client, 'plan_run', plan_id=plan['id']).get('isError')
    wait_for(lambda: not client.app.state.plans.status()['running'])
    assert received[-1][0] == target and len(received) == 3
    call(client, 'arm', armed=True)
    assert not call(client, 'run_example', example_id='14_preset_actions',
                    options={'action':'stand', 'speed':40}).get('isError')
    wait_for(lambda: not call(client, 'get_status')['value']['examples']['running'])
    assert received[-1][0] == target and len(received) == 4


def test_custom_action_runs_in_new_routine_and_can_be_controller_mapped(client, monkeypatch):
    call(client, 'action_save', name='greeting', frames=[frame()])
    draft = call(client, 'plan_draft', text='greeting. stand')['value']
    assert [s['name'] for s in draft['steps']] == ['greeting', 'stand']
    saved = call(client, 'plan_save', name='New custom routine', steps=draft['steps'])['value']
    robot = client.app.state.controller
    received = []
    monkeypatch.setattr(robot, '_move_path', lambda path, speed: received.extend(list(angles) for angles in path))
    pad = connect(client)
    assert not call(client, 'controller_save_mapping', speed=40, deadzone=.35,
                    bindings=[{'input':'button:South', 'action':'greeting', 'mode':'press'}]).get('isError')
    call(client, 'arm', armed=True)
    ready(client)
    tap(pad, 'South')
    wait_for(lambda: received)
    assert received[0] == frame()['angles']
    wait_for(lambda: not robot.status()['busy'])
    assert not call(client, 'plan_run', plan_id=saved['id']).get('isError')
    wait_for(lambda: not client.app.state.plans.status()['running'])
    assert len(received) == 3 and received[1] == frame()['angles']


def test_shared_edits_require_disarm_and_invalid_edits_do_not_change_permissions(client):
    call(client, 'arm', armed=True)
    assert call(client, 'action_save', name='stand', frames=[frame()])['isError']
    assert call(client, 'action_save', name='stand', frames=[frame(speed=999)], disarm=True)['isError']
    assert client.app.state.controller.status()['armed']
    assert not call(client, 'action_save', name='stand', frames=[frame()], disarm=True).get('isError')
    assert not client.app.state.controller.status()['armed']
    restored = call(client, 'action_reset', name='stand')['value']
    assert restored['builtin'] and not restored['modified']
    assert call(client, 'action_delete', name='stand')['isError']
    assert client.app.state.controller.status()['servo_angles'] == [None]*12


def test_custom_delete_preserves_referenced_routines_and_controller_bindings(client):
    call(client, 'action_save', name='greeting', frames=[frame()])
    routine = call(client, 'plan_save', name='Reference', steps=[{'type':'action', 'name':'greeting'}])['value']
    assert call(client, 'action_delete', name='greeting')['isError']
    call(client, 'plan_delete', plan_id=routine['id'])
    call(client, 'controller_save_mapping', speed=40, deadzone=.35,
         bindings=[{'input':'button:South', 'action':'greeting', 'mode':'press'}])
    assert call(client, 'action_delete', name='greeting')['isError']
    call(client, 'controller_reset_mapping')
    assert call(client, 'action_delete', name='greeting')['value']['deleted'] == 'greeting'
    assert 'greeting' not in {a['name'] for a in call(client, 'action_list')['value']['actions']}


def test_delete_serializes_with_new_routine_references(client, monkeypatch):
    call(client, 'action_save', name='greeting', frames=[frame()])
    actions = client.app.state.controller.actions
    original = actions.delete
    entered, release = threading.Event(), threading.Event()
    save_entered = threading.Event()
    original_save = client.app.state.plans.save_plan
    def delayed_delete(name):
        entered.set()
        assert release.wait(5)
        return original(name)
    monkeypatch.setattr(actions, 'delete', delayed_delete)
    def observed_save(*args, **kwargs):
        save_entered.set()
        return original_save(*args, **kwargs)
    monkeypatch.setattr(client.app.state.plans, 'save_plan', observed_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(call, client, 'action_delete', name='greeting')
        try:
            assert entered.wait(5)
            save = pool.submit(call, client, 'plan_save', name='Concurrent reference',
                               steps=[{'type': 'action', 'name': 'greeting'}])
            assert not save_entered.wait(.2), 'Routine save bypassed the action deletion gate'
        finally:
            release.set()
        assert not deletion.result(timeout=5).get('isError')
        assert save.result(timeout=5)['isError']
    assert not client.app.state.plans.list_plans()


def test_edit_during_active_routine_does_not_disarm_before_rejection(client, monkeypatch):
    call(client, 'arm', armed=True)
    monkeypatch.setattr(client.app.state.plans, 'status', lambda: {'running': True})
    assert call(client, 'action_save', name='stand', frames=[frame()], disarm=True)['isError']
    assert client.app.state.controller.status()['armed']
