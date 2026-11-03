from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from pibotpal.settings import DEFAULTS, Settings, endpoint


def provider(**changes):
    return {'name':'Qwen','kind':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen3:32b',
            'api_key':'secret-api-key',**changes}


def connection(**changes):
    return {'name':'Research','url':'http://127.0.0.1:9022/mcp','transport':'streamable_http',
            'bearer_token':'secret-mcp-token','allowed_tools':['lookup'],**changes}


def test_private_persistence_redaction_and_restart(tmp_path):
    store=Settings(tmp_path)
    p=store.save_provider(provider())
    c=store.save_connection(connection())
    store.save_defaults({'test_provider_id':p['id'],'assistant_provider_id':p['id']})
    public=store.public()
    assert p['api_key_set'] is True and 'api_key' not in p
    assert c['token_set'] is True and 'bearer_token' not in c
    assert 'secret-api-key' not in json.dumps(public) and 'secret-mcp-token' not in json.dumps(public)
    assert os.stat(tmp_path/'settings.json').st_mode & 0o777 == 0o600
    resumed=Settings(tmp_path)
    assert resumed.public()==public
    assert resumed.provider()['api_key']=='secret-api-key'
    assert resumed.connection(c['id'])['bearer_token']=='secret-mcp-token'
    assert list(tmp_path.glob('.settings-*'))==[]


def test_browser_public_form_roundtrip_preserves_secrets(tmp_path):
    store=Settings(tmp_path)
    p=store.save_provider(provider())
    c=store.save_connection(connection())
    p.update({'name':'Renamed','api_key':''})
    c.update({'name':'Renamed MCP','bearer_token':''})
    store.save_provider(p)
    store.save_connection(c)
    assert store.provider(p['id'])['api_key']=='secret-api-key'
    assert store.connection(c['id'])['bearer_token']=='secret-mcp-token'
    store.save_provider({'id':p['id'],'clear_api_key':True})
    store.save_connection({'id':c['id'],'clear_bearer_token':True})
    assert not store.public()['providers'][0]['api_key_set']
    assert not store.public()['connections'][0]['token_set']


@pytest.mark.parametrize('field,new_value',[('base_url','http://10.0.0.2:11434'),('kind','openai')])
def test_provider_endpoint_or_kind_change_cannot_reuse_secret(tmp_path,field,new_value):
    store=Settings(tmp_path)
    item=store.save_provider(provider())
    before=(tmp_path/'settings.json').read_bytes()
    with pytest.raises(ValueError,match='Endpoint changed'):
        store.save_provider({'id':item['id'],field:new_value,'api_key':''})
    assert (tmp_path/'settings.json').read_bytes()==before
    store.save_provider({'id':item['id'],field:new_value,'api_key':'new-explicit-secret'})
    assert store.provider(item['id'])['api_key']=='new-explicit-secret'


@pytest.mark.parametrize('field,new_value',[('url','https://other.test/mcp'),('transport','sse')])
def test_mcp_endpoint_or_transport_change_requires_credential_decision(tmp_path,field,new_value):
    store=Settings(tmp_path)
    item=store.save_connection(connection())
    with pytest.raises(ValueError,match='Endpoint changed'):
        store.save_connection({'id':item['id'],field:new_value})
    store.save_connection({'id':item['id'],field:new_value,'clear_bearer_token':True})
    assert store.connection(item['id'])['bearer_token']==''


def test_defaults_delete_disable_and_integer_normalization(tmp_path):
    store=Settings(tmp_path)
    p=store.save_provider(provider())
    store.save_defaults({'test_provider_id':p['id'],'assistant_provider_id':p['id'],'max_tokens':128.0})
    assert isinstance(store.public()['defaults']['max_tokens'],int)
    store.save_provider({'id':p['id'],'enabled':False})
    assert store.public()['defaults']['test_provider_id'] is None
    with pytest.raises(ValueError,match='enabled provider'):
        store.save_defaults({'test_provider_id':p['id']})
    store.save_provider({'id':p['id'],'enabled':True})
    store.save_defaults({'test_provider_id':p['id'],'assistant_provider_id':p['id']})
    store.delete_provider(p['id'])
    assert store.public()['defaults']['test_provider_id'] is None
    assert store.public()['defaults']['assistant_provider_id'] is None


