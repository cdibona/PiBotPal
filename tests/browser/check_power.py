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
    for width in (1440, 390):
        state = {'host': {'cpu': {'current_mhz': 900, 'max_mhz': 1500, 'min_mhz': 600, 'governor': 'schedutil'}, 'under_voltage': False, 'under_voltage_occurred': True, 'throttled': False, 'throttled_occurred': True, 'temperature_c': 48.5, 'available': True, 'error': None, 'timestamp': time.time()}, 'servos': {'max_active_servos': 3, 'per_servo_deg_s': 30, 'total_deg_s': 60, 'settle_ms': 80}, 'armed': False, 'busy': False}
        requests = []
        failures = {'read': False, 'save': False}
        page = browser.new_page(viewport={'width': width, 'height': 1050})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(route):
            path = route.request.url.removeprefix('http://power.test').split('?', 1)[0]
            if path == '/':
                route.fulfill(content_type='text/html', body=HTML)
            elif path.startswith('/static/'):
                file = ROOT / 'pibotpal' / path.lstrip('/')
                route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
            elif path == '/mock':
                request = route.request.post_data_json
                requests.append(request)
                name = request['name']
                if name == 'power_get':
                    result = {'error': 'Host telemetry disconnected'} if failures['read'] else copy.deepcopy(state)
                elif name == 'servo_power_set':
                    assert not state['armed'] and not state['busy'], 'UI saved while motion was armed or busy'
                    if failures['save']:
                        result = {'error': 'Disarm before saving'}
                    else:
                        state['servos'] = request['args']
                        result = copy.deepcopy(state)
                else:
                    raise AssertionError('Unexpected tool call: ' + name)
                route.fulfill(content_type='application/json', body=json.dumps(result))
            else:
                raise AssertionError('Unexpected network destination: ' + route.request.url)
        page.route('**/*', route)
        page.goto('http://power.test/', wait_until='domcontentloaded')
        expect(page.locator('#power-cpu-max')).to_have_text('1500 MHz')
        expect(page.locator('#power-undervoltage-now')).to_have_text('Clear')
        expect(page.locator('#power-undervoltage-history')).to_have_text('Recorded')
        expect(page.locator('#servo-power-save')).to_be_disabled()
        page.locator('#servo-power-count').fill('2')
        page.locator('#servo-power-per-servo').fill('25')
        expect(page.locator('#servo-power-save')).to_be_enabled()
        page.locator('#hardware-power-refresh').click()
        page.wait_for_timeout(100)
        assert page.locator('#servo-power-count').input_value() == '2', 'Refresh lost an unsaved setting'
        state['armed'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save')).to_be_disabled()
        expect(page.locator('#servo-power-save-hint')).to_contain_text('Disarm')
        assert page.locator('#servo-power-count').is_enabled(), 'Armed state must not prevent editing'
        state['armed'] = False
        state['busy'] = True
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save-hint')).to_contain_text('Wait for')
        state['busy'] = False
        page.locator('#hardware-power-refresh').click()
        expect(page.locator('#servo-power-save')).to_be_enabled()
        page.locator('#servo-power-count').fill('7')
        expect(page.locator('#servo-power-save')).to_be_disabled()
        page.locator('#servo-power-count').fill('2')
        page.locator('#servo-power-save').click()
        expect(page.locator('#hardware-power-notice')).to_have_text('Servo command limits saved.')
        assert state['servos'] == {'max_active_servos': 2, 'per_servo_deg_s': 25, 'total_deg_s': 60, 'settle_ms': 80}
        expect(page.locator('#servo-power-save')).to_be_disabled()
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
            page.screenshot(path=str(ROOT / f'artifacts/power-admin-{theme}-{width}.png'), full_page=True)
        # The Hardware pane owns polling; leaving it must stop new requests.
        page.evaluate('window.powerAdmin.setActive(false)')
        page.wait_for_timeout(150)
        count = len(requests)
        page.wait_for_timeout(2200)
        assert len(requests) == count, 'Power telemetry polled while Hardware was inactive'
        page.evaluate('window.powerAdmin.setActive(true)')
        expect(page.locator('#hardware-power-state')).to_have_text('POWER WARNING')
        assert len(requests) > count
        assert not errors, errors
        assert {r['name'] for r in requests} == {'power_get', 'servo_power_set'}
        print(f'Power admin browser checks passed: {width}px, dark/light, no actuation calls')
        page.close()
    browser.close()
