"""Async Ollama/OpenAI-compatible and remote MCP clients.

Only configured HTTP endpoints are used. LAN, Tailscale, and loopback servers
are intentional; metadata/link-local destinations and redirects are refused.
No model downloads or local processes are started by these clients.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import ipaddress
import json
import math
import socket
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOOLS = 256


class IntegrationError(RuntimeError):
    """An operator-safe error that excludes credentials and remote response bodies."""


def _timeout(value, default=30):
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 180:
        raise ValueError('Timeout must be between 1 and 180 seconds')
    return float(value)


def _origin_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError('A valid HTTP endpoint is required')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('Invalid endpoint URL') from None
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Endpoint must use HTTP(S), without embedded credentials, query, or fragment')
    if port == 0:
        raise ValueError('Endpoint port must be positive')
    return value.rstrip('/')


def _safe_ip(value):
    address = ipaddress.ip_address(value.split('%', 1)[0])
    if getattr(address, 'ipv4_mapped', None):
        address = address.ipv4_mapped
    if address.is_loopback:
        return
    if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_reserved:
        raise IntegrationError('This destination address is not allowed')
    if address.version == 4 and address in ipaddress.ip_network('0.0.0.0/8'):
        raise IntegrationError('This destination address is not allowed')


async def _check_destination(url):
    """Check every request, including SDK-generated SSE message endpoints."""
    host = url.host
    try:
        _safe_ip(host)
        return
    except ValueError:
        pass  # A hostname: resolve without blocking the server event loop.
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, url.port or (443 if url.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise IntegrationError('Server hostname could not be resolved') from None
    if not addresses:
        raise IntegrationError('Server hostname could not be resolved')
    for address in addresses:
        _safe_ip(address[4][0])


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.count = 0

    async def __aiter__(self):
        async for chunk in self.wrapped:
            self.count += len(chunk)
            if self.count > MAX_RESPONSE_BYTES:
                raise IntegrationError('Server response exceeded the 2 MiB limit')
            yield chunk

    async def aclose(self):
        await self.wrapped.aclose()


async def _request_guard(request):
    await _check_destination(request.url)


async def _response_guard(response):
    # SDK 1.x has its own redirect handling, independent of follow_redirects.
    if 300 <= response.status_code < 400:
        await response.aclose()
        raise IntegrationError('Server redirects are disabled; save the final endpoint URL')
    length = response.headers.get('content-length', '')
    if length.isdecimal() and int(length) > MAX_RESPONSE_BYTES:
        await response.aclose()
        raise IntegrationError('Server response exceeded the 2 MiB limit')
    if response.is_stream_consumed:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise IntegrationError('Server response exceeded the 2 MiB limit')
    else:
        response.stream = _LimitedStream(response.stream)
    # The raw stream cap also needs a decoded cap for servers that compress
    # despite Accept-Encoding: identity. MCP consumes aiter_lines -> aiter_bytes.
    original_bytes = response.aiter_bytes
    async def limited_bytes(*args, **kwargs):
        count = 0
        async for chunk in original_bytes(*args, **kwargs):
            count += len(chunk)
            if count > MAX_RESPONSE_BYTES:
                raise IntegrationError('Server response exceeded the 2 MiB limit')
            yield chunk
    response.aiter_bytes = limited_bytes


def _client(*, headers=None, timeout=30, auth=None, **kwargs):
    # Not honoring HTTP_PROXY prevents accidental credential routing through an
    # unrelated environment proxy. TLS certificate verification remains on.
    return httpx.AsyncClient(headers={'Accept-Encoding': 'identity', **(headers or {})}, timeout=timeout, auth=auth,
                             follow_redirects=False, trust_env=False,
                             event_hooks={'request': [_request_guard], 'response': [_response_guard]})


def _auth(record, key):
    secret = record.get(key)
    if not secret:
        return {}
    if not isinstance(secret, str) or any(c in secret for c in ('\r', '\n')):
        raise ValueError('Invalid stored credential')
    return {'Authorization': 'Bearer ' + secret}


def _redact(value, secret):
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, '[redacted]')
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secret) for key, item in value.items()}
    return value


def _safe_error(exc, service):
    if isinstance(exc, IntegrationError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return IntegrationError(f'{service} timed out; check the endpoint and configured timeout')
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return IntegrationError(f'{service} refused authentication (HTTP {status}); check the saved credential')
        if status == 404:
            return IntegrationError(f'{service} endpoint or model was not found (HTTP 404)')
        if status == 429:
            return IntegrationError(f'{service} is rate limited (HTTP 429)')
        return IntegrationError(f'{service} returned HTTP {status}; check endpoint, model, and API compatibility')
    if isinstance(exc, httpx.RequestError):
        return IntegrationError(f'{service} could not be reached; check its address, network, and TLS certificate')
    # ExceptionGroup from the MCP transport often wraps HTTP failures.
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            if isinstance(child, (IntegrationError, httpx.HTTPError, asyncio.TimeoutError, BaseExceptionGroup)):
                return _safe_error(child, service)
    return IntegrationError(f'{service} returned an invalid response or could not complete the request')


def _provider(provider, *, require_enabled=True):
    if require_enabled and provider.get('enabled', True) is not True:
        raise ValueError('This provider is disabled')
    if provider.get('kind') not in ('ollama', 'openai'):
        raise ValueError('Unknown provider type')
    return _origin_url(provider.get('base_url'))


async def _json_request(method, url, *, headers, timeout, body=None):
    async with _client(headers=headers, timeout=httpx.Timeout(timeout, connect=min(timeout, 10))) as client:
        # Stream explicitly so an unexpected/chunked body cannot allocate without a bound.
        async with client.stream(method, url, json=body) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise IntegrationError('Server response exceeded the 2 MiB limit')
            return json.loads(data)


async def discover_models(provider, timeout=10):
    """List installed/available models; this never pulls a model or runs inference."""
    base = _provider(provider, require_enabled=False)
    timeout = _timeout(timeout)
    try:
        async with asyncio.timeout(timeout):
            data = await _json_request('GET', base + ('/api/tags' if provider['kind'] == 'ollama' else '/models'),
                                       headers=_auth(provider, 'api_key'), timeout=timeout)
        rows = data['models'] if provider['kind'] == 'ollama' else data['data']
        if not isinstance(rows, list):
            raise ValueError('Invalid model list')
        models = []
        for row in rows[:1024]:
            identifier = row.get('name') or row.get('model') if provider['kind'] == 'ollama' else row.get('id')
            if not isinstance(identifier, str) or not identifier:
                continue
            model = {'id': identifier}
            if provider['kind'] == 'ollama':
                model.update({k: row[k] for k in ('size', 'modified_at', 'details') if k in row})
            else:
                model.update({k: row[k] for k in ('owned_by',) if k in row})
            models.append(model)
        return _redact({'models': models, 'provider_id': provider.get('id'), 'kind': provider['kind']}, provider.get('api_key'))
    except Exception as exc:
        raise _safe_error(exc, 'Model discovery') from None


def _messages(messages, native=False):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError('Provide between 1 and 64 chat messages')
    converted, names = [], {}
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant', 'tool'):
            raise ValueError('Invalid chat message role')
        content = message.get('content') or ''
        if not isinstance(content, str) or len(content) > 131072:
            raise ValueError('Chat message content must be text, at most 131072 characters')
        result = {'role': message['role'], 'content': content}
        calls = message.get('tool_calls')
        if calls:
            if not isinstance(calls, list) or len(calls) > 32:
                raise ValueError('Too many tool calls')
            result['tool_calls'] = []
            for call in calls:
                function = call['function']
                args = function.get('arguments', '{}')
                args = json.loads(args) if isinstance(args, str) else args
                if not isinstance(args, dict):
                    raise ValueError('Tool arguments must be an object')
                names[call.get('id')] = function['name']
                if native:
                    result['tool_calls'].append({'function': {'name': function['name'], 'arguments': args}})
                else:
                    result['tool_calls'].append({'id': call['id'], 'type': 'function',
                                                 'function': {'name': function['name'], 'arguments': json.dumps(args)}})
        if message['role'] == 'tool':
            if native:
                result['tool_name'] = message.get('tool_name') or names.get(message.get('tool_call_id'), '')
            else:
                result['tool_call_id'] = message['tool_call_id']
        converted.append(result)
    if len(json.dumps(converted)) > MAX_RESPONSE_BYTES:
        raise ValueError('Chat context is too large')
    return converted


def _normalize_message(message):
    content = message.get('content') or ''
    if not isinstance(content, str):
        raise ValueError('Invalid assistant message')
    normalized = {'role': 'assistant', 'content': content, 'tool_calls': []}
    calls = message.get('tool_calls') or []
    if not isinstance(calls, list) or len(calls) > 32:
        raise ValueError('Invalid tool call list')
    for call in calls:
        function = call['function']
        name, arguments = function['name'], function.get('arguments', {})
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError('Invalid tool name')
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError('Invalid tool arguments')
        normalized['tool_calls'].append({'id': call.get('id') or 'call_' + uuid4().hex,
                                         'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}})
    return normalized


async def complete(provider, messages, tools=None, options=None):
    """One bounded model turn, returning normalized OpenAI-style tool calls."""
    base = _provider(provider)
    model = provider.get('model')
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ValueError('Select a model for this provider')
    options = options or {}
    timeout = _timeout(options.get('timeout_seconds'), 120)
    tokens = options.get('max_tokens', 1024)
    temperature = options.get('temperature', .2)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= 8192:
        raise ValueError('Maximum output must be between 1 and 8192 tokens')
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError('Temperature must be between 0 and 2')
    if tools is not None and (not isinstance(tools, list) or len(tools) > MAX_TOOLS):
        raise ValueError('Too many tools')
    native = provider['kind'] == 'ollama'
    if 'think' in options and not isinstance(options['think'], bool):
        raise ValueError('Thinking option must be a boolean')
    body = {'model': model, 'messages': _messages(messages, native), 'stream': False}
    if native:
        body.update({'keep_alive': '5m', 'options': {'temperature': temperature, 'num_predict': tokens}})
        if 'think' in options:
            body['think'] = options['think']
    else:
        body.update({'temperature': temperature, 'max_tokens': tokens})
    if tools:
        body['tools'] = tools
    if len(json.dumps(body)) > MAX_RESPONSE_BYTES:
        raise ValueError('Model request is too large')
    try:
        async with asyncio.timeout(timeout):
            data = await _json_request('POST', base + ('/api/chat' if native else '/chat/completions'),
                                       headers=_auth(provider, 'api_key'), timeout=timeout, body=body)
        message = data['message'] if native else data['choices'][0]['message']
        result = {'message': _normalize_message(message), 'model': data.get('model', model),
                  'usage': ({'prompt_tokens': data.get('prompt_eval_count'), 'completion_tokens': data.get('eval_count')}
                            if native else data.get('usage', {})),
                  'provider_id': provider.get('id')}
        return _redact(result, provider.get('api_key'))
    except Exception as exc:
        raise _safe_error(exc, 'Language model') from None


@asynccontextmanager
async def _mcp_session(connection, timeout):
    url = _origin_url(connection.get('url'))
    headers = _auth(connection, 'bearer_token')
    transport = connection.get('transport', 'streamable_http')
    if transport == 'streamable_http':
        async with _client(headers=headers, timeout=httpx.Timeout(timeout, connect=min(timeout, 10))) as client:
            async with streamable_http_client(url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)) as session:
                    initialized = await session.initialize()
                    yield session, initialized
    elif transport == 'sse':
        async with sse_client(url, headers=headers, timeout=min(timeout, 10), sse_read_timeout=timeout,
                              httpx_client_factory=_client) as streams:
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)) as session:
                initialized = await session.initialize()
                yield session, initialized
    else:
        raise ValueError('Unsupported MCP transport')


def _dump(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json', by_alias=True, exclude_none=True)
    return value


async def inspect_mcp(connection, timeout=15):
    """Initialize and list tools. Disabled connections may still be explicitly tested."""
    timeout = _timeout(timeout)
    try:
        async with asyncio.timeout(timeout):
            async with _mcp_session(connection, timeout) as (session, initialized):
                tools, cursor, seen = [], None, set()
                while True:
                    page = await session.list_tools(cursor=cursor)
                    tools.extend(_dump(tool) for tool in page.tools)
                    cursor = page.nextCursor
                    if len(tools) > MAX_TOOLS:
                        raise IntegrationError('MCP server advertises more than 256 tools')
                    if not cursor:
                        break
                    if cursor in seen:
                        raise IntegrationError('MCP server returned a repeated pagination cursor')
                    seen.add(cursor)
                result = {'server': _dump(initialized.serverInfo), 'protocol_version': initialized.protocolVersion,
                          'tools': tools, 'connection_id': connection.get('id')}
                if len(json.dumps(result)) > MAX_RESPONSE_BYTES:
                    raise IntegrationError('MCP tool catalog exceeded the 2 MiB limit')
                return _redact(result, connection.get('bearer_token'))
    except Exception as exc:
        raise _safe_error(exc, 'MCP server') from None


async def call_mcp(connection, tool_name, arguments, timeout=30):
    """Call exactly one explicitly allowed tool through a configured MCP connection."""
    timeout = _timeout(timeout)
    if connection.get('enabled', True) is not True:
        raise ValueError('This MCP connection is disabled')
    if not isinstance(tool_name, str) or tool_name not in connection.get('allowed_tools', []):
        raise ValueError('This tool is not enabled for the MCP connection')
    if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 131072:
        raise ValueError('Tool arguments must be an object, at most 128 KiB')
    try:
        async with asyncio.timeout(timeout):
            async with _mcp_session(connection, timeout) as (session, _):
                result = _dump(await session.call_tool(tool_name, arguments=arguments))
                if len(json.dumps(result)) > MAX_RESPONSE_BYTES:
                    raise IntegrationError('MCP tool result exceeded the 2 MiB limit')
                return _redact(result, connection.get('bearer_token'))
    except Exception as exc:
        raise _safe_error(exc, 'MCP tool') from None
