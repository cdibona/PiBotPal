"""Mocked controller streaming, tap visibility, mapping and navigation checks."""
import copy
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
inputs=[{'id':'button:'+name,'label':label,'kind':'button'} for name,label in [('South','A / × (South)'),('East','B / ○ (East)'),('West','X / □ (West)'),('North','Y / △ (North)'),('Select','Select'),('Start','Start')]]
inputs += [{'id':f'axis:{axis}:{sign}','label':f'{axis} {sign}','kind':'axis'} for axis in ['X','Y','Hat X','Hat Y'] for sign in ['-','+']]
mapping={'version':1,'speed':40,'deadzone':.35,'inputs':inputs,'reserved':['button:Start'],'actions':[{'id':name,'label':name.title(),'hold':name in ('forward','backward','turn left','turn right')} for name in ('forward','backward','turn left','turn right','stand','sit','ready','wave','stop')],
 'bindings':[{'input':'button:South','action':'stand','mode':'press'},{'input':'axis:Y:-','action':'forward','mode':'hold'},{'input':'button:Select','action':'stop','mode':'press'}]}
controller={'devices':[{'id':'usb','name':'USB WirelessGamepad','vendor_id':'2563','product_id':'0575','stable_path':'/dev/input/by-id/test-joystick','readable':True}],
 'active_device_id':'usb','connected':True,'attached':True,'enabled':False,'armed':False,'system_armed':False,'raw_axes':[0]*6,'raw_buttons':[False]*6,'axes':[0]*6,'buttons':[False]*6,'axis_names':['X','Y','Z','RZ','Hat X','Hat Y'],'button_names':['South','East','West','North','Select','Start'],'events_seen':0,'input_revision':17,'recent_events':[],
 'mapping':mapping,'control':{'state':'controller_disarmed','message':'Controller disarmed. Start toggles controller arming.'}}

