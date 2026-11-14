"""Pi power telemetry is sampled from fake files and commands; no host writes."""
from types import SimpleNamespace
import time
import pytest
from pibotpal.power import PowerMonitor, decode_throttled


def test_throttle_bits_distinguish_current_from_historical():
    assert decode_throttled('throttled=0x50000\n') == {
        'under_voltage': False, 'under_voltage_occurred': True, 'throttled': False,
        'throttled_occurred': True, 'frequency_capped': False, 'firmware_flags': '0x50000'}
    state = decode_throttled('throttled=0x50007')
    assert state['under_voltage'] and state['throttled'] and state['frequency_capped']
    with pytest.raises(ValueError):
        decode_throttled('unavailable')


@pytest.fixture
def monitor(tmp_path):
    cpu = tmp_path / 'policy0'; cpu.mkdir()
    for key, value in {'scaling_cur_freq': '1500000', 'scaling_max_freq': '1500000',
                       'scaling_min_freq': '1500000', 'scaling_governor': 'ondemand'}.items():
        (cpu / key).write_text(value)
    thermal = tmp_path / 'temp'; thermal.write_text('43000')
    response = {'output': 'throttled=0x0', 'error': None, 'calls': 0}
    def run(command, **kwargs):
        assert command == ['/usr/bin/vcgencmd', 'get_throttled'] and kwargs['timeout'] == .5
        response['calls'] += 1
        if response['error']: raise response['error']
        return SimpleNamespace(stdout=response['output'])
    return PowerMonitor(cpu_root=tmp_path, thermal_file=thermal, runner=run), response


def test_frequency_readings_are_cached_and_sample_fault_generation(monitor):
    power, response = monitor
    state = power.sample()
    assert state['cpu'] == {'current_mhz': 1500, 'max_mhz': 1500, 'min_mhz': 1500, 'governor': 'ondemand'}
    assert state['temperature_c'] == 43 and state['available']
    for _ in range(20): power.status()
    assert response['calls'] == 1
    response['output'] = 'throttled=0x50005'
    assert power.sample()['fault_generation'] == 1
    assert power.sample()['fault_generation'] == 1
    response['output'] = 'throttled=0x50000'
    assert power.sample()['fault_generation'] == 1
    response['output'] = 'throttled=0x50005'
    assert power.sample()['fault_generation'] == 2


def test_past_fault_at_startup_does_not_become_new_motion_stop(monitor):
    power, response = monitor
    response['output'] = 'throttled=0x50000'
    assert power.sample()['fault_generation'] == 0
    assert power.status()['under_voltage_occurred']


def test_short_fault_latched_between_samples_is_counted(monitor):
    power, response = monitor
    power.sample()
    response['output'] = 'throttled=0x10000'
    state = power.sample()
    assert not state['under_voltage'] and state['fault_generation'] == 1


def test_failed_or_stale_telemetry_is_unknown_not_clear(monitor, monkeypatch):
    power, response = monitor
    power.sample()
    response['error'] = OSError('command unavailable')
    assert power.sample()['under_voltage'] is None
    assert not power.status()['available'] and power.status()['error']
    response['error'] = None
    state = power.sample()
    monkeypatch.setattr('pibotpal.power.time.time', lambda: state['timestamp'] + 4)
    assert power.status()['under_voltage'] is None and not power.status()['available']


def test_simulation_never_opens_host_devices():
    power = PowerMonitor(simulate=True, runner=lambda *a, **k: pytest.fail('Unexpected command'))
    power.start(); power.sample(); power.close()
    assert power.status()['under_voltage'] is None


# Reuse the simulated service fixture; no physical servo or audio devices open.
from tests.test_app import client, call


def test_power_tools_are_read_only_and_budget_saving_requires_disarm(client):
    state = call(client, 'power_get')['structuredContent']
    assert state['servos']['max_active_servos'] == 3 and not state['armed']
    budget = {'max_active_servos': 2, 'per_servo_deg_s': 20, 'total_deg_s': 40, 'settle_ms': 100}
    saved = call(client, 'servo_power_set', **budget)
    assert all(saved['structuredContent']['servos'][key] == value for key, value in budget.items())
    assert client.app.state.controller.status()['servo_angles'] == [None] * 12
    call(client, 'arm', armed=True)
    assert call(client, 'servo_power_set', **budget)['isError']


def test_current_undervoltage_blocks_arm_and_new_fault_stops_motion(client, monkeypatch):
    power = client.app.state.power_monitor
    original = power.status
    flags = {'under_voltage': True, 'fault_generation': 1}
    monkeypatch.setattr(power, 'status', lambda: {**original(), **flags})
    assert call(client, 'arm', armed=True)['isError']
    assert not client.app.state.controller.status()['armed']
    flags['under_voltage'] = False
    # Let the watchdog acknowledge the old fault while disarmed.
    time.sleep(1.1)
    assert call(client, 'arm', armed=True)['structuredContent']['armed']
    flags['fault_generation'] += 1  # brief event was latched between samples
    deadline = time.monotonic() + 2
    while client.app.state.controller.status()['armed'] and time.monotonic() < deadline:
        time.sleep(.02)
    assert not client.app.state.controller.status()['armed']
    state = call(client, 'power_get')['structuredContent']
    assert 'undervoltage' in state['last_motion_stop']['reason']
    assert client.app.state.controller.status()['servo_angles'] == [None] * 12


def test_disarm_and_save_validates_first_and_persists_sequence(client, tmp_path, monkeypatch):
    import json
    budget = {'max_active_servos': 4, 'per_servo_deg_s': 30, 'total_deg_s': 60,
              'settle_ms': 80, 'body_sequence': 'joints', 'disarm': True}
    spoken = []
    monkeypatch.setattr(client.app.state.media, 'announce', spoken.append)
    call(client, 'arm', armed=True)
    assert call(client, 'servo_power_set', **{**budget, 'body_sequence': 'invalid'})['isError']
    assert call(client, 'servo_power_set', **{**budget, 'max_active_servos': 12})['isError']
    assert client.app.state.controller.status()['armed']
    assert not (tmp_path / 'servo-power.json').exists()
    result = call(client, 'servo_power_set', **budget)['structuredContent']
    assert not result['armed'] and result['servos']['body_sequence'] == 'joints'
    assert result['servos']['max_active_servos'] == 4
    assert json.loads((tmp_path / 'servo-power.json').read_text())['body_sequence'] == 'joints'
    assert spoken == ['system armed', 'system disarmed']
    assert client.app.state.controller.status()['servo_angles'] == [None]*12


def test_omitted_body_sequence_preserves_saved_choice(client):
    budget = {'max_active_servos': 3, 'per_servo_deg_s': 30, 'total_deg_s': 60, 'settle_ms': 80}
    assert call(client, 'servo_power_set', **budget, body_sequence='legs')['structuredContent']['servos']['body_sequence'] == 'legs'
    assert call(client, 'servo_power_set', **budget)['structuredContent']['servos']['body_sequence'] == 'legs'


def test_disarm_and_save_rejects_busy_without_changing_permission(client, monkeypatch):
    controller = client.app.state.controller
    call(client, 'arm', armed=True)
    monkeypatch.setattr(controller, '_busy', True)
    budget = {'max_active_servos': 4, 'per_servo_deg_s': 30, 'total_deg_s': 60, 'settle_ms': 80}
    result = call(client, 'servo_power_set', **budget, disarm=True)
    assert result['isError'] and 'Stop the current movement' in str(result)
    assert controller.status()['armed'] and controller.servo_budget()['max_active_servos'] == 3
