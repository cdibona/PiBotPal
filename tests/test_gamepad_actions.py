"""Controller mapping and motion decisions, without accessing robot hardware."""
import json

import pytest

from pibotpal.gamepad_actions import GamepadDriver, MappingStore, defaults, validate_mapping


def binding(key, action, mode='press'):
    return {'input':key, 'action':action, 'mode':mode}


class Rig:
    def __init__(self, tmp_path, monkeypatch, bindings=None):
        monkeypatch.setattr('pibotpal.gamepad_actions.time.time', lambda:1000.)
        self.store = MappingStore(tmp_path/'controller-mapping.json')
        if bindings is not None:
            self.store.save(40, .35, bindings)
        self.driver = GamepadDriver(self.store)
        self.pad = {'connected':True, 'armed':True, 'input_epoch':1,
                    'button_names':['South', 'East', 'West', 'North', 'Select', 'Start'],
                    'raw_buttons':[False]*6, 'axis_names':['X', 'Y', 'Hat X', 'Hat Y'],
                    'raw_axes':[0.]*4}
        self.robot = {'armed':True, 'estop':False, 'busy':False, 'motion_id':0}
        self.seq = 0
        self.now = 0.

    def event(self, key, value, *, age=0., epoch=None):
        kind, name = key.split(':')
        names, values = ('button_names', 'raw_buttons') if kind == 'button' else ('axis_names', 'raw_axes')
        index = self.pad[names].index(name)
        self.pad[values][index] = value
        self.seq += 1
        return {'seq':self.seq, 'time':1000.-age, 'epoch':self.pad['input_epoch'] if epoch is None else epoch,
                'kind':kind, 'name':name, 'index':index, 'value':value}

    def step(self, *events, blocked=False, now=None):
        if now is not None:
            self.now = now
        return self.driver.update(self.pad, self.robot, list(events), blocked=blocked, now=self.now)

    def ready(self):
        assert self.step() is None
        assert self.driver.state['state'] == 'ready'
        return self

    def started(self, decision):
        assert decision['kind'] == 'move'
        self.robot.update(busy=True, motion_id=self.robot['motion_id']+1)
        self.driver.started(decision, self.robot)

    def finished(self):
        self.robot['busy'] = False


def test_default_mapping_is_bounded_and_covers_dpad_stick_face_and_stop(tmp_path):
    store = MappingStore(tmp_path/'controller-mapping.json')
    value = validate_mapping(store.value)
    assert value['speed'] == 40 and value['deadzone'] == .35
    by_input = {b['input']:b for b in value['bindings']}
    assert by_input['axis:Hat Y:-'] == binding('axis:Hat Y:-', 'forward', 'hold')
    assert by_input['axis:X:+']['action'] == 'turn right'
    assert by_input['button:South']['action'] == 'stand'
    assert by_input['button:Select']['action'] == 'stop'
    assert 'button:Start' not in by_input
    assert not store.path.exists()


def test_mapping_roundtrip_is_private_and_independent_of_caller_mutation(tmp_path):
    path = tmp_path/'settings'/'controller-mapping.json'
    store = MappingStore(path)
    bindings = [binding('button:L1', 'wave'), binding('axis:RX:-', 'turn left', 'hold')]
    store.save(63, .4, bindings)
    bindings[0]['action'] = 'sit'
    assert path.stat().st_mode & 0o777 == 0o600
    restored = MappingStore(path)
    assert restored.value == store.value and restored.value['bindings'][0]['action'] == 'wave'
    public = restored.public({'button_names':['South', 'Start'], 'axis_names':['X']})
    assert public['reserved'] == ['button:Start']
    assert any(item['id'] == 'button:L1' and 'not attached' in item['label'] for item in public['inputs'])
    assert not any(item['id'].endswith(' angle') for item in public['actions'])
    public['bindings'].clear()
    assert len(restored.value['bindings']) == 2


@pytest.mark.parametrize('profile, z_label, rz_label', [
    ('shanwan-2563:0575', 'Right stick left', 'Right stick down'),
    ('linux', 'Z -', 'RZ +'),
])
def test_right_stick_labels_respect_shanwan_axis_layout(tmp_path, profile, z_label, rz_label):
    store = MappingStore(tmp_path/'controller-mapping.json')
    public = store.public({'axis_names':['RX', 'RY', 'Z', 'RZ'], 'button_mapping':profile})
    labels = {item['id']:item['label'] for item in public['inputs']}
    assert labels['axis:RX:-'] == 'Right stick left' and labels['axis:RY:+'] == 'Right stick down'
    assert labels['axis:Z:-'] == z_label and labels['axis:RZ:+'] == rz_label


