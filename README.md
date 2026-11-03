# PiBotPal

A local PiCrawler hardware service, private Tailscale dashboard, and Streamable HTTP MCP server. The web interface calls the same MCP tools that Android and agent clients can use. One process owns all hardware.

## Run on this Pi

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pibotpal
```

Requires the installed SunFounder `picrawler` and `robot_hat` packages, Raspberry Pi OS `picamera2`, OpenCV, ALSA, FFmpeg, and eSpeak. System site packages preserve the Pi's native camera/GPIO drivers. No Node or runtime CDN is needed.

Install the persistent user service and Tailscale HTTPS proxy with `scripts/install-service.sh`. It starts at boot via user lingering, binds only `127.0.0.1:8765`, and restarts on failure. It starts disarmed and microphone-muted. The camera starts for monitoring. Tailnet access rules determine who can reach the robot; this service trusts connections from Tailscale Serve and local processes. Do not expose the backend publicly.

- Dashboard: https://spiderbot.tail689a35.ts.net/
- MCP: https://spiderbot.tail689a35.ts.net/mcp
- Logs: `journalctl --user -u pibotpal -f`
- Restart: `systemctl --user restart pibotpal`
- Disable: `systemctl --user disable --now pibotpal`

Configuration can be placed in `~/.config/pibotpal/environment`: `PIBOTPAL_HOST`, `PIBOTPAL_DATA`, `PICRAWLER_HOME`, `PIBOTPAL_MIC_DEVICE` (default `plughw:CARD=Device,DEV=0`), `PIBOTPAL_SPEAKER_DEVICE` (default `default`). Never commit secrets. Use `PIBOTPAL_SIMULATE=1` to develop without GPIO/motion; the UI labels simulated state and does not fabricate camera/range data.

## Control contract

Initialize an MCP Streamable HTTP session at `/mcp`, then use `tools/list` and `tools/call`. The server uses the official MCP Python SDK with stateless JSON responses. Send `Accept: application/json, text/event-stream`. Web control, test programs, and MCP all share the controller; there are no subprocesses launching vendor motion scripts.

Tools: `get_status`, `arm`, `stop`, `move`, `set_leg`, `set_servo`, `set_offsets`, `list_examples`, `run_example`, `camera`, `microphone`, `speak`, `play_sound`, `record_video`.

Read status, explicitly arm, and issue bounded actions. Poll `get_status` at least every 5 seconds while armed. The heartbeat is shared by connected clients: any client polling status keeps the control lease alive. After 15 seconds without a status heartbeat the server cancels movement and disarms. Stop cancels work, disarms, and mutes audio; it does **not** cut servo power. This is a software control, not a hardware emergency power switch. No physical pose is assumed on startup. Use a known pose command before editing individual legs. Do not run separate vendor programs while this server owns the hardware.

Camera frames and audio use separate media URLs for efficient streaming; their controls go through MCP. `/audio.wav` cannot open the microphone while muted. Recordings are silent, bounded to 30 seconds, and saved under `~/.local/share/pibotpal/recordings`; the dashboard offers downloads. Microphone audio is live only and is not stored.

## Geometry and limits

The 3D view uses commanded logical servo angles and forward kinematics from the vendor geometry: 33 mm coxa, 48 mm upper link, 78 mm lower link, 77 mm hip spacing. It shows the model's joints rather than pretending it is a CAD scan. Decorative chassis, camera, and sensor placement are approximate. Servo calibration and direction are reported separately. These PWM servos have no position feedback: a stalled or manually displaced limb will differ from the model. There is no identified IMU, so world roll/pitch/yaw are unavailable. Dragging rotates the **view**, not the robot.

Ultrasonic telemetry is a single forward range measurement, drawn as a return/range cone; it is not a 3D map or scanning lidar. Invalid readings are shown as unavailable.

## Testing and example coverage

The Testing tab lists each local vendor example, its parameters, and dependency limitations. Tests are bounded and cancellable. Cloud STT/LLM/voice examples require provider configuration and are explicitly unavailable when it is missing. They never silently activate the microphone or upload audio. Calibration changes require deliberate test controls; vendor scripts are not executed directly.

```sh
.venv/bin/pip install -r requirements-dev.txt
PIBOTPAL_SIMULATE=1 .venv/bin/python -m pytest -q
```

Android is a future client of this same MCP contract; no Android binary is included in this first web implementation.

Protocol/hosting references: [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

## License

GNU GPL v3; see `LICENSE` and `NOTICE`. Vendored Three.js retains its MIT license.
