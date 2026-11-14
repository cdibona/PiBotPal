"""Joystick identity and event processing checks; no robot or device writes."""
import errno
import json
from pathlib import Path
import struct
import time
import threading

import pytest
from pibotpal.gamepad import EVENT, GamepadMonitor, discover_gamepads



def arming_event(monitor, armed, *, arm_system=False):
    return {'armed': armed, 'arm_system': arm_system,
            'input_epoch': monitor.status()['input_epoch']}


def device(identity='gamepad-a'):
    return {'id':identity,'name':'USB WirelessGamepad','vendor_id':'2563','product_id':'0575',
            'path':'/dev/input/js0','stable_path':'/dev/input/by-id/usb-test-joystick','readable':True}


def fake_caps(fd):
    return {'axes':[0.,0.],'buttons':[False,False],
            'axis_names':['X','Y'],'button_names':['South','East']}


def ioctl_monitor(monkeypatch, *, identity=None, codes=None, clock=None):
    """Use the real capability parser with the receiver's captured ioctl layout."""
    codes = list(range(0x130, 0x13d)) if codes is None else codes
    replies = {
        0x80016a11: bytes([6]),
        0x80016a12: bytes([len(codes)]),
        0x81006a13: b'USB WirelessGamepad'.ljust(256, b'\0'),
        0x80406a32: bytes([0, 1, 2, 5, 16, 17]).ljust(64, b'\0'),
        0x84006a34: struct.pack('=512H', *(codes + [0]*(512-len(codes)))),
    }
    def ioctl(fd, request, target, mutate):
        assert fd == 7 and mutate and len(target) == len(replies[request])
        target[:] = replies[request]
        return 0
    monkeypatch.setattr('pibotpal.gamepad.fcntl.ioctl', ioctl)
    monitor = GamepadMonitor(discover=lambda:[identity or device()], opener=lambda p:7,
                             close_fd=lambda fd:None, monotonic=clock)
    monitor._refresh()
    return monitor


def test_shanwan_sequential_hid_map_uses_physical_start_button(monkeypatch):
    now = [1.0]
    monitor = ioctl_monitor(monkeypatch, clock=lambda:now[0])
    state = monitor.status()
    assert state['button_mapping'] == 'shanwan-2563:0575'
    assert state['button_codes'] == list(range(0x130, 0x13d))
    assert state['button_names'] == ['North', 'East', 'South', 'West', 'L1', 'R1',
                                    'L2', 'R2', 'Select', 'Start', 'Left stick',
                                    'Right stick', 'Mode']
    monitor._events(b''.join(EVENT.pack(0, 0, 0x81, n) for n in range(13)))
    # Linux calls index 11 BTN_START on this descriptor, but the handset
    # actually uses it for the right stick. It must never toggle arming.
    monitor._events(EVENT.pack(1, 1, 1, 11)+EVENT.pack(2, 0, 1, 11))
    assert monitor.status()['armed'] and monitor.drain_notifications() == []
    monitor._events(EVENT.pack(3, 1, 1, 9))
    assert not monitor.status()['armed'] and monitor.status()['connected']
    assert monitor.drain_notifications() == [arming_event(monitor, False)]
    now[0] = 2.0
    monitor._events(EVENT.pack(4, 0, 1, 9)+EVENT.pack(5, 1, 1, 9))
    assert monitor.status()['armed']
    assert monitor.drain_notifications() == [arming_event(monitor, True)]
    monitor.close()


@pytest.mark.parametrize('identity_change', [{'vendor_id':'1234'}, {'product_id':'0576'}])
def test_other_receiver_identities_keep_linux_button_semantics(monkeypatch, identity_change):
    monitor = ioctl_monitor(monkeypatch, identity=dict(device(), **identity_change))
    state = monitor.status()
    assert state['button_mapping'] == 'linux'
    assert state['button_names'][9] == 'R2' and state['button_names'][11] == 'Start'
    monitor._events(EVENT.pack(0, 0, 0x81, 11)+EVENT.pack(1, 1, 1, 11))
    assert not monitor.status()['armed']
    monitor.close()


