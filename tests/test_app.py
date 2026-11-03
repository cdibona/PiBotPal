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
