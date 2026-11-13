import time
import json
import pytest
from fastapi.testclient import TestClient
from pibotpal.app import create_app
from pibotpal.hardware import Controller
from pibotpal.media import Media

HEADERS = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-03-26'}

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    monkeypatch.setenv('PIBOTPAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PIBOTPAL_SIMULATE', '1')
    controller = Controller(simulate=True, config_path=tmp_path / 'calibration')
    media = Media(simulate=True)
    with TestClient(create_app(controller, media), base_url='http://127.0.0.1:8765') as client:
        yield client


def rpc(client, method, params=None):
    response = client.post('/mcp', headers=HEADERS,
                           json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
    assert response.status_code == 200, response.text
    return response.json()


def call(client, tool_name, **arguments):
    result = rpc(client, 'tools/call', {'name': tool_name, 'arguments': arguments})['result']
    if not result.get('isError') and 'structuredContent' not in result:
        result['structuredContent'] = json.loads(result['content'][0]['text'])
    return result


def test_mcp_protocol_and_disarmed_start(client):
    initialized = rpc(client, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
    assert initialized['result']['serverInfo']['name'] == 'PiBotPal'
    tools = rpc(client, 'tools/list')['result']['tools']
    assert {'get_status', 'arm', 'stop', 'move', 'run_example', 'microphone'} <= {t['name'] for t in tools}
    status = call(client, 'get_status')['structuredContent']
    assert not status['armed'] and status['servo_angles'] == [None] * 12
    assert status['microphone']['muted'] and not status['microphone']['listeners']
    assert call(client, 'move', name='forward')['isError']


def test_arm_stop_and_validation(client):
    assert call(client, 'arm', armed=True)['structuredContent']['armed']
    assert call(client, 'move', name='__dict__')['isError']
    status = call(client, 'stop')['structuredContent']
    assert not status['armed'] and status['estop'] and status['microphone']['muted']
    assert status['servo_angles'] == [None] * 12


def test_cross_origin_and_host_rejected(client):
    assert client.get('/health', headers={'Host': 'attacker.example'}).status_code == 421
    assert client.get('/snapshot.jpg', headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert client.post('/mcp', headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'}, json={}).status_code == 403
    assert client.get('/health').status_code == 200


def test_muted_audio_and_unavailable_media(client):
    assert client.get('/audio.wav').status_code == 409
    assert client.get('/snapshot.jpg').status_code == 503
    assert client.get('/video.mjpg').status_code == 503
    assert call(client, 'microphone', enabled=True)['isError']
    assert call(client, 'run_example', example_id='17_online_llm_test')['isError']
    assert client.get('/recordings/missing.avi').status_code == 404


def test_calibration_read_can_run_disarmed(client):
    result = call(client, 'run_example', example_id='0_calibration', options={'operation': 'read_offsets'})
    assert not result.get('isError')
    for _ in range(20):
        status = call(client, 'get_status')['structuredContent']
        if not status['examples']['running']:
            break
        time.sleep(.02)
    assert status['examples']['phase'] == 'complete'
    assert not status['armed']


def test_heartbeat_expiry_disarms_and_mutes(client, monkeypatch):
    from types import SimpleNamespace
    clock=[100.0]
    monkeypatch.setattr('pibotpal.app.time', SimpleNamespace(monotonic=lambda: clock[0]))
    call(client, 'arm', armed=True)
    client.app.state.media._mic_enabled=True  # synthetic consent; no capture device opened
    clock[0]+=16
    time.sleep(1.2)
    assert not client.app.state.controller.status()['armed']
    assert not client.app.state.media.status()['microphone']['enabled']


def test_microphone_lease_expires_while_disarmed(client, monkeypatch):
    from types import SimpleNamespace
    clock=[100.0]
    monkeypatch.setattr('pibotpal.app.time', SimpleNamespace(monotonic=lambda: clock[0]))
    call(client, 'get_status')
    client.app.state.media._mic_enabled=True
    clock[0]+=16
    time.sleep(1.2)
    assert not client.app.state.media.status()['microphone']['enabled']


def test_admin_credentials_redacted_defaults_enable_test(client):
    saved=call(client,'admin_save_provider',provider={'name':'Test provider','kind':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen-test','api_key':'secret-test-key'})
    assert 'secret-test-key' not in json.dumps(saved)
    provider_id=saved['structuredContent']['id']
    assert saved['structuredContent']['api_key_set']
    assert 'secret-test-key' not in json.dumps(call(client,'admin_get'))
    defaults=call(client,'admin_save_defaults',defaults={'test_provider_id':provider_id})
    assert not defaults['isError']
    catalog=call(client,'list_examples')['structuredContent']
    if isinstance(catalog,dict): catalog=catalog['result']
    assert next(e for e in catalog if e['id']=='17_online_llm_test')['available']
    assert call(client,'admin_save_provider',provider={'id':provider_id,'base_url':'http://new-server:11434'})['isError']
    call(client,'admin_delete_provider',provider_id=provider_id)
    assert call(client,'admin_get')['structuredContent']['defaults']['test_provider_id'] is None


def test_admin_model_job_runs_through_builtin_mcp_tools(client,monkeypatch):
    provider=call(client,'admin_save_provider',provider={'name':'Fake model','kind':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen-test'})['structuredContent']
    call(client,'admin_save_defaults',defaults={'test_provider_id':provider['id']})
    calls=[]
    async def complete(provider,messages,tools=None,options=None):
        calls.append(messages.copy())
        if len(calls)==1:
            assert {t['function']['name'] for t in tools}=={'robot_get_status','robot_list_examples'}
            return {'model':'qwen-test','message':{'role':'assistant','content':'','tool_calls':[{'id':'t1','type':'function','function':{'name':'robot_get_status','arguments':'{}'}}]}}
        return {'model':'qwen-test','message':{'role':'assistant','content':'Robot is disarmed.'},'usage':{}}
    monkeypatch.setattr('pibotpal.assistant.integrations.complete',complete)
    job=call(client,'llm_start',prompt='Check status.',use_tools=True)['structuredContent']
    for _ in range(50):
        job=call(client,'llm_job',job_id=job['id'])['structuredContent']
        if job['status']!='running': break
        time.sleep(.02)
    assert job['status']=='complete',job
    assert job['trace'][0]['result'].get('armed') is False, job['trace']
    assert not client.app.state.controller.status()['armed']
    assert all(a is None for a in client.app.state.controller.status()['servo_angles'])


def test_power_telemetry_distinguishes_unknown_values(client):
    power=call(client,'get_status')['structuredContent']['power']
    assert power['powered']
    assert power['charging'] is None and power['estimated_runtime_minutes'] is None
    assert power['voltage'] is None and not power['valid']


def test_app_downloads_are_allowlisted_attachments(client, tmp_path):
    directory = tmp_path / 'downloads'
    directory.mkdir()
    (directory / 'PiBotPal-Android.apk').write_bytes(b'APK build')
    (directory / 'PiBotPal-Windows-x64.zip').write_bytes(b'ZIP build')
    (directory / 'secret.txt').write_text('not downloadable')
    for name, kind in [('PiBotPal-Android.apk', 'application/vnd.android.package-archive'),
                       ('PiBotPal-Windows-x64.zip', 'application/zip')]:
        response = client.get('/downloads/' + name)
        assert response.status_code == 200
        assert response.headers['content-type'] == kind
        assert 'attachment;' in response.headers['content-disposition']
    assert client.get('/downloads/secret.txt').status_code == 404
    assert client.get('/downloads/build.json').status_code == 404


def test_restart_identity_is_stable_and_shared_with_telemetry(client):
    first=client.get('/health').json()['server_instance_id']
    second=client.get('/health').json()['server_instance_id']
    assert len(first)==32 and first==second
    assert call(client,'get_status')['structuredContent']['server_instance_id']==first


def test_camera_catalog_and_controller_monitor_are_read_only_in_simulation(client):
    result=call(client,'camera_list')['structuredContent']
    assert result=={'cameras':[],'primary_camera_id':None}
    monitor=call(client,'controller_get')['structuredContent']
    assert monitor['motion_enabled'] is False and not monitor['connected']
    assert client.get('/cameras/not-a-camera/snapshot.jpg').status_code==404
    assert client.get('/cameras/not-a-camera/video.mjpg').status_code==404
    assert call(client,'camera_select',camera_id='not-a-camera')['isError']
    assert call(client,'camera_configure',camera_id='not-a-camera',width=640,height=480)['isError']
    assert call(client,'get_status')['structuredContent']['servo_angles']==[None]*12


def test_usb_input_toggle_is_persisted_shared_and_never_arms(client,tmp_path):
    original=call(client,'controller_get')['structuredContent']
    assert original['enabled'] and not original['attached']
    for enabled in (False,True):
        result=call(client,'controller_set_enabled',enabled=enabled)['structuredContent']
        assert result['enabled'] is enabled and not result['motion_enabled']
        status=call(client,'get_status')['structuredContent']
        assert status['gamepad']['enabled'] is enabled
        assert not status['armed'] and status['servo_angles']==[None]*12
        assert not status['microphone']['enabled']
        settings=tmp_path/'controller.json'
        assert json.loads(settings.read_text())=={'version':1,'enabled':enabled}
        assert settings.stat().st_mode & 0o777==0o600
        inventory=call(client,'hardware_get')['structuredContent']
        gamepad=next(item for item in inventory['devices'] if item['id']=='gamepad')
        assert gamepad['enabled'] is enabled and gamepad['options']['enabled'] is enabled
    rejected=call(client,'hardware_save',device_id='gamepad',options={'enabled':False})
    assert rejected['isError'] and 'controller_set_enabled' in str(rejected)
    assert call(client,'controller_get')['structuredContent']['enabled']


def test_distinct_camera_snapshots_and_persisted_primary_selection(client,tmp_path):
    import threading
    media=client.app.state.media
    ids=['csi-aaaaaaaaaaaa','csi-bbbbbbbbbbbb']
    for index,identifier in enumerate(ids):
        media._cameras[identifier]={'id':identifier,'hardware_id':'/sensor/'+str(index),
            'index':index,'model':'mock'+str(index),'sensor_rotation':0,'connection':'csi',
            'thread':None,'stop':threading.Event(),'frame':None,'jpeg':b'frame'+bytes([index]),
            'timestamp':time.time(),'error':None}
    media._primary_camera_id=ids[0]
    media._sync_primary(media._cameras[ids[0]])
    assert client.get('/snapshot.jpg').content==b'frame\0'
    assert client.get('/cameras/'+ids[1]+'/snapshot.jpg').content==b'frame\1'
    result=call(client,'camera_select',camera_id=ids[1])['structuredContent']
    assert result['primary_camera_id']==ids[1]
    assert client.get('/snapshot.jpg').content==b'frame\1'
    assert json.loads((tmp_path/'cameras.json').read_text())['primary_camera_id']==ids[1]
    assert (tmp_path/'cameras.json').stat().st_mode & 0o777==0o600


def test_mcp_resolution_configuration_preserves_disarmed_and_muted_state(client, tmp_path):
    import threading
    media = client.app.state.media
    identifier = 'csi-aaaaaaaaaaaa'
    media._cameras[identifier] = {'id': identifier, 'hardware_id': '/sensor/mock',
        'index': 0, 'model': 'mock', 'sensor_rotation': 0, 'connection': 'csi',
        'thread': None, 'stop': threading.Event(), 'frame': None, 'jpeg': None,
        'timestamp': None, 'error': None, 'width': 640, 'height': 480, 'frame_rate': 12,
        'resolutions': [{'width': 640, 'height': 480, 'frame_rate': 12},
                        {'width': 1280, 'height': 720, 'frame_rate': 12}]}
    media._primary_camera_id = identifier
    result = call(client, 'camera_configure', camera_id=identifier, width=1280, height=720)
    assert not result.get('isError')
    camera = result['structuredContent']['cameras'][0]
    assert (camera['width'], camera['height']) == (1280, 720)
    assert not camera['enabled'] and not camera['available']
    assert call(client, 'camera_configure', camera_id=identifier, width=8000, height=8000)['isError']
    state = call(client, 'get_status')['structuredContent']
    assert not state['armed'] and state['microphone']['muted']
    assert state['servo_angles'] == [None] * 12
    saved = json.loads((tmp_path / 'cameras.json').read_text())
    assert saved['resolutions'][identifier] == {'width': 1280, 'height': 720}


def test_open_legacy_video_follows_primary_but_named_stream_is_pinned(client, monkeypatch):
    import asyncio
    media=client.app.state.media
    media._primary_camera_id='csi-aaaaaaaaaaaa'
    monkeypatch.setattr(media,'jpeg',lambda camera_id=None:
        b'first' if (camera_id or media.primary_camera_id)=='csi-aaaaaaaaaaaa' else b'second')
    class Request:
        async def is_disconnected(self): return False
    legacy=next(r.endpoint for r in client.app.routes if getattr(r,'path',None)=='/video.mjpg')
    named=next(r.endpoint for r in client.app.routes if getattr(r,'path',None)=='/cameras/{camera_id}/video.mjpg')
    async def check():
        response=await legacy(Request())
        assert (await anext(response.body_iterator)).endswith(b'first\r\n')
        media._primary_camera_id='csi-bbbbbbbbbbbb'
        assert (await anext(response.body_iterator)).endswith(b'second\r\n')
        await response.body_iterator.aclose()
        pinned=await named('csi-aaaaaaaaaaaa',Request())
        assert (await anext(pinned.body_iterator)).endswith(b'first\r\n')
        await pinned.body_iterator.aclose()
    asyncio.run(check())


def test_video_does_not_retransmit_unchanged_high_resolution_frames(client, monkeypatch):
    import asyncio
    media = client.app.state.media
    current = [b'first-frame']
    monkeypatch.setattr(media, 'jpeg', lambda camera_id=None: current[0])
    class Request:
        polls = 0
        async def is_disconnected(self):
            self.polls += 1
            if self.polls == 4:
                current[0] = b'next-frame'
            return False
    request = Request()
    route = next(r.endpoint for r in client.app.routes if getattr(r, 'path', None) == '/video.mjpg')
    async def check():
        response = await route(request)
        assert (await anext(response.body_iterator)).endswith(b'first-frame\r\n')
        assert (await anext(response.body_iterator)).endswith(b'next-frame\r\n')
        assert request.polls == 4
        await response.body_iterator.aclose()
    asyncio.run(check())
