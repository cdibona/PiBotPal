"""Shared keyframe library and executor regressions; all devices are simulated."""
import json

import pytest

from pibotpal.actions import ActionStore, BUILTIN_ACTIONS
from pibotpal.hardware import Controller, MotionCancelled
from test_motion_timing import setup_controller


def frame(angle=0, speed=30, hold=0):
    return {'angles': [angle]*12, 'speed_deg_s': speed, 'hold_seconds': hold}


def test_defaults_are_editable_keyframes_without_creating_a_file(tmp_path):
    store = ActionStore(tmp_path/'actions.json')
    assert store.names() == list(BUILTIN_ACTIONS)
    catalog = store.catalog()
    assert catalog['limits']['max_frames'] == 128
    assert not store.path.exists()
    assert store.get('stand')['playback'] == 'keyframes'
    assert store.get('forward')['playback'] == 'continuous'
    assert len(store.get('wave')['frames']) > 1
    assert all(item['builtin'] and not item['modified'] and item['revision'] == 0 for item in catalog['actions'])
    for item in catalog['actions']:
        assert item['frames'] == store.validate_frames(item['frames'])
    catalog['actions'][0]['frames'][0]['angles'][0] = 0
    assert store.get('stand')['frames'][0]['angles'][0] != 0


def test_custom_actions_and_builtin_overrides_persist_and_reset(tmp_path):
    path = tmp_path/'actions.json'
    store = ActionStore(path)
    original = store.get('wave')
    edited = store.save('wave', [frame(1, 12, .1)], 'Small wave')
    assert edited['builtin'] and edited['modified'] and edited['revision'] == 1
    assert store.save('wave', [frame(2)])['revision'] == 2
    custom = store.save('tiny bow', [frame(0), frame(1, 8, .2)])
    assert not custom['builtin'] and 'tiny bow' in store.names()
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = ActionStore(path)
    assert loaded.get('wave')['frames'] == [frame(2)]
    assert loaded.get('tiny bow')['frames'] == custom['frames']
    reset = loaded.reset('wave')
    assert not reset['modified'] and reset['revision'] == 3
    assert reset['frames'] == original['frames']
    with pytest.raises(ValueError, match='Built-in'):
        loaded.delete('stand')
    with pytest.raises(ValueError, match='built-in'):
        loaded.reset('tiny bow')
    assert loaded.delete('tiny bow') == {'deleted': 'tiny bow'}
    assert 'tiny bow' not in ActionStore(path).names()


@pytest.mark.parametrize('name', ['stop', 'arm', 'halt', '../stand', 'Stand', '', ' wave', 'two  words', 'x'*65])
def test_names_cannot_replace_control_commands_or_paths(tmp_path, name):
    store = ActionStore(tmp_path/'actions.json')
    with pytest.raises(ValueError):
        store.save(name, [frame()])
    assert not store.path.exists()


@pytest.mark.parametrize('frames', [[], [frame()]*129, [{'angles':[0]*11}],
                                    [frame(float('nan'))], [frame(91)], [frame(True)],
                                    [frame(0, 0)], [frame(0, 145)], [frame(0, True)],
                                    [frame(0, 30, -1)], [frame(0, 30, 31)],
                                    [{**frame(), 'code':'run()'}], [frame(0,30,30)]*11])
def test_invalid_keyframes_are_rejected_without_writing(tmp_path, frames):
    store = ActionStore(tmp_path/'actions.json')
    with pytest.raises(ValueError):
        store.save('custom', frames)
    assert not store.path.exists()


def test_storage_failure_preserves_memory_disk_and_snapshot(tmp_path, monkeypatch):
    store = ActionStore(tmp_path/'actions.json')
    before = store.save('custom', [frame(1)])
    encoded = store.path.read_bytes()
    def failed(*args):
        raise OSError('Storage unavailable')
    monkeypatch.setattr('pibotpal.actions.os.replace', failed)
    with pytest.raises(OSError):
        store.save('custom', [frame(2)])
    assert store.get('custom') == before and store.path.read_bytes() == encoded
    assert list(tmp_path.glob('.actions-*')) == []
    before['frames'][0]['angles'][0] = 50
    assert store.get('custom')['frames'][0]['angles'][0] == 1


