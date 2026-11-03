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
