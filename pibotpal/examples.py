# SPDX-License-Identifier: MIT
"""Independent bounded hardware exercises mapped to SunFounder example topics.

Original upstream examples remain separately licensed reference material. This
runner imports only PiBotPal interfaces; it never imports or executes examples.
Movement routines use PiBotPal's original small trajectories and public actions.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import threading
import time

from .motion import MotionPlan, stance


def choice(*values, default=None, description=""):
    return {"type": "string", "enum": list(values), "default": default or values[0], "description": description}


def number(default, minimum, maximum, description="", integer=False):
    return {"type": "integer" if integer else "number", "default": default,
            "minimum": minimum, "maximum": maximum, "description": description}


SPEED = number(35, 10, 60, "Motion speed (%)", integer=True)
SECONDS = number(10, 1, 30, "Maximum run duration (seconds)")
MOVES = ("forward", "backward", "turn left", "turn right", "turn left angle", "turn right angle", "stand", "sit")
PRESETS = ("sit", "stand", "look_up", "look_down", "wave_hand", "shake_hand", "fighting", "excited", "play_dead", "nod", "shake_head", "look_left", "look_right", "warm_up", "push_up", "dance")
SOUNDS = ("talk1", "talk2", "talk3", "sign", "happy", "happy2", "error", "depress", "depress2", "battle", "vigilance", "warning", "bell")
COLORS = ("red", "orange", "yellow", "green", "blue", "purple")


def entry(source, title, description, requires=(), unavailable=None, **parameters):
    return {"id": source.removesuffix(".py"), "source": source, "title": title,
            "description": description, "requires": list(requires),
            "available": unavailable is None, "reason": unavailable,
            "parameters": {"type": "object", "properties": parameters, "additionalProperties": False}}


CATALOG = [
    entry("0_calibration.py", "Calibration", "Arm only to enter the centered ready pose. Read or save twelve offsets while disarmed; saving does not move the legs.", ("motion_optional",),
          operation=choice("pose", "read_offsets", "save_offsets"), offsets={"type": "array", "items": {"type": "number", "minimum": -20, "maximum": 20}, "minItems": 12, "maxItems": 12}),
    entry("1_move.py", "Movement", "One selected gait, with a bounded number of cycles.", ("motion",), action=choice(*MOVES), speed=SPEED, steps=number(1, 1, 3, integer=True)),
    entry("2_keyboard_control.py", "Keyboard control", "Execute one keyboard command; the dashboard also provides directional controls.", ("motion",), key=choice("w", "a", "s", "d", "space"), speed=SPEED),
    entry("3_sound_effect.py", "Sound effects", "Play one synthesized PiBotPal effect through the robot speaker.", ("speaker",), sound=choice(*SOUNDS)),
    entry("4_avoid.py", "Obstacle avoidance", "Bounded sonar navigation: turn below the threshold, otherwise advance. Invalid or stale ranging stops the run.", ("motion", "ultrasonic"), seconds=SECONDS, threshold_cm=number(30, 15, 100), speed=SPEED),
    entry("5_display.py", "Camera and vision", "Enable video and inspect a frame, color target, or QR code. QR decoding requires optional zxing-cpp; face detection is currently unavailable.", ("camera",), mode=choice("display", "snapshot", "color", "face", "qr"), color=choice(*COLORS)),
    entry("6_record_video.py", "Record video", "Save a bounded silent camera recording on this Pi.", ("camera",), seconds=SECONDS),
    entry("7_bull_fight.py", "Red target tracking", "Bounded red-target pursuit. Turn toward red, advance only with clear valid sonar; hold when the target is lost.", ("motion", "camera", "ultrasonic"), seconds=SECONDS, speed=SPEED),
    entry("8_treasure_hunt.py", "Treasure hunt", "Find a selected color for up to 30 seconds. Use dashboard movement controls to search; this test never moves automatically.", ("camera",), color=choice(*COLORS), seconds=SECONDS),
    entry("9_do_step.py", "Custom step", "Stand, lift the left rear foot, then return to stand once.", ("motion",), speed=SPEED),
    entry("10_do_single_leg.py", "Single leg", "Move one foot in leg-local coordinates, in millimeters.", ("motion",), leg=number(0, 0, 3, "0 right front, 1 left front, 2 left rear, 3 right rear", True), x=number(50, 0, 120), y=number(45, 0, 120), z=number(-40, -90, 30), speed=SPEED),
    entry("11_record_new_step.py", "Record new steps", "Capture commanded joint angles and foot positions, replay up to 20 saved poses, or clear the saved sequence. Arm only to play. Stored locally across restarts.", ("motion_optional",), operation=choice("capture", "play", "clear"), speed=SPEED),
    entry("12_twist.py", "Twist", "One original small body-sway cycle using PiBotPal trajectories.", ("motion",), speed=SPEED),
    entry("13_emotional_robot.py", "Emotional robot", "One bounded expressive routine using the shared movement controller.", ("motion",), emotion=choice("handwork", "twist", "pushup", "swimming"), speed=SPEED),
    entry("14_preset_actions.py", "Preset actions", "Original small gestures selected by familiar example names. Play-dead uses the resting pose.", ("motion",), action=choice(*PRESETS), speed=SPEED),
    entry("15_stt.py", "Speech to text", "Recognize speech from the robot microphone.", ("microphone", "speech model"), unavailable="A bounded local speech recognizer and language model have not been configured. Microphone stays muted."),
    entry("16_tts.py", "Text to speech", "Speak text once through the robot speaker using the installed local speech engine.", ("speaker",), text={"type": "string", "default": "Hello! I am PiBotPal.", "maxLength": 500}),
    entry("17_online_llm_test.py", "Online language model", "Test a configured remote language model.", ("provider credentials",), unavailable="No language-model provider is configured for this server. This test does not send data to a cloud service."),
    entry("18_voice_active_crawler_gpt.py", "GPT voice assistant", "Speech, conversation, and robot actions.", ("microphone", "speech model", "provider credentials"), unavailable="Requires configured STT, GPT credentials, and a bounded voice-to-action adapter."),
    entry("19_voice_active_crawler_doubao.py", "Doubao voice assistant", "Speech, conversation, and robot actions.", ("microphone", "speech model", "provider credentials"), unavailable="Requires configured STT, Doubao credentials, and a bounded voice-to-action adapter."),
    entry("20_voice_active_crawler_ollama.py", "Ollama voice assistant", "Local model conversation and robot actions.", ("microphone", "speech model", "Ollama"), unavailable="Requires a configured Ollama endpoint/model, STT, and a bounded voice-to-action adapter."),
    entry("servo_zeroing.py", "Calibrated servo neutral", "Move all twelve logical servo commands to zero. Preserves calibration offsets; raw assembly-jig zeroing is not performed on the assembled robot.", ("motion",), speed=SPEED),
    entry("voice_active_crawler.py", "Voice assistant framework", "Base class used by the provider examples.", ("voice adapter",), unavailable="Support module, not a standalone demo. Provider examples require voice configuration."),
]


def list_examples():
    """Return independent metadata objects; hardware state is checked at run time."""
    return copy.deepcopy(CATALOG)


class Cancelled(Exception):
    pass


class DurationElapsed(Exception):
    """An operator-selected test duration ended normally."""


class ExampleRunner:
    def __init__(self, controller, media, data_dir=None):
        self.controller = controller
        self.media = media
        self.data_dir = Path(data_dir or os.environ.get("PIBOTPAL_DATA_DIR", str(Path.home() / ".local/share/pibotpal")))
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {"running": False, "permits_manual_motion": False, "example_id": None, "phase": "idle", "result": None,
                       "error": None, "started_at": None, "finished_at": None}
        self._deadline = 0
        self._timed = False
        self._owns_motion = False

    def list(self):
        return list_examples()

    def status(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    def run(self, example_id, options=None):
        item = next((e for e in CATALOG if e["id"] == example_id), None)
        if item is None:
            raise ValueError("Unknown example")
        if not item["available"]:
            raise ValueError(item["reason"])
        options = self._validate(item, {} if options is None else options)
        with self._lock:
            if self._state["running"]:
                raise RuntimeError("Another test is running; stop it first")
            hardware = self.controller.status()
            needs_motion = "motion" in item["requires"]
            if example_id == "0_calibration":
                needs_motion = options["operation"] == "pose"
            if example_id == "11_record_new_step":
                needs_motion = options["operation"] == "play"
            if needs_motion:
                if not hardware.get("armed"):
                    raise RuntimeError("Arm the robot before running this movement test")
                if hardware.get("busy"):
                    raise RuntimeError("Robot is busy")
            self._cancel.clear()
            self._state = {"running": True, "permits_manual_motion": example_id == "8_treasure_hunt",
                           "example_id": example_id, "phase": "starting",
                           "result": None, "error": None, "started_at": time.time(), "finished_at": None}
            self._timed = example_id in ("4_avoid", "7_bull_fight", "8_treasure_hunt")
            self._owns_motion = needs_motion
            self._deadline = time.monotonic() + min(90, options["seconds"] if self._timed else 90)
            self._thread = threading.Thread(target=self._execute, args=(item, options), name="pibotpal-example", daemon=True)
            self._thread.start()
            return self.status()

    @staticmethod
    def _validate(item, options):
        if not isinstance(options, dict):
            raise ValueError("Options must be an object")
        schema = item["parameters"]["properties"]
        unknown = set(options) - set(schema)
        if unknown:
            raise ValueError(f"Unknown options: {', '.join(sorted(unknown))}")
        values = {}
        for key, field in schema.items():
            if key not in options and "default" not in field:
                continue
            value = options.get(key, field.get("default"))
            kind = field["type"]
            if kind in ("number", "integer"):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{key} must be a finite number")
                if kind == "integer" and int(value) != value:
                    raise ValueError(f"{key} must be an integer")
                if not field["minimum"] <= value <= field["maximum"]:
                    raise ValueError(f"{key} must be between {field['minimum']} and {field['maximum']}")
                value = int(value) if kind == "integer" else value
            elif kind == "string":
                if not isinstance(value, str) or len(value) > field.get("maxLength", 1000):
                    raise ValueError(f"Invalid {key}")
                if "enum" in field and value not in field["enum"]:
                    raise ValueError(f"Invalid {key}")
            elif kind == "array":
                if not isinstance(value, list) or len(value) != 12 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not -20 <= v <= 20 for v in value):
                    raise ValueError("Offsets must be twelve finite angles between -20 and 20 degrees")
            values[key] = value
        return values

    def stop(self):
        with self._lock:
            running = self._state["running"]
            self._cancel.set()
            if running:
                self._state["phase"] = "stopping"
        self.controller.stop()
        return self.status()

    def close(self):
        self.stop()
        if self._thread:
            self._thread.join(timeout=2)

    def _check(self):
        if self._cancel.is_set():
            raise Cancelled()
        if time.monotonic() >= self._deadline:
            # Interrupt an active gait instead of letting its remaining steps
            # run past the user's duration. Treasure hunt owns no motion, so
            # its timeout must not cancel an independent manual command.
            if self._owns_motion:
                self.controller.stop()
            if self._timed:
                raise DurationElapsed()
            raise RuntimeError("Test exceeded its 90 second execution limit")

    def _pause(self, seconds):
        self._check()
        if self._cancel.wait(min(seconds, max(0, self._deadline - time.monotonic()))):
            raise Cancelled()
        self._check()

    def _motion(self, method, *args, **kwargs):
        self._check()
        getattr(self.controller, method)(*args, **kwargs)
        while self.controller.status().get("busy"):
            self._pause(.04)
        state = self.controller.status()
        if state.get("action_error"):
            raise RuntimeError(state["action_error"])
        if not state.get("armed"):
            raise Cancelled()
        self._check()

    def _action(self, name, speed):
        self._update(phase=name)
        self._motion("action", name, speed=speed, steps=1)

    def _pose(self, coords, speed):
        self._motion("set_pose", coords, speed=speed)

    def _distance(self):
        reading = self.controller.status().get("ultrasonic", {})
        distance, timestamp = reading.get("distance_cm"), reading.get("timestamp")
        if not reading.get("valid") or not isinstance(distance, (int, float)) or not math.isfinite(distance) or not timestamp or time.time() - timestamp > 2:
            raise RuntimeError("Ultrasonic return is invalid or stale; navigation stopped")
        return distance

    def _execute(self, item, opts):
        try:
            result = self._dispatch(item["id"], opts)
            self._check()
            self._update(phase="complete", result=result)
        except Cancelled:
            self._update(phase="cancelled")
        except DurationElapsed:
            result = {"completed": True, "duration_elapsed": True}
            if item["id"] == "8_treasure_hunt":
                result.update(found=False, color=opts["color"])
            self._update(phase="complete", result=result)
        except Exception as exc:
            if "motion" in item["requires"] or "motion_optional" in item["requires"]:
                self.controller.stop()
            self._update(phase="failed", error=str(exc))
        finally:
            self._update(running=False, permits_manual_motion=False, finished_at=time.time())

    def _dispatch(self, identity, o):
        speed = o.get("speed", 35)
        if identity == "0_calibration":
            if o["operation"] == "pose":
                self._action("ready", speed)
            elif o["operation"] == "save_offsets":
                if "offsets" not in o:
                    raise ValueError("Provide all twelve offsets to save")
                self.controller.set_offsets(o["offsets"])
            return {"offsets": self.controller.status().get("servo_offsets")}
        if identity == "1_move":
            for _ in range(o["steps"]):
                self._action(o["action"], speed)
        elif identity == "2_keyboard_control":
            self._action({"w": "forward", "s": "backward", "a": "turn left", "d": "turn right", "space": "sit"}[o["key"]], speed)
        elif identity == "3_sound_effect":
            return self.media.play_sound(o["sound"] + ".wav")
        elif identity == "4_avoid":
            while time.monotonic() < self._deadline:
                self._check()
                self._action("turn left angle" if self._distance() < o["threshold_cm"] else "forward", speed)
        elif identity == "5_display":
            self.media.camera(True)
            if o["mode"] in ("display", "snapshot"):
                return {"video_url": "/video.mjpg", "snapshot_url": "/snapshot.jpg"}
            self._pause(.5)
            if o["mode"] == "color":
                return self.media.detect_color(o["color"])
            if o["mode"] == "face":
                raise RuntimeError("Face detection is unavailable: no permissively licensed detector is configured")
            try:
                import zxingcpp
            except ImportError as exc:
                raise RuntimeError("QR decoding requires the optional Apache-2.0 zxing-cpp package") from exc
            frame = self.media.get_frame()
            if frame is None:
                raise RuntimeError("Camera has no current frame")
            # get_frame uses BGR memory ordering; ZXing expects RGB.
            found = zxingcpp.read_barcodes(frame[:, :, ::-1].copy(), formats=zxingcpp.BarcodeFormat.QRCode)
            codes = [{"text": code.text, "points": [[point.x, point.y] for point in
                      (code.position.top_left, code.position.top_right,
                       code.position.bottom_right, code.position.bottom_left)]}
                     for code in found if code.valid]
            return {"detected": bool(codes), "text": codes[0]["text"] if codes else "",
                    "points": codes[0]["points"] if codes else None, "codes": codes}
        elif identity == "6_record_video":
            self.media.camera(True)
            return self.media.record(o["seconds"])
        elif identity == "7_bull_fight":
            self.media.camera(True)
            while time.monotonic() < self._deadline:
                self._check()
                target = self.media.detect_color("red")
                self._update(result=target)
                if not target.get("detected"):
                    self._update(phase="waiting for red target")
                    self._pause(.2)
                    continue
                width = target.get("frame_width", 640)
                x = target["x"]
                distance = self._distance()
                if x < width * .4:
                    self._action("turn left", speed)
                elif x > width * .6:
                    self._action("turn right", speed)
                elif distance > 30:
                    self._action("forward", speed)
                else:
                    self._update(phase="target reached; holding")
                    self._pause(.2)
        elif identity == "8_treasure_hunt":
            self.media.camera(True)
            self._update(phase="searching for " + o["color"])
            while time.monotonic() < self._deadline:
                self._check()
                target = self.media.detect_color(o["color"])
                self._update(result=target)
                if target.get("detected") and target.get("width", 0) > 100:
                    return {"found": True, "color": o["color"], "target": target}
                self._pause(.2)
            return {"found": False, "color": o["color"]}
        elif identity == "9_do_step":
            self._action("ready", speed)
            raised = stance()
            raised[2][2] += 10
            self._pose(raised, speed)
            self._pause(.3)
            self._action("stand", speed)
        elif identity == "10_do_single_leg":
            self._motion("set_leg", o["leg"], o["x"], o["y"], o["z"], speed=speed)
        elif identity == "11_record_new_step":
            return self._record_steps(o["operation"], speed)
        elif identity == "12_twist":
            self._twist(speed)
        elif identity == "13_emotional_robot":
            emotion = o["emotion"]
            if emotion == "twist":
                self._twist(speed)
            elif emotion == "handwork":
                self._preset("wave_hand", speed)
                self._preset("shake_hand", speed)
            elif emotion == "pushup":
                self._preset("push_up", speed)
            else:
                # A small alternating gesture replaces the teaching swim loop.
                self._action("look left", speed)
                self._action("wave", speed)
                self._action("look right", speed)
                self._action("stand", speed)
        elif identity == "14_preset_actions":
            self._preset(o["action"], speed)
        elif identity == "16_tts":
            return self.media.speak(o["text"])
        elif identity == "servo_zeroing":
            self._motion("set_angles", [0] * 12, speed=speed)
        else:
            raise ValueError("Example has no runner")
        return {"completed": True}

    def _twist(self, speed):
        self._update(phase="body sway")
        for pose in MotionPlan().dance:
            self._pose(pose, speed)

    def _preset(self, name, speed):
        # These are independent compositions of the public, bounded primitives.
        routines = {
            "sit": ("sit",), "stand": ("stand",),
            "wave_hand": ("wave",), "shake_hand": ("wave", "ready"),
            "look_up": ("look up",), "look_down": ("look down",),
            "look_left": ("look left",), "look_right": ("look right",),
            "push_up": ("push up",), "play_dead": ("sit",),
            "dance": ("dance",), "warm_up": ("ready", "dance"),
            "nod": ("look down", "look up", "stand"),
            "shake_head": ("look right", "look left", "stand"),
            "excited": ("push up", "wave", "stand"),
            "fighting": ("ready", "wave", "stand"),
        }
        for action in routines[name]:
            self._action(action, speed)

    def _record_steps(self, operation, speed):
        path = self.data_dir / "saved_steps.json"
        poses = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(poses, list) or len(poses) > 20:
            raise ValueError("Invalid saved steps file")
        if operation == "capture":
            if len(poses) >= 20:
                raise ValueError("Maximum of 20 saved poses reached")
            state = self.controller.status()
            # Prefer the actual commanded-angle FK foot positions in leg-local axes.
            coords = state.get("foot_coordinates") or state.get("coordinates")
            if coords is None:
                coords = [leg.get("foot_local_mm") for leg in state.get("legs", [])]
            if len(coords) != 4 or any(p is None for p in coords):
                raise RuntimeError("No commanded pose yet; move to a known pose first")
            angles = state.get("servo_angles", [])
            if len(angles) != 12 or any(isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a) for a in angles):
                raise RuntimeError("No complete commanded joint pose to record")
            # Preserve the commands themselves: a valid joint pose can lie
            # outside the narrower inverse-kinematics foot workspace.
            poses.append({"angles": angles, "coords": coords})
        elif operation == "clear":
            poses = []
        else:
            if not poses:
                raise ValueError("No saved poses to replay")
            for pose in poses:
                if isinstance(pose, dict):
                    self._motion("set_angles", pose["angles"], speed=speed)
                else:
                    # Compatibility with earlier coordinate-only saves.
                    self._pose(pose, speed)
            return {"played": len(poses)}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(poses, indent=2) + "\n")
        temporary.replace(path)
        return {"saved_steps": len(poses)}
