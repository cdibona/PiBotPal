"""Power admin browser regression with mocked MCP. No hardware/network access.

Run: .venv/bin/python tests/browser/check_power.py
"""
import copy
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
HTML = '''<!doctype html><html data-theme="dark"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/shell.css"><link rel="stylesheet" href="/static/power-admin.css"></head><body><main id="fixture"></main><script type="module">import {initPowerAdmin} from '/static/power-admin.js';const call=async(name,args)=>{const r=await fetch('/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,args})});const data=await r.json();if(data.error)throw new Error(data.error);return data;};window.powerAdmin=initPowerAdmin({call,root:document.getElementById('fixture')});window.powerAdmin.setActive(true);</script></body></html>'''

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    for fixture, width in (('isolated', 1440), ('isolated', 390), ('full', 1440), ('full', 390)):
        state = {'host': {'cpu': {'current_mhz': 900, 'max_mhz': 1500, 'min_mhz': 600, 'governor': 'schedutil'}, 'under_voltage': False, 'under_voltage_occurred': True, 'throttled': False, 'throttled_occurred': True, 'temperature_c': 48.5, 'available': True, 'error': None, 'timestamp': time.time()}, 'servos': {'max_active_servos': 3, 'per_servo_deg_s': 30, 'total_deg_s': 60, 'settle_ms': 80, 'body_sequence': 'joints'}, 'armed': False, 'busy': False}
        requests = []
        failures = {'read': False, 'save': False}
        page = browser.new_page(viewport={'width': width, 'height': 1050})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(route):
            path = route.request.url.removeprefix('http://power.test').split('?', 1)[0]
            if path == '/':
                if fixture == 'full': route.fulfill(path=str(ROOT / 'pibotpal/static/index.html'))
                else: route.fulfill(content_type='text/html', body=HTML)
            elif path.startswith('/static/'):
                file = ROOT / 'pibotpal' / path.lstrip('/')
                route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
            elif path in ('/mock', '/mcp'):
                request = route.request.post_data_json
                rpc_id = None
                if path == '/mcp':
                    rpc_id = request['id']
                    if request['method'] == 'initialize':
                        route.fulfill(content_type='application/json', body=json.dumps({'jsonrpc': '2.0', 'id': rpc_id, 'result': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'serverInfo': {'name': 'mock', 'version': '1'}}}))
                        return
                    request = {'name': request['params']['name'], 'args': request['params']['arguments']}
                requests.append(request)
                name = request['name']
                if name == 'power_get':
                    result = {'error': 'Host telemetry disconnected'} if failures['read'] else copy.deepcopy(state)
                elif name == 'servo_power_set':
                    args = request['args'].copy()
                    disarm = args.pop('disarm', False)
                    assert not state['busy'], 'UI saved while motion was busy'
                    assert not state['armed'] or disarm, 'UI did not explicitly request disarming before saving'
                    if failures['save']:
                        result = {'error': 'Disarm before saving'}
                    else:
                        if disarm: state['armed'] = False
                        state['servos'] = args
                        result = copy.deepcopy(state)
                elif name == 'get_status':
                    result = {'server_instance_id': 'mock', 'armed': state['armed'], 'busy': state['busy'], 'servo_angles': [None]*12, 'ultrasonic': {'valid': False}, 'camera': {'enabled': False}, 'cameras': [], 'microphone': {'enabled': False}, 'speaker': {}, 'recordings': [], 'plan': {'running': False}, 'examples': {}, 'gamepad': {'enabled': True, 'armed': False, 'attached': False, 'connected': False}}
                elif name == 'list_examples': result = []
                elif name == 'admin_get': result = {'providers': [], 'connections': [], 'defaults': {}, 'server': {}}
                elif name == 'hardware_get': result = {'host': {'model': 'Raspberry Pi'}, 'devices': []}
                elif name == 'plan_list': result = {'poses': [], 'plans': [], 'execution': {'running': False}}
                elif name == 'camera_list': result = {'cameras': [], 'primary_camera_id': None}
                else:
                    raise AssertionError('Unexpected tool call: ' + name)
                if rpc_id is not None:
                    failed = isinstance(result, dict) and 'error' in result
                    result = {'jsonrpc': '2.0', 'id': rpc_id, 'result': {'isError': failed, 'content': [{'type': 'text', 'text': result['error'] if failed else json.dumps(result)}]}}
                route.fulfill(content_type='application/json', body=json.dumps(result))
            else:
                raise AssertionError('Unexpected network destination: ' + route.request.url)
        page.route('**/*', route)
        page.goto('http://power.test/#admin/hardware', wait_until='domcontentloaded')
        expect(page.locator('#power-cpu-max')).to_have_text('1500 MHz')
        expect(page.locator('#power-undervoltage-now')).to_have_text('Clear')
        expect(page.locator('#power-undervoltage-history')).to_have_text('Recorded')
        expect(page.locator('#servo-power-save')).to_be_disabled()
        expect(page.locator('#servo-power-save-hint')).to_have_text('Saved. Change a setting to enable saving.')
        expect(page.locator('#servo-power-sequence')).to_have_value('joints')
        expect(page.locator('#servo-power-sequence-hint')).to_contain_text('3 + 1')
        page.locator('#servo-power-count').fill('2')
        expect(page.locator('#servo-power-sequence-hint')).to_contain_text('2 + 2')
        page.locator('#servo-power-per-servo').fill('25')
        expect(page.locator('#servo-power-save')).to_be_enabled()
        page.locator('#hardware-power-refresh').click()
        page.wait_for_timeout(100)
        assert page.locator('#servo-power-count').input_value() == '2', 'Refresh lost an unsaved setting'
        state['armed'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save')).to_be_enabled()
        expect(page.locator('#servo-power-save')).to_have_text('Disarm and save')
        expect(page.locator('#servo-power-save-hint')).to_contain_text('Saving will disarm')
        assert not any(r['name'] == 'servo_power_set' for r in requests), 'Editing or polling must not disarm or save'
        assert page.locator('#servo-power-count').is_enabled(), 'Armed state must not prevent editing'
        state['armed'] = False
        state['busy'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save-hint')).to_contain_text('Stop the current movement')
        expect(page.locator('#servo-power-save')).to_be_disabled()
        state['busy'] = False
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save')).to_be_enabled()
        page.locator('#servo-power-count').fill('7')
        expect(page.locator('#servo-power-save')).to_be_disabled()
        expect(page.locator('#servo-power-save-hint')).to_have_text('Servos commanded together must be a whole number from 1 to 6.')
        page.locator('#servo-power-count').fill('2')
        state['armed'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save')).to_have_text('Disarm and save')
        page.locator('#servo-power-save').click()
        expect(page.locator('#hardware-power-notice')).to_have_text('Servo command limits saved.')
        expect(page.locator('#hardware-power-notice')).to_be_in_viewport()
        assert not state['armed']
        assert state['servos'] == {'max_active_servos': 2, 'per_servo_deg_s': 25, 'total_deg_s': 60, 'settle_ms': 80, 'body_sequence': 'joints'}
        assert next(r for r in requests if r['name'] == 'servo_power_set')['args']['disarm'] is True
        expect(page.locator('#servo-power-save')).to_be_disabled()
        # A change event from a select must independently enable saving, and
        # the sequence remains a string in the shared MCP payload.
        page.locator('#servo-power-sequence').evaluate("select => { select.value = 'legs'; select.dispatchEvent(new Event('change', {bubbles: true})); }")
        expect(page.locator('#servo-power-sequence-hint')).to_contain_text('Complete one leg')
        expect(page.locator('#servo-power-save')).to_be_enabled()
        page.locator('#servo-power-save').click()
        expect(page.locator('#servo-power-save')).to_be_disabled()
        assert state['servos']['body_sequence'] == 'legs'
        # Polls preserve deliberate edits; dropping the connection blocks a save.
        page.locator('#servo-power-total').fill('45')
        failures['read'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#hardware-power-state')).to_have_text('UNAVAILABLE')
        expect(page.locator('#servo-power-save')).to_be_disabled()
        assert page.locator('#servo-power-total').input_value() == '45'
        expect(page.locator('#power-undervoltage-now')).to_have_text('Unknown')
        failures['read'] = False
        state['host']['under_voltage'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#hardware-power-state')).to_have_text('POWER WARNING')
        expect(page.locator('#power-undervoltage-now')).to_have_text('Detected')
        expect(page.locator('#servo-power-save')).to_be_enabled()
        page.locator('#servo-power-reset').click()
        assert page.locator('#servo-power-total').input_value() == '60'
        expect(page.locator('#servo-power-save')).to_be_disabled()
        # Unknown telemetry is never shown as LIVE, and actionable server errors
        # explain why the robot stopped or cannot arm with stored settings.
        state['host'].update(under_voltage=None, throttled=None, available=False,
                             error='Pi power telemetry is stale')
        state['last_motion_stop'] = {'reason': 'Pi undervoltage detected'}
        state['servos']['error'] = 'Invalid saved budget'
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#hardware-power-state')).to_have_text('PARTIAL TELEMETRY')
        expect(page.locator('#power-undervoltage-now')).to_have_text('Unknown')
        expect(page.locator('#power-host-error')).to_contain_text('stale')
        expect(page.locator('#power-last-stop')).to_contain_text('Pi undervoltage detected')
        expect(page.locator('#servo-power-error')).to_contain_text('Save valid limits')
        expect(page.locator('#servo-power-save')).to_be_enabled()
        expect(page.locator('#servo-power-save-hint')).to_contain_text('replace the invalid stored limits')
        # A save rejected by a racing arm action retains the edited values.
        page.locator('#servo-power-total').fill('45')
        failures['save'] = True
        page.locator('#servo-power-save').click()
        expect(page.locator('#hardware-power-notice')).to_have_text('Disarm before saving')
        expect(page.locator('#hardware-power-notice')).to_be_in_viewport()
        assert page.locator('#servo-power-total').input_value() == '45'
        failures['save'] = False
        state['servos'].pop('error')
        state['host'].update(under_voltage=True, throttled=False, available=True, error=None)
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#hardware-power-state')).to_have_text('POWER WARNING')
        page.locator('#servo-power-reset').click()
        for theme in ('dark', 'light'):
            page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{width}px overflow'
            page.screenshot(path=str(ROOT / f'artifacts/power-admin-{fixture}-{theme}-{width}.png'), full_page=True)
        # The Hardware pane owns polling; leaving it must stop new requests.
        if fixture == 'full': page.get_by_role('tab', name='Models', exact=True).click()
        else: page.evaluate('window.powerAdmin.setActive(false)')
        page.wait_for_timeout(150)
        count = sum(r['name'] == 'power_get' for r in requests)
        page.wait_for_timeout(2200)
        assert sum(r['name'] == 'power_get' for r in requests) == count, 'Power telemetry polled while Hardware was inactive'
        if fixture == 'full': page.get_by_role('tab', name='Hardware', exact=True).click()
        else: page.evaluate('window.powerAdmin.setActive(true)')
        expect(page.locator('#hardware-power-state')).to_have_text('POWER WARNING')
        assert sum(r['name'] == 'power_get' for r in requests) > count
        assert not errors, errors
        assert {r['name'] for r in requests} <= {'power_get', 'servo_power_set', 'get_status', 'list_examples', 'admin_get', 'hardware_get', 'plan_list', 'camera_list'}
        print(f'Power admin browser checks passed: {fixture}, {width}px, dark/light, no actuation calls')
        page.close()
    browser.close()
