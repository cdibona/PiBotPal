"""Shared action and routine editor browser checks. All robot traffic is mocked.

Run: .venv/bin/python tests/browser/check_planner.py
"""
import copy
import json
from pathlib import Path
import sys
import tempfile

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pibotpal.actions import ActionStore

HTML = '''<!doctype html><html data-theme="dark"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/shell.css"><link rel="stylesheet" href="/static/planner.css"></head><body><main><section id="plan"></section></main><script type="module">import {initPlans} from '/static/plans.js';const call=async(name,args={})=>{const r=await fetch('/mock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,args})});const data=await r.json();if(data.error)throw new Error(data.error);return data;};window.robot={armed:false,busy:false,servo_angles:Array(12).fill(null),servo_budget:{per_servo_deg_s:30}};window.changedCatalog=0;window.planner=initPlans({call,getStatus:()=>window.robot,onActionsChanged:()=>{window.changedCatalog++}});await window.planner.refresh();window.planner.update(window.robot);</script></body></html>'''

with tempfile.TemporaryDirectory(prefix='pibotpal-planner-browser-') as directory, sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    for fixture, width in (('isolated', 1440), ('isolated', 390), ('full', 1440), ('full', 390)):
        store = ActionStore(Path(directory) / f'{fixture}-{width}.json')
        state = {'server_instance_id': 'mock', 'armed': False, 'busy': False, 'servo_angles': [None]*12, 'servo_budget': {'per_servo_deg_s': 30}, 'ultrasonic': {'valid': False}, 'camera': {'enabled': False}, 'cameras': [], 'microphone': {'enabled': False}, 'speaker': {}, 'recordings': [], 'plan': {'running': False}, 'examples': {}, 'gamepad': {'enabled': True, 'armed': False, 'attached': False, 'connected': False}}
        requests, plans, poses, errors = [], [], [], []
        failures = {'save': False}
        page = browser.new_page(viewport={'width': width, 'height': 1050})
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(route):
            path = route.request.url.removeprefix('http://planner.test').split('?', 1)[0]
            if path == '/':
                route.fulfill(path=str(ROOT / 'pibotpal/static/index.html')) if fixture == 'full' else route.fulfill(content_type='text/html', body=HTML)
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
                requests.append(copy.deepcopy(request))
                name, args = request['name'], request.get('args', {})
                if name == 'plan_list': result = {'poses': poses, 'plans': plans, 'actions': store.catalog()['actions'], 'action_limits': store.catalog()['limits'], 'execution': state['plan']}
                elif name == 'action_list': result = store.catalog()
                elif name in ('action_save', 'action_reset'):
                    assert not state['busy'], 'UI saved while motion was active'
                    assert not state['armed'] or args.get('disarm'), 'Armed save needs an explicit disarm flag'
                    if failures['save']: result = {'error': 'Storage temporarily unavailable'}
                    else:
                        if args.get('disarm'): state['armed'] = False
                        values = {k: v for k, v in args.items() if k != 'disarm'}
                        result = store.save(**values) if name == 'action_save' else store.reset(args['name'])
                elif name == 'action_delete':
                    assert not state['armed'] and not state['busy']
                    store.delete(args['name']); result = {'deleted': args['name']}
                elif name == 'move':
                    assert state['armed'] and not state['busy']
                    assert args == {'name': 'stand', 'speed': 80, 'steps': 1}
                    result = {'started': True}
                elif name == 'plan_save':
                    result = {**args, 'id': args.get('plan_id', 'routine-' + str(len(plans) + 1))}
                    plans[:] = [plan for plan in plans if plan['id'] != result['id']] + [result]
                elif name == 'plan_run':
                    assert state['armed']
                    result = {'running': True, 'plan_name': plans[-1]['name'], 'total_steps': len(plans[-1]['steps']), 'step_index': 0}
                elif name == 'get_status': result = copy.deepcopy(state)
                elif name == 'list_examples': result = []
                elif name == 'admin_get': result = {'providers': [], 'connections': [], 'defaults': {}, 'server': {}}
                elif name == 'hardware_get': result = {'host': {'model': 'Raspberry Pi'}, 'devices': []}
                elif name == 'camera_list': result = {'cameras': [], 'primary_camera_id': None}
                elif name == 'gamepad_get': result = state['gamepad']
                else: raise AssertionError('Unexpected tool call: ' + name)
                if rpc_id is not None:
                    failed = isinstance(result, dict) and 'error' in result
                    result = {'jsonrpc': '2.0', 'id': rpc_id, 'result': {'isError': failed, 'content': [{'type': 'text', 'text': result['error'] if failed else json.dumps(result)}]}}
                route.fulfill(content_type='application/json', body=json.dumps(result))
            else: raise AssertionError('Unexpected network destination: ' + route.request.url)
        page.route('**/*', route)
        page.goto('http://planner.test/#plan', wait_until='domcontentloaded')
        expect(page.locator('#action-count')).to_have_text('16 ACTIONS', timeout=30000)
        expect(page.locator('#action-editor-title')).to_have_text('Stand')
        expect(page.locator('#frame-capture')).to_be_disabled()
        expect(page.locator('#action-run')).to_be_disabled()
        assert page.locator('#frame-angle-0').input_value() != '0', 'Unknown position must never create zero-angle targets'

        def sync(**changes):
            state.update(changes)
            if fixture == 'isolated': page.evaluate('(s)=>{window.robot=s;window.planner.update(s)}', state)
            else: page.wait_for_timeout(650)

        # Edits, preview, library refresh and same-action selection must not move,
        # save prematurely, or replace an unsaved draft.
        page.locator('#frame-speed').fill('17')
        page.locator('#frame-hold').fill('0.4')
        page.locator('#frame-angle-0').fill('-40')
        page.locator('[data-action="stand"]').click()
        expect(page.locator('#frame-speed')).to_have_value('17')
        page.locator('#plan-refresh').click()
        expect(page.locator('#frame-speed')).to_have_value('17')
        page.locator('#frame-duplicate').click()
        expect(page.locator('.keyframe-card')).to_have_count(2)
        page.locator('#frame-speed').fill('23')
        page.locator('#frame-left').click()
        expect(page.locator('#frame-editor-title')).to_contain_text('Frame 1')
        expect(page.locator('#frame-speed')).to_have_value('23')
        page.locator('#frame-right').click()
        page.locator('#action-preview').click()
        page.wait_for_timeout(150)
        page.locator('#action-scrub').fill('0.5')
        expect(page.locator('#action-preview-detail')).to_contain_text('Preview frame 1.50')
        page.locator('#frame-remove').click()
        expect(page.locator('.keyframe-card')).to_have_count(1)
        expect(page.locator('#frame-speed')).to_have_value('17')
        assert not any(r['name'] in ('move', 'action_save', 'plan_run') for r in requests)
        sync(busy=True)
        expect(page.locator('#action-save')).to_be_disabled()
        sync(busy=False, armed=True)
        expect(page.locator('#action-save')).to_have_text('Disarm and save action')
        page.locator('#frame-speed').fill('145')
        expect(page.locator('#action-save')).to_be_disabled()
        expect(page.locator('#action-validation')).to_contain_text('1–144')
        page.locator('#frame-speed').fill('17')
        page.locator('#frame-angle-1').fill('-11')
        expect(page.locator('#action-save')).to_be_disabled()
        expect(page.locator('#action-validation')).to_contain_text('Shoulder')
        page.locator('#frame-angle-1').fill('45')
        failures['save'] = True
        page.locator('#action-save').click()
        expect(page.locator('#action-notice')).to_have_text('Storage temporarily unavailable')
        expect(page.locator('#frame-speed')).to_have_value('17')
        failures['save'] = False
        page.locator('#action-save').click()
        expect(page.locator('#action-notice')).to_contain_text('Action saved')
        assert not state['armed']
        assert store.get('stand')['frames'][0]['speed_deg_s'] == 17
        assert store.get('stand')['frames'][0]['angles'][:2] == [-40, 45]
        assert all(r['args'].get('disarm') for r in requests if r['name'] == 'action_save')
        sync(armed=True)
        expect(page.locator('#action-run')).to_be_enabled()
        page.locator('#action-run').click()
        expect(page.locator('#action-notice')).to_contain_text('Saved action started')
        assert len([r for r in requests if r['name'] == 'move']) == 1
        sync(armed=False)

        # Wave and gait definitions are real library frames, editable like Stand.
        page.locator('[data-action="wave"]').click()
        assert page.locator('.keyframe-card').count() > 1
        page.locator('[data-action="forward"]').click()
        expect(page.locator('#action-playback')).to_have_value('continuous')
        page.locator('#action-playback').select_option('keyframes')
        expect(page.locator('#action-edit-state')).to_have_text('UNSAVED CHANGES')
        page.locator('#action-discard').click()
        expect(page.locator('#action-playback')).to_have_value('continuous')

        # Create a genuinely new action, without ever claiming measured positions.
        page.locator('#action-new').click()
        expect(page.locator('#action-notice')).to_contain_text('saved Stand keyframe')
        page.locator('#action-name').fill('my greeting')
        page.locator('#frame-add').click()
        page.locator('#frame-speed').fill('21')
        page.locator('#frame-angle-0').fill('-32')
        page.locator('#action-save').click()
        expect(page.locator('#action-notice')).to_contain_text('Action saved')
        expect(page.locator('[data-action="my greeting"]')).to_be_visible()
        assert len(store.get('my greeting')['frames']) == 2
        if fixture == 'full': expect(page.locator('#dashboard-action option[value="my greeting"]')).to_have_count(1)
        else: assert page.evaluate('window.changedCatalog') == 2
        page.locator('#action-copy').click()
        page.locator('#action-name').fill('temporary action')
        page.locator('#action-save').click()
        expect(page.locator('#action-notice')).to_contain_text('Action saved')
        sync(servo_angles=[-31, 44, 2]*4)
        page.locator('#frame-capture').click()
        expect(page.locator('#frame-angle-0')).to_have_value('-31')
        page.locator('#action-discard').click()
        page.locator('#action-delete').click()
        expect(page.locator('#action-notice')).to_have_text('Custom action deleted.')
        assert 'temporary action' not in store.names()
        page.locator('#action-reset').click()
        expect(page.locator('#action-notice')).to_have_text('Built-in action restored.')
        assert store.get('stand')['modified'] is False

        # New plan must save a new routine and allow custom shared actions.
        page.locator('#planner-routines-tab').click()
        page.locator('#plan-new').click()
        page.locator('#plan-name').fill('A greeting and a walk')
        page.locator('#plan-add-action').click()
        page.locator('#plan-steps select').select_option('my greeting')
        page.locator('#plan-steps input').nth(0).fill('2')
        page.locator('#plan-steps input').nth(1).fill('50')
        page.locator('#plan-add-wait').click()
        page.locator('#plan-steps input').last.fill('2.5')
        page.locator('#plan-editor button[type="submit"]').click()
        expect(page.locator('#plan-notice')).to_contain_text('Plan saved')
        assert plans[-1]['steps'] == [{'type': 'action', 'name': 'my greeting', 'steps': 2, 'speed': 50}, {'type': 'wait', 'seconds': 2.5}]
        # Saving refreshes the action library while its panel is hidden. On
        # returning, the preview must use its actual visible width again.
        page.locator('#planner-actions-tab').click()
        expect(page.locator('#action-pose-preview')).to_have_attribute('viewBox', '0 0 680 190' if width == 1440 else '0 0 340 380')
        page.locator('#planner-routines-tab').click()
        expect(page.locator('#plan-run')).to_be_disabled()
        sync(armed=True)
        page.locator('#plan-run').click()
        expect(page.locator('#plan-notice')).to_contain_text('Saved plan started')
        assert len([r for r in requests if r['name'] == 'plan_run']) == 1
        sync(armed=False)
        page.locator('#plan-new').click()
        expect(page.locator('#plan-name')).to_have_value('')
        expect(page.locator('.plan-step')).to_have_count(0)
        page.locator('#planner-actions-tab').click()
        page.locator('[data-action="my greeting"]').click()
        page.locator('#action-add-to-plan').click()
        expect(page.locator('#planner-routines')).to_be_visible()
        expect(page.locator('#plan-steps select')).to_have_value('my greeting')
        assert not errors, errors
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'Horizontal page overflow: {fixture} {width}'
        page.locator('#planner-actions-tab').click()
        page.locator('[data-action="wave"]').click()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=f'/tmp/pibotpal-planner-{fixture}-{width}.png', full_page=True)
        page.evaluate("document.documentElement.dataset.theme='light'")
        page.screenshot(path=f'/tmp/pibotpal-planner-{fixture}-{width}-light.png', full_page=True)
        page.close()
        print(f'PASS planner {fixture} {width}px: shared actions, frames, save gates, preview, new action, new routine')
    browser.close()
