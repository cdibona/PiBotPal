"""Browser regression for telemetry and MJPEG recovery; every request is mocked.

Run: .venv/bin/python tests/browser/check_reconnect.py
Requires Chromium and Playwright, and never connects to robot hardware.
"""
from pathlib import Path
import io
import json
import sys
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
stream=io.BytesIO()
Image.new('RGB',(4,4),(40,140,70)).save(stream,'JPEG')
jpeg=stream.getvalue()
packet=b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n'
large_stream=io.BytesIO()
Image.effect_noise((1920,1440),100).convert('RGB').save(large_stream,'JPEG',quality=90)
large_jpeg=large_stream.getvalue()
assert len(large_jpeg)>1024*1024
large_packet=b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(large_jpeg)).encode()+b'\r\n\r\n'+large_jpeg+b'\r\n'

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page()
    page.add_init_script('''
      const originalFetch=window.fetch;
      window.fetch=async(...args)=>{
        const response=await originalFetch(...args);
        if(!String(args[0]).includes('/video.mjpg'))return response;
        const bytes=new Uint8Array(await response.arrayBuffer());
        // Deliberately split boundary, headers, and JPEG markers across chunks.
        return new Response(new ReadableStream({start(controller){
          let offset=0;for(let n=1;offset<bytes.length;n++){
            const size=offset<100?[1,2,3,7,13][n%5]:65536;controller.enqueue(bytes.slice(offset,offset+size));offset+=size;
          }controller.close();
        }}),{status:response.status,headers:response.headers});
      };
    ''')
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    calls=[];state={'instance':'first','outage':False,'streams':0,'bad_stream':False,'large':False}
    def route(request):
        path=request.request.url.split('robot.test',1)[-1].split('?',1)[0]
        if path=='/mcp':
            if state['outage']:
                request.abort();return
            data=request.request.post_data_json
            if data['method']=='initialize':
                result={'protocolVersion':'2025-03-26','capabilities':{},'serverInfo':{'name':'Mock','version':'1'}}
            else:
                name=data.get('params',{}).get('name');calls.append(name)
                if name=='get_status':
                    value={'server_instance_id':state['instance'],'armed':False,'busy':False,'estop':False,
                           'servo_angles':[None]*12,'ultrasonic':{'valid':False},
                           'camera':{'enabled':True,'available':True},'microphone':{'enabled':False},
                           'speaker':{},'recordings':[],'plan':{'running':False,'phase':'idle'},'examples':{}}
                elif name=='list_examples':value=[]
                elif name == 'action_list': value = {'actions': [], 'limits': {}}
                elif name=='plan_list':value={'poses':[],'plans':[],'execution':{'running':False}}
                elif name=='admin_get':value={'providers':[],'connections':[],'defaults':{}}
                else:raise AssertionError('Unexpected hardware/tool call: '+str(name))
                result={'content':[{'type':'text','text':json.dumps(value)}]}
            request.fulfill(content_type='application/json',body=json.dumps({'jsonrpc':'2.0','id':data.get('id'),'result':result}))
        elif path=='/video.mjpg':
            state['streams']+=1
            body=(b'--frame\r\nContent-Length: 999999999\r\n\r\n' if state['bad_stream'] else large_packet if state['large'] else packet)
            # A valid frame followed by clean EOF simulates a closed server stream
            # while all telemetry polls keep succeeding.
            request.fulfill(content_type='multipart/x-mixed-replace; boundary=frame',body=body)
        elif path.startswith('/static/'):
            file=ROOT/'pibotpal'/path.lstrip('/')
            if file.is_file():request.fulfill(path=str(file))
            else:request.fulfill(status=404)
        elif path=='/':request.fulfill(path=str(ROOT/'pibotpal/static/index.html'))
        else:request.fulfill(status=404)
    page.route('http://robot.test/**',route)
    page.goto('http://robot.test/',wait_until='domcontentloaded')
    page.wait_for_function("document.querySelector('#connection-text').textContent==='Connected'")
    page.wait_for_function("document.querySelector('#camera-feed').src.startsWith('blob:')")
    initial=state['streams']
    page.wait_for_timeout(1300)
    assert state['streams']>initial,'Clean EOF did not reconnect while telemetry remained healthy'
    assert page.locator('#connection-text').inner_text()=='Connected'
    # The server incarnation changes without a failed status request.
    initial=state['streams'];state['instance']='second'
    page.wait_for_timeout(700)
    assert state['streams']>initial,'Server instance change did not refresh video'
    # Native-size sensor frames can exceed 1 MiB; render them without losing
    # the bounded parser or its handling of fragmented headers and bodies.
    state['large']=True;state['instance']='large-frame'
    page.wait_for_function("document.querySelector('#camera-feed').naturalWidth===1920")
    state['large']=False
    # Reject impossible frame sizes; do not display arbitrary payloads or spin.
    state['bad_stream']=True
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    page.wait_for_timeout(300)
    page.wait_for_timeout(1500)
    assert not errors,errors
    # A real telemetry outage disables arm, then recovers without reload.
    state['outage']=True
    page.wait_for_function("document.querySelector('#connection-text').textContent==='Disconnected'")
    assert page.locator('#arm').is_disabled()
    state['outage']=False;state['bad_stream']=False
    page.wait_for_function("document.querySelector('#connection-text').textContent==='Connected'")
    page.wait_for_function("document.querySelector('#camera-feed').src.startsWith('blob:')")
    assert not errors,errors
    assert not ({'arm','move','set_servo','set_leg','microphone','camera','plan_run'} & set(calls))
    print('Browser recovery passed: clean EOF, server restart, malformed frame, telemetry outage/reconnect; no actuation calls')
    browser.close()
