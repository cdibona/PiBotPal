import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import httpx
import pytest
from mcp import types

from pibotpal import integrations as integrations

REAL_HTTP_CLIENT = httpx.AsyncClient


OLLAMA = {'id': 'local', 'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434',
          'model': 'qwen-custom:14b-q4_K_M', 'enabled': True}
OPENAI = {'id': 'cloud', 'kind': 'openai', 'base_url': 'https://127.0.0.1:444/v1',
          'model': 'chosen-model', 'api_key': 'private-test-key', 'enabled': True}
MCP = {'id': 'remote', 'url': 'http://127.0.0.1:9022/mcp', 'transport': 'streamable_http',
       'enabled': True, 'allowed_tools': ['lookup'], 'bearer_token': 'private-mcp-token'}


def run(awaitable):
    return asyncio.run(awaitable)


def mocked_http(monkeypatch, handler):
    def client(**kwargs):
        return REAL_HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(integrations.httpx, 'AsyncClient', client)


def test_discovery_preserves_custom_qwen_tag_and_does_not_infer(monkeypatch):
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/api/tags'
        return httpx.Response(200, json={'models': [{'name': OLLAMA['model'], 'size': 123}]})
    mocked_http(monkeypatch, handler)
    result=run(integrations.discover_models(OLLAMA))
    assert result['models'] == [{'id': OLLAMA['model'], 'size': 123}]
    assert len(requests)==1


def test_openai_models_authentication_and_secret_redaction(monkeypatch):
    def handler(request):
        assert request.url.path == '/v1/models'
        assert request.headers['authorization']=='Bearer private-test-key'
        return httpx.Response(200, json={'data':[{'id':'chosen-model','owned_by':'private-test-key'}]})
    mocked_http(monkeypatch, handler)
    result=run(integrations.discover_models(OPENAI))
    assert result['models'][0]['owned_by']=='[redacted]'
    assert 'private-test-key' not in json.dumps(result)


def test_native_ollama_tool_messages_and_calls_normalize(monkeypatch):
    tool={'type':'function','function':{'name':'lookup','description':'Lookup','parameters':{'type':'object'}}}
    messages=[{'role':'user','content':'Read status'},
              {'role':'assistant','content':'','tool_calls':[{'id':'call_1','type':'function','function':{'name':'lookup','arguments':'{"value": 2}'}}]},
              {'role':'tool','tool_call_id':'call_1','content':'found'}]
    def handler(request):
        assert request.url.path=='/api/chat'
        body=json.loads(request.content)
        assert body['model']==OLLAMA['model']
        assert body['stream'] is False and body['keep_alive']=='5m'
        assert body['think'] is False
        assert body['options']=={'temperature':.3,'num_predict':256}
        assert body['messages'][1]['tool_calls'][0]['function']['arguments']=={'value':2}
        assert body['messages'][2]['tool_name']=='lookup'
        assert body['tools']==[tool]
        return httpx.Response(200,json={'model':OLLAMA['model'],'message':{'role':'assistant','content':'',
                 'tool_calls':[{'function':{'name':'lookup','arguments':{'value':3}}}]},'eval_count':9})
    mocked_http(monkeypatch,handler)
    result=run(integrations.complete(OLLAMA,messages,[tool],{'max_tokens':256,'temperature':.3,'think':False}))
    call=result['message']['tool_calls'][0]
    assert call['id'].startswith('call_')
    assert json.loads(call['function']['arguments'])=={'value':3}
    assert result['usage']['completion_tokens']==9


def test_openai_completion_payload_and_response(monkeypatch):
    def handler(request):
        body=json.loads(request.content)
        assert request.url.path=='/v1/chat/completions'
        assert body['max_tokens']==1024 and body['stream'] is False
        assert body['messages']==[{'role':'user','content':'Hello'}]
        return httpx.Response(200,json={'model':'chosen-model','choices':[{'message':{'role':'assistant','content':'Hello!'}}],
                                       'usage':{'total_tokens':4}})
    mocked_http(monkeypatch,handler)
    result=run(integrations.complete(OPENAI,[{'role':'user','content':'Hello'}]))
    assert result['message']=={'role':'assistant','content':'Hello!','tool_calls':[]}
    assert result['usage']=={'total_tokens':4}


@pytest.mark.parametrize('status',[301,401,403,404,429,500])
def test_http_errors_do_not_expose_remote_body_or_secrets(monkeypatch,status):
    mocked_http(monkeypatch,lambda request:httpx.Response(status,headers={'Location':'http://127.0.0.1/other'},text='private-test-key untrusted body'))
    with pytest.raises(integrations.IntegrationError) as error:
        run(integrations.discover_models(OPENAI))
    assert 'private-test-key' not in str(error.value)
    assert 'untrusted body' not in str(error.value)
    if status==301:
        assert 'redirect' in str(error.value)


def test_response_limit_and_invalid_json(monkeypatch):
    mocked_http(monkeypatch,lambda request:httpx.Response(200,headers={'Content-Length':str(integrations.MAX_RESPONSE_BYTES+1)},content=b'{}'))
    with pytest.raises(integrations.IntegrationError,match='2 MiB'):
        run(integrations.discover_models(OLLAMA))
    mocked_http(monkeypatch,lambda request:httpx.Response(200,content=b'not-json-secret'))
    with pytest.raises(integrations.IntegrationError,match='invalid response'):
        run(integrations.discover_models(OLLAMA))


