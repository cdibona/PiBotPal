"""Mocked appearance, page navigation and responsive shell regression.

Run .venv/bin/python tests/browser/check_shell.py. No requests reach the robot.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    for width in (1440, 1024, 768, 390, 320):
        context = browser.new_context(viewport={'width': width, 'height': 1000}, color_scheme='dark')
        page = context.new_page(); errors = []; calls = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(route):
            path = route.request.url.split('robot.test', 1)[-1].split('?', 1)[0]
            if path == '/mcp':
                request = route.request.post_data_json
                if request['method'] == 'initialize':
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {}, 'serverInfo': {'name': 'mock', 'version': '1'}}
                else:
                    name = request['params']['name']; calls.append(name)
                    if name == 'get_status':
                        value = {'server_instance_id': 'mock', 'armed': False, 'busy': False, 'servo_angles': [None]*12, 'ultrasonic': {'valid': False}, 'camera': {'enabled': False}, 'cameras': [], 'microphone': {'enabled': False}, 'speaker': {}, 'recordings': [], 'plan': {'running': False}, 'examples': {}}
                    elif name == 'list_examples': value = []
                    elif name == 'admin_get': value = {'providers': [], 'connections': [], 'defaults': {}, 'server': {}}
                    elif name == 'hardware_get': value = {'host': {'model': 'Raspberry Pi'}, 'devices': []}
                    elif name == 'plan_list': value = {'poses': [], 'plans': [], 'execution': {'running': False}}
                    elif name == 'camera_list': value = {'cameras': [], 'primary_camera_id': None}
                    else: raise AssertionError('Unexpected tool '+name)
                    result = {'content': [{'type': 'text', 'text': json.dumps(value)}]}
                route.fulfill(content_type='application/json', body=json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}))
            elif path.startswith('/static/'):
                file = ROOT/'pibotpal'/path.lstrip('/')
                route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
            elif path == '/': route.fulfill(path=str(ROOT/'pibotpal/static/index.html'))
            else: route.fulfill(status=404)
        context.route('http://robot.test/**', route)
        page.goto('http://robot.test/#apps', wait_until='domcontentloaded')
        page.locator('#apps').wait_for(state='visible')
        assert page.locator('html').get_attribute('data-appearance') == 'auto'
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        assert page.locator('.power-panel').is_hidden()
        assert page.locator('.app-download').count() == 2
        # Default Auto follows OS changes while the page stays open.
        page.emulate_media(color_scheme='light')
        page.wait_for_function("document.documentElement.dataset.theme==='light'")
        # Explicit Dark persists across reload and ignores OS changes.
        page.locator('#theme-toggle').click()
        page.get_by_role('menuitemradio', name='Dark', exact=True).click()
        page.reload(wait_until='domcontentloaded')
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        assert page.locator('html').get_attribute('data-appearance') == 'dark'
        for theme in ('Light', 'Dark'):
            page.locator('#theme-toggle').click()
            page.get_by_role('menuitemradio', name=theme, exact=True).click()
            assert page.locator('#theme-toggle').get_attribute('aria-expanded') == 'false'
            assert page.locator('html').get_attribute('data-theme') == theme.lower()
            # Readable footer in both palettes, measured against its actual canvas.
            footer = page.locator('footer').evaluate('''el => {
                const s=getComputedStyle(el);return {size:parseFloat(s.fontSize),fg:s.color,bg:getComputedStyle(document.body).backgroundColor};
            }''')
            def luminance(rgb):
                channels = [int(v.strip())/255 for v in rgb.removeprefix('rgb(').removesuffix(')').split(',')]
                channels = [v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
                return sum(a*b for a,b in zip(channels, [.2126,.7152,.0722]))
            fg, bg = luminance(footer['fg']), luminance(footer['bg'])
            assert footer['size'] >= 13 and (max(fg,bg)+.05)/(min(fg,bg)+.05) >= 4.5, footer
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width,theme)
        # Escape closes the menu and restores focus. Arrow keys select a menu item.
        page.locator('#theme-toggle').press('ArrowDown')
        page.get_by_role('menuitemradio', name='Dark', exact=True).press('Home')
        assert page.get_by_role('menuitemradio', name='Auto', exact=True).evaluate('(el)=>el===document.activeElement')
        page.get_by_role('menuitemradio', name='Auto', exact=True).press('Escape')
        assert page.locator('#theme-toggle').evaluate('(el)=>el===document.activeElement')
        for name in ('dashboard', 'plan', 'testing', 'admin'):
            page.locator(f'[data-tab={name}]').click()
            assert page.locator('#apps').is_hidden(), name
            assert page.locator(f'#{name}').is_visible(), name
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width,name)
        for control in ('#arm', '#stop', '#theme-toggle'):
            box = page.locator(control).bounding_box()
            assert box and box['x']>=0 and box['x']+box['width']<=width, (width,control,box)
        if width in (1440, 390):
            page.screenshot(path=str(ROOT/f'artifacts/shell-dark-{width}.png'),full_page=True)
            page.locator('#theme-toggle').click();page.get_by_role('menuitemradio',name='Light',exact=True).click()
            page.screenshot(path=str(ROOT/f'artifacts/shell-light-{width}.png'),full_page=True)
        assert not errors, errors
        assert not ({'arm','move','set_servo','set_leg','microphone','camera','camera_configure','plan_run'} & set(calls))
        print(f'Shell passed: {width}px; appearance/OS/persistence/keyboard, Apps routing, readable footer; no actuation')
        context.close()
    browser.close()
