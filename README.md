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
- Logs: `journalctl --user-unit=pibotpal.service -f`
- Restart: `systemctl --user restart pibotpal`
- Disable: `systemctl --user disable --now pibotpal`

Configuration can be placed in `~/.config/pibotpal/environment`: `PIBOTPAL_HOST`, `PIBOTPAL_DATA`, `PICRAWLER_HOME`, `PIBOTPAL_MIC_DEVICE` (default `plughw:CARD=Device,DEV=0`), `PIBOTPAL_SPEAKER_DEVICE` (default `default`). Never commit secrets. Use `PIBOTPAL_SIMULATE=1` to develop without GPIO/motion; the UI labels simulated state and does not fabricate camera/range data.

## Control contract

Initialize an MCP Streamable HTTP session at `/mcp`, then use `tools/list` and `tools/call`. The server uses the official MCP Python SDK with stateless JSON responses. Send `Accept: application/json, text/event-stream`. Web control, test programs, and MCP all share the controller; there are no subprocesses launching vendor motion scripts.

Tools: `get_status`, `arm`, `stop`, `move`, `set_leg`, `set_servo`, `set_offsets`, `list_examples`, `run_example`, `camera`, `microphone`, `speak`, `play_sound`, `record_video`.

Read status, explicitly arm, and issue bounded actions. Poll `get_status` at least every 5 seconds while armed. The heartbeat is shared by connected clients: any client polling status keeps the control lease alive. After 15 seconds without a status heartbeat the server cancels movement, disarms, and mutes the microphone (including when motion was already disarmed). Stop cancels work, disarms, and mutes audio; it does **not** cut servo power. This is a software control, not a hardware emergency power switch. No physical pose is assumed on startup. Use a known pose command before editing individual legs. Do not run separate vendor programs while this server owns the hardware.

Camera frames and audio use separate media URLs for efficient streaming; their controls go through MCP. `/audio.wav` cannot open the microphone while muted. Recordings are silent, bounded to 30 seconds, and saved under `~/.local/share/pibotpal/recordings`; the dashboard offers downloads. Microphone audio is live only and is not stored.

## Geometry and limits

The 3D view uses commanded logical servo angles and forward kinematics from the vendor geometry: 33 mm coxa, 48 mm upper link, 78 mm lower link, 77 mm hip spacing. It shows the model's joints rather than pretending it is a CAD scan. Decorative chassis, camera, and sensor placement are approximate. Servo calibration and direction are reported separately. These PWM servos have no position feedback: a stalled or manually displaced limb will differ from the model. There is no identified IMU, so world roll/pitch/yaw are unavailable. Dragging rotates the **view**, not the robot.

Ultrasonic telemetry is a single forward range measurement, drawn as a return/range cone; it is not a 3D map or scanning lidar. Invalid readings are shown as unavailable, with raw return diagnostics. An echo timeout on D2/D3 means no valid pulse was captured: check sensor power, trigger/echo connections, and a solid target in front of it. The sensor is not treated as clear space when a reading fails.

## Testing and example coverage

The Testing tab lists each local vendor example, its parameters, and dependency limitations. Tests are bounded and cancellable. The language-model test uses the default testing provider configured in Admin. STT and voice-to-action adapters remain unavailable; adding an LLM provider does not implement speech recognition or enable microphone capture. They never silently activate the microphone or upload audio. Calibration changes require deliberate test controls; vendor scripts are not executed directly.

```sh
.venv/bin/pip install -r requirements-dev.txt
PIBOTPAL_SIMULATE=1 .venv/bin/python -m pytest -q
```

Native Android and Windows MVP clients now use this same MCP contract and decode the MJPEG video stream. See [client setup and builds](clients/README.md) and the [Native clients build artifacts](https://github.com/cdibona/PiBotPal/actions/workflows/clients.yml). Their first versions cover motion, video, and telemetry; 3D, Admin, and the testing catalog remain in the web dashboard.

Protocol/hosting references: [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

## Admin: model providers and MCP connections

Open the [Admin tab](https://spiderbot.tail689a35.ts.net/#admin). Add an **Ollama** server with its origin (`http://your-server:11434`), or an **OpenAI-compatible** Chat Completions API base (`https://your-provider/v1`). Save, choose **Discover models**, select an exact installed model tag, then save the model. Discovery never pulls or downloads a model. Qwen is supported through Ollama; both Ollama native and its OpenAI-compatible endpoint have been tested against the provided server.

Choose separate testing and assistant defaults. The playground sends only the text you enter; camera/audio data is not included. Model jobs are bounded by the configured whole-job timeout, five model rounds, and eight tool calls, and can be cancelled. The language-model example becomes available after selecting the default testing provider.

Remote MCP connections support Streamable HTTP and legacy SSE with optional bearer authentication. Save a connection, inspect its tools, select an explicit allowlist, then save. Tools are used only when a prompt enables MCP tools. The model's built-in tool permissions default to status/catalog reads; movement permissions still require manual arming. Admin, arming, and microphone controls are excluded from the built-in model tool list. Remote servers are trusted only for the tools you select; selecting a remote tool authorizes that server's behavior.

Settings live in `~/.local/share/pibotpal/settings.json` with mode `0600`, outside Git. Credentials are stored locally in plaintext protected by filesystem permissions, sent only to their saved endpoint, and omitted from API/UI reads. Blank credential fields preserve existing secrets; removal is explicit. Changing a credential's destination requires re-entering or clearing it. HTTP endpoints are useful for LAN/Tailscale services; use HTTPS for public providers. Redirects are rejected. OAuth-only MCP servers and local stdio commands are not supported in this version.

Admin uses the same Tailscale access boundary as the hardware controls. Every tailnet user/local process allowed to access this server can administer it; there is no additional user-role system. Review tailnet ACLs when sharing the robot.

The shared MCP API adds `admin_get`, `admin_save_provider`, `admin_delete_provider`, `admin_discover_models`, `admin_save_defaults`, `admin_save_connection`, `admin_delete_connection`, `admin_probe_connection`, `llm_start`, `llm_job`, and `llm_cancel`. Job histories are memory-only and reset on service restart.

## Power monitoring

The dashboard has a dedicated power panel. `get_status.power` reports powered state, measured voltage, sample timestamp/freshness, and source. The Robot HAT exposes battery voltage, but this installation has no charging-state, external-power, current-draw, or capacity telemetry. Charging and estimated runtime are therefore unknown; no charge percentage is invented. There is no automatic sleep/suspend/shutdown policy. The 15-second control heartbeat disarms motion and mutes audio; it does not power down the Pi or servos.

API references: [Ollama chat](https://docs.ollama.com/api/chat), [Ollama model discovery](https://docs.ollama.com/api/tags), [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

## License

GNU GPL v3; see `LICENSE` and `NOTICE`. Vendored Three.js retains its MIT license.
