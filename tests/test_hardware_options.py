import json
import pytest
from pibotpal.hardware_options import HardwareOptions


def test_inventory_is_passive_and_saves_expansion_preferences(tmp_path, monkeypatch):
    options = HardwareOptions(tmp_path)
    data = options.get()
    lidar = next(d for d in data['devices'] if d['id']=='lidar')
    assert lidar['options']['model']=='RPLIDAR C1'
    assert lidar['options']['baud']==460800
    assert 'pending' in lidar['status']
    options.save('lidar',{'enabled':True,'device':'/dev/serial/by-id/test-C1','yaw_deg':90})
    reloaded = HardwareOptions(tmp_path)
    assert reloaded.options['lidar']['enabled'] is True
    assert reloaded.options['lidar']['device']=='/dev/serial/by-id/test-C1'
    assert reloaded.path.stat().st_mode & 0o777 == 0o600
    assert 'configuration only' in next(d for d in reloaded.get()['devices'] if d['id']=='lidar')['status']


def test_hardware_settings_reject_invalid_values_without_changing_file(tmp_path):
    options = HardwareOptions(tmp_path)
    options.save('rear_camera',{'device':'/dev/v4l/by-id/rear'})
    previous=options.path.read_bytes()
    for device,value in [('lidar',{'baud':float('nan')}),('rear_camera',{'fps':1000}),
                         ('rear_camera',{'device':'/etc/passwd'}),('rear_camera',{'device':'/dev/../../etc/passwd'}),
                         ('coral',{'connection':'shell'}),('imu',{'address':999}),('lidar',{'enabled':'yes'}),
                         ('shell',{}),('lidar',{'command':'run'})]:
        with pytest.raises(ValueError): options.save(device,value)
    assert options.path.read_bytes()==previous
