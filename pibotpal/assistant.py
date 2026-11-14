"""Bounded model jobs with explicit MCP tool allowlists and cancellable network calls."""
from __future__ import annotations
import asyncio
import copy
import json
import time
from uuid import uuid4
from . import integrations


class Assistant:
    def __init__(self, settings, list_robot_tools, call_robot_tool):
        self.settings = settings
        self.list_robot_tools = list_robot_tools
        self.call_robot_tool = call_robot_tool
        self.jobs = {}
        self.tasks = {}

    def snapshot(self, job_id):
        if job_id not in self.jobs:
            raise ValueError('Unknown model job; jobs are cleared when the service restarts')
        return copy.deepcopy(self.jobs[job_id])

    def summaries(self):
        return [{k: job[k] for k in ('id', 'status', 'provider_id', 'model', 'started_at', 'finished_at')}
                for job in reversed(list(self.jobs.values()))]

    def start(self, prompt, provider_id=None, purpose='tests', use_tools=False):
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
            raise ValueError('Prompt must contain 1–16000 characters')
        if not isinstance(use_tools, bool):
            raise ValueError('use_tools must be a boolean')
        if any(not t.done() for t in self.tasks.values()):
            raise ValueError('A model job is already running; cancel it or wait')
        provider = self.settings.provider(provider_id, purpose)
        if not provider.get('enabled') or not provider.get('model'):
            raise ValueError('Enable a provider and select its model in Admin')
        defaults = self.settings.public()['defaults']
        identifier = uuid4().hex
        while len(self.jobs) >= 20:
            oldest = next(iter(self.jobs))
            self.jobs.pop(oldest)
            self.tasks.pop(oldest, None)
        self.jobs[identifier] = {'id': identifier, 'status': 'running', 'provider_id': provider['id'],
                                 'model': provider['model'], 'started_at': time.time(), 'finished_at': None,
                                 'result': None, 'error': None, 'trace': []}
        self.tasks[identifier] = asyncio.create_task(self._run(identifier, prompt.strip(), provider, defaults, use_tools))
        return self.snapshot(identifier)

    async def cancel(self, job_id):
        self.snapshot(job_id)
        task = self.tasks.get(job_id)
        if task is asyncio.current_task():
            raise ValueError('A model job cannot cancel itself')
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first instruction never enters _run.
            if self.jobs[job_id]['status'] == 'running':
                self.jobs[job_id].update(status='cancelled', finished_at=time.time())
        return self.snapshot(job_id)

    async def cancel_all(self):
        for job_id in list(self.tasks):
            if self.tasks[job_id] is not asyncio.current_task() and not self.tasks[job_id].done():
                await self.cancel(job_id)

    async def _tools(self, defaults):
        specifications, dispatch = [], {}
        allowed_robot = set(defaults['robot_tools'])
        for tool in await self.list_robot_tools():
            if tool.name not in allowed_robot:
                continue
            alias = 'robot_' + tool.name
            parameters = copy.deepcopy(tool.inputSchema)
            description = tool.description or ''
            if tool.name in ('move', 'plan_run'):
                # Editor arm-and-play is an operator permission, never a
                # capability granted by enabling a model's movement tool.
                parameters.get('properties', {}).pop('arm_motion', None)
                if 'required' in parameters:
                    parameters['required'] = [key for key in parameters['required'] if key != 'arm_motion']
                description = ('Play a saved robot action.' if tool.name == 'move' else 'Play a saved robot plan.') + ' The operator must already have armed motion; models cannot arm the robot.'
            specifications.append({'type': 'function', 'function': {'name': alias, 'description': description,
                                                                     'parameters': parameters}})
            dispatch[alias] = ('robot', tool.name)
        for public in self.settings.public()['connections']:
            if not public['enabled'] or not public['allowed_tools']:
                continue
            connection = self.settings.connection(public['id'])
            catalog = await integrations.inspect_mcp(connection)
            allowed = set(connection['allowed_tools'])
            for tool in catalog['tools']:
                if tool['name'] not in allowed:
                    continue
                if len(specifications) >= 40:
                    raise ValueError('Select at most 40 combined robot and remote MCP tools')
                alias = 'remote_' + str(len(specifications))
                specifications.append({'type': 'function', 'function': {
                    'name': alias, 'description': (connection['name'] + ': ' + tool.get('description', tool['name']))[:2000],
                    'parameters': tool['inputSchema']}})
                dispatch[alias] = (connection['id'], tool['name'])
        return specifications, dispatch

    async def _execute_tool(self, target, arguments):
        connection_id, name = target
        # Re-read policy on every call; edits/revocations apply to active jobs.
        if connection_id == 'robot':
            if name not in self.settings.public()['defaults']['robot_tools']:
                raise ValueError('Robot tool permission was revoked')
            if name in ('move', 'plan_run') and arguments.get('arm_motion', False) is not False:
                raise ValueError('Models cannot arm robot motion; the operator must arm it explicitly')
            return await self.call_robot_tool(name, arguments)
        connection = self.settings.connection(connection_id)
        return await integrations.call_mcp(connection, name, arguments)

    def _check_provider(self, provider):
        current = self.settings.provider(provider['id'])
        if not current['enabled']:
            raise ValueError('Provider was disabled during this job')
        # Never send context or execute returned tools after endpoint/credential
        # changes. A response already in flight is subject to the same policy.
        if any(current[k] != provider[k] for k in ('base_url', 'kind', 'model', 'api_key')):
            raise ValueError('Provider changed during this job; start a new request')

    async def _run(self, identifier, prompt, provider, defaults, use_tools):
        job = self.jobs[identifier]
        try:
            # One whole-job budget, including MCP discovery, model calls and tools.
            async with asyncio.timeout(defaults['timeout_seconds']):
                tools, dispatch = await self._tools(defaults) if use_tools else ([], {})
                messages = [{'role': 'system', 'content': defaults['system_prompt']}, {'role': 'user', 'content': prompt}]
                count = 0
                for round_number in range(5):
                    self._check_provider(provider)
                    response = await integrations.complete(provider, messages, tools=tools or None, options=defaults)
                    self._check_provider(provider)
                    message = response['message']
                    calls = message.get('tool_calls') or []
                    if not calls:
                        job['result'] = {'text': message.get('content', ''), 'model': response['model'],
                                         'usage': response.get('usage', {}), 'tool_calls': count}
                        job['status'] = 'complete'
                        return
                    messages.append(message)
                    for tool_call in calls:
                        self._check_provider(provider)
                        if count >= 8:
                            raise ValueError('Model reached the eight-tool-call limit')
                        name = tool_call['function']['name']
                        if name not in dispatch:
                            raise ValueError('Model requested a tool outside its allowed list')
                        try:
                            arguments = json.loads(tool_call['function']['arguments'])
                        except (ValueError, TypeError):
                            raise ValueError('Model returned invalid tool arguments') from None
                        if not isinstance(arguments, dict):
                            raise ValueError('Tool arguments must be a JSON object')
                        count += 1
                        target = dispatch[name]
                        entry = {'tool': target[1], 'connection': target[0], 'arguments': arguments, 'status': 'running'}
                        job['trace'].append(entry)
                        try:
                            result = await self._execute_tool(target, arguments)
                            entry['status'] = 'error' if isinstance(result, dict) and result.get('isError') else 'complete'
                        except Exception as exc:
                            result = {'isError': True, 'error': str(exc)[:500]}
                            entry['status'] = 'error'
                        content = json.dumps(result, default=str, ensure_ascii=False)
                        if len(content) > 16000:
                            content = json.dumps({'truncated': True, 'excerpt': content[:15000]})
                        entry['result'] = json.loads(content)
                        messages.append({'role': 'tool', 'tool_call_id': tool_call['id'], 'name': name, 'content': content})
                raise ValueError('Model reached the five-round limit')
        except asyncio.CancelledError:
            job['status'] = 'cancelled'
        except TimeoutError:
            job.update(status='failed', error='Model job timed out; increase the timeout or choose a smaller model')
        except Exception as exc:
            # Network clients already sanitize upstream failures and echoed secrets.
            job.update(status='failed', error=str(exc)[:1000])
        finally:
            if job['status'] in ('failed', 'cancelled') and any(
                    entry['connection'] == 'robot' and entry['tool'] in ('move', 'set_leg', 'set_servo')
                    for entry in job['trace']):
                # Trusted cleanup bypasses the mutable model allowlist. A user
                # revoking model access must not disable our ability to stop.
                try:
                    await self.call_robot_tool('stop', {})
                except Exception:
                    job['cleanup_error'] = 'Could not confirm robot stop; use the physical power switch if needed'
            job['finished_at'] = time.time()
            for entry in job['trace']:
                if entry['status'] == 'running':
                    entry['status'] = 'cancelled'
