"""Mocked Admin navigation, camera resolution, and controller-monitor regression.

Run .venv/bin/python tests/browser/check_admin_cameras.py. No robot requests escape.
"""
import io
import json
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]
buffer=io.BytesIO();Image.new('RGB',(4,4),(30,130,100)).save(buffer,'JPEG');jpeg=buffer.getvalue()
packet=b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n'

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    for width in (1440,390):
        page=browser.new_page(viewport={'width':width,'height':950});errors=[];calls=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        cameras=[{'id':key,'hardware_id':'/base/'+model,'model':model,'enabled':True,'available':True,'error':None,'timestamp':1,'width':640,'height':480,'frame_rate':12,'sensor_resolution':{'width':2592,'height':1944},'resolutions':[{'width':320,'height':240,'frame_rate':12},{'width':640,'height':480,'frame_rate':12},{'width':1280,'height':720,'frame_rate':12},{'width':1920,'height':1080,'frame_rate':8},{'width':2592,'height':1944,'frame_rate':4}],'video_url':f'/cameras/{key}/video.mjpg','snapshot_url':f'/cameras/{key}/snapshot.jpg'} for key,model in [('csi-a','ov5647'),('csi-b','imx477')]]
        state={'primary':'csi-a','provider_saved':None,'reject_resolution':False,'reject_power':None}
        provider={'id':'p1','name':'Local Qwen','kind':'ollama','base_url':'http://ollama:11434','model':'qwen','enabled':True,'api_key_set':True}
        defaults={'test_provider_id':'p1','assistant_provider_id':'p1','system_prompt':'Help','max_tokens':1024,'temperature':.2,'timeout_seconds':120,'robot_tools':['get_status']}
        def catalog():return {'cameras':cameras,'primary_camera_id':state['primary']}
        def route(route):
            path=route.request.url.split('robot.test',1)[-1].split('?',1)[0]
            if path=='/mcp':
                request=route.request.post_data_json
                if request['method']=='initialize':value={'protocolVersion':'2025-03-26','capabilities':{},'serverInfo':{'name':'mock','version':'1'}}
                else:
                    name=request.get('params',{}).get('name');args=request.get('params',{}).get('arguments',{});calls.append((name,args))
                    if name=='get_status':value={'server_instance_id':'mock','armed':False,'busy':False,'estop':False,'servo_angles':[None]*12,'ultrasonic':{'valid':False},'camera':next(c for c in cameras if c['id']==state['primary']),'microphone':{'enabled':False},'speaker':{},'recordings':[],'plan':{'running':False},'examples':{},**catalog()}
                    elif name=='list_examples':value=[]
                    elif name == 'action_list': value = {'actions': [], 'limits': {}}
                    elif name=='plan_list':value={'poses':[],'plans':[],'execution':{'running':False}}
                    elif name=='admin_get':value={'providers':[provider],'connections':[],'defaults':defaults,'server':{}}
                    elif name=='admin_save_provider':state['provider_saved']=args['provider'];value=provider
                    elif name=='hardware_get':value={'host':{'model':'Pi5','memory_total_mb':4096},'discovered':{},'devices':[{'id':'servos','name':'Servos','options':{'enabled':True},'detected':None,'status':'active driver'},{'id':'gamepad','name':'USB gamepad','options':{},'detected':True}]}
                    elif name=='power_get':value={'host':{'available':True,'cpu':{'current_mhz':1500,'max_mhz':1500,'min_mhz':1500,'governor':'ondemand'},'temperature_c':42,'under_voltage':False,'under_voltage_occurred':False,'throttled':False,'throttled_occurred':False},'servos':{'max_active_servos':3,'per_servo_deg_s':30,'total_deg_s':60,'settle_ms':80},'armed':False,'busy':False}
                    elif name=='controller_get':value={'devices':[{'id':'pad','name':'USB WirelessGamepad','vendor_id':'2563','product_id':'0575','path':'/dev/input/js0','readable':True}],'connected':True,'axes':[.5,-.25],'axis_names':['X','Y'],'buttons':[True,False],'button_names':['South','East'],'motion_enabled':False,'note':'Read-only. Receiver pairing unverified.'}
                    elif name=='camera_list':value=catalog()
                    elif name=='camera_select':state['primary']=args['camera_id'];value=catalog()
                    elif name=='camera_configure':
                        camera=next(c for c in cameras if c['id']==args['camera_id'])
                        if state['reject_resolution']:
                            value={'content':[{'type':'text','text':'Camera could not start this resolution. Previous capture restored.'}],'isError':True}
                            route.fulfill(content_type='application/json',body=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}))
                            return
                        selected=next(mode for mode in camera['resolutions'] if mode['width']==args['width'] and mode['height']==args['height'])
                        camera.update(selected);value=catalog()
                    elif name=='camera':
                        if state['reject_power']==args['camera_id']:
                            value={'content':[{'type':'text','text':'Synthetic power save failure. Previous capture state restored.'}],'isError':True}
                            route.fulfill(content_type='application/json',body=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}))
                            return
                        camera=next(c for c in cameras if c['id']==args['camera_id']);camera['enabled']=args['enabled'];camera['enabled_preference']=args['enabled'];camera['available']=args['enabled'];value=camera
                    else:raise AssertionError('Unexpected tool '+str(name))
                    value={'content':[{'type':'text','text':json.dumps(value)}]}
                route.fulfill(content_type='application/json',body=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}))
            elif path.endswith('/video.mjpg'):route.fulfill(content_type='multipart/x-mixed-replace; boundary=frame',body=packet)
            elif path.endswith('/snapshot.jpg'):route.fulfill(content_type='image/jpeg',body=jpeg)
            elif path.startswith('/static/'):
                file=ROOT/'pibotpal'/path.lstrip('/');route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
            elif path=='/':route.fulfill(path=str(ROOT/'pibotpal/static/index.html'))
            else:route.fulfill(status=404)
        page.route('http://robot.test/**',route)
        page.goto('http://robot.test/#admin',wait_until='domcontentloaded')
        page.get_by_role('tab',name='Hardware',exact=True).wait_for()
        assert page.get_by_role('tab',name='Hardware',exact=True).get_attribute('aria-selected')=='true'
        assert page.get_by_role('tab').count()==7
        assert page.locator('#controller-status').is_hidden()
        assert page.locator('#provider-form').is_hidden()
        assert page.locator('.power-panel').is_hidden() and page.locator('.metrics').is_hidden()
        options=page.locator('details.hardware-device').first
        assert not options.get_attribute('open')
        options.locator('summary').click()
        assert options.locator('form').is_visible()
        options.locator('summary').click()
        assert options.locator('form').is_hidden()
        assert page.locator('#hardware-devices').get_by_text('USB gamepad',exact=True).count()==0
        page.get_by_role('tab',name='Controllers',exact=True).click()
        page.wait_for_function("document.querySelector('#controller-status').textContent==='RECEIVER CONNECTED'")
        # The mocked SSE endpoint closes, briefly clearing input before the
        # polling fallback renders its next sample.
        expect(page.locator('#controller-axes output').first).to_have_text('0.50')
        expect(page.locator('#controller-buttons .is-held')).to_have_count(1)
        assert page.locator('#hardware-devices').is_hidden()
        page.get_by_role('tab',name='Models',exact=True).click()
        page.wait_for_timeout(100)
        controller_calls=sum(name=='controller_get' for name,args in calls)
        page.wait_for_timeout(1100)
        assert sum(name=='controller_get' for name,args in calls)==controller_calls, 'Controllers must stop polling while hidden'
        page.locator('#provider-list button').click()
        assert page.locator('#provider-secret-status').inner_text()=='KEY SAVED'
        page.locator('#provider-form button[type=submit]').click()
        page.wait_for_function("document.querySelector('#admin-notice').textContent.includes('Provider saved')")
        assert state['provider_saved']['api_key']==''
        page.locator('#provider-test').click()
        assert page.get_by_role('tab',name='Playground',exact=True).get_attribute('aria-selected')=='true'
        assert page.locator('#llm-prompt').is_visible()
        page.get_by_role('tab',name='MCP connections',exact=True).click()
        assert page.locator('#mcp-connection-form').is_visible() and page.locator('#llm-test-form').is_hidden()
        page.get_by_role('tab',name='System',exact=True).click()
        assert page.locator('#local-mcp-url').is_visible()
        page.get_by_role('tab',name='Cameras',exact=True).click()
        page.wait_for_function("document.querySelectorAll('#camera-admin-list .csi-camera-card').length===2")
        page.wait_for_function("[...document.querySelectorAll('#camera-admin-list img')].every(i=>Number(i.dataset.frameCount)>0)")
        cards=page.locator('#camera-admin-list .csi-camera-card')
        first=cards.nth(0)
        choice=first.locator('.camera-resolution-select')
        assert choice.input_value()=='640x480'
        assert first.get_by_role('button',name='Apply',exact=True).is_disabled()
        choice.select_option('1280x720')
        page.locator('#cameras-refresh').click()
        page.wait_for_timeout(1200)
        assert choice.input_value()=='1280x720', 'Telemetry must preserve an unapplied choice'
        first.get_by_role('button',name='Apply',exact=True).click()
        page.wait_for_function("document.querySelector('#camera-admin-list .camera-stream-detail').textContent.includes('1280 × 720')")
        assert cameras[0]['width']==1280 and cameras[1]['width']==640
        assert first.locator('.csi-camera-stage').evaluate('(element)=>getComputedStyle(element).aspectRatio')=='1280 / 720'
        assert first.locator('img').evaluate('(element)=>getComputedStyle(element).objectFit')=='contain'
        assert first.get_by_role('button',name='Apply',exact=True).is_disabled()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for theme in ('light','dark'):
            page.locator('#theme-toggle').click()
            page.get_by_role('menuitemradio',name=theme.title(),exact=True).click()
            page.evaluate('scrollTo(0,0)')
            page.wait_for_timeout(250)
            page.screenshot(path=str(ROOT/f'artifacts/camera-resolutions-{theme}-{width}.png'),full_page=True)
        state['reject_resolution']=True
        choice.select_option('1920x1080')
        first.get_by_role('button',name='Apply',exact=True).click()
        page.wait_for_function("document.querySelector('#camera-admin-list .camera-resolution-status').textContent.includes('Previous capture restored')")
        assert cameras[0]['width']==1280 and choice.input_value()=='1920x1080'
        state['reject_resolution']=False
        choice.select_option('1280x720')
        cards.nth(0).locator('.camera-power-toggle').click()
        expect(cards.nth(0).locator('.camera-power-toggle')).to_have_text('Turn on')
        expect(cards.nth(0).locator('.camera-power-state')).to_have_text('Off · stays off after restart')
        assert cameras[1]['enabled'], 'Turning off one camera must leave the other running'
        assert cards.nth(0).get_by_role('link',name='Save snapshot').get_attribute('aria-disabled')=='true'
        choice.select_option('320x240')
        first.get_by_role('button',name='Apply',exact=True).click()
        page.wait_for_function("document.querySelector('#camera-admin-list .camera-stream-detail').textContent.includes('320 × 240')")
        assert not cameras[0]['enabled'], 'Changing resolution must not start a stopped camera'
        cards.nth(0).locator('.camera-power-toggle').click()
        expect(cards.nth(0).locator('.camera-power-toggle')).to_have_text('Turn off')
        assert cameras[0]['enabled'] and cameras[1]['enabled']
        calls_before=len(calls)
        page.get_by_role('button',name='Turn both off',exact=True).click()
        expect(cards.nth(0).locator('.camera-power-toggle')).to_have_text('Turn on')
        expect(cards.nth(1).locator('.camera-power-toggle')).to_have_text('Turn on')
        assert not any(camera['enabled'] for camera in cameras)
        assert [(args['camera_id'], args['enabled']) for name,args in calls[calls_before:] if name=='camera']==[('csi-a',False),('csi-b',False)]
        assert page.get_by_role('button',name='Turn both off',exact=True).is_disabled()
        cards.nth(1).locator('.camera-power-toggle').click()
        expect(cards.nth(1).locator('.camera-power-toggle')).to_have_text('Turn off')
        assert not cameras[0]['enabled'] and cameras[1]['enabled']
        cards.nth(0).locator('.camera-power-toggle').click()
        expect(cards.nth(0).locator('.camera-power-toggle')).to_have_text('Turn off')
        state['reject_power']='csi-a'
        page.get_by_role('button',name='Turn both off',exact=True).click()
        expect(page.locator('#cameras-notice')).to_contain_text('Some cameras could not be turned off')
        assert cameras[0]['enabled'] and not cameras[1]['enabled'], 'A failed camera must not prevent switching off the other'
        state['reject_power']=None
        cards.nth(1).get_by_role('button',name='Use as primary').click()
        page.wait_for_function("document.querySelector('#primary-camera').value==='csi-b'")
        page.locator('[data-tab=dashboard]').click()
        assert page.locator('.power-panel').is_visible() and page.locator('.metrics').is_visible()
        assert page.locator('#other-camera-feeds .csi-camera-card').count()==1
        page.select_option('#primary-camera','csi-a')
        page.wait_for_timeout(600)
        assert state['primary']=='csi-a'
        page.locator('[data-tab=admin]').click()
        page.get_by_role('tab',name='Models',exact=True).click()
        page.get_by_role('tab',name='Models',exact=True).press('ArrowDown' if width>=1100 else 'ArrowRight')
        assert page.get_by_role('tab',name='MCP connections',exact=True).get_attribute('aria-selected')=='true'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        assert not ({'arm','move','set_leg','set_servo','microphone','plan_run'} & {name for name,args in calls})
        page.screenshot(path=str(ROOT/f'artifacts/admin-subtabs-{width}.png'))
        print('Admin/camera browser workflow passed:',width,'px; no actuation calls')
        page.close()
    browser.close()