def test_shanwan_corrected_kernel_mapping_is_not_mapped_twice(monkeypatch):
    codes = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x138, 0x139,
             0x13a, 0x13b, 0x13c, 0x13d, 0x13e]
    monitor = ioctl_monitor(monkeypatch, codes=codes)
    state = monitor.status()
    assert state['button_mapping'] == 'linux' and state['button_codes'] == codes
    assert state['button_names'] == ['South', 'East', 'North', 'West', 'L1', 'R1',
                                    'L2', 'R2', 'Select', 'Start', 'Mode',
                                    'Left stick', 'Right stick']
    monitor.close()


def test_shanwan_incomplete_button_map_is_not_assumed_to_match(monkeypatch):
    monitor = ioctl_monitor(monkeypatch, codes=list(range(0x130, 0x13c)))
    assert monitor.status()['button_mapping'] == 'linux'
    assert monitor.status()['button_names'][11] == 'Start'
    monitor.close()


def test_passive_discovery_keeps_usb_identity_and_stable_path(tmp_path):
    sys=tmp_path/'sys';dev=tmp_path/'dev';node=sys/'js0'/'device';(node/'id').mkdir(parents=True)
    (node/'name').write_text('USB WirelessGamepad ')
    (node/'id/vendor').write_text('2563');(node/'id/product').write_text('0575')
    (dev/'by-id').mkdir(parents=True);(dev/'js0').write_bytes(b'')
    (dev/'by-id/usb-test-joystick').symlink_to('../js0')
    # The corresponding event node must not cause a second controller entry.
    (dev/'by-id/usb-test-event-joystick').symlink_to('../event5')
    rows=discover_gamepads(sys,dev)
    assert len(rows)==1
    row=rows[0]
    assert row['vendor_id']=='2563' and row['product_id']=='0575'
    assert row['name']=='USB WirelessGamepad'
    assert row['stable_path']==str(dev/'by-id/usb-test-joystick')
    assert row['readable']
    assert 'not verified' in row['compatibility']
    assert discover_gamepads(sys,dev)[0]['id']==row['id']


def test_partial_events_initial_state_and_bounds_are_handled():
    monitor=GamepadMonitor(simulate=True)
    monitor._state.update(fake_caps(1))
    initial=EVENT.pack(100,32767,0x82,0)
    monitor._events(initial[:3]);assert monitor.status()['axes']==[0.,0.]
    monitor._events(initial[3:])
    assert monitor.status()['axes']==[1.,0.]
    assert monitor.status()['events_seen']==0 and monitor.status()['last_event_at'] is None
    monitor._events(EVENT.pack(110,-32768,2,1)+EVENT.pack(111,1,1,0))
    state=monitor.status()
    assert state['axes']==[1.,-1.] and state['buttons']==[True,False]
    assert state['events_seen']==2 and state['last_event_at'] is not None
    monitor._events(EVENT.pack(112,1,1,200)+EVENT.pack(113,20,2,200))
    assert monitor.status()['events_seen']==2
    assert not monitor.status()['motion_enabled']


def test_disconnect_clears_held_buttons_axes_and_closes_descriptor():
    closed=[]
    monitor=GamepadMonitor(discover=lambda:[device()],opener=lambda path:7,
                          close_fd=closed.append,capability_reader=fake_caps)
    monitor._refresh();assert monitor.status()['connected']
    monitor._events(EVENT.pack(1,1,1,0))
    monitor._disconnect('Controller disconnected')
    state=monitor.status()
    assert closed==[7] and not state['connected']
    assert state['buttons']==[] and state['axes']==[]
    assert state['error']=='Controller disconnected'


def test_hotplug_removes_old_device_and_can_open_replacement():
    devices=[device()];opened=[];closed=[]
    def opener(path):opened.append(path);return len(opened)
    monitor=GamepadMonitor(discover=lambda:list(devices),opener=opener,
                          close_fd=closed.append,capability_reader=fake_caps)
    monitor._refresh();assert monitor.status()['active_device_id']=='gamepad-a'
    devices.clear();monitor._refresh()
    assert not monitor.status()['connected'] and closed==[1]
    devices.append(device('gamepad-b'));monitor._refresh()
    assert monitor.status()['active_device_id']=='gamepad-b'
    assert len(opened)==2
    monitor.close()


