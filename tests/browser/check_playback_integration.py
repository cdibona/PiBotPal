"""Browser-to-MCP playback integration on an isolated simulated controller.

Run .venv/bin/python tests/browser/check_playback_integration.py.
Every browser request is intercepted; no real hardware or service is contacted.
"""
from pathlib import Path
import sys,tempfile,json,logging
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root))
logging.getLogger("httpx").setLevel(logging.WARNING)
from pytest import MonkeyPatch
from tests.test_controller_app import client as fixture,call,HEADERS
from playwright.sync_api import sync_playwright,expect
with tempfile.TemporaryDirectory() as directory,MonkeyPatch.context() as mp:
 gen=fixture.__wrapped__(Path(directory),mp);client=next(gen)
 try:
  robot=client.app.state.controller
  assert robot.simulate
  frame=robot.actions.get('stand')['frames'][0]
  second=json.loads(json.dumps(frame));second['angles'][0]+=1
  action=call(client,'action_save',name='editor playback check',frames=[frame,second])['value']
  plan=call(client,'plan_save',name='Editor playback check',steps=[{'type':'action','name':action['name']},{'type':'wait','seconds':1}])['value']
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
   page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   def route(route):
    path=route.request.url.removeprefix('http://editor.test').split('?',1)[0]
    if path=='/mcp':
     r=client.post('/mcp',headers=HEADERS,json=route.request.post_data_json)
     route.fulfill(status=r.status_code,content_type='application/json',body=r.content)
    elif path=='/':route.fulfill(path=str(root/'pibotpal/static/index.html'))
    elif path.startswith('/static/'):route.fulfill(path=str(root/'pibotpal'/path.lstrip('/')))
    else:route.fulfill(status=404)
   page.route('http://editor.test/**',route)
   page.goto('http://editor.test/#plan',wait_until='domcontentloaded')
   page.locator('[data-action="editor playback check"]').click(timeout=30000)
   expect(page.locator('#action-run')).to_contain_text('Arm & play')
   page.locator('#action-run').click()
   expect(page.locator('#action-progress')).to_contain_text('playing',timeout=10000)
   expect(page.locator('#action-progress')).to_contain_text('complete',timeout=20000)
   assert robot.status()['servo_angles']==second['angles']
   page.locator('#action-stop').click()
   expect(page.locator('#action-transport-notice')).to_contain_text('stopped')
   assert not robot.status()['armed']
   page.locator('#planner-routines-tab').click()
   page.locator('#plan-library button').filter(has_text='Editor playback check').click()
   page.locator('#plan-run').click()
   expect(page.locator('#plan-state')).to_have_text('RUNNING')
   expect(page.locator('#plan-state')).to_have_text('COMPLETE',timeout=15000)
   assert not robot.status()['armed']
   page.locator('#plan-run').click()
   expect(page.locator('#plan-state')).to_have_text('RUNNING')
   page.locator('#plan-stop').click()
   expect(page.locator('#plan-state')).to_have_text('CANCELLED')
   assert not robot.status()['armed'] and not robot.status()['busy']
   page.screenshot(path='/tmp/pibotpal-routine-playback-1440.png',full_page=True)
   assert not errors,errors
   browser.close()
  assert robot._hat is None and robot._servos == []
  print('PASS real MCP + browser on simulated controller: action arm/play/complete/stop, routine arm/play/complete/replay/stop; no physical hardware.',flush=True)
 finally:
  try:next(gen)
  except StopIteration:pass
