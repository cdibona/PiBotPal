"""Joystick identity and event processing checks; no robot or device writes."""
import errno
import json
from pathlib import Path
import struct
import time
import threading

import pytest
from pibotpal.gamepad import EVENT, GamepadMonitor, discover_gamepads


def device(identity='gamepad-a'):
    return {'id':identity,'name':'USB WirelessGamepad','vendor_id':'2563','product_id':'0575',
            'path':'/dev/input/js0','stable_path':'/dev/input/by-id/usb-test-joystick','readable':True}


def fake_caps(fd):
    return {'axes':[0.,0.],'buttons':[False,False],
            'axis_names':['X','Y'],'button_names':['South','East']}


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


def test_disable_closes_input_and_discards_held_and_partial_events(tmp_path):
    closed=[]; opened=[]
    def opener(path): opened.append(path); return 7
    monitor=GamepadMonitor(discover=lambda:[device()],opener=opener,
                          close_fd=closed.append,capability_reader=fake_caps,
                          settings_path=tmp_path/'controller.json')
    monitor._refresh()
    assert monitor.status()['enabled'] and monitor.status()['attached'] and monitor.status()['connected']
    monitor._events(EVENT.pack(1,1,1,0)+EVENT.pack(2,32767,2,0)+EVENT.pack(3,1,1,1)[:4])
    disabled=monitor.set_enabled(False)
    assert disabled['attached'] and not disabled['enabled'] and not disabled['connected']
    assert disabled['axes']==[] and disabled['buttons']==[] and disabled['events_seen']==0
    assert disabled['last_event_at'] is None and closed==[7] and not monitor._buffer
    monitor._events(EVENT.pack(4,1,1,0));monitor._refresh()
    assert len(opened)==1 and monitor.status()['events_seen']==0
    enabled=monitor.set_enabled(True)
    assert enabled['enabled'] and enabled['connected'] and len(opened)==2
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
    restored._refresh();assert restored.status()['attached'] and not opened
    devices.clear();restored._refresh();assert not restored.status()['attached']
    devices.append(device());restored._refresh()
    assert restored.status()['attached'] and not restored.status()['enabled'] and not opened
    restored.set_enabled(True);assert restored.status()['connected'] and len(opened)==1
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


def test_inflight_poll_cannot_read_after_disable_or_reused_descriptor():
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
        monitor.set_enabled(False)
        assert closed==[7] and not monitor.status()['connected']
        monitor.set_enabled(True)
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