def test_permission_error_reports_help_without_privilege_changes():
    def denied(path):raise PermissionError('no permission')
    monitor=GamepadMonitor(discover=lambda:[device()],opener=denied,capability_reader=fake_caps)
    monitor._refresh();state=monitor.status()
    assert not state['connected'] and 'input-group' in state['error']
    assert not state['motion_enabled']


def test_failed_capability_probe_closes_descriptor():
    def failed(fd):raise OSError('unplugged')
    closed=[]
    monitor=GamepadMonitor(discover=lambda:[device()],opener=lambda p:3,
                          close_fd=closed.append,capability_reader=failed)
    monitor._refresh()
    assert closed==[3] and not monitor.status()['connected']


def test_monitor_reads_nonblocking_events_and_stops_thread():
    packets=[EVENT.pack(10,5000,2,0)]
    closed=[]
    def read(fd,count):
        if packets:return packets.pop(0)
        raise BlockingIOError()
    monitor=GamepadMonitor(discover=lambda:[device()],opener=lambda p:3,read=read,
                          close_fd=closed.append,capability_reader=fake_caps,
                          poll=lambda fd,timeout:bool(packets))
    monitor.start()
    deadline=time.monotonic()+1
    while monitor.status()['events_seen']==0 and time.monotonic()<deadline:time.sleep(.01)
    assert monitor.status()['axes'][0]==pytest.approx(5000/32767)
    monitor.close()
    assert not monitor._thread.is_alive() and closed==[3]
    assert not monitor.status()['connected']


def test_runtime_device_removal_is_reported_and_does_not_hold_input():
    closed=[]
    def removed(fd,count): raise OSError(errno.ENODEV,'removed')
    monitor=GamepadMonitor(discover=lambda:[device()],opener=lambda p:3,read=removed,
                          close_fd=closed.append,capability_reader=fake_caps,
                          poll=lambda fd,timeout:True)
    monitor.start()
    deadline=time.monotonic()+1
    while not closed and time.monotonic()<deadline:time.sleep(.01)
    state=monitor.status()
    assert closed==[3] and not state['connected']
    assert state['axes']==[] and state['buttons']==[]
    assert state['error']=='Controller disconnected'
    monitor.close()


def test_disarm_keeps_start_connection_and_clears_ordinary_inputs(tmp_path):
    closed=[]; opened=[]
    def opener(path): opened.append(path); return 7
    monitor=GamepadMonitor(discover=lambda:[device()],opener=opener,
                          close_fd=closed.append,capability_reader=fake_caps,
                          settings_path=tmp_path/'controller.json')
    monitor._refresh()
    assert monitor.status()['enabled'] and monitor.status()['attached'] and monitor.status()['connected']
    monitor._events(EVENT.pack(1,1,1,0)+EVENT.pack(2,32767,2,0)+EVENT.pack(3,1,1,1)[:4])
    disabled=monitor.set_enabled(False)
    assert disabled['attached'] and not disabled['enabled'] and disabled['connected'] and not disabled['armed']
    assert disabled['axes']==[0.,0.] and disabled['buttons']==[False,False] and disabled['events_seen']==0
    assert disabled['last_event_at'] is None and closed==[]
    # Preserve framing of a partially received ordinary event while disarmed.
    monitor._events(EVENT.pack(3,1,1,1)[4:]+EVENT.pack(4,1,1,0));monitor._refresh()
    assert len(opened)==1 and monitor.status()['events_seen']==0
    enabled=monitor.set_enabled(True)
    assert enabled['enabled'] and enabled['connected'] and enabled['armed'] and len(opened)==1
    assert enabled['axes']==[0.,0.] and enabled['buttons']==[False,False]
    monitor.close()


