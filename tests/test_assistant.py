import asyncio
import json

import pytest
from mcp import types

from pibotpal.assistant import Assistant
from pibotpal import integrations
from pibotpal.settings import Settings


def call(name, arguments=None, id='call_1'):
    return {'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(arguments or {})}}


def response(text='', calls=None):
    return {'message':{'role':'assistant','content':text,'tool_calls':calls or []},'model':'qwen-test','usage':{'completion_tokens':5}}


def setup(tmp_path, tools=None):
    settings=Settings(tmp_path)
    provider=settings.save_provider({'name':'Test Qwen','kind':'ollama','base_url':'http://127.0.0.1:11434',
                                      'model':'qwen-test','api_key':'test-secret'})
    settings.save_defaults({'test_provider_id':provider['id'],'assistant_provider_id':provider['id'],
                            'robot_tools':tools or ['get_status','list_examples']})
    catalog=[types.Tool(name=name,description=name,inputSchema={'type':'object'}) for name in
             ['get_status','list_examples','action_list','move','stop','set_leg','set_servo','speak','arm','camera','admin_get','admin_save_provider']]
    async def listing(): return catalog
    calls=[]
    async def invoke(name,args):
        calls.append((name,args))
        return {'ok':True}
    return Assistant(settings,listing,invoke),settings,provider['id'],calls


def run(coro): return asyncio.run(coro)


async def finish(assistant, job):
    await assistant.tasks[job['id']]
    return assistant.snapshot(job['id'])


def test_plain_prompt_never_declares_or_invokes_tools(tmp_path,monkeypatch):
    assistant,_,_,calls=setup(tmp_path)
    async def complete(provider,messages,tools,options):
        assert tools is None
        assert messages[-1]=={'role':'user','content':'Hello'}
        return response('Hello back')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Hello'))
        assert job['status']=='complete' and job['result']['text']=='Hello back'
        assert job['trace']==[] and calls==[]
    run(scenario())


def test_tool_declarations_exclude_arm_admin_and_unselected_tools(tmp_path,monkeypatch):
    assistant,settings,_,calls=setup(tmp_path)
    with pytest.raises(ValueError): settings.save_defaults({'robot_tools':['arm']})
    async def complete(provider,messages,tools,options):
        names={tool['function']['name'] for tool in tools}
        assert names=={'robot_get_status','robot_list_examples'}
        return response('Read-only tools available')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('What tools?',use_tools=True))
        assert job['status']=='complete' and calls==[]
    run(scenario())


def test_action_catalog_can_be_enabled_without_movement_tools(tmp_path, monkeypatch):
    assistant, _, _, calls = setup(tmp_path, tools=['action_list'])
    async def complete(provider, messages, tools, options):
        assert {tool['function']['name'] for tool in tools} == {'robot_action_list'}
        if not calls:
            return response(calls=[call('robot_action_list')])
        return response('Shared actions available')
    monkeypatch.setattr(integrations, 'complete', complete)
    async def scenario():
        job = await finish(assistant, assistant.start('Read available actions', use_tools=True))
        assert job['status'] == 'complete'
        assert calls == [('action_list', {})]
    run(scenario())


@pytest.mark.parametrize('name', ['move', 'plan_run'])
def test_model_playback_schema_excludes_operator_arming(tmp_path, monkeypatch, name):
    assistant, settings, _, calls = setup(tmp_path, tools=['move'])
    # Include plan_run in this fake policy to protect a future allowlist change.
    public = settings.public()
    public['defaults']['robot_tools'] = [name]
    monkeypatch.setattr(settings, 'public', lambda: public)
    schema = {'type': 'object', 'properties': {'name': {'type': 'string'},
               'arm_motion': {'type': 'boolean', 'default': False}}, 'required': ['name']}
    async def listing():
        return [types.Tool(name=name, description='Explicit arm_motion=true arms and plays', inputSchema=schema)]
    assistant.list_robot_tools = listing
    async def scenario():
        tools, _ = await assistant._tools(public['defaults'])
        assert 'arm_motion' not in tools[0]['function']['parameters']['properties']
        assert 'operator must already have armed' in tools[0]['function']['description']
        assert 'arm_motion' in schema['properties'], 'The public MCP schema must remain unchanged'
        assert calls == []
    run(scenario())


@pytest.mark.parametrize('name', ['move', 'plan_run'])
@pytest.mark.parametrize('arm_motion', [True, 'true', 1, '1', None, 0])
def test_forged_model_arm_and_play_is_rejected_before_dispatch(tmp_path, monkeypatch, name, arm_motion):
    assistant, settings, _, calls = setup(tmp_path, tools=['move'])
    public = settings.public()
    public['defaults']['robot_tools'] = [name]
    monkeypatch.setattr(settings, 'public', lambda: public)
    async def scenario():
        with pytest.raises(ValueError, match='Models cannot arm'):
            await assistant._execute_tool(('robot', name), {'name': 'stand', 'arm_motion': arm_motion})
        assert calls == []
        await assistant._execute_tool(('robot', name), {'name': 'stand', 'arm_motion': False})
        assert calls == [(name, {'name': 'stand', 'arm_motion': False})]
    run(scenario())


