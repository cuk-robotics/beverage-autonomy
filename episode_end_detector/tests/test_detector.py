import unittest

from episode_end_detector import EpisodeEndDetector


class DetectorTests(unittest.TestCase):
    def setUp(self):
        model = {"version": 1, "feature": "observation.state",
                 "joint_names": [f"joint_{i}" for i in range(12)],
                 "lower": [-1.] * 12, "upper": [1.] * 12}
        self.inside = [0.] * 12
        self.outside = [2.] + [0.] * 11
        self.detector = EpisodeEndDetector(model, instructions=["first", "second"])

    def complete(self, base):
        self.detector.update(self.outside, base)
        for i in range(21):
            result = self.detector.update(self.inside, base + .1 + i * .1)
        return result

    def test_config_and_bundled_files(self):
        detector = EpisodeEndDetector.load_config()
        self.assertEqual(detector.hold_seconds, 2.)
        self.assertEqual(detector.max_gap_seconds, .2)
        self.assertEqual(len(detector.names), 12)
        self.assertEqual(len(detector.instructions), 3)

    def test_waits_for_exit_and_exact_two_second_boundary(self):
        self.assertEqual(self.detector.start_episode(), "first")
        for i in range(31):
            result = self.detector.update(self.inside, i * .1)
        self.assertEqual(result["status"], "waiting_exit")
        self.detector.update(self.outside, 3.1)
        for i in range(20):
            self.detector.update(self.inside, 3.2 + i * .1)
        self.assertFalse(self.detector.update(self.inside, 5.19)["episode_done"])
        result = self.detector.update(self.inside, 5.2)
        self.assertEqual(result["next_instruction"], "second")
        self.assertEqual(result["event"], "episode_end")

    def test_sequence_stops_until_restart(self):
        self.detector.start_episode()
        self.assertEqual(self.complete(0)["next_instruction"], "second")
        self.assertEqual(self.detector.update(self.inside, 2.2)["status"], "waiting_exit")
        self.assertTrue(self.complete(3)["sequence_done"])
        result = self.detector.update(None, -1)
        self.assertTrue(result["sequence_done"])
        self.assertIsNone(result["event"])
        self.assertIsNone(result["in_range"])
        self.assertEqual(self.detector.start_episode(), "first")
        self.assertFalse(self.detector.sequence_done)

    def test_gap_and_invalid_input_break_continuity(self):
        self.detector.start_episode()
        self.detector.update(self.outside, 0)
        for i in range(15):
            self.detector.update(self.inside, .1 + i * .1)
        self.assertEqual(self.detector.update(self.inside, 1.8)["dwell_seconds"], 0)
        self.detector.update(self.inside, 1.9)
        with self.assertRaises(ValueError):
            self.detector.update([float("nan")] * 12, 2.)
        self.assertEqual(self.detector.update(self.inside, 2.)["dwell_seconds"], 0)

    def test_replaces_instructions_and_rejects_empty_list(self):
        self.assertEqual(self.detector.start_episode(["custom"]), "custom")
        with self.assertRaises(ValueError):
            self.detector.start_episode([])
        self.assertEqual(self.detector.current_instruction, "custom")


if __name__ == "__main__":
    unittest.main()
