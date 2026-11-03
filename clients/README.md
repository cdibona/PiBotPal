# PiBotPal native clients

These first Android and Windows clients use the robot's existing **MCP server** for every control and status request. They display the camera through a native multipart JPEG decoder; they do not embed the web dashboard.

Both provide a configurable robot URL, explicit arm/disarm, persistent STOP, one-cycle directional movement, stand/sit, measured battery voltage, forward ultrasonic range, and live video. They initialize MCP, poll `get_status` every second in the foreground, and honor busy/disarmed state. They never enable or capture microphone audio. Both include a **Plan** tab for saved servo poses and motion sequences. The website's 3D view, calibration/testing catalog, and hardware/LLM administration remain in the web interface.

## Connect

1. Install Tailscale on the Android device or Windows computer and join the robot's tailnet.
2. Open PiBotPal and use `https://spiderbot.tail689a35.ts.net` (or your robot's HTTPS base URL).
3. Press **Connect**. The video starts if the robot camera is already enabled; otherwise choose **Enable camera**.
4. Place the robot appropriately, then use **Arm motion** before movement. Every movement request is one gait cycle.

A valid HTTPS certificate is required. There is no certificate-validation bypass. Only loopback development addresses allow HTTP (Android additionally allows its emulator host alias `10.0.2.2`). The robot's existing Tailscale access rules control access; these apps do not store credentials.

Leaving the foreground or losing the connection sends a best-effort stop and requires **Connect** again. STOP is a software command that holds the last PWM command, not a power cutoff. The server's existing 15-second heartbeat timeout is a fallback; its heartbeat is shared, so another connected client can keep it alive. Do not rely on closing an app as a hardware emergency stop.

Power display shows measured voltage. It does not invent charge percentage, charging state, or estimated runtime. Null/invalid readings are shown as unavailable. A responsive robot service establishes that the Pi is powered, not whether it is charging.

## Plan tab

The Plan tab uses the shared MCP plan library, so poses and plans saved from the website appear in both apps. Refresh the library after another interface edits it.

1. Give a pose a name and choose **Save current servo positions** after the robot has a complete commanded pose. These are commanded angles; the servos have no position feedback.
2. Describe a sequence, for example: “Stand. Move forward 4 steps, turn left, move forward four steps.” Choose **Draft steps for review**. The default uses the server’s bounded command parser; optionally select **Use configured LLM to draft** to use the Admin assistant provider. Drafting never executes movement.
3. Review or edit the steps as JSON, name the plan, and save. The editor accepts action, saved-pose, and wait steps. Select a saved pose to append it to the draft.
4. Select a saved plan, explicitly arm motion, then choose **Run selected plan**. Global **STOP** cancels the plan and disarms. Leaving the app/window retains the existing stop behavior.

Example step array:

```json
[
  {"type": "action", "name": "stand", "speed": 35, "steps": 1},
  {"type": "action", "name": "forward", "speed": 35, "steps": 4},
  {"type": "action", "name": "turn left", "speed": 35, "steps": 1},
  {"type": "wait", "seconds": 1}
]
```

The server validates and bounds every plan. Sensor conditions and autonomous decision steps are not executed by these initial clients.

## Download from the robot

Open the **PiBotPal apps** section at the bottom of the [robot website](https://spiderbot.tail689a35.ts.net/#apps). Downloads are served directly by the robot over Tailscale; no GitHub login is needed.

- [Android APK](https://spiderbot.tail689a35.ts.net/downloads/PiBotPal-Android.apk)
- [Windows x64 ZIP](https://spiderbot.tail689a35.ts.net/downloads/PiBotPal-Windows-x64.zip)
- [Build source and SHA-256 checksums](https://spiderbot.tail689a35.ts.net/downloads/build.json)

To refresh the hosted files from the latest successful main-branch client build, run `python3 scripts/sync-client-downloads.py` on the robot with GitHub CLI authentication. Files live under `~/.local/share/pibotpal/downloads`, outside Git. An explicit successful build can be selected with `--run ID`.

## Download CI builds

The **Native clients** GitHub Actions workflow creates:

- `PiBotPal-Android-debug`: an installable debug APK. Download/unzip and install on Android 8.0 or newer. Android may ask to allow installation from the browser/file manager. This is a development build, not a Play Store release.
- `PiBotPal-Windows-x64`: a ZIP containing the self-contained Windows x64 program and runtime. Extract all files together, then run `PiBotPal.exe`. No separate .NET installation is required. The program is not code-signed yet.

Workflow runs build source and execute transport parser tests. Physical-device behavior, foreground transitions, and TLS/video performance should also be checked on actual Android and Windows hardware before relying on them. This Raspberry Pi cannot run either native GUI.

## Build Android

Use JDK 17, Gradle 8.9, Android SDK platform 35, and build tools 34.0.0. The project pins Android Gradle Plugin 8.7.3 and Kotlin 2.2.21. Set `ANDROID_HOME` or create ignored `clients/android/local.properties` with `sdk.dir=...`.

```sh
gradle -p clients/android :app:assembleDebug :app:testDebugUnitTest
```

APK: `clients/android/app/build/outputs/apk/debug/app-debug.apk`.

Android Studio can import `clients/android`. A Gradle installation is used explicitly so no generated wrapper binary is committed. The native activity uses platform Views and the standard HTTP/JSON libraries; there are no camera or microphone permissions.

## Build Windows

Install the .NET 10 SDK. The app uses WPF and requires Windows to run.

```powershell
dotnet run --project clients/windows-tests/PiBotPal.TransportTests.csproj -c Release
dotnet publish clients/windows/PiBotPal.Windows.csproj -c Release -r win-x64 --self-contained true -o dist/windows
```

`EnableWindowsTargeting` allows compile checks from another OS with a .NET SDK and internet access for the Windows targeting pack. Only Windows can validate the running WPF UI. A compile check and the transport parser tests have passed on the Pi, and the native C# transport received real MCP status and an MJPEG frame over Tailscale HTTPS without moving the robot. For ARM64 Windows, publish with `-r win-arm64` instead.

For a read-only transport check against a running robot:

```sh
dotnet run --project clients/windows-tests/PiBotPal.TransportTests.csproj -c Release -- --smoke https://spiderbot.tail689a35.ts.net
```

This initializes MCP, reads status, and decodes one camera frame. It does not arm or move the robot.

The Windows client saves its endpoint under `%LOCALAPPDATA%\PiBotPal\client.json`; Android uses private SharedPreferences. Keys and other admin settings remain on the robot.

## Transport contract

- `POST /mcp`: JSON-RPC 2.0, `Accept: application/json, text/event-stream`.
- Initialize at protocol version `2025-03-26`, then use the server-negotiated protocol version and session header when supplied.
- `tools/call`: `get_status`, `arm`, `stop`, `move`, and `camera`.
- `GET /video.mjpg`: multipart JPEG, each part with `Content-Length`; frames are limited to 1 MiB and header lengths are bounded.
- Responses accept both current `structuredContent` and older JSON text content. The server uses stateless JSON responses; the clients also accept a finite SSE result.

Compatibility references: [AGP 8.7 requirements](https://developer.android.com/build/releases/agp-8-7-0-release-notes), [Kotlin Gradle compatibility](https://kotlinlang.org/docs/gradle-configure-project.html), [.NET 10 WPF](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/whats-new/net100).

## License

Original Android and Windows client source is MIT licensed; see `clients/LICENSE`. These clients use HTTP/MCP and do not bundle SunFounder Python libraries. Platform and runtime dependencies retain their upstream licenses.
