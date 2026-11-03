"""Web and standards-based Streamable HTTP MCP share a single controller."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import fcntl
import logging
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .hardware import Controller
from .media import Media
from .examples import ExampleRunner
from .settings import Settings
from .assistant import Assistant
from . import integrations


def create_app(controller=None, media=None):
    logging.getLogger('mcp').setLevel(logging.WARNING)
    controller = controller or Controller()
    media = media or Media(simulate=os.environ.get('PIBOTPAL_SIMULATE') == '1')
    examples = ExampleRunner(controller, media, data_dir=media.root)
    domain = os.environ.get('PIBOTPAL_HOST', 'spiderbot.tail689a35.ts.net')
    hosts = {'127.0.0.1:8765', 'localhost:8765', domain}
    hosts.update(filter(None, os.environ.get('PIBOTPAL_EXTRA_HOSTS', '').split(',')))
    origins = {f'http://{h}' for h in hosts if h.startswith(('localhost', '127.0.0.1'))}
    origins.add(f'https://{domain}')
    lease = {'last_seen': 0.0}
    settings = Settings(media.root)
    llm_example = {'id': None}
    mcp = FastMCP('PiBotPal', instructions='Controls a physical PiCrawler. Read get_status first. '
                 'Arm explicitly before movement. Call get_status every 5 seconds to maintain the 15-second '
                 'control lease. Movements are bounded. Servo positions are commanded estimates, not feedback. '
                 'Microphone is muted by default. stop cancels work and disarms; it does not remove power.',
                 stateless_http=True, json_response=True,
                 transport_security=TransportSecuritySettings(allowed_hosts=sorted(hosts), allowed_origins=sorted(origins)))

    async def invoke(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def call_robot_tool(name, arguments):
        result = await mcp.call_tool(name, arguments)
        if isinstance(result, tuple):
            content, structured = result
            return structured
        if isinstance(result, dict):
            return result
        blocks = [block.model_dump(mode='json') for block in result]
        texts = [block['text'] for block in blocks if block.get('type') == 'text']
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except ValueError:
                pass
        return {'content': blocks}

    assistant = Assistant(settings, mcp.list_tools, call_robot_tool)

    def example_catalog():
        catalog = examples.list()
        item = next(e for e in catalog if e['id'] == '17_online_llm_test')
        item['title'] = 'Configured language model'
        item['description'] = 'Send one text prompt to the default testing provider selected in Admin. No camera or microphone is sent.'
        item['requires'] = ['LLM provider']
        item['parameters'] = {'type': 'object', 'properties': {
            'prompt': {'type': 'string', 'default': 'Say hello and describe how you can help test a four-legged robot.', 'maxLength': 16000}}, 'additionalProperties': False}
        try:
            provider = settings.provider(purpose='tests')
            item['available'] = bool(provider['enabled'] and provider['model'])
            item['reason'] = None if item['available'] else 'Select an enabled provider and model in Admin.'
        except ValueError:
            item['reason'] = 'Choose a default testing provider and model in Admin.'
        return catalog

    @mcp.tool()
    async def get_status() -> dict:
        """Read live hardware/media state and renew the 15-second operator heartbeat."""
        lease['last_seen'] = time.monotonic()
        state = await invoke(controller.status)
        state.update(media.status())
        state['examples'] = examples.status()
        if llm_example['id']:
            try:
                job = assistant.snapshot(llm_example['id'])
                if job['started_at'] >= (state['examples'].get('started_at') or 0):
                    state['examples'] = {'running': job['status'] == 'running', 'permits_manual_motion': True,
                                         'example_id': '17_online_llm_test', 'phase': job['status'],
                                         'result': job['result'], 'error': job['error'], 'started_at': job['started_at'],
                                         'finished_at': job['finished_at']}
            except ValueError:
                llm_example['id'] = None
        state['control_lease_seconds'] = 15
        return state

    @mcp.tool()
    async def arm(armed: bool) -> dict:
        """Explicitly enable motion, or disarm and cancel all active work. No motion on arming."""
        lease['last_seen'] = time.monotonic()
        if armed:
            ensure_manual()
        if not armed:
            await invoke(examples.stop)
        result = await invoke(controller.arm, armed)
        return result if isinstance(result, dict) else await get_status()

    @mcp.tool()
    async def stop() -> dict:
        """Cancel examples, stop sending movement commands, disarm and mute microphone/speaker."""
        await invoke(controller.stop)
        await invoke(examples.stop)
        await invoke(media.stop)
        await assistant.cancel_all()
        return await get_status()

    def ensure_manual():
        state = examples.status()
        if state.get('running') and not state.get('permits_manual_motion'):
            raise ValueError('Stop the active example before manual movement')

    @mcp.tool()
    async def move(name: str, speed: int = 40, steps: int = 1) -> dict:
        """Run a bounded gait/action, e.g. stand, sit, forward, backward, turn left/right, wave."""
        ensure_manual()
        result = await invoke(controller.action, name, speed=speed, steps=steps)
        return result if isinstance(result, dict) else await get_status()

    @mcp.tool()
    async def set_leg(leg: int, x: float, y: float, z: float, speed: int = 30) -> dict:
        """Set one leg's local foot position in mm, zero-based RF/LF/LR/RR. Requires known pose."""
        ensure_manual()
        result = await invoke(controller.set_leg, leg, x, y, z, speed=speed)
        return result if isinstance(result, dict) else await get_status()

    @mcp.tool()
    async def set_servo(index: int, angle: float, speed: int = 30) -> dict:
        """Set one logical servo angle in degrees; joint limits and physical PWM bounds apply."""
        ensure_manual()
        result = await invoke(controller.set_servo, index, angle, speed=speed)
        return result if isinstance(result, dict) else await get_status()

    @mcp.tool()
    async def set_offsets(offsets: list[float]) -> dict:
        """Save 12 calibration offsets while disarmed. Does not move the robot."""
        ensure_manual()
        result = await invoke(controller.set_offsets, offsets)
        return result if isinstance(result, dict) else await get_status()

    @mcp.tool()
    async def list_examples() -> list[dict]:
        """List reimplemented examples, parameters, dependencies, and unavailable capabilities."""
        return example_catalog()

    @mcp.tool()
    async def run_example(example_id: str, options: dict | None = None) -> dict:
        """Start a bounded test from the example catalog. Motion requires arming; mic stays opt-in."""
        if example_id == '17_online_llm_test':
            options = options or {}
            if set(options) - {'prompt'}:
                raise ValueError('Unknown language-model test options')
            job = assistant.start(options.get('prompt', 'Say hello briefly.'), purpose='tests', use_tools=False)
            llm_example['id'] = job['id']
            return job
        return await invoke(examples.run, example_id, options or {})

    @mcp.tool()
    async def camera(enabled: bool) -> dict:
        """Enable/disable the shared camera. Video: /video.mjpg; still image: /snapshot.jpg."""
        return await invoke(media.camera, enabled)

    @mcp.tool()
    async def microphone(enabled: bool) -> dict:
        """Explicit microphone consent; disabling immediately terminates capture. Stream: /audio.wav."""
        lease['last_seen'] = time.monotonic()
        return await invoke(media.microphone, enabled)

    @mcp.tool()
    async def speak(text: str) -> dict:
        """Speak up to 500 characters through the robot speaker using local eSpeak."""
        return await invoke(media.speak, text)

    @mcp.tool()
    async def play_sound(name: str) -> dict:
        """Play an allowlisted vendor sound/music filename for at most 30 seconds."""
        return await invoke(media.play_sound, name)

    @mcp.tool()
    async def record_video(seconds: int = 10) -> dict:
        """Record a silent local camera clip for 1–30 seconds; returns a download URL."""
        return await invoke(media.record, seconds)

    @mcp.tool()
    async def admin_get() -> dict:
        """Read integration settings without credentials, and server connection information."""
        state = settings.public()
        state['server'] = {'mcp_url': f'https://{domain}/mcp', 'transport': 'streamable_http',
                           'auth': 'Tailscale network access; local processes and tailnet users with access are trusted administrators.'}
        state['jobs'] = assistant.summaries()
        return state

    @mcp.tool()
    async def admin_save_provider(provider: dict) -> dict:
        """Create/update an LLM endpoint. Blank API key preserves the stored key; clear_api_key removes it."""
        return await invoke(settings.save_provider, provider)

    @mcp.tool()
    async def admin_delete_provider(provider_id: str) -> dict:
        """Delete a saved provider and clear defaults referencing it."""
        return await invoke(settings.delete_provider, provider_id)

    @mcp.tool()
    async def admin_discover_models(provider_id: str) -> dict:
        """Test a saved endpoint by listing available models. Does not download or pull models."""
        return await integrations.discover_models(settings.provider(provider_id))

    @mcp.tool()
    async def admin_save_defaults(defaults: dict) -> dict:
        """Save default providers, model limits, system prompt, and explicit robot tool permissions."""
        return await invoke(settings.save_defaults, defaults)

    @mcp.tool()
    async def admin_save_connection(connection: dict) -> dict:
        """Save a remote MCP endpoint and its allowed tools. Blank bearer_token preserves its secret."""
        return await invoke(settings.save_connection, connection)

    @mcp.tool()
    async def admin_delete_connection(connection_id: str) -> dict:
        """Remove a remote MCP server and its saved credential."""
        return await invoke(settings.delete_connection, connection_id)

    @mcp.tool()
    async def admin_probe_connection(connection_id: str) -> dict:
        """Initialize a remote MCP connection and inspect tools without executing any of them."""
        return await integrations.inspect_mcp(settings.connection(connection_id))

    @mcp.tool()
    async def llm_start(prompt: str, provider_id: str | None = None, purpose: str = 'tests', use_tools: bool = False) -> dict:
        """Start a bounded model job using saved configuration. Only explicitly allowed MCP tools may run."""
        return assistant.start(prompt, provider_id, purpose, use_tools)

    @mcp.tool()
    async def llm_job(job_id: str) -> dict:
        """Read model job output, status, and tool trace."""
        return assistant.snapshot(job_id)

    @mcp.tool()
    async def llm_cancel(job_id: str) -> dict:
        """Cancel a model job and stop any robot motion it issued."""
        result = await assistant.cancel(job_id)
        if any(entry['connection'] == 'robot' and entry['tool'] in ('move', 'set_leg', 'set_servo') for entry in result['trace']):
            await invoke(controller.stop)
        return result

    async def watchdog():
        while True:
            await asyncio.sleep(1)
            if time.monotonic() - lease['last_seen'] > 15 and (controller.status().get('armed') or media.status()['microphone']['enabled']):
                await invoke(controller.stop)
                await assistant.cancel_all()
                await invoke(examples.stop)
                await invoke(media.stop)

    @asynccontextmanager
    async def lifespan(app):
        lockfile = media.root / 'controller.lock'
        with lockfile.open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another PiBotPal process owns this hardware')
            await invoke(controller.start)
            await invoke(media.start)
            task = asyncio.create_task(watchdog())
            try:
                async with mcp.session_manager.run():
                    yield
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await invoke(controller.stop)
                await assistant.cancel_all()
                await invoke(examples.close)
                await invoke(controller.close)
                await invoke(media.close)

    app = FastAPI(title='PiBotPal', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.controller, app.state.media, app.state.mcp = controller, media, mcp
    app.state.settings, app.state.assistant = settings, assistant

    @app.middleware('http')
    async def private_origin(request: Request, call_next):
        # Tailscale Serve is the only network entrypoint. Binding is loopback-only.
        if request.headers.get('host') not in hosts:
            return JSONResponse({'error': 'Unrecognized host'}, status_code=421)
        origin = request.headers.get('origin')
        if origin and origin not in origins:
            return JSONResponse({'error': 'Cross-origin access refused'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error': 'Cross-site access refused'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'service': 'PiBotPal', 'simulation': os.environ.get('PIBOTPAL_SIMULATE') == '1'}

    @app.get('/snapshot.jpg')
    async def snapshot():
        try:
            return Response(media.jpeg(), media_type='image/jpeg')
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))

    @app.get('/video.mjpg')
    async def video(request: Request):
        try:
            media.jpeg()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        async def frames():
            while not await request.is_disconnected():
                try:
                    jpeg = media.jpeg()
                except RuntimeError:
                    break
                yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n'
                await asyncio.sleep(0.1)
        return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame')

    @app.get('/audio.wav')
    async def audio():
        try:
            stream = media.audio_stream()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return StreamingResponse(stream, media_type='audio/wav')

    @app.get('/recordings/{name}')
    async def recording(name: str):
        path = (media.recordings / name).resolve()
        if path.parent != media.recordings.resolve() or path.suffix != '.avi' or not path.is_file():
            raise HTTPException(404)
        if media.status()['recording']['active']:
            raise HTTPException(409, 'Wait for the recording to finish')
        return FileResponse(path, filename=path.name, media_type='video/x-msvideo')

    static = Path(__file__).parent / 'static'
    @app.get('/')
    async def index():
        return FileResponse(static / 'index.html')
    app.mount('/static', StaticFiles(directory=static, check_dir=False), name='static')
    app.mount('/', mcp.streamable_http_app())
    return app
