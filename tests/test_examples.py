import copy
import tempfile
import time
import unittest
from pathlib import Path

from pibotpal.examples import ExampleRunner, list_examples
from pibotpal.kinematics import inverse


class FakeController:
    def __init__(self):
        self.calls = []
        self.state = {"armed": False, "busy": False, "servo_offsets": [0]*12,
                      "legs": [{"foot_local_mm": [50, 40, -45]} for _ in range(4)],
                      "ultrasonic": {"valid": True, "distance_cm": 100, "timestamp": time.time()}}

    def status(self):
        return copy.deepcopy(self.state)

    def stop(self):
        self.state["armed"] = False
        self.calls.append(("stop",))

    def action(self, name, **kwargs):
        self.calls.append(("action", name))

    def set_pose(self, coords, **kwargs):
        for coord in coords:
            inverse(coord)
        self.calls.append(("pose", coords))

    def set_angles(self, angles, **kwargs):
        self.calls.append(("angles", angles))

    def set_offsets(self, offsets):
        if self.state["armed"]:
            raise RuntimeError("Disarm first")
        self.state["servo_offsets"] = offsets


class FakeMedia:
    def camera(self, enabled):
        return {"enabled": enabled}

    def detect_color(self, color):
        return {"detected": False}


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = FakeController()
        self.runner = ExampleRunner(self.controller, FakeMedia(), self.directory.name)

    def tearDown(self):
        self.runner.close()
        self.directory.cleanup()

    def finish(self):
        deadline = time.monotonic() + 2
        while self.runner.status()["running"] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.runner.status()["running"])
        return self.runner.status()

    def test_catalog_covers_installed_sources(self):
        examples = Path.home() / "picrawler/examples"
        if examples.exists():
            self.assertEqual({p.name for p in examples.glob("*.py")}, {e["source"] for e in list_examples()})

    def test_unavailable_voice_cannot_start_recording(self):
        with self.assertRaisesRegex(ValueError, "speech"):
            self.runner.run("15_stt")
        self.assertFalse(self.runner.status()["running"])
        self.assertEqual(self.controller.calls, [])

    def test_motion_requires_arm_and_options_are_bounded(self):
        with self.assertRaisesRegex(RuntimeError, "Arm"):
            self.runner.run("1_move")
        self.controller.state["armed"] = True
        for options in ({"steps": 99}, {"speed": float("nan")}, {"action": "__import__('os')"}, {"unknown": 1}, {"steps": True}, []):
            with self.assertRaises(ValueError):
                self.runner.run("1_move", options)
        self.runner.run("1_move", {"action": "forward", "steps": 3})
        self.assertEqual(self.finish()["phase"], "complete")
        self.assertEqual(self.controller.calls, [("action", "forward")] * 3)

    def test_stop_cancels_navigation_and_disarms(self):
        self.controller.state["armed"] = True
        # Keep an action busy to check cancellation while awaiting completion.
        def busy(*args, **kwargs):
            self.controller.state["busy"] = True
        self.controller.action = busy
        self.runner.run("4_avoid")
        self.runner.stop()
        self.assertEqual(self.finish()["phase"], "cancelled")
        self.assertFalse(self.controller.state["armed"])

    def test_stale_range_never_drives(self):
        self.controller.state["armed"] = True
        self.controller.state["ultrasonic"]["timestamp"] -= 10
        self.runner.run("4_avoid")
        self.assertEqual(self.finish()["phase"], "failed")
        self.assertEqual(self.controller.calls, [("stop",)])

    def test_duration_interrupts_busy_gait_as_successful_completion(self):
        self.controller.state["armed"] = True
        def busy(*args, **kwargs):
            self.controller.state["busy"] = True
        self.controller.action = busy
        started = time.monotonic()
        self.runner.run("4_avoid", {"seconds": 1})
        state = self.finish()
        self.assertLess(time.monotonic() - started, 1.3)
        self.assertEqual(state["phase"], "complete", state)
        self.assertTrue(state["result"]["duration_elapsed"])
        self.assertIsNone(state["error"])
        self.assertFalse(self.controller.state["armed"])
        self.assertEqual(self.controller.calls, [("stop",)])

    def test_treasure_allows_manual_motion_and_timeout_does_not_stop_it(self):
        self.controller.state["armed"] = True
        state = self.runner.run("8_treasure_hunt", {"seconds": 1})
        self.assertTrue(state["permits_manual_motion"])
        state = self.finish()
        self.assertEqual(state["phase"], "complete", state)
        self.assertFalse(state["result"]["found"])
        self.assertFalse(state["permits_manual_motion"])
        self.assertTrue(self.controller.state["armed"])
        self.assertEqual(self.controller.calls, [])

    def test_calibration_saves_only_disarmed(self):
        self.runner.run("0_calibration", {"operation": "save_offsets", "offsets": [1] * 12})
        self.assertEqual(self.finish()["result"]["offsets"], [1] * 12)
        self.assertEqual(self.controller.calls, [])

    def test_saved_poses_persist_and_replay(self):
        self.runner.run("11_record_new_step", {"operation": "capture"})
        self.assertEqual(self.finish()["result"], {"saved_steps": 1})
        other = ExampleRunner(self.controller, FakeMedia(), self.directory.name)
        self.controller.state["armed"] = True
        other.run("11_record_new_step", {"operation": "play"})
        other._thread.join(timeout=2)
        self.assertEqual(other.status()["result"], {"played": 1})
        self.assertEqual(self.controller.calls[0][0], "pose")

    def test_generated_custom_gestures_stay_in_workspace(self):
        self.controller.state["armed"] = True
        for identity, options in (("12_twist", {}), ("13_emotional_robot", {"emotion": "swimming"}), ("14_preset_actions", {"action": "excited"})):
            self.runner.run(identity, options)
            state = self.finish()
            self.assertEqual(state["phase"], "complete", state)


if __name__ == "__main__":
    unittest.main()