def test_disabled_preference_survives_restart_and_hotplug(tmp_path):
    path=tmp_path/'controller.json';devices=[device()];opened=[]
    def opener(p): opened.append(p); return 7
    original=GamepadMonitor(discover=lambda:list(devices),opener=opener,close_fd=lambda fd:None,
                            capability_reader=fake_caps,settings_path=path)
    original.set_enabled(False);original.close()
    assert json.loads(path.read_text())=={'version':1,'enabled':False}
    assert path.stat().st_mode & 0o777==0o600
    restored=GamepadMonitor(discover=lambda:list(devices),opener=opener,close_fd=lambda fd:None,
                           capability_reader=fake_caps,settings_path=path)
    restored._refresh();assert restored.status()['attached'] and restored.status()['connected']
    assert not restored.status()['armed'] and len(opened)==2
    devices.clear();restored._refresh();assert not restored.status()['attached']
    devices.append(device());restored._refresh()
    assert restored.status()['attached'] and not restored.status()['enabled'] and len(opened)==3
    restored.set_enabled(True);assert restored.status()['connected'] and restored.status()['armed'] and len(opened)==3
    restored.close()


def test_enable_without_receiver_keeps_auto_connection_preference():
    devices=[]
    monitor=GamepadMonitor(discover=lambda:list(devices),opener=lambda p:7,
                          close_fd=lambda fd:None,capability_reader=fake_caps)
    state=monitor.set_enabled(True)
    assert state['enabled'] and not state['attached'] and not state['connected']
    devices.append(device());monitor._refresh()
    assert monitor.status()['connected']
    monitor.close()


def test_inflight_poll_cannot_read_after_hotplug_reuses_descriptor():
    polling=threading.Event(); release=threading.Event();stale_returned=threading.Event()
    reads=[];closed=[]; polls=0
    def poll(fd,timeout):
        nonlocal polls
        polls+=1
        if polls==1:
            polling.set();release.wait(2);stale_returned.set();return True
        time.sleep(.005)
        return False
    def read(fd,count):
        reads.append(fd);return EVENT.pack(1,1,1,0)
    monitor=GamepadMonitor(discover=lambda:[device()],opener=lambda p:7,read=read,
                          close_fd=closed.append,capability_reader=fake_caps,poll=poll)
    monitor.start()
    try:
        assert polling.wait(1)
        # The second open deliberately reuses fd 7; generation, not integer
        # descriptor equality, must reject the old poll's readiness result.
        monitor._disconnect()
        assert closed==[7] and not monitor.status()['connected']
        monitor._refresh()
        release.set();assert stale_returned.wait(1)
        deadline=time.monotonic()+1
        while polls<2 and time.monotonic()<deadline:time.sleep(.005)
        assert polls>=2 and reads==[] and monitor.status()['events_seen']==0
    finally:
        release.set();monitor.close()


def test_bad_preference_and_failed_save_do_not_silently_enable_input(tmp_path,monkeypatch):
    path=tmp_path/'controller.json';path.write_text('{"version":1,"enabled":"yes"}')
    with pytest.raises(RuntimeError,match='repair controller.json'):
        GamepadMonitor(settings_path=path)
    path.unlink()
    monitor=GamepadMonitor(simulate=True,settings_path=path)
    for value in (None,1,'false'):
        with pytest.raises(ValueError,match='boolean'):monitor.set_enabled(value)
    def failed(enabled):raise OSError('disk full')
    monkeypatch.setattr(monitor,'_save_enabled',failed)
    with pytest.raises(OSError,match='disk full'):monitor.set_enabled(False)
    assert monitor.status()['enabled'] and not path.exists()


def start_monitor(tmp_path=None, *, clock=None, system_armed=None):
    def caps(fd):
        data=fake_caps(fd)
        data.update(buttons=[False,False,False],button_names=['South','Start','East'])
        return data
    monitor=GamepadMonitor(discover=lambda:[device()],opener=lambda p:7,close_fd=lambda fd:None,
                           capability_reader=caps,settings_path=tmp_path/'controller.json' if tmp_path else None,
                           monotonic=clock, system_armed=system_armed)
    monitor._refresh()
    return monitor