with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 for width in (1440,390,320):
  page=browser.new_page(viewport={'width':width,'height':950});errors=[];calls=[];state={'controller':copy.deepcopy(controller)}
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.add_init_script('''
   window.controllerStreams=[];
   class MockSource {
    constructor(url){this.url=url;this.closed=false;window.controllerStreams.push(this);setTimeout(()=>{if(this.closed)return;this.onopen?.();this.emit(window.initialController);this.timer=setInterval(()=>this.emit(this.lastValue||window.initialController),50);},2000);}
    close(){this.closed=true;clearInterval(this.timer);}
    emit(value){this.lastValue=value;if(!this.closed)this.onmessage?.({data:JSON.stringify(value)});}
    fail(){if(!this.closed)this.onerror?.({});}
   }
   window.EventSource=MockSource;
  '''+f'window.initialController={json.dumps(controller)};')
  def route(route):
   path=route.request.url.split('robot.test',1)[-1].split('?',1)[0]
   if path=='/mcp':
    request=route.request.post_data_json
    if request['method']=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{},'serverInfo':{'name':'mock','version':'1'}}
    else:
     name=request.get('params',{}).get('name');args=request.get('params',{}).get('arguments',{});calls.append((name,args))
     if name=='get_status':value={'armed':False,'busy':False,'estop':False,'servo_angles':[None]*12,'ultrasonic':{'valid':False},'camera':{'enabled':False},'microphone':{'enabled':False},'speaker':{},'recordings':[],'examples':{},'gamepad':state['controller']}
     elif name=='list_examples':value=[]
     elif name=='admin_get':value={'providers':[],'connections':[],'defaults':{},'server':{}}
     elif name=='controller_get':value=state['controller']
     elif name=='controller_save_mapping':state['controller']['mapping'].update(args);value=state['controller']
     elif name=='controller_reset_mapping':state['controller']['mapping']=copy.deepcopy(mapping);value=state['controller']
     else:raise AssertionError('Unexpected tool '+str(name))
     result={'content':[{'type':'text','text':json.dumps(value)}]}
    route.fulfill(content_type='application/json',body=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}))
   elif path.startswith('/static/'):
    file=ROOT/'pibotpal'/path.lstrip('/');route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
   elif path=='/':route.fulfill(path=str(ROOT/'pibotpal/static/index.html'))
   else:route.fulfill(status=404)
  page.route('http://robot.test/**',route)
  page.goto('http://robot.test/#admin/controllers',wait_until='domcontentloaded')
  page.locator('#controller-transport').wait_for(state='attached')
  page.wait_for_timeout(1700)
  assert page.evaluate('window.controllerStreams.length===1&&!window.controllerStreams[0].closed'), 'Connecting SSE aborted before delayed headers arrived'
  page.wait_for_function("document.querySelector('#controller-transport').textContent.includes('20 Hz')")
  assert page.locator('#controller-gate-state').inner_text()=='CONTROLLER DISARMED'
  assert page.locator('#controller-event-count').inner_text()=='17 EVENTS'
  assert page.locator('#controller-mapping-rows tr').count()==len(inputs)
  assert page.get_by_label('Action for Start',exact=True).count()==0
  assert 'arm controller actions and system motion together' in page.locator('#controller-mapping-form').inner_text()
  # A press and release between display frames still leaves a visible tap.
  page.evaluate('''()=>{const s=JSON.parse(JSON.stringify(window.initialController));const now=Date.now()/1000;s.recent_events=[{seq:1,time:now,kind:'button',index:0,name:'South',value:true},{seq:2,time:now,kind:'button',index:0,name:'South',value:false}];s.events_seen=2;window.controllerStreams.at(-1).emit(s);}''')
  assert 'is-tapped' in page.locator('[data-pad-button=South]').get_attribute('class')
  assert 'is-held' not in page.locator('[data-pad-button=South]').get_attribute('class')
  assert page.locator('#controller-event-log li').count()==2
  page.wait_for_timeout(420)
  assert 'is-tapped' not in page.locator('[data-pad-button=South]').get_attribute('class')
  page.evaluate('''()=>{const s=JSON.parse(JSON.stringify(window.initialController));s.raw_buttons[0]=true;s.raw_axes[0]=.75;s.raw_axes[4]=1;s.recent_events=[{seq:3,time:Date.now()/1000,kind:'button',index:0,name:'South',value:true}];window.controllerStreams.at(-1).emit(s);}''')
  assert 'is-held' in page.locator('[data-pad-button=South]').get_attribute('class')
  assert 'is-held' in page.locator('[data-pad-axis="hatx:+"]').get_attribute('class')
  assert '18.75px' in page.locator('#controller-left-stick i').get_attribute('style')
  # A dirty form is preserved while live values continue to update.
  page.get_by_label('Action for A / × (South)',exact=True).select_option('wave')
  page.locator('#controller-map-speed').fill('35')
  page.evaluate('window.controllerStreams.at(-1).emit(window.initialController)')
  assert page.get_by_label('Action for A / × (South)',exact=True).input_value()=='wave'
  page.locator('#controller-map-save').click()
  page.wait_for_function("document.querySelector('#controller-message').textContent==='Mapping saved.'")
  saved=next(args for name,args in calls if name=='controller_save_mapping')
  assert saved['speed']==35 and any(b['input']=='button:South' and b['action']=='wave' for b in saved['bindings'])
  assert all(b['input']!='button:Start' for b in saved['bindings'])
  page.locator('#controller-map-reset').click()
  page.wait_for_function("document.querySelector('#controller-message').textContent==='Default mapping restored.'")
  # SSE failure falls back to fast polling; leaving the tab closes transport.
  initial=sum(name=='controller_get' for name,args in calls)
  assert page.evaluate('''()=>{const source=window.controllerStreams.at(-1);const held=JSON.parse(JSON.stringify(window.initialController));held.raw_buttons[0]=true;source.emit(held);source.fail();return document.querySelector('.controller-visual-layout').classList.contains('inputs-stale')&&!document.querySelector('[data-pad-button=South]').classList.contains('is-held');}''')
  page.wait_for_timeout(450)
  assert sum(name=='controller_get' for name,args in calls)>=initial+2
  page.get_by_role('tab',name='Models',exact=True).click()
  assert page.evaluate('window.controllerStreams.every(stream=>stream.closed)')
  initial=sum(name=='controller_get' for name,args in calls);page.wait_for_timeout(350)
  assert sum(name=='controller_get' for name,args in calls)<=initial+1
  page.get_by_role('tab',name='Controllers',exact=True).click()
  page.locator('#controller-transport').wait_for(state='attached')
  page.wait_for_function("document.querySelector('#controller-transport').textContent.includes('20 Hz')")
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert not errors,errors
  assert not ({'arm','move','set_servo','set_leg','controller_set_enabled','plan_run'} & {name for name,args in calls})
  page.screenshot(path=str(ROOT/f'artifacts/controllers-mapping-{width}.png'))
  print('Controller UI passed',width,'px: raw inputs, short taps, hold, mapping, reset, SSE fallback, leave/re-enter')
  page.close()
 browser.close()