def test_model_receives_arm_and_play_rejection_without_moving(tmp_path, monkeypatch):
    assistant, _, _, calls = setup(tmp_path, tools=['move'])
    turns = []
    async def complete(provider, messages, tools, options):
        turns.append(True)
        if len(turns) == 1:
            return response(calls=[call('robot_move', {'name': 'stand', 'arm_motion': True})])
        assert 'Models cannot arm' in messages[-1]['content']
        return response('Please arm motion manually first.')
    monkeypatch.setattr(integrations, 'complete', complete)
    async def scenario():
        job = await finish(assistant, assistant.start('Play stand', use_tools=True))
        assert job['status'] == 'complete' and job['trace'][0]['status'] == 'error'
        assert calls == []
    run(scenario())


@pytest.mark.parametrize('name,use_tools',[('robot_arm',True),('robot_admin_save_provider',True),('unknown',True),('robot_get_status',False)])
def test_unknown_or_undeclared_tool_calls_never_execute(tmp_path,monkeypatch,name,use_tools):
    assistant,_,_,calls=setup(tmp_path)
    async def complete(*a,**kw): return response(calls=[call(name)])
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Try tools',use_tools=use_tools))
        assert job['status']=='failed' and 'outside its allowed list' in job['error']
        assert calls==[]
    run(scenario())


def test_tool_error_is_returned_as_data_and_trace_preserved(tmp_path,monkeypatch):
    assistant,_,_,_=setup(tmp_path)
    async def invoke(name,args): return {'isError':True,'error':'Sensor unavailable'}
    assistant.call_robot_tool=invoke
    turns=[]
    async def complete(provider,messages,tools,options):
        turns.append(messages.copy())
        if len(turns)==1: return response(calls=[call('robot_get_status')])
        result=json.loads(messages[-1]['content'])
        assert messages[-1]['role']=='tool' and messages[-1]['tool_call_id']=='call_1'
        assert result=={'isError':True,'error':'Sensor unavailable'}
        return response('The sensor is unavailable')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Read sensor',use_tools=True))
        assert job['status']=='complete' and job['trace'][0]['status']=='error'
        assert job['result']['tool_calls']==1
    run(scenario())


def test_eight_call_limit_stops_before_ninth_invocation(tmp_path,monkeypatch):
    assistant,_,_,calls=setup(tmp_path)
    async def complete(*a,**kw):
        return response(calls=[call('robot_get_status',id=f'call_{i}') for i in range(9)])
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Read many',use_tools=True))
        assert job['status']=='failed' and 'eight-tool-call' in job['error']
        assert len(calls)==8
    run(scenario())


def test_five_round_limit(tmp_path,monkeypatch):
    assistant,_,_,calls=setup(tmp_path)
    async def complete(*a,**kw): return response(calls=[call('robot_get_status')])
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Keep going',use_tools=True))
        assert job['status']=='failed' and 'five-round' in job['error']
        assert len(calls)==5
    run(scenario())


def test_robot_permission_revoked_while_model_waits(tmp_path,monkeypatch):
    assistant,settings,_,calls=setup(tmp_path)
    turns=[]
    async def complete(provider,messages,tools,options):
        turns.append(True)
        if len(turns)==1:
            settings.save_defaults({'robot_tools':[]})
            return response(calls=[call('robot_get_status')])
        assert 'revoked' in messages[-1]['content']
        return response('Access was revoked')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Read status',use_tools=True))
        assert job['status']=='complete' and job['trace'][0]['status']=='error'
        assert calls==[]
    run(scenario())


@pytest.mark.parametrize('change',['disable','delete','model','endpoint','key'])
def test_provider_changed_inflight_cannot_execute_returned_tools(tmp_path,monkeypatch,change):
    assistant,settings,provider_id,calls=setup(tmp_path)
    async def complete(*a,**kw):
        if change=='delete': settings.delete_provider(provider_id)
        else:
            update={'id':provider_id}
            if change=='disable': update['enabled']=False
            elif change=='model': update['model']='different'
            elif change=='endpoint': update.update(base_url='http://127.0.0.1:9999',clear_api_key=True)
            else: update['api_key']='new-key'
            settings.save_provider(update)
        return response(calls=[call('robot_get_status')])
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Read status',use_tools=True))
        assert job['status']=='failed' and calls==[]
        assert 'test-secret' not in json.dumps(job)
    run(scenario())