def test_start_toggles_disarm_and_rearm_with_listener_still_connected(tmp_path):
    now=[1.0];monitor=start_monitor(tmp_path,clock=lambda:now[0])
    assert monitor.status()['armed'] and monitor.status()['start_listening']
    assert monitor.drain_notifications()==[]
    # Released initialization arms edge detection, but is never a toggle.
    monitor._events(EVENT.pack(1,0,0x81,1))
    press=EVENT.pack(2,1,1,1)
    monitor._events(press[:3]);assert monitor.status()['armed']
    monitor._events(press[3:])
    assert not monitor.status()['armed'] and monitor.status()['connected']
    assert monitor.drain_notifications()==[arming_event(monitor, False)]
    monitor._events(EVENT.pack(3,32767,2,0)+EVENT.pack(4,1,1,0))
    assert monitor.status()['axes']==[0.,0.] and monitor.status()['buttons']==[False]*3
    # A held Start, regardless of elapsed time, cannot arm again.
    now[0]=2.0;monitor._events(press)
    assert not monitor.status()['armed'] and monitor.drain_notifications()==[]
    monitor._events(EVENT.pack(5,0,1,1)+press)
    assert monitor.status()['armed'] and monitor.status()['connected']
    assert monitor.drain_notifications()==[arming_event(monitor, True)]
    assert json.loads((tmp_path/'controller.json').read_text())['enabled'] is True
    monitor.close()


def test_start_ignores_initial_held_state_contact_bounce_and_hotplug():
    now=[1.0];monitor=start_monitor(clock=lambda:now[0])
    pressed=EVENT.pack(1,1,1,1);released=EVENT.pack(2,0,1,1)
    monitor._events(EVENT.pack(0,1,0x81,1)+pressed)
    assert monitor.status()['armed'] and monitor.drain_notifications()==[]
    monitor._events(released+pressed)
    assert not monitor.status()['armed']
    assert monitor.drain_notifications()==[arming_event(monitor, False)]
    now[0]=1.1;monitor._events(released+pressed)
    assert not monitor.status()['armed'] and monitor.drain_notifications()==[]
    now[0]=1.5;monitor._events(released+pressed)
    assert monitor.status()['armed'] and monitor.drain_notifications()==[arming_event(monitor, True)]
    monitor._disconnect();monitor._refresh()
    monitor._events(EVENT.pack(0,1,0x81,1)+pressed)
    assert monitor.status()['armed'] and monitor.drain_notifications()==[]
    monitor.close()


def test_explicit_transitions_only_notify_actual_arming_and_never_noops():
    monitor=GamepadMonitor(simulate=True)
    monitor.set_enabled(False);monitor.set_enabled(True)
    assert not monitor.status()['armed'] and monitor.drain_notifications()==[]
    connected=start_monitor()
    connected.set_enabled(True);assert connected.drain_notifications()==[]
    connected.set_enabled(False);connected.set_enabled(False)
    assert connected.drain_notifications()==[arming_event(connected, False)]
    connected.set_enabled(True)
    assert connected.drain_notifications()==[arming_event(connected, True)]
    connected._disconnect();connected._refresh()
    assert connected.drain_notifications()==[]
    connected.close()


def test_failed_start_preference_save_keeps_state_and_monitor_usable(monkeypatch):
    now=[1.0];monitor=start_monitor(clock=lambda:now[0])
    def failed(enabled):raise OSError('disk full')
    monkeypatch.setattr(monitor,'_save_enabled',failed)
    monitor._events(EVENT.pack(0,0,0x81,1)+EVENT.pack(1,1,1,1))
    assert monitor.status()['armed'] and monitor.status()['connected']
    assert 'disk full' in monitor.status()['error'] and monitor.drain_notifications()==[]
    monitor._refresh()
    assert 'disk full' in monitor.status()['error']
    # A held button cannot repeatedly retry a failed transition.
    monitor._events(EVENT.pack(2,1,1,1))
    monkeypatch.setattr(monitor,'_save_enabled',lambda enabled:None)
    now[0]=2.0
    monitor._events(EVENT.pack(3,0,1,1)+EVENT.pack(4,1,1,1))
    assert not monitor.status()['armed'] and monitor.drain_notifications()==[arming_event(monitor, False)]
    assert monitor.status()['error'] is None
    monitor.close()