@pytest.mark.parametrize('url',['http://169.254.169.254','http://0.0.0.0','http://224.0.0.1','http://[fe80::1]','http://[::ffff:169.254.169.254]'])
def test_metadata_and_unroutable_destinations_rejected_before_http(monkeypatch,url):
    def handler(request):
        pytest.fail('Blocked destination reached HTTP transport')
    mocked_http(monkeypatch,handler)
    with pytest.raises(integrations.IntegrationError,match='not allowed'):
        run(integrations.discover_models({**OLLAMA,'base_url':url}))


def test_dns_destination_is_validated(monkeypatch):
    async def check():
        loop=asyncio.get_running_loop()
        async def resolve(*a,**kw):
            return [(2,1,6,'',('169.254.169.254',80))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve)
        await integrations._check_destination(httpx.URL('http://metadata.example'))
    with pytest.raises(integrations.IntegrationError,match='not allowed'):
        run(check())


def test_provider_validation_and_cancellation(monkeypatch):
    with pytest.raises(ValueError,match='disabled'):
        run(integrations.complete({**OLLAMA,'enabled':False},[{'role':'user','content':'Hello'}]))
    with pytest.raises(ValueError,match='Timeout'):
        run(integrations.complete(OLLAMA,[{'role':'user','content':'Hello'}],options={'timeout_seconds':999}))
    with pytest.raises(ValueError,match='tokens'):
        run(integrations.complete(OLLAMA,[{'role':'user','content':'Hello'}],options={'max_tokens':99999}))
    with pytest.raises(ValueError,match='embedded credentials'):
        run(integrations.discover_models({**OLLAMA,'base_url':'http://user:secret@example.com'}))
    closed=[]
    async def request(*args,**kwargs):
        try:
            await asyncio.sleep(100)
        finally:
            closed.append(True)
    monkeypatch.setattr(integrations,'_json_request',request)
    async def cancel():
        task=asyncio.create_task(integrations.complete(OLLAMA,[{'role':'user','content':'Hello'}]))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(cancel())
    assert closed==[True]


def fake_mcp(monkeypatch,session):
    lifecycles=[]
    @asynccontextmanager
    async def connection(record,timeout):
        lifecycles.append('open')
        try:
            yield session,SimpleNamespace(serverInfo={'name':'fixture','version':'1'},protocolVersion='2025-11-25')
        finally:
            lifecycles.append('close')
    monkeypatch.setattr(integrations,'_mcp_session',connection)
    return lifecycles


def test_mcp_inspection_paginates_without_invoking_tools(monkeypatch):
    class Session:
        async def list_tools(self,cursor=None):
            if cursor is None:
                return types.ListToolsResult(tools=[types.Tool(name='lookup',description='A tool',inputSchema={'type':'object'})],nextCursor='second')
            assert cursor=='second'
            return types.ListToolsResult(tools=[types.Tool(name='other',inputSchema={'type':'object'})])
        async def call_tool(self,*a,**kw):
            pytest.fail('Inspect must never invoke a tool')
    lifecycle=fake_mcp(monkeypatch,Session())
    result=run(integrations.inspect_mcp({**MCP,'enabled':False}))
    assert result['server']['name']=='fixture'
    assert [t['name'] for t in result['tools']]==['lookup','other']
    assert lifecycle==['open','close']


def test_mcp_call_allowlist_and_redaction(monkeypatch):
    class Session:
        async def call_tool(self,name,arguments):
            assert name=='lookup' and arguments=={'query':'test'}
            return types.CallToolResult(content=[types.TextContent(type='text',text='private-mcp-token result')],structuredContent={'found':True})
    lifecycle=fake_mcp(monkeypatch,Session())
    for record,name in [({**MCP,'enabled':False},'lookup'),(MCP,'not_allowed')]:
        with pytest.raises(ValueError):
            run(integrations.call_mcp(record,name,{}))
    assert lifecycle==[]
    result=run(integrations.call_mcp(MCP,'lookup',{'query':'test'}))
    assert result['content'][0]['text']=='[redacted] result'
    assert result['structuredContent']=={'found':True}
    assert result['isError'] is False
    assert lifecycle==['open','close']


def test_mcp_repeated_cursor_and_unsafe_errors_sanitized(monkeypatch):
    class Session:
        async def list_tools(self,cursor=None):
            return types.ListToolsResult(tools=[],nextCursor='same')
    fake_mcp(monkeypatch,Session())
    with pytest.raises(integrations.IntegrationError,match='repeated pagination'):
        run(integrations.inspect_mcp(MCP))
    class BadSession:
        async def list_tools(self,cursor=None):
            raise RuntimeError('private-mcp-token from https://host/?secret=abc')
    fake_mcp(monkeypatch,BadSession())
    with pytest.raises(integrations.IntegrationError) as error:
        run(integrations.inspect_mcp(MCP))
    assert 'secret' not in str(error.value) and 'private-mcp-token' not in str(error.value)


def test_loopback_and_tailscale_addresses_allowed():
    for address in ('127.0.0.1', '::1', '100.79.199.6', 'fd7a:115c:a1e0::1'):
        integrations._safe_ip(address)


def test_oversized_chunked_or_compressed_response_rejected(monkeypatch):
    import gzip
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(b'x' * (integrations.MAX_RESPONSE_BYTES + 100))
    mocked_http(monkeypatch, lambda request: httpx.Response(200, headers={'content-encoding':'gzip'}, stream=Chunks()))
    with pytest.raises(integrations.IntegrationError, match='2 MiB'):
        run(integrations.discover_models(OLLAMA))
