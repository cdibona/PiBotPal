# PiBotPal

A local PiCrawler hardware service, private Tailscale dashboard, and Streamable HTTP MCP server. The web interface calls the same MCP tools that Android and agent clients can use. One process owns all hardware.

## Run on this Pi

```sh
sudo apt install python3-libcamera python3-numpy python3-pil libasound2t64 libflite1
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pibotpal
```

Requires Raspberry Pi OS `python3-libcamera`, NumPy, Pillow, `libasound2t64`, and the Flite shared libraries (`libflite1`). Install Python requirements below; system site packages expose the Pi's camera bindings. PiBotPal controls the Robot HAT directly using MIT-licensed python-periphery. It does not import `picrawler`, `robot_hat`, Picamera2, OpenCV, or python-prctl, and does not invoke FFmpeg, eSpeak, arecord, or aplay. No Node or runtime CDN is needed. Optional QR decoding uses `zxing-cpp` (Apache-2.0), installed with `.venv/bin/pip install -r requirements-vision.txt`; it is installed on this robot. Face detection is currently unavailable.

Install the persistent user service and Tailscale HTTPS proxy with `scripts/install-service.sh`. It starts at boot via user lingering, binds only `127.0.0.1:8765`, and restarts on failure. It starts disarmed and microphone-muted. Cameras resume their saved on/off preferences; newly discovered cameras start for monitoring. Tailnet access rules determine who can reach the robot; this service trusts connections from Tailscale Serve and local processes. Do not expose the backend publicly.

- Dashboard: https://spiderbot.tail689a35.ts.net/
- MCP: https://spiderbot.tail689a35.ts.net/mcp
- Logs: `journalctl --user-unit=pibotpal.service -f`
- Restart: `systemctl --user restart pibotpal`
- Disable: `systemctl --user disable --now pibotpal`

Systemd waits for the read-only `/health` readiness probe before reporting the
service started. `/health` and `get_status` share a per-process
`server_instance_id`, so clients can identify a restarted backend. Shutdown
allows three seconds for open streams before cancelling them, then releases
hardware normally. The dashboard retries telemetry and camera EOF/errors/stalls
independently; recovering video never arms motion or unmutes the microphone.
Reload an older open page once after updating to load this recovery logic.

For the mocked browser recovery regression (no hardware/network calls):
`.venv/bin/python tests/browser/check_reconnect.py`.

Configuration can be placed in `~/.config/pibotpal/environment`: `PIBOTPAL_HOST`, `PIBOTPAL_DATA`, `PIBOTPAL_MIC_DEVICE` (default `plughw:CARD=Device,DEV=0`), `PIBOTPAL_SPEAKER_DEVICE` (default `default`). Never commit secrets. Use `PIBOTPAL_SIMULATE=1` to develop without GPIO/motion; the UI labels simulated state and does not fabricate camera/range data.

## Control contract

Initialize an MCP Streamable HTTP session at `/mcp`, then use `tools/list` and `tools/call`. The server uses the official MCP Python SDK with stateless JSON responses. Send `Accept: application/json, text/event-stream`. Web control, test programs, and MCP all share the controller; there are no subprocesses launching vendor motion scripts.

Tools: `get_status`, `arm`, `stop`, `move`, `set_leg`, `set_servo`, `set_offsets`, `list_examples`, `run_example`, `camera`, `microphone`, `speak`, `play_sound`, `record_video`.

Read status, explicitly arm, and issue bounded actions. Poll `get_status` at least every 5 seconds while armed. The heartbeat is shared by connected clients: any client polling status keeps the control lease alive. After 15 seconds without a status heartbeat the server cancels movement, disarms, and mutes the microphone (including when motion was already disarmed). Stop cancels work, disarms, and mutes audio; it does **not** cut servo power. This is a software control, not a hardware emergency power switch. No physical pose is assumed on startup. Use a known pose command before editing individual legs. Do not run separate vendor programs while this server owns the hardware.

Camera frames and audio use separate media URLs for efficient streaming; their controls go through MCP. `/audio.wav` cannot open the microphone while muted. Recordings are silent, bounded to 30 seconds, and saved under `~/.local/share/pibotpal/recordings`; the dashboard offers downloads. Microphone audio is live only and is not stored.