def test_raw_inputs_remain_visible_while_disarmed_and_init_never_dispatches():
    monitor = start_monitor()
    monitor.set_enabled(False)
    monitor._events(EVENT.pack(0, 20000, 0x82, 0)+EVENT.pack(0, 1, 0x81, 0))
    state = monitor.status()
    assert state['raw_axes'] == [pytest.approx(20000/32767), 0.]
    assert state['raw_buttons'] == [True, False, False]
    assert state['axes'] == [0., 0.] and state['buttons'] == [False]*3
    assert state['input_revision'] == 0 and state['recent_events'] == []
    assert monitor.drain_input_events() == []
    monitor._events(EVENT.pack(1, -32768, 2, 1)+EVENT.pack(2, 0, 1, 0))
    state = monitor.status()
    assert state['raw_axes'][1] == -1. and state['raw_buttons'][0] is False
    assert state['axes'] == [0., 0.] and state['buttons'] == [False]*3
    assert state['events_seen'] == 0 and state['last_event_at'] is None
    assert state['input_revision'] == 2
    assert [(e['kind'], e['value']) for e in state['recent_events']] == [('axis', -1.), ('button', False)]
    assert len(monitor.drain_input_events()) == 2
    # Re-arming does not invent neutral raw input or replay old queued actions.
    monitor.set_enabled(True)
    assert monitor.status()['raw_axes'] == state['raw_axes']
    assert monitor.status()['axes'] == [0., 0.]
    assert monitor.drain_input_events() == []
    monitor.close()


def test_fast_press_release_and_partial_packets_have_independent_ordered_history():
    monitor = start_monitor()
    press, release = EVENT.pack(1, 1, 1, 0), EVENT.pack(2, 0, 1, 0)
    monitor._events(press[:3])
    assert monitor.status()['input_revision'] == 0
    monitor._events(press[3:]+release)
    state = monitor.status()
    assert state['raw_buttons'][0] is False and state['buttons'][0] is False
    events = monitor.drain_input_events()
    assert [(e['seq'], e['kind'], e['index'], e['name'], e['value']) for e in events] == [
        (1, 'button', 0, 'South', True), (2, 'button', 0, 'South', False)]
    assert all(e['epoch'] == state['input_epoch'] and e['time'] > 0 for e in events)
    assert events == state['recent_events'] and monitor.drain_input_events() == []
    events[0]['name'] = 'changed by consumer'
    state['recent_events'][1]['value'] = True
    assert monitor.status()['recent_events'][0]['name'] == 'South'
    assert monitor.status()['recent_events'][1]['value'] is False
    # Unknown kinds and out-of-range indices cannot advance the event stream.
    monitor._events(EVENT.pack(3, 1, 3, 0)+EVENT.pack(4, 1, 1, 200)+EVENT.pack(5, 1, 2, 200))
    assert monitor.status()['input_revision'] == 2 and monitor.drain_input_events() == []
    monitor.close()


def test_input_epoch_invalidates_stale_holds_but_revision_survives_reconnect():
    monitor = start_monitor()
    connected_epoch = monitor.status()['input_epoch']
    monitor._events(EVENT.pack(1, 1, 1, 0))
    monitor.set_enabled(False)
    disabled = monitor.status()
    assert disabled['input_epoch'] == connected_epoch+1
    assert disabled['raw_buttons'][0] is True and disabled['buttons'][0] is False
    assert disabled['input_revision'] == 1 and len(disabled['recent_events']) == 1
    assert monitor.drain_input_events() == []
    monitor.set_enabled(False)
    assert monitor.status()['input_epoch'] == disabled['input_epoch']
    monitor._events(EVENT.pack(2, 0, 1, 0))
    monitor._disconnect()
    disconnected = monitor.status()
    assert disconnected['input_epoch'] == disabled['input_epoch']+1
    assert disconnected['raw_axes'] == [] and disconnected['raw_buttons'] == []
    assert disconnected['recent_events'] == [] and disconnected['input_revision'] == 2
    assert monitor.drain_input_events() == []
    monitor._refresh()
    assert monitor.status()['input_epoch'] == disconnected['input_epoch']+1
    monitor._events(EVENT.pack(0, 1, 0x81, 0))
    assert monitor.status()['raw_buttons'][0] is True and monitor.drain_input_events() == []
    monitor._events(EVENT.pack(3, 0, 1, 0))
    assert monitor.drain_input_events()[0]['seq'] == 3
    monitor.close()