@pytest.mark.parametrize('change', [
    {'version':True}, {'version':2}, {'unexpected':1}, {'speed':True}, {'speed':0}, {'speed':81},
    {'deadzone':True}, {'deadzone':float('nan')}, {'deadzone':float('inf')}, {'deadzone':.1}, {'deadzone':.9},
    {'deadzone':10**400},
    {'bindings':'bad'}, {'bindings':[binding('button:South', 'stand')]*65},
    {'bindings':[binding('button:Start', 'sit')]},
    {'bindings':[binding('button:South', 'sit'), binding('button:South', 'stand')]},
    {'bindings':[binding('axis:X', 'stand')]}, {'bindings':[binding('button:bad\nname', 'sit')]},
    {'bindings':[binding('button:South', 'stand', 'hold')]},
    {'bindings':[binding('button:South', 'turn left angle')]},
    {'bindings':[binding('button:South', [])]}, {'bindings':[binding('button:South', 'sit', [])]},
])
def test_invalid_mapping_fields_are_rejected(change):
    with pytest.raises(ValueError):
        validate_mapping(dict(defaults(), **change))


def test_failed_mapping_write_preserves_loaded_settings_and_removes_tempfile(tmp_path, monkeypatch):
    store = MappingStore(tmp_path/'controller-mapping.json')
    store.save(40, .35, defaults()['bindings'])
    old = store.path.read_bytes()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr('pibotpal.gamepad_actions.os.replace', fail)
    with pytest.raises(OSError, match='disk full'):
        store.save(50, .4, [])
    assert store.path.read_bytes() == old and store.value == defaults()
    assert not list(tmp_path.glob('.controller-mapping-*'))


def test_invalid_persisted_mapping_fails_closed(tmp_path):
    path = tmp_path/'controller-mapping.json'
    path.write_text(json.dumps(dict(defaults(), bindings=[binding('button:Start', 'stand')])))
    with pytest.raises(RuntimeError, match='repair controller-mapping.json'):
        MappingStore(path)