## Motion timing

Joint motion runs at 50 Hz with a shared commanded-motion budget. By default,
whole-body changes (such as stand and sit) move matching joints across all
four legs: **end/knee joints → shoulders → hip angle joints**. Each phase is
split to honor the concurrency limit, which defaults to three servos (3+1
for each group of four). Set **Servos commanded together** to four if you want
all four matching joints to change together. The defaults retain a limit of
30 degrees/second per servo, 60 degrees/second summed across the group, and
80 ms settling between different groups. Requested speed can lower these rates
but cannot exceed the budget. Consecutive gait samples for the same group stream
continuously; single-leg swings stay coordinated and body transfers use the
selected sequence. Non-gait gestures complete every whole-body pose before
starting their next pose. Scheduler stalls extend
the movement instead of producing catch-up jumps. Unchanged poses return
immediately. The HAT uses a 50 Hz
PWM period with one-microsecond pulse resolution (about 0.09 degrees), and
unchanged pulse values do not generate redundant bus writes. Startup/arming
remain free of PWM writes; the first explicit full-pose command still starts
from an unknown physical position because the servos have no encoders. Unknown
joints are initialized in separate groups with at least one second of settling
after each group, including the last. That delay cannot confirm physical arrival
or rate-limit an unknown initial position.

Change the limits and **Whole-body sequence** in
**Admin → Hardware → Power & motion budget**. Choose matching joints or the
previous leg-by-leg order. **Save servo limits** is available while disarmed
and idle; when armed but idle, **Disarm and save** explicitly disarms first.
During movement, stop or wait before saving. Invalid values are rejected before
disarming; saving never moves a servo or re-arms motion. Settings persist in
`servo-power.json` (0600) and apply to web, native,
MCP, controller, testing, and plan movements through the shared controller.
This is a concurrency/travel-rate budget, not a measured amperage or wattage
limit. Holding servos still consume power; software STOP holds their last PWM
commands. Staggering changes gait timing and needs physical balance checks.

## Geometry and limits

The replacement crawl and gesture trajectories have been validated in simulation, but still need physical balance and travel checks on the assembled bot.

The 3D view uses commanded logical servo angles and its own forward kinematics using assembly dimensions: 33 mm coxa, 48 mm upper link, 78 mm lower link, 77 mm hip spacing. It shows the model's joints rather than pretending it is a CAD scan. Decorative chassis, camera, and sensor placement are approximate. Servo calibration and direction are reported separately. These PWM servos have no position feedback: a stalled or manually displaced limb will differ from the model. There is no identified IMU, so world roll/pitch/yaw are unavailable. Dragging rotates the **view**, not the robot.

Ultrasonic telemetry is a single forward range measurement, drawn as a return/range cone; it is not a 3D map or scanning lidar. Invalid readings are shown as unavailable, with raw return diagnostics. An echo timeout on D2/D3 means no valid pulse was captured: check sensor power, trigger/echo connections, and a solid target in front of it. The sensor is not treated as clear space when a reading fails.

## Testing and example coverage

The Testing tab maps each local vendor example to an independent bounded exercise, its parameters, and dependency limitations. Original vendor programs and their licenses are preserved separately; see [reference notes](references/README.md). Tests are bounded and cancellable. The language-model test uses the default testing provider configured in Admin. STT and voice-to-action adapters remain unavailable; adding an LLM provider does not implement speech recognition or enable microphone capture. They never silently activate the microphone or upload audio. Calibration changes require deliberate test controls; vendor scripts are not executed directly.

```sh
.venv/bin/pip install -r requirements-dev.txt
PIBOTPAL_SIMULATE=1 .venv/bin/python -m pytest -q
```