def test_start_toggle_preserves_raw_edge_and_sets_event_epoch_after_transition():
    now = [1.0]
    monitor = start_monitor(clock=lambda:now[0])
    monitor._events(EVENT.pack(0, 0, 0x81, 1)+EVENT.pack(1, 1, 1, 0))
    old_epoch = monitor.status()['input_epoch']
    monitor._events(EVENT.pack(2, 1, 1, 1))
    state = monitor.status()
    assert state['input_epoch'] == old_epoch+1 and not state['armed']
    assert state['raw_buttons'] == [True, True, False] and state['buttons'] == [False]*3
    assert len(state['recent_events']) == 2
    events = monitor.drain_input_events()
    assert len(events) == 1 and events[0]['name'] == 'Start' and events[0]['value'] is True
    assert events[0]['seq'] == 2 and events[0]['epoch'] == state['input_epoch']
    assert monitor.drain_notifications() == [arming_event(monitor, False)]
    now[0] = 2.0
    monitor._events(EVENT.pack(3, 0, 1, 1)+EVENT.pack(4, 1, 1, 1))
    assert monitor.status()['armed'] and monitor.status()['input_epoch'] == old_epoch+2
    assert monitor.drain_notifications() == [arming_event(monitor, True)]
    monitor.close()


def test_start_requests_system_arm_once_from_saved_enabled_startup():
    system = {'armed': False}
    monitor = start_monitor(system_armed=lambda: system['armed'])
    initial_epoch = monitor.status()['input_epoch']
    monitor._events(EVENT.pack(0, 0, 0x81, 1))
    assert monitor.drain_notifications() == [] and not system['armed']
    monitor._events(EVENT.pack(1, 1, 1, 1))
    event, = monitor.drain_notifications()
    assert event == arming_event(monitor, True, arm_system=True)
    assert event['input_epoch'] > initial_epoch
    assert not system['armed'], 'The monitor must not bypass application arming guards'
    assert monitor.apply_arm_request(event['input_epoch'], lambda: system.update(armed=True))
    assert system['armed']
    # Repeated held reports produce no additional request.
    monitor._events(EVENT.pack(2, 1, 1, 1))
    assert monitor.drain_notifications() == []
    monitor.close()


def test_second_start_press_cancels_queued_arm_before_app_processes_it():
    now = [1.0]
    monitor = start_monitor(clock=lambda: now[0], system_armed=lambda: False)
    monitor._events(EVENT.pack(0, 0, 0x81, 1) + EVENT.pack(1, 1, 1, 1))
    first, = monitor.drain_notifications()
    now[0] = 2.0
    monitor._events(EVENT.pack(2, 0, 1, 1) + EVENT.pack(3, 1, 1, 1))
    assert not monitor.status()['enabled']
    assert monitor.drain_notifications() == [arming_event(monitor, False)]
    calls = []
    assert not monitor.apply_arm_request(first['input_epoch'], lambda: calls.append('armed'))
    assert calls == []
    monitor.close()


def test_system_stop_discard_invalidates_already_dequeued_start_request():
    monitor = start_monitor(system_armed=lambda: False)
    monitor._events(EVENT.pack(0, 0, 0x81, 1) + EVENT.pack(1, 1, 1, 1))
    event, = monitor.drain_notifications()
    monitor.discard_pending_actions()
    calls = []
    assert not monitor.apply_arm_request(event['input_epoch'], lambda: calls.append('armed'))
    assert calls == [] and monitor.drain_input_events() == []
    assert monitor.drain_notifications() == []
    assert monitor.status()['enabled'], 'Discarding stale work must preserve the saved input preference'
    monitor.close()