@pytest.mark.parametrize('data', [{}, {'version': 2, 'actions': []},
                                  {'version': 1, 'actions': [{'name':'stand'}]},
                                  {'version': 1, 'actions': {}, 'extra':1}])
def test_invalid_library_prevents_controller_startup(tmp_path, data):
    path = tmp_path/'actions.json'
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match='Saved actions'):
        Controller(simulate=True, config_path=tmp_path/'calibration', action_path=path)


def test_named_action_uses_saved_override_and_per_frame_rates(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.set_servo_budget(4, 60, 180, 40)
    c.actions.save('stand', [frame(1, 5, .2), frame(2, 10)], 'Edited standing movement')
    monkeypatch.setattr(c, '_submit', lambda name, operation: operation())
    c.action('stand', speed=80)
    assert c._angles == pytest.approx([2]*12)
    before = [0.]*12
    for _, angles, changed in trace:
        assert max(abs(a-b) for a,b in zip(before, angles)) <= .2 + 1e-8
        before = angles
    # All twelve joints reach frame one before the first hold/next frame.
    reached = next(i for i,row in enumerate(trace) if row[1] == pytest.approx([1]*12))
    assert trace[reached+1][0] - trace[reached][0] >= .2
    assert c.status()['keyframes']['phase'] == 'complete'
    assert c.status()['keyframes']['revision'] == 1


def test_action_snapshots_definition_when_submitted(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.actions.save('custom', [frame(1)])
    queued = []
    monkeypatch.setattr(c, '_submit', lambda name, operation: queued.append(operation))
    c.action('custom')
    c.actions.save('custom', [frame(2)])
    queued[0]()
    assert c._angles == pytest.approx([1]*12)
    assert c.status()['keyframes']['revision'] == 1


def test_action_rechecks_calibration_before_submission(monkeypatch, tmp_path):
    c, _, _ = setup_controller(monkeypatch, tmp_path)
    values = [0]*12; values[0] = 85
    c.actions.save('custom', [{'angles': values}])
    c._offsets[0] = 10
    monkeypatch.setattr(c, '_submit', lambda *args: pytest.fail('Invalid calibrated frame was submitted'))
    with pytest.raises(ValueError, match='calibrated'):
        c.action('custom')


def test_hold_is_cancellable_and_cannot_reach_later_frame(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.actions.save('hold pose', [frame(0, 30, 5), frame(1)])
    clock.cancel_at = .1
    monkeypatch.setattr(c, '_submit', lambda name, operation: operation())
    with pytest.raises(MotionCancelled):
        c.action('hold pose')
    assert not trace and c._angles == [0]*12
    assert c.status()['keyframes']['phase'] == 'cancelled'


def test_whole_action_deadline_includes_repeated_holds(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    c.actions.save('wait here', [frame(0,30,30)]*3)
    monkeypatch.setattr(c, '_submit', lambda name, operation: operation())
    with pytest.raises(RuntimeError, match='300 second'):
        c.action('wait here', steps=5)
    assert clock.now == pytest.approx(300)
    assert not trace and c.status()['keyframes']['phase'] == 'failed'
    assert c._action_deadline is None


def test_continuous_frames_share_timeline_until_rate_or_hold_boundary(monkeypatch, tmp_path):
    c, clock, trace = setup_controller(monkeypatch, tmp_path)
    frames = [frame(v,10) for v in (0,1,2)] + [frame(3,20,.1),frame(4,20)]
    c.actions.save('path', frames, playback='continuous')
    batches = []
    monkeypatch.setattr(c, '_move_path', lambda targets, speed: batches.append((targets,speed)))
    monkeypatch.setattr(c, '_submit', lambda name, operation: operation())
    c.action('path', speed=80)
    assert [len(targets) for targets,_ in batches] == [3,1,1]
    assert [speed*1.8 for _,speed in batches] == pytest.approx([10,20,20])
    assert clock.now == pytest.approx(.1)
