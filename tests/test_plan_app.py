"""MCP plan contract and exclusive motion ownership, all in simulation."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pibotpal import integrations
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


def tool(client, tool_name, **arguments):
    response = client.post('/mcp', headers=HEADERS,
                           json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': tool_name, 'arguments': arguments}})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    if result.get('isError'):
        return result
    return result.get('structuredContent') or json.loads(result['content'][0]['text'])


def finish(client, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = tool(client, 'plan_list')['execution']
        if not state['running']:
            return state
        time.sleep(.01)
    pytest.fail('Plan did not finish')


def waiting_plan(client):
    return tool(client, 'plan_save', name='Waiting', steps=[
        {'type': 'wait', 'seconds': 20}, {'type': 'action', 'name': 'forward'}])


def configure_model(client):
    provider = tool(client, 'admin_save_provider', provider={
        'name':'Mock Qwen', 'kind':'ollama', 'model':'qwen-test',
        'base_url':'http://127.0.0.1:11434'})
    tool(client, 'admin_save_defaults', defaults={'assistant_provider_id':provider['id']})


def test_requested_plan_drafts_without_saving_or_movement(client):
    result = tool(client, 'plan_draft', text='Stand. move forward 4 steps, turn left, move forward four steps.')
    assert [(step['name'], step['steps']) for step in result['steps']] == [
        ('stand',1), ('forward',4), ('turn left',1), ('forward',4)]
    assert result['requires_review']
    catalog = tool(client, 'plan_list')
    assert catalog['plans'] == [] and catalog['poses'] == []
    state = tool(client, 'get_status')
    assert not state['armed'] and state['servo_angles'] == [None]*12


def test_saved_pose_and_plan_execute_only_after_manual_arm(client):
    pose = tool(client, 'pose_save', name='Neutral', angles=[0]*12)
    plan = tool(client, 'plan_save', name='Neutral routine',
                steps=[{'type':'pose','pose_id':pose['id'],'speed':80}])
    assert tool(client, 'plan_run', plan_id=plan['id'])['isError']
    assert not tool(client, 'get_status')['armed']
    tool(client, 'arm', armed=True)
    result = tool(client, 'plan_run', plan_id=plan['id'])
    assert result['running']
    # Initial pose establishes six capped joint groups with a one-second settle.
    assert finish(client, timeout=8)['phase'] == 'complete'
    state = tool(client, 'get_status')
    assert state['servo_angles'] == [0]*12 and not state['armed']


def test_active_plan_excludes_manual_motion_examples_and_rearming(client):
    plan = waiting_plan(client)
    tool(client, 'arm', armed=True)
    tool(client, 'plan_run', plan_id=plan['id'])
    for name, arguments in [('move',{'name':'forward'}), ('set_servo',{'index':0,'angle':0}),
                            ('set_leg',{'leg':0,'x':45,'y':45,'z':-50}),
                            ('set_offsets',{'offsets':[0]*12}), ('arm',{'armed':True}),
                            ('run_example',{'example_id':'1_move'}),
                            ('plan_run',{'plan_id':plan['id']})]:
        result = tool(client, name, **arguments)
        assert result['isError'], (name, result)
        assert 'active plan' in result['content'][0]['text']
    tool(client, 'plan_stop')
    assert finish(client)['phase'] == 'cancelled'


@pytest.mark.parametrize('method,arguments', [('stop',{}), ('plan_stop',{}), ('arm',{'armed':False})])
def test_all_stop_interfaces_cancel_pending_plan_actions(client, method, arguments):
    plan = waiting_plan(client)
    tool(client, 'arm', armed=True)
    tool(client, 'plan_run', plan_id=plan['id'])
    tool(client, method, **arguments)
    assert finish(client)['phase'] == 'cancelled'
    state = tool(client, 'get_status')
    assert not state['armed'] and state['microphone']['muted']
    assert state['servo_angles'] == [None]*12


def test_watchdog_cancels_plan_without_refreshing_its_own_lease(client, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('pibotpal.app.time', SimpleNamespace(monotonic=lambda:clock[0]))
    plan = waiting_plan(client)
    tool(client, 'arm', armed=True)
    tool(client, 'plan_run', plan_id=plan['id'])
    clock[0] += 16
    time.sleep(1.2)
    assert finish(client)['phase'] == 'cancelled'
    state = tool(client, 'get_status')
    assert not state['armed'] and state['servo_angles'] == [None]*12


def test_llm_draft_has_no_tools_and_cannot_save_or_execute(client, monkeypatch):
    configure_model(client)
    async def complete(provider, messages, tools, options):
        assert tools is None and options['temperature'] == 0
        assert messages[-1]['content'] == 'Walk a little, then rest.'
        return {'message':{'content':json.dumps({'name':'Model draft','steps':[
            {'type':'action','name':'forward','steps':1}, {'type':'action','name':'sit'}]}),
            'tool_calls':[{'function':{'name':'arm','arguments':'{"armed":true}'}}]}}
    monkeypatch.setattr(integrations,'complete',complete)
    draft = tool(client,'plan_draft',text='Walk a little, then rest.',use_llm=True)
    assert draft['source'] == 'llm' and draft['requires_review']
    assert tool(client,'plan_list')['plans'] == []
    state = tool(client,'get_status')
    assert state['servo_angles'] == [None]*12 and not state['armed']


@pytest.mark.parametrize('content', ['not JSON', '[]',
    '{"name":"Unsafe","steps":[{"type":"action","name":"arm"}]}',
    '{"name":"Condition","steps":[{"type":"if","sensor":"sonar"}]}',
    '{"name":"Loop","steps":[{"type":"wait","seconds":1}],"repeat":100}',
    '{"name":"Infinite","steps":[{"type":"wait","seconds":NaN}]}',
    '{"name":"Fast","steps":[{"type":"action","name":"forward","speed":100}]}',
])
def test_invalid_llm_drafts_are_rejected_without_execution(client, monkeypatch, content):
    configure_model(client)
    async def complete(*args, **kwargs):
        return {'message':{'content':content}}
    monkeypatch.setattr(integrations,'complete',complete)
    assert tool(client,'plan_draft',text='Try a route',use_llm=True)['isError']
    assert not tool(client,'get_status')['armed']
    assert tool(client,'plan_list')['plans'] == []


@pytest.mark.parametrize('invent_angles', [False, True])
def test_llm_pose_steps_resolve_saved_library_not_invented_angles(client, monkeypatch, invent_angles):
    configure_model(client)
    pose=tool(client,'pose_save',name='Crouch',angles=[0]*12)
    async def complete(provider,messages,tools,options):
        assert pose['id'] in messages[0]['content']
        step={'type':'pose','pose_id':pose['id'],'speed':35}
        if invent_angles: step['angles']=[1]*12
        return {'message':{'content':json.dumps({'name':'Pose plan','steps':[step]})}}
    monkeypatch.setattr(integrations,'complete',complete)
    result=tool(client,'plan_draft',text='Use my Crouch pose',use_llm=True)
    if invent_angles: assert result['isError']
    else: assert result['steps'][0]['angles']==pose['angles']
    assert not tool(client,'get_status')['armed']
    assert tool(client,'plan_list')['plans']==[]