@pytest.mark.parametrize('invalidate', ['disable', 'disconnect', 'new_epoch'])
def test_queued_start_request_cannot_arm_after_input_session_changes(invalidate):
    monitor = start_monitor(system_armed=lambda: False)
    monitor._events(EVENT.pack(0, 0, 0x81, 1) + EVENT.pack(1, 1, 1, 1))
    event, = monitor.drain_notifications()
    if invalidate == 'disable':
        monitor.set_enabled(False)
    elif invalidate == 'disconnect':
        monitor._disconnect()
        monitor._refresh()
    else:
        monitor.set_enabled(True, request_system_arm=True)
    calls = []
    assert not monitor.apply_arm_request(event['input_epoch'], lambda: calls.append('armed'))
    assert calls == []
    monitor.close()


def test_failed_start_guard_disarms_input_and_records_failure(tmp_path):
    monitor = start_monitor(tmp_path, system_armed=lambda: False)
    monitor._events(EVENT.pack(0, 0, 0x81, 1) + EVENT.pack(1, 1, 1, 1))
    event, = monitor.drain_notifications()
    def reject():
        raise ValueError('Pi undervoltage is active')
    with pytest.raises(ValueError, match='undervoltage'):
        monitor.apply_arm_request(event['input_epoch'], reject)
    state = monitor.status()
    assert not state['enabled'] and not state['armed']
    assert 'undervoltage' in state['error']
    assert json.loads((tmp_path/'controller.json').read_text())['enabled'] is False
    assert monitor.drain_notifications() == []
    monitor.close()


def test_failed_preference_save_never_calls_system_arm(tmp_path, monkeypatch):
    monitor = start_monitor(tmp_path)
    monitor.set_enabled(False)
    calls = []
    def failed(enabled):
        raise OSError('disk full')
    monkeypatch.setattr(monitor, '_save_enabled', failed)
    with pytest.raises(OSError, match='disk full'):
        monitor.set_enabled(True, arm_system=lambda: calls.append('armed'))
    assert calls == [] and not monitor.status()['enabled']
    assert json.loads((tmp_path/'controller.json').read_text())['enabled'] is False
    monitor.close()


def test_failed_system_arm_restores_preference_without_announcement(tmp_path):
    monitor = start_monitor(tmp_path)
    monitor.set_enabled(False)
    monitor.drain_notifications()
    def reject():
        raise ValueError('Repair calibration before arming')
    with pytest.raises(ValueError, match='calibration'):
        monitor.set_enabled(True, arm_system=reject)
    assert not monitor.status()['enabled'] and not monitor.status()['armed']
    assert json.loads((tmp_path/'controller.json').read_text())['enabled'] is False
    assert monitor.drain_notifications() == []
    monitor.close()


def test_raw_history_is_bounded_without_losing_dispatch_edges():
    monitor = start_monitor()
    monitor._events(b''.join(EVENT.pack(n, n, 2, 0) for n in range(100)))
    state = monitor.status()
    assert state['input_revision'] == 100 and len(state['recent_events']) == 64
    assert state['recent_events'][0]['seq'] == 37
    assert len(monitor.drain_input_events()) == 100
    assert len(monitor.status()['recent_events']) == 64
    monitor.close()


def test_dispatch_queue_overflow_changes_epoch_so_consumers_drop_stale_holds():
    monitor = start_monitor()
    epoch = monitor.status()['input_epoch']
    monitor._events(b''.join(EVENT.pack(n, n, 2, 0) for n in range(513)))
    state = monitor.status()
    assert state['input_epoch'] == epoch+1 and state['input_revision'] == 513
    events = monitor.drain_input_events()
    assert len(events) == 1 and events[0]['seq'] == 513 and events[0]['epoch'] == epoch+1
    assert len(state['recent_events']) == 64
    monitor.close()