Native Android and Windows MVP clients now use this same MCP contract and decode the MJPEG video stream. See [client setup and builds](clients/README.md) and the [Native clients build artifacts](https://github.com/cdibona/PiBotPal/actions/workflows/clients.yml). Their first versions cover motion, video, and telemetry; 3D, Admin, and the testing catalog remain in the web dashboard.

Protocol/hosting references: [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

## Admin: model providers and MCP connections

Open the [Admin tab](https://spiderbot.tail689a35.ts.net/#admin/hardware). Settings are grouped into **Hardware**, **Cameras**, **Controllers**, **Models**, **MCP connections**, **Playground**, and **System** sections. A sidebar on desktop becomes a compact tab grid on smaller screens. Each section has a direct `#admin/<section>` link and supports keyboard tab/arrow navigation. Admin hides dashboard telemetry to keep settings near the top; device options are collapsed under current hardware and expansion groups. The header arm/STOP controls stay available. The appearance icon offers Auto (follow the device), Light, and Dark, with the choice saved in this browser. Apps has its own page in the top navigation; download cards are not repeated below settings. Add an **Ollama** server with its origin (`http://your-server:11434`), or an **OpenAI-compatible** Chat Completions API base (`https://your-provider/v1`). Save, choose **Discover models**, select an exact installed model tag, then save the model. Discovery never pulls or downloads a model. Qwen is supported through Ollama; both Ollama native and its OpenAI-compatible endpoint have been tested against the provided server.

Choose separate testing and assistant defaults. The playground sends only the text you enter; camera/audio data is not included. Model jobs are bounded by the configured whole-job timeout, five model rounds, and eight tool calls, and can be cancelled. The language-model example becomes available after selecting the default testing provider.

Remote MCP connections support Streamable HTTP and legacy SSE with optional bearer authentication. Save a connection, inspect its tools, select an explicit allowlist, then save. Tools are used only when a prompt enables MCP tools. The model's built-in tool permissions default to status/catalog reads; movement permissions still require manual arming. Admin, arming, and microphone controls are excluded from the built-in model tool list. Remote servers are trusted only for the tools you select; selecting a remote tool authorizes that server's behavior.

Settings live in `~/.local/share/pibotpal/settings.json` with mode `0600`, outside Git. Credentials are stored locally in plaintext protected by filesystem permissions, sent only to their saved endpoint, and omitted from API/UI reads. Blank credential fields preserve existing secrets; removal is explicit. Changing a credential's destination requires re-entering or clearing it. HTTP endpoints are useful for LAN/Tailscale services; use HTTPS for public providers. Redirects are rejected. OAuth-only MCP servers and local stdio commands are not supported in this version.

Admin uses the same Tailscale access boundary as the hardware controls. Every tailnet user/local process allowed to access this server can administer it; there is no additional user-role system. Review tailnet ACLs when sharing the robot.

The shared MCP API adds `admin_get`, `admin_save_provider`, `admin_delete_provider`, `admin_discover_models`, `admin_save_defaults`, `admin_save_connection`, `admin_delete_connection`, `admin_probe_connection`, `llm_start`, `llm_job`, and `llm_cancel`. Job histories are memory-only and reset on service restart.

## Power monitoring

The dashboard has a dedicated power panel. `get_status.power` reports powered state, measured voltage, sample timestamp/freshness, and source. The Robot HAT exposes battery voltage, but this installation has no charging-state, external-power, current-draw, or capacity telemetry. Charging and estimated runtime are therefore unknown; no charge percentage is invented. There is no automatic sleep/suspend/shutdown policy. The 15-second control heartbeat disarms motion and mutes audio; it does not power down the Pi or servos.

**Admin → Hardware** also shows the Pi CPU ceiling, current frequency,
temperature, and current/historical undervoltage and throttling flags.
`power_get` reads this telemetry and the servo budget; `servo_power_set` saves
the four numeric limits and optional `body_sequence` (`joints` or `legs`).
Omitting the sequence preserves the saved choice. The optional `disarm: true`
performs the explicit disarm-and-save operation while idle. Existing budget
files without a sequence use matching joints; their numeric limits are preserved.
Pi firmware flags are sampled
once per second. Current undervoltage blocks arming; a detected new undervoltage
event stops and disarms motion. Historical flags already present at server
startup are shown separately. This sampled protection cannot prevent every
brief voltage dip and is not a current sensor or hardware cutoff.

This Pi's CPU ceiling is reduced from 2.4 GHz to **1.5 GHz** by the root-owned
`pibotpal-cpu-limit.service`. The cap is independent of the robot server and
persists across boot without changing voltage or the CPU governor. Install it
on a compatible Pi with:

```sh
sudo install -Dm755 scripts/cpu-limit.py /usr/local/libexec/pibotpal-cpu-limit.py
sudo install -m644 scripts/pibotpal-cpu-limit.service /etc/systemd/system/pibotpal-cpu-limit.service
sudo systemctl daemon-reload
sudo systemctl enable --now pibotpal-cpu-limit.service
```

The helper validates the cap against the host's supported frequencies before
writing it. To remove this ceiling on this Pi 5, disable the unit with
`sudo systemctl disable --now pibotpal-cpu-limit.service`, then restore the
supported maximum with
`sudo /usr/bin/python3 -I /usr/local/libexec/pibotpal-cpu-limit.py --max-khz 2400000`.
Other hosts may have different supported limits. CPU settings are read-only
in the web interface.

API references: [Ollama chat](https://docs.ollama.com/api/chat), [Ollama model discovery](https://docs.ollama.com/api/tags), [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

## License

Current PiBotPal code, including the replacement HAT driver, geometry, motion planner, and Testing tab, is **MIT licensed**; see `LICENSE`. SunFounder's original examples retain their own licenses and remain separate. Historical versions are not relicensed. Three.js retains its MIT notice. System libcamera and ALSA libraries are LGPL-2.1-or-later; this is not a claim that the operating system or every dependency is MIT. See [dependency inventory](DEPENDENCIES.md) and `NOTICE`.

## Plan: saved poses and routines

The web **Plan** tab and the Android/Windows **Plan** tabs share the same MCP
library and runner. Save a named pose from twelve known, idle servo commands;
these are commanded positions, not measurements of hand-positioned legs.
Build, reorder, and edit movement, saved-pose, and wait steps. The native editors
also expose the step JSON for direct editing. Saved plans snapshot pose angles,
so later library edits/deletions do not silently change an existing plan.

Describe a routine such as “Stand. Move forward 4 steps, turn left, move forward
four steps.” The built-in parser handles this without an LLM. Optional **Use
configured LLM** sends instructions and up to 32 saved pose names/IDs to the Admin assistant provider and receives a
strictly validated draft without tool access. Review and save the draft, then
explicitly arm and run. Drafting and saving never move the robot. The model does
not arm or run its draft. Gait cycles do not imply calibrated meters or degrees.

Execution reports current step/progress. Plans allow 64 entries, five gait
cycles per movement entry, 30 seconds per wait, and a 300-second whole-run limit.
Keep an operator interface connected: the existing 15-second heartbeat still
applies. Plans disarm on completion, cancellation, timeout, or error; STOP,
disarming, service shutdown, and the heartbeat watchdog cancel the plan. Manual
motion/tests cannot compete with an active plan. Restarting never resumes one.
Plans and poses persist locally in `plans.json` under the service data directory.

MCP adds `plan_list`, `pose_save`, `pose_delete`, `plan_save`, `plan_delete`,
`plan_draft`, `plan_run`, `plan_stop`, `hardware_get`, and `hardware_save`.
Sensor conditions/branching are not implemented in this version; plans do not
provide automatic obstacle avoidance. Unsupported conditional instructions are
rejected by the simple parser rather than silently dropped.

## Hardware inventory and expansion

Admin’s Hardware section lists the HAT/servos, cameras, sonar, power telemetry, microphone,
speaker, rear USB webcam, RPLIDAR C1, Coral USB, USB gamepad, and an optional IMU. It shows
passively discovered stable USB camera/serial paths, Coral IDs, and Pi memory.
Settings are validated and atomically stored in `hardware.json`. These are
inventory/provisioning preferences: saving does not activate a driver, change
live servo wiring, or grant microphone consent. Pending drivers are marked.

The supplied lidar is an **RPLIDAR C1 over USB serial**. Its default serial rate
is **460800**, matching [SLAMTEC's C1 configuration](https://github.com/Slamtec/sllidar_ros2/blob/main/launch/sllidar_c1_launch.py).
The [SLAMTEC SDK](https://github.com/Slamtec/rplidar_sdk) is BSD-2-Clause, compatible
with the project's dependency preference. The next integration should emit
bounded timestamped scans with mounting transforms and freshness/quality status,
then draw a local obstacle map and expose compact clearance summaries to MCP.
Begin with stationary scans: a crawler pitches and rolls, so mapping needs
motion compensation or stop-and-scan operation. Add an IMU and localization
confidence before treating gait commands as world-frame odometry. A 2D lidar
does not itself provide SLAM, reliable travel distance, or drop-off detection.

A rear UVC webcam should be a second independently named stream with a stable
`/dev/v4l/by-id` path, independent health/fps controls, rear mounting transform,
and front/rear selection in each client. It should share timestamps and camera
processing with perception without enabling microphone capture or automatic
cloud uploads. The current inventory prepares these options; a rear capture
stream is not implemented yet.

The Coral USB is useful for compatible compiled int8 TensorFlow Lite perception
models, such as object detection. It is not a drop-in accelerator for llama.cpp
text generation; see [Coral model requirements](https://coral.ai/docs/edgetpu/models-intro/).
A separate perception worker should produce structured detections with source,
timestamp, frame, and confidence. Avoid streaming unbounded images/scans into
text-model context or putting inference in the servo/control thread.

Future decision steps should use typed sensor predicates, freshness limits,
explicit missing-data behavior, bounded retries, and a whole-plan deadline.
A deterministic executor should check those conditions between small movement
segments and stop on stale/invalid navigation data. LLMs can suggest plans or
explain observations; the executor owns motion limits and failure handling.
Plan schema version 1 deliberately supports only action/pose/wait. A future
version can add guards while retaining readable/reviewable steps and logs.

## Small models on this Pi

This robot is a Raspberry Pi 5 with approximately 4 GB RAM. Start with a small
four-bit model, a short context (around 2048 tokens), and bounded output. These
are candidates to benchmark, not measured throughput or memory guarantees:

| Candidate | Role | Model license |
| --- | --- | --- |
| [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | First local candidate; disable thinking for short plan drafts | Apache-2.0 |
| [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) | Small instruction/JSON drafting baseline | Apache-2.0 |
| [SmolLM2-360M-Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct) | Lowest-footprint simple language baseline; expect weaker planning | Apache-2.0 |

Use [llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT) with an explicitly
licensed GGUF quantization and its OpenAI-compatible server bound to loopback.
Configure that local `/v1` endpoint in Admin as an OpenAI-compatible provider.
Start with two inference threads and monitor camera fps/control responsiveness,
RSS, temperature, and power before raising limits. Raw four-bit weights for
these sizes are only a lower bound: metadata, quantization scales, KV cache,
and runtime buffers add memory. Models are not downloaded or installed by this
change. Keep the existing remote Ollama Qwen server for stronger drafting while
using the deterministic parser/executor offline for simple routines.

## Multiple CSI cameras and USB controller recognition

The dashboard shows the selected primary camera and additional CSI feeds at the
same time. [Admin → Cameras](https://spiderbot.tail689a35.ts.net/#admin/cameras)
provides independent on/off switches, **Turn all off**, previews, snapshots,
resolution selection, and primary selection. Turning a camera off releases its
capture pipeline and stops frame processing; it does not switch a physical
power rail. Each camera's choice persists across server restarts. Changing the
resolution of an off camera leaves it off, and stopping the server preserves
the saved preferences.
Stable camera IDs derive from the physical libcamera sensor ID, rather than
array order. IMX477 and OV5647 simultaneous capture has been tested on this
robot; only the IMX477 is enumerated by Linux on the latest boot. An absent
sensor cannot be turned on by the dashboard. New cameras start at
640×480/12 fps; the OV5647 is preferred as initial primary when present. Primary
selection, on/off preferences, and per-camera resolutions persist in
`cameras.json` (0600). A shared request dispatcher
routes the process-wide libcamera completion queue to each camera, allowing
simultaneous capture without one stream consuming the other's requests.

The resolution menu contains RGB output presets validated by libcamera, including
320×240, 640×480, 1280×720, and full sensor output. On this robot the IMX477 exposes
18 presets up to 4056×3040 and the OV5647 exposes 14 up to 2592×1944, both down to 160×120.
Use **Apply** to restart only the chosen camera; a failed change restores the prior
mode. Capture targets 12 fps through 720p, 8 fps through 1080p, 4 fps through 5 MP,
and 2 fps above that to limit RGB copying and JPEG encoding. These are application
capture targets, not maximum sensor frame rates. Actual JPEG dimensions are reported
in status; changing output aspect ratio may change the field of view. Higher resolution
uses more bandwidth. The web decoder supports JPEG frames up to 16 MiB.

MCP adds `camera_list`, `camera_select(camera_id)`,
`camera_configure(camera_id, width, height)`, and optional `camera_id` on
`camera(enabled, camera_id)`. Each sensor has `/cameras/{id}/video.mjpg` and
`/cameras/{id}/snapshot.jpg`. Existing `/video.mjpg`, `/snapshot.jpg`, vision tests,
and recordings use the primary camera. A recording pins its starting camera, dimensions,
and frame rate until completion; finish recording before resizing that camera. Named streams remain pinned to their
sensor; the legacy video stream follows an explicit primary change. Existing
Android/Windows clients continue using the primary feed. Camera status includes
brightness and read-only exposure/gain metadata to help diagnose a covered lens
or low light. Capture start is independent of microphone consent and arming.

[Admin → Controllers](https://spiderbot.tail689a35.ts.net/#admin/controllers) recognizes
Linux USB joystick devices, shows their stable path and USB identity, and
streams axes/buttons live while that section is visible. The attached receiver identifies as `2563:0575`,
`USB WirelessGamepad` (ShanWan), with six axes and thirteen buttons. This is
consistent with a PS2-style controller receiver; the USB identity does not prove
the retail brand or that a wireless handset is paired. Move a stick or press a
button to confirm activity. `controller_get` and `get_status.gamepad` expose the
same input state, raw readings, recent events, saved mappings and control readiness.
Explicit controller arming also enables system motion, so it needs only one step.
The monitor uses the Linux joystick interface directly, without a new library.

For receiver `2563:0575` with the original sequential HID button codes, PiBotPal
uses its physical button layout: **Start is index 9**, while index 11 is the
right stick button. Generic Linux labels incorrectly call index 11 Start.
The layout agrees with the matching Linux entry in
[SDL_GameControllerDB](https://github.com/mdqinc/SDL_GameControllerDB/blob/master/gamecontrollerdb.txt).
Other receivers and already-corrected kernel mappings keep their Linux labels;
telemetry includes the raw `button_codes` and selected `button_mapping`.

The gamepad icon beside **Arm motion** and a fresh **Start** button press both
toggle controller arming. Arming from either control also arms system motion;
the first Start press works even when the saved input preference is already on.
The icon is filled when both are armed and the receiver is connected, outlined
when disarmed, and unavailable when no receiver is attached. Controller input is
enabled by default; the preference persists in private `controller.json` across page
reloads, service restarts, and unplug/replug. Startup and connection alone never
arm system motion. Controller disarming blocks mapped actions
but keeps the raw input monitor and Start available. Startup
button states, held/repeated presses and rapid switch bounce never toggle arming.
`controller_set_enabled(enabled)` provides the same shared setting over MCP;
`controller_get` and `get_status.gamepad` report `enabled` (saved preference),
`armed` (enabled, connected, and system armed), `system_armed`, `attached`,
`connected`, and `start_listening`. With no connected receiver, saving the input
preference does not arm motion. Undervoltage, invalid calibration/budget, and
active plan/test checks still apply to controller arming.

The Controllers page shows a gamepad diagram, numerical axes, and a recent input
log. The read-only `/controllers/events` stream updates at up to 20 Hz; 350 ms
tap highlights preserve short presses even after release. Monitoring remains
available while disarmed. Hidden pages close the stream; failed streams fall
back to 100 ms MCP polling without overlapping requests.

Default action mappings are:

| Input | Action |
| --- | --- |
| D-pad or left stick forward/back | Forward/backward while held |
| D-pad or left stick left/right | Turn left/right while held |
| A / × (South) | Stand |
| B / ○ (East) | Sit |
| X / □ (West) | Wave |
| Y / △ (North) | Ready |
| Select | Stop and disarm |
| Start | Toggle controller arming (reserved) |

Every other input can be assigned or left unassigned in the mapping editor.
Walking and turning support **Press** (one bounded cycle) or **Hold** (repeat
one cycle at a time); poses and gestures execute once per fresh press. Speed
is adjustable from 1–80 and stick dead zone from 0.15–0.8. MCP tools
`controller_save_mapping` and `controller_reset_mapping` share the same private
`controller-mapping.json`. Saving/resetting does not start motion.

Arming the controller enables mapped movement and system motion together.
After arming, reconnecting, or changing a mapping, release mapped controls to
neutral before the next action. Releasing a held direction cancels its current
motion while retaining system arming; controller disarm or USB disconnect also
cancels its motion. Unrelated manual or planned actions are not canceled by a
controller release. Conflicting directions stop controller motion, and a
continuous hold is limited to ten seconds before release is required: USB
receiver presence alone cannot detect a lost wireless handset. Busy actions
are not queued for later playback. Controller disarming leaves system arming
available for other interfaces. Select and the header STOP cancel work and
disarm; system disarm/STOP also discard pending Start requests so an earlier
press cannot re-arm later. A fresh Start press or controller-icon click can
explicitly re-arm. MCP `halt` cancels manual
motion while preserving arming, unlike `stop`.

Explicit controller state changes say **controller armed** or **controller
disarmed** through the robot speaker. The system **Arm motion** button and MCP
`arm` tool say **system armed** or **system disarmed** after successful changes.
Controller arming announces **system armed** as well when it enables system motion.
Startup, reconnection and unchanged settings remain silent. Speech runs on a
separate worker, with only the latest pending state per source retained. STOP
cancels queued/current audio. Audio errors appear in speaker status without
changing arming results or microphone consent. Arming never moves a servo.

Playback explicitly opens the named ALSA `robothat` device from the HAT audio
configuration. The desktop sound server can redirect `default`, so it is not
used for robot speech. `PIBOTPAL_SPEAKER_DEVICE` overrides the output when needed;
`get_status.speaker.device` reports the selected route.

On Pi 5 with the V4 HAT's `sndrpihifiberry` playback device, the kernel reserves
GPIO20 as an unused I²S input even though the HAT uses it for amplifier enable.
PiBotPal recognizes that specific pin-group configuration and uses Raspberry
Pi's BSD-licensed `/usr/bin/pinctrl` to switch GPIO20, keeping it low after audio
finishes or stops. Other GPIO configurations use the normal character-device
interface. No SunFounder runtime code is required.


## Camera pan servo expansion

The twelve leg servos occupy every Robot HAT servo output, P0–P11. SunFounder
[V4 hardware](https://docs.sunfounder.com/projects/robot-hat-v4/en/stable/hardware_introduction.html)
and [V5 hardware](https://docs.sunfounder.com/projects/robot-hat-v4/en/latest/robot_hat_v5/hardware_introduction.html)
both expose twelve servo headers; higher PWM channels feed DC motor drivers.
An additional servo controller is the straightforward path for IMX477 camera pan.

A PCA9685 I²C breakout at its default address `0x40` can share the bus with the
current HAT at `0x14`. Use Pi 3.3 V for logic VCC, a common ground, and a regulated
servo V+ supply rated for the servo’s voltage and current requirements. See
[Adafruit’s wiring/power guide](https://learn.adafruit.com/16-channel-pwm-servo-driver/pinouts).
Its [PCA9685 driver is MIT licensed](https://github.com/adafruit/Adafruit_CircuitPython_PCA9685).
The extra servo is not configured or driven yet; its range and center need
calibration once the board and servo specifications are available. Keep camera
pan separate from the twelve leg-joint channels and report commanded position.
