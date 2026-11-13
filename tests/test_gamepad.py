"""Joystick identity and event processing checks; no robot or device writes."""
import errno
from pathlib import Path
import struct
import time

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