@pytest.mark.parametrize('values',[
    {'max_tokens':True},{'max_tokens':128.5},{'max_tokens':1},{'temperature':float('nan')},
    {'timeout_seconds':181},{'robot_tools':['arm']},{'unknown':'value'}, {'test_provider_id':'missing'},
])
def test_invalid_defaults_leave_previous_state(tmp_path,values):
    store=Settings(tmp_path)
    before=store.public()
    with pytest.raises(ValueError):
        store.save_defaults(values)
    assert store.public()==before
    assert not store.path.exists()


def test_secret_clear_conflict_and_newline_rejected_without_leak(tmp_path):
    store=Settings(tmp_path)
    for values in [provider(api_key='secret\r\nInjected: yes'),provider(api_key='secret',clear_api_key=True),provider(clear_api_key='true')]:
        with pytest.raises(ValueError) as error:
            store.save_provider(values)
        assert 'secret' not in str(error.value)
    assert store.public()['providers']==[]


@pytest.mark.parametrize('url',[
    'file:///etc/passwd','http://user:secret@example.com','http://example.com/?secret=1',
    'http://example.com/#fragment','http://169.254.169.254','http://224.0.0.1','http://0.0.0.0',
    'http://example.com:0','http://example.com:99999','http://bad host','http://metadata.google.internal',
    'http://[::ffff:169.254.169.254]', 'http://0.1.2.3',
])
def test_endpoint_rejects_unsafe_shapes(url):
    with pytest.raises(ValueError): endpoint(url)


def test_local_endpoints_remain_supported():
    for url in ['http://localhost:11434','http://127.0.0.1:11434','http://[::1]:11434','http://100.79.199.6:11434','http://192.168.1.3:8080/v1']:
        assert endpoint(url)==url
    assert endpoint('HTTPS://EXAMPLE.COM/v1/')=='https://example.com/v1'


def test_atomic_save_failure_preserves_previous_configuration(tmp_path,monkeypatch):
    store=Settings(tmp_path)
    item=store.save_provider(provider())
    before=(tmp_path/'settings.json').read_bytes()
    def failure(*a,**kw): raise OSError('Disk failure')
    monkeypatch.setattr('pibotpal.settings.os.replace',failure)
    with pytest.raises(OSError): store.save_provider({'id':item['id'],'name':'Changed'})
    assert (tmp_path/'settings.json').read_bytes()==before
    assert store.provider(item['id'])['name']=='Qwen'
    assert list(tmp_path.glob('.settings-*'))==[]


@pytest.mark.parametrize('corruption', ['not-json', {'version':1,'providers':[{}],'connections':[],'defaults':{}},
    {'version':1,'providers':[],'connections':[],'defaults':{'api_key':'hidden-secret'}},
    {'version':1,'providers':[],'connections':[],'defaults':{'max_tokens':True}}])
def test_corrupt_settings_fail_closed_without_rewrite(tmp_path,corruption):
    path=tmp_path/'settings.json'
    raw=corruption if isinstance(corruption,str) else json.dumps(corruption)
    path.write_text(raw)
    with pytest.raises(RuntimeError,match='repair settings.json') as error:
        Settings(tmp_path)
    assert 'hidden-secret' not in str(error.value)
    assert path.read_text()==raw


def test_duplicate_ids_and_extra_secret_fields_fail_closed(tmp_path):
    store=Settings(tmp_path)
    store.save_provider(provider())
    original=json.loads(store.path.read_text())
    broken=json.loads(store.path.read_text())
    broken['providers'].append(broken['providers'][0].copy())
    store.path.write_text(json.dumps(broken))
    with pytest.raises(RuntimeError): Settings(tmp_path)
    original['providers'][0]['unrecognized_secret']='never-public'
    store.path.write_text(json.dumps(original))
    with pytest.raises(RuntimeError): Settings(tmp_path)


def test_concurrent_saves_are_not_lost_and_results_are_copies(tmp_path):
    store=Settings(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries=list(pool.map(lambda i: store.save_provider(provider(name=f'Provider {i}',api_key='')),range(8)))
    assert len(store.public()['providers'])==8
    assert len(Settings(tmp_path).public()['providers'])==8
    data=store.public()
    data['providers'][0]['name']='mutated'
    internal=store.provider(entries[0]['id'])
    internal['model']='mutated'
    assert all(p['name']!='mutated' for p in store.public()['providers'])
    assert store.provider(entries[0]['id'])['model']=='qwen3:32b'
