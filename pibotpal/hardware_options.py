# SPDX-License-Identifier: MIT
"""Passive hardware inventory and validated expansion configuration.

Saving options never opens devices, installs drivers, or changes active wiring.
"""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import threading

from .gamepad import discover_gamepads

DEFAULTS = {
 'servos': ('Robot HAT / twelve servos', 'motion', {'enabled': True}, 'Active direct I2C driver. Wiring and calibration are managed by the controller.'),
 'front_camera': ('Front CSI camera', 'camera', {'enabled': True}, 'Active libcamera stream. Use camera controls to start or stop capture.'),
 'ultrasonic': ('Forward ultrasonic', 'range', {'enabled': True}, 'Active D2/D3 single return. No map or rear coverage.'),
 'power': ('Battery / power', 'power', {'enabled': True}, 'Voltage only. Charging, current and remaining capacity need additional hardware.'),
 'microphone': ('USB microphone', 'audio', {'enabled': False}, 'Capture consent is controlled separately; saving options never unmutes.'),
 'speaker': ('Robot HAT speaker', 'audio', {'enabled': True}, 'Active ALSA output; playback requires an explicit request.'),
 'rear_camera': ('Rear USB webcam', 'camera', {'enabled': False, 'device': '', 'width': 640, 'height': 480, 'fps': 10, 'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'yaw_deg': 180}, 'Provisioning only: USB capture driver and rear feed are not implemented yet. Prefer /dev/v4l/by-id paths.'),
 'lidar': ('SLAM lidar', 'range', {'enabled': False, 'model': 'RPLIDAR C1', 'device': '', 'baud': 460800, 'x_mm': 0, 'y_mm': 0, 'z_mm': 0, 'yaw_deg': 0}, 'Provisioning only: RPLIDAR C1 USB serial, 460800 baud; use SLAMTEC BSD-2-Clause SDK. Scan timestamps and mounting transform are required; lidar alone does not provide reliable leg odometry.'),
 'coral': ('Coral USB Edge TPU', 'accelerator', {'enabled': False, 'connection': 'usb', 'model_path': ''}, 'Provisioning only: compiled int8 TensorFlow Lite perception models; not an accelerator for llama.cpp text LLMs. Runtime integration pending.'),
 'gamepad': ('USB PS2-style controller', 'input', {'enabled': False}, 'Read-only recognition and input monitor. USB presence does not prove handset pairing. Sticks and buttons are not mapped to robot motion.'),
 'imu': ('Body IMU', 'orientation', {'enabled': False, 'model': '', 'bus': '/dev/i2c-1', 'address': 104, 'yaw_deg': 0}, 'Provisioning only: no IMU driver identified. World orientation remains unavailable.'),
}


class HardwareOptions:
 def __init__(self, root):
  self.path = Path(root) / 'hardware.json'
  self.lock = threading.RLock()
  self.options = {key: copy.deepcopy(row[2]) for key,row in DEFAULTS.items()}
  if self.path.exists():
   data = json.loads(self.path.read_text())
   if not isinstance(data,dict) or set(data)-set(DEFAULTS):
    raise ValueError('Invalid hardware settings')
   for key,value in data.items(): self.options[key] = self.validate(key,value)

 def validate(self, identity, options):
  if identity not in DEFAULTS or not isinstance(options,dict): raise ValueError('Unknown hardware or invalid options')
  defaults = DEFAULTS[identity][2]
  if set(options)-set(defaults): raise ValueError('Unknown hardware option')
  result = copy.deepcopy(defaults)
  for key,value in options.items():
   default = defaults[key]
   if isinstance(default,bool):
    if type(value) is not bool: raise ValueError(f'{key} must be boolean')
   elif isinstance(default,(float,int)):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value): raise ValueError(f'Invalid {key}')
    low,high = {'width':(160,1920),'height':(120,1080),'fps':(1,30),'baud':(9600,3000000),'address':(3,119),'yaw_deg':(-180,180)}.get(key,(-1000,1000))
    if not low<=value<=high: raise ValueError(f'{key} must be between {low} and {high}')
    if key in ('width','height','fps','baud','address') and int(value)!=value: raise ValueError(f'{key} must be an integer')
   else:
    if not isinstance(value,str) or len(value)>512 or any(ord(c)<32 for c in value): raise ValueError(f'Invalid {key}')
    if key in ('device','bus') and value and (not value.startswith('/dev/') or '..' in Path(value).parts): raise ValueError('Device must be an absolute /dev path')
    if key=='connection' and value not in ('usb','m2','pcie'): raise ValueError('Choose usb, m2 or pcie')
   result[key]=value
  return result

 def save(self, identity, options):
  validated=self.validate(identity,options)
  with self.lock:
   data=copy.deepcopy(self.options);data[identity]=validated
   self.path.parent.mkdir(parents=True,exist_ok=True)
   fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.hardware-')
   try:
    with os.fdopen(fd,'w') as out:
     os.fchmod(out.fileno(),0o600);json.dump(data,out,indent=2,allow_nan=False);out.flush();os.fsync(out.fileno())
    os.replace(name,self.path)
   finally:
    if os.path.exists(name):os.unlink(name)
   self.options=data
  return self.get()

 def get(self, robot=None, media=None, gamepad=None):
  robot,media=robot or {},media or {}
  gamepad = gamepad or {}
  gamepads = gamepad.get('devices') if 'devices' in gamepad else discover_gamepads()
  serial=sorted(str(p) for p in Path('/dev/serial/by-id').glob('*'))
  webcams=sorted(str(p) for p in Path('/dev/v4l/by-id').glob('*'))
  coral=[]
  for node in Path('/sys/bus/usb/devices').glob('*'):
   try:
    identifier=(node/'idVendor').read_text().strip()+':'+(node/'idProduct').read_text().strip()
    if identifier in ('1a6e:089a','18d1:9302'):coral.append(identifier)
   except OSError:pass
  detected={'servos':None,'front_camera':bool(media.get('camera',{}).get('available')),
            'ultrasonic':bool(robot.get('ultrasonic',{}).get('valid')),'power':bool(robot.get('power',{}).get('valid')),
            'microphone':bool(media.get('microphone',{}).get('available')),'speaker':bool(media.get('speaker',{}).get('available')),
            'rear_camera':bool(webcams),'coral':bool(coral),'lidar':None,'imu':None,'gamepad':bool(gamepads)}
  devices=[]
  with self.lock:
   for key,(name,kind,_,notes) in DEFAULTS.items():
    options=copy.deepcopy(self.options[key]);active=key not in ('rear_camera','lidar','coral','imu')
    if key=='gamepad' and 'enabled' in gamepad:
     options['enabled']=gamepad['enabled']
     notes+=' Use controller_set_enabled to change USB input; the setting is shared across clients.'
    devices.append({'id':key,'name':name,'kind':kind,'configured':bool(options.get('enabled')),
       'enabled':options['enabled'],'detected':detected[key],'options':options,
       'status':(('read-only input monitor; motion disabled' if options['enabled'] else 'USB input disabled; motion disabled') if key=='gamepad' else 'active driver' if active else 'configuration only; driver pending'),'notes':notes})
  mem={}
  try:
   mem={line.split(':')[0]:int(line.split()[1])//1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}
  except OSError:pass
  try:model=Path('/proc/device-tree/model').read_text().rstrip('\0')
  except OSError:model='Unknown host'
  return {'devices':devices,'host':{'model':model,'memory_total_mb':mem.get('MemTotal'),'memory_available_mb':mem.get('MemAvailable')},
          'discovered':{'serial_ports':serial,'usb_cameras':webcams,'coral_usb_ids':coral,'gamepads':gamepads},
          'configuration_effect':'Inventory/provisioning only. Saving never activates hardware, changes drivers, or grants microphone consent.'}
