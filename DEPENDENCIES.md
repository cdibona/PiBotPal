# Application dependency policy and inventory

PiBotPal's own code is MIT. Avoid GPL/AGPL application dependencies and prefer
MIT, BSD, Apache-2.0, or similarly permissive libraries. Preserve all upstream
notices. This policy covers the service and clients, not the Raspberry Pi OS,
Linux kernel, Tailscale installation, development tools, or unrelated vendor
packages already installed on the machine. Do not remove those packages.

| Component | License / usage |
| --- | --- |
| python-periphery 2.4.1 | MIT; direct Linux I2C/GPIO HAT transport |
| Raspberry Pi pinctrl (raspi-utils-core) | BSD-3-Clause; Pi 5 / V4 HAT speaker-enable workaround |
| FastAPI / uvicorn / MCP Python SDK | MIT / BSD-3-Clause / MIT; HTTP and MCP |
| NumPy / Pillow | BSD-3-Clause / MIT-CMU; pixel processing and JPEG |
| Three.js / OrbitControls | MIT; vendored notice retained |
| libcamera core and Python bindings | **LGPL-2.1-or-later** system libraries; direct camera capture |
| Raspberry Pi libcamera pipeline/IPA | BSD-2-Clause; hardware camera pipeline |
| libasound | **LGPL-2.1-or-later** system shared library; direct PCM capture/playback |
| Flite core and CMU voice | Permissive CMU license; local speech synthesis |
| certifi | MPL-2.0; HTTPS certificate bundle, retains its own terms |
| zxing-cpp (optional) | Apache-2.0; QR decoding |
| Native Kotlin/Android and .NET/WPF clients | Original code MIT; platform dependencies retain their licenses |

LGPL libraries remain explicit system dependencies, dynamically loaded and
unmodified. This is not an all-permissive-stack claim. Distribution of system
libraries must respect their own terms. Debian package copyright files include
build tools and unused utilities with other licenses; the entire OS package
collection has not been relabeled MIT.

Removed from PiBotPal's runtime: SunFounder picrawler/robot_hat, vendor motion
tables/audio assets, Picamera2's eager GPL python-prctl import, OpenCV/FFmpeg,
eSpeak, arecord, and aplay. Sound effects are newly synthesized tones. Recording
uses a small original MJPEG AVI muxer. Face detection is unavailable pending a
suitable replacement; QR decoding is optional and fails clearly if absent.

The original SunFounder examples remain at `~/picrawler/examples`; the Testing
tab uses their filenames only as cross-references. Prior adapter implementations
remain in Git history with their original notices. See references/README.md.

Upstream license evidence:

- https://github.com/vsergeev/python-periphery/blob/master/LICENSE
- https://github.com/raspberrypi/utils/blob/master/LICENCE
- https://github.com/libcamera-org/libcamera/blob/master/src/py/libcamera/py_main.cpp
- https://github.com/libcamera-org/libcamera/blob/master/LICENSES/LGPL-2.1-or-later.txt
- https://github.com/festvox/flite/blob/master/COPYING
- https://github.com/alsa-project/alsa-lib/blob/master/COPYING
- https://github.com/zxing-cpp/zxing-cpp/blob/master/LICENSE

Installed system notices are available under `/usr/share/doc/<package>/copyright`.

The installed transitive Python requirement graph was reviewed on 2026-11-02: MIT, BSD, Apache-2.0, PSF-2.0, and certifi MPL-2.0 metadata; no GPL/AGPL requirement was found. This does not cover every unrelated package exposed by system-site-packages. Live camera capture was also checked without prctl, Picamera2, OpenCV, PyAV, or SunFounder imports.