def test_remote_allowlist_revocation_is_honored(tmp_path,monkeypatch):
    assistant,settings,_,calls=setup(tmp_path)
    connection=settings.save_connection({'name':'Remote','url':'http://127.0.0.1:9000/mcp','allowed_tools':['lookup']})
    async def inspect(record):
        return {'tools':[{'name':'lookup','description':'Look up','inputSchema':{'type':'object'}},
                         {'name':'never','inputSchema':{'type':'object'}}]}
    monkeypatch.setattr(integrations,'inspect_mcp',inspect)
    seen=[]
    async def complete(provider,messages,tools,options):
        if not seen:
            remote=[tool['function']['name'] for tool in tools if tool['function']['name'].startswith('remote_')]
            assert len(remote)==1
            seen.extend(remote)
            settings.save_connection({'id':connection['id'],'allowed_tools':[]})
            return response(calls=[call(remote[0])])
        assert 'not enabled' in messages[-1]['content']
        return response('Access revoked')
    monkeypatch.setattr(integrations,'complete',complete)
    async def session(*a,**kw): pytest.fail('No MCP session should open after revocation')
    monkeypatch.setattr(integrations,'_mcp_session',session)
    async def scenario():
        job=await finish(assistant,assistant.start('Remote lookup',use_tools=True))
        assert job['status']=='complete' and job['trace'][0]['status']=='error' and calls==[]
    run(scenario())


def test_cancel_before_task_starts_finishes_job(tmp_path,monkeypatch):
    assistant,_,_,calls=setup(tmp_path)
    async def complete(*a,**kw): pytest.fail('Cancelled job should not reach provider')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=assistant.start('Cancel me')
        result=await assistant.cancel(job['id'])
        assert result['status']=='cancelled' and result['finished_at'] is not None
        assert calls==[]
    run(scenario())


def test_cancel_network_request_closes_and_marks_trace(tmp_path,monkeypatch):
    assistant,_,_,calls=setup(tmp_path,['move'])
    entered,closed=asyncio.Event(),[]
    async def complete(*a,**kw): return response(calls=[call('robot_move',{'name':'stand'})])
    async def invoke(name,args):
        calls.append((name,args))
        if name=='stop': return {'stopped':True}
        try:
            entered.set()
            await asyncio.sleep(100)
        finally: closed.append(True)
    assistant.call_robot_tool=invoke
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=assistant.start('Stand',use_tools=True)
        await entered.wait()
        result=await assistant.cancel(job['id'])
        assert result['status']=='cancelled' and result['trace'][0]['status']=='cancelled'
        assert closed==[True] and calls[-1]==('stop',{})
    run(scenario())


def test_model_robot_stop_does_not_cancel_or_await_itself(tmp_path,monkeypatch):
    assistant,_,_,calls=setup(tmp_path,['stop'])
    turns=[]
    async def invoke(name,args):
        calls.append((name,args))
        await assistant.cancel_all()
        return {'stopped':True}
    assistant.call_robot_tool=invoke
    async def complete(*a,**kw):
        turns.append(True)
        return response(calls=[call('robot_stop')]) if len(turns)==1 else response('Stopped')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await asyncio.wait_for(finish(assistant,assistant.start('Stop',use_tools=True)),1)
        assert job['status']=='complete' and calls==[('stop',{})]
    run(scenario())


def test_failure_after_motion_stops_even_when_permission_revoked(tmp_path,monkeypatch):
    assistant,settings,_,calls=setup(tmp_path,['move'])
    turns=[]
    async def complete(*a,**kw):
        turns.append(True)
        if len(turns)==1: return response(calls=[call('robot_move',{'name':'stand'})])
        settings.save_defaults({'robot_tools':[]})
        raise integrations.IntegrationError('Language model timed out')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Stand',use_tools=True))
        assert job['status']=='failed' and job['error']=='Language model timed out'
        assert calls==[('move',{'name':'stand'}),('stop',{})]
    run(scenario())


def test_job_timeout_cancels_network_and_bounds_entire_job(tmp_path,monkeypatch):
    assistant,settings,_,calls=setup(tmp_path)
    public=settings.public
    def short_budget():
        data=public();data['defaults']['timeout_seconds']=.02;return data
    monkeypatch.setattr(settings,'public',short_budget)
    closed=[]
    async def complete(*a,**kw):
        try: await asyncio.sleep(100)
        finally: closed.append(True)
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        job=await finish(assistant,assistant.start('Slow request'))
        assert job['status']=='failed' and 'timed out' in job['error']
        assert closed==[True] and calls==[]
    run(scenario())


def test_single_job_and_history_bound(tmp_path,monkeypatch):
    assistant,_,_,_=setup(tmp_path)
    async def complete(*a,**kw): return response('Done')
    monkeypatch.setattr(integrations,'complete',complete)
    async def scenario():
        first=assistant.start('One')
        with pytest.raises(ValueError,match='already running'): assistant.start('Two')
        await finish(assistant,first)
        for _ in range(21): await finish(assistant,assistant.start('Next'))
        assert len(assistant.jobs)==20 and len(assistant.tasks)==20
        with pytest.raises(ValueError,match='Unknown model job'): assistant.snapshot(first['id'])
    run(scenario())