def test_quick_tap_between_snapshots_fires_once(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    press = rig.event('button:South', True)
    release = rig.event('button:South', False)
    decision = rig.step(press, release)
    assert decision == {'kind':'move', **binding('button:South', 'stand'), 'speed':40, 'steps':1}
    rig.started(decision)
    assert rig.step() is None
    rig.finished()
    assert rig.step() is None
    assert rig.step(press, release) is None  # sequence replay cannot queue a second action


def test_held_button_and_duplicate_press_reports_do_not_repeat_one_shot(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.started(rig.step(rig.event('button:South', True)))
    rig.finished()
    assert rig.step(rig.event('button:South', True)) is None
    assert rig.step() is None
    assert rig.step(rig.event('button:South', False)) is None
    assert rig.step(rig.event('button:South', True))['action'] == 'stand'


def test_hold_repeats_one_step_only_after_prior_step_finishes_and_release_halts(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    first = rig.step(rig.event('axis:Y', -1.))
    assert first['action'] == 'forward' and first['steps'] == 1
    rig.started(first)
    assert rig.step(now=.2) is None
    rig.finished()
    second = rig.step(now=.4)
    assert second['action'] == 'forward' and second['steps'] == 1
    rig.started(second)
    assert rig.step(rig.event('axis:Y', 0.), now=.5) == {'kind':'halt'}
    # Halt is a separate decision from stop/disarm; execution belongs to app.
    assert rig.robot['armed']
    rig.driver.halted(); rig.finished()
    assert rig.step() is None


@pytest.mark.parametrize('state_change, reason', [
    ({'pad':{'armed':False}}, 'controller_disarmed'),
    ({'pad':{'connected':False}}, 'offline'),
    ({'robot':{'armed':False}}, 'system_disarmed'),
    ({'robot':{'estop':True}}, 'system_disarmed'),
])
def test_both_arms_and_connection_required_and_disable_halts_only_owned_motion(tmp_path, monkeypatch, state_change, reason):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.started(rig.step(rig.event('axis:Y', -1.)))
    for name, values in state_change.items():
        getattr(rig, name).update(values)
    assert rig.step() == {'kind':'halt'} and rig.driver.state['state'] == reason
    rig.driver.halted(); rig.finished()
    assert rig.step(rig.event('button:South', True)) is None


def test_system_rearm_requires_release_before_new_input(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.robot['armed'] = False
    assert rig.step(rig.event('axis:Y', -1.)) is None
    rig.robot['armed'] = True
    assert rig.step() is None and rig.driver.state['state'] == 'waiting_neutral'
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', -1.))['action'] == 'forward'


def test_epoch_change_halts_owned_motion_and_reconnect_does_not_replay_hold(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.started(rig.step(rig.event('axis:Y', -1.)))
    rig.pad['input_epoch'] += 1
    assert rig.step() == {'kind':'halt'}
    rig.driver.halted(); rig.finished()
    assert rig.step() is None
    rig.pad.update(connected=False, armed=False, input_epoch=3)
    assert rig.step() is None
    rig.pad.update(connected=True, armed=True, input_epoch=4)
    assert rig.step() is None
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', -1.))['action'] == 'forward'


@pytest.mark.parametrize('key, value', [('button:South', True), ('axis:Y', -1.)])
@pytest.mark.parametrize('event_change', [{'age':.501}, {'age':-1.}, {'epoch':0}])
def test_stale_future_or_previous_epoch_events_cannot_start_motion(tmp_path, monkeypatch, key, value, event_change):
    rig = Rig(tmp_path, monkeypatch).ready()
    assert rig.step(rig.event(key, value, **event_change)) is None
    assert rig.step() is None


def test_other_motion_id_is_never_halted_on_controller_release_or_disable(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.started(rig.step(rig.event('axis:Y', -1.)))
    rig.robot['motion_id'] += 1  # manual/MCP command replaced our motion
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert not rig.driver.owns(rig.robot)
    rig.pad['armed'] = False
    assert rig.step() is None


def test_conflicting_directions_halt_and_require_neutral(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.started(rig.step(rig.event('axis:Y', -1.)))
    assert rig.step(rig.event('axis:Hat Y', 1.)) == {'kind':'halt'}
    assert 'Conflicting' in rig.driver.state['message']
    rig.driver.halted(); rig.finished()
    assert rig.step(rig.event('axis:Hat Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', -1.))['action'] == 'forward'


def test_ten_second_hold_limit_cannot_restart_until_released(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.started(rig.step(rig.event('axis:Y', -1.), now=1.))
    assert rig.step(now=10.99) is None
    assert rig.step(now=11.) == {'kind':'halt'}
    assert 'Hold limit' in rig.driver.state['message']
    rig.driver.halted(); rig.finished()
    assert rig.step(now=20.) is None
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', -1.))['action'] == 'forward'


def test_external_busy_drops_presses_and_requires_neutral_after_completion(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.robot.update(busy=True, motion_id=20)
    assert rig.step() is None
    assert rig.step(rig.event('axis:Y', -1.)) is None
    rig.finished()
    assert rig.step() is None and rig.driver.state['state'] == 'waiting_neutral'
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', -1.))['action'] == 'forward'


def test_busy_quick_tap_is_consumed_without_queueing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.robot.update(busy=True, motion_id=20)
    press, release = rig.event('button:South', True), rig.event('button:South', False)
    assert rig.step(press, release) is None
    rig.finished()
    assert rig.step() is None and rig.step() is None


def test_hold_during_owned_one_shot_does_not_queue_walking_after_it_finishes(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    press, release = rig.event('button:South', True), rig.event('button:South', False)
    rig.started(rig.step(press, release))
    assert rig.step(rig.event('axis:Y', -1.)) is None
    rig.finished()
    assert rig.step() is None and rig.driver.state['state'] == 'waiting_neutral'
    assert rig.step(rig.event('axis:Y', 0.)) is None
    assert rig.step(rig.event('axis:Y', -1.))['action'] == 'forward'


def test_stop_works_while_plan_blocked_and_system_disarmed_without_repeat(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.robot.update(armed=False, busy=True, motion_id=20)
    assert rig.step(rig.event('button:Select', True), blocked=True) == {'kind':'stop'}
    assert rig.step(rig.event('button:Select', True), blocked=True) is None
    assert rig.step(rig.event('button:Select', False), blocked=True) is None
    assert rig.step(rig.event('button:Select', True), blocked=True) == {'kind':'stop'}
    rig.pad['armed'] = False
    assert rig.step(rig.event('button:Select', False), blocked=True) is None
    assert rig.step(rig.event('button:Select', True), blocked=True) is None


def test_axis_stop_obeys_deadzone_and_hysteresis(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, [binding('axis:X:+', 'stop')]).ready()
    assert rig.step(rig.event('axis:X', .1), blocked=True) is None
    assert rig.step(rig.event('axis:X', .36), blocked=True) == {'kind':'stop'}
    assert rig.step(rig.event('axis:X', .3), blocked=True) is None
    assert rig.step(rig.event('axis:X', .4), blocked=True) is None
    assert rig.step(rig.event('axis:X', 0.), blocked=True) is None
    assert rig.step(rig.event('axis:X', .5), blocked=True) == {'kind':'stop'}


def test_mapping_reset_cannot_submit_held_control_without_fresh_neutral_cycle(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    rig.step(rig.event('axis:Y', -1.))
    rig.store.save(50, .4, [binding('axis:Y:-', 'backward', 'hold')])
    rig.driver.reset()
    assert rig.step() is None
    assert rig.step(rig.event('axis:Y', 0.)) is None
    decision = rig.step(rig.event('axis:Y', -1.))
    assert decision['action'] == 'backward' and decision['speed'] == 50


def test_two_one_shot_actions_in_one_snapshot_are_rejected(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch).ready()
    assert rig.step(rig.event('button:South', True), rig.event('button:East', True)) is None
    assert rig.driver.state['state'] == 'waiting_neutral'
