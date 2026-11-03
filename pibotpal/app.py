"""Web and standards-based Streamable HTTP MCP share a single controller."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import fcntl
import logging
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
    mcp = FastMCP('PiBotPal', instructions='Controls a physical PiCrawler. Read get_status first. '
                 'Arm explicitly before movement. Call get_status every 5 seconds to maintain the 15-second '
                 'control lease. Movements are bounded. Servo positions are commanded estimates, not feedback. '
                 'Microphone is muted by default. stop cancels work and disarms; it does not remove power.',
                 stateless_http=True, json_response=True,
                 transport_security=TransportSecuritySettings(allowed_hosts=sorted(hosts), allowed_origins=sorted(origins)))

    async def invoke(fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    @mcp.tool()
    async def get_status() -> dict:
        """Read live hardware/media state and renew the 15-second operator heartbeat."""
        lease['last_seen'] = time.monotonic()
        state = await invoke(controller.status)
        state.update(media.status())
        state['examples'] = examples.status()
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
        return examples.list()

    @mcp.tool()
    async def run_example(example_id: str, options: dict | None = None) -> dict:
        """Start a bounded test from the example catalog. Motion requires arming; mic stays opt-in."""
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

    async def watchdog():
        while True:
            await asyncio.sleep(1)
            if time.monotonic() - lease['last_seen'] > 15 and (controller.status().get('armed') or media.status()['microphone']['enabled']):
                await invoke(controller.stop)
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
                await invoke(examples.close)
                await invoke(controller.close)
                await invoke(media.close)

    app = FastAPI(title='PiBotPal', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.controller, app.state.media, app.state.mcp = controller, media, mcp

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
