import unittest

from comfy_dlss_experimental.fg_contract import (
    FGCapability, FGSettings, FGTimeline, FG_FOREGROUND_NOTICE, MAX_TIMESTAMP_NS,
)


class FGContractTests(unittest.TestCase):
    def test_native_mfg_is_not_emulated_on_fg_only_runtime(self):
        capability = FGCapability(True, 1, True)
        capability.require(FGSettings(2))
        with self.assertRaisesRegex(ValueError, "no emulated MFG"):
            capability.require(FGSettings(3))
        FGCapability(True, 5, True).require(FGSettings(6))

    def test_adapter_query_and_actual_output_are_separate_gates(self):
        for capability, message in ((FGCapability(False, None), "does not support"),
                                   (FGCapability(True, None), "not been queried"),
                                   (FGCapability(True, 1), "offline")):
            with self.subTest(capability=capability), self.assertRaisesRegex(ValueError, message):
                capability.require(FGSettings())
        FGCapability(True, 1).require(FGSettings(), offline=False)

    def test_unverified_offline_output_explains_the_scoped_foreground_requirement(self):
        with self.assertRaises(ValueError) as error:
            FGCapability(True, 1).require(FGSettings())
        self.assertIn(FG_FOREGROUND_NOTICE, str(error.exception))
        for text in ("FG_FOREGROUND_REQUIRED", "currently tested", "GPU host",
                     "not the ComfyUI browser", "eOff disables generation",
                     "eRetainResourcesWhenOff only retains resources"):
            self.assertIn(text, FG_FOREGROUND_NOTICE)
        # Adding a notice must not promote offline output or change MFG limits.
        with self.assertRaisesRegex(ValueError, "exceeds runtime limit"):
            FGCapability(True, 1).require(FGSettings(3))
        FGCapability(True, 1, True).require(FGSettings())

    def test_options_are_strict_and_generated_count_is_not_multiplier(self):
        self.assertEqual(FGSettings(2).generated_per_interval, 1)
        self.assertEqual(FGSettings(2).feature, "fg")
        self.assertEqual(FGSettings(4).feature, "mfg")
        for value in (True, 1, 0, 7, 2.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FGSettings(value)
        with self.assertRaises(ValueError):
            FGCapability(False, 0, True)

    def test_two_times_keeps_real_pts_and_exact_end_duration(self):
        timeline = FGTimeline(FGSettings(2))
        self.assertEqual(timeline.push(0, 1_000_000_000), ())
        first = timeline.push(1, 1_033_333_333)
        second = timeline.push(2, 1_066_666_667)
        tail = timeline.finish(1_100_000_000)
        slots = first + second + tail
        self.assertEqual([slot.pts_ns for slot in slots],
                         [1_000_000_000, 1_016_666_666, 1_033_333_333, 1_050_000_000, 1_066_666_667])
        self.assertEqual(sum(slot.duration_ns for slot in slots), 100_000_000)
        self.assertEqual(first[1].origin, "generated")
        self.assertEqual((first[1].left_source, first[1].right_source), (0, 1))
        self.assertEqual((first[1].phase_numerator, first[1].phase_denominator), (1, 2))

    def test_six_times_and_cut_never_generate_across_discontinuity(self):
        timeline = FGTimeline(FGSettings(6))
        timeline.push(0, 0)
        slots = timeline.push(1, 101)
        self.assertEqual(len(slots), 6)
        self.assertEqual(sum(slot.duration_ns for slot in slots), 101)
        held = timeline.push(2, 1000, scene_cut=True)
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].origin, "rendered")
        self.assertEqual(held[0].duration_ns, 899)
        self.assertEqual(timeline.finish(1100)[0].duration_ns, 100)

    def test_invalid_pts_does_not_advance_the_planner(self):
        timeline = FGTimeline(FGSettings(6))
        with self.assertRaises(ValueError):
            timeline.push(1, 0)
        timeline.push(0, 10)
        for index, pts in ((1, 10), (2, 100), (1, 12), (1, MAX_TIMESTAMP_NS + 1), (True, 100)):
            with self.subTest(index=index, pts=pts), self.assertRaises(ValueError):
                timeline.push(index, pts)
        self.assertEqual(len(timeline.push(1, 100)), 6)
        with self.assertRaises(ValueError):
            timeline.finish(100)
        timeline.finish(110)
        with self.assertRaises(RuntimeError):
            timeline.push(2, 120)
        with self.assertRaises(RuntimeError):
            timeline.finish(120)

    def test_one_frame_is_held_not_fabricated_into_interpolation(self):
        timeline = FGTimeline(FGSettings(4))
        timeline.push(0, 0)
        slots = timeline.finish(100)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].origin, "rendered")
        with self.assertRaises(ValueError):
            FGTimeline(FGSettings()).finish(100)


if __name__ == '__main__':
    unittest.main()
