import importlib.util
import unittest

from comfy_dlss_experimental.temporal_guides import GuideSettings, TemporalGuideGenerator

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("numpy", "cv2"))


class GuideSettingsTests(unittest.TestCase):
    def test_invalid_settings(self):
        for value in (0, 2, float("nan"), True):
            with self.assertRaises(ValueError):
                GuideSettings(analysis_scale=value).validate()


@unittest.skipUnless(HAS_DEPS, "OpenCV/numpy integration tests require the Linux media environment")
class OpticalFlowTests(unittest.TestCase):
    def test_direction_and_resize_restore_pixel_units(self):
        import cv2
        import numpy as np
        rng = np.random.default_rng(123)
        gray = cv2.GaussianBlur(rng.integers(0, 256, (256, 256), dtype=np.uint8), (5, 5), 0)
        first = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGBA)
        translated = cv2.warpAffine(first, np.float32([[1, 0, 6], [0, 1, 4]]), (256, 256), borderMode=cv2.BORDER_REFLECT)
        for scale in (0.5, 1.0):
            generator = TemporalGuideGenerator(256, 256, GuideSettings(analysis_scale=scale))
            zero, initial = generator.process(first.tobytes())
            self.assertTrue(initial["reset"])
            self.assertEqual(set(zero), {0})
            motion, metrics = generator.process(translated.tobytes())
            flow = np.frombuffer(motion, "<f2").reshape(256, 256, 2).astype(np.float32)
            middle = np.median(flow[40:-40, 40:-40], axis=(0, 1))
            np.testing.assert_allclose(middle, [-6, -4], atol=0.7)
            self.assertFalse(metrics["reset"])
            self.assertLess(metrics["warped_mae"], metrics["negated_flow_mae"] * 0.5)

    def test_scene_cut_and_explicit_reset(self):
        generator = TemporalGuideGenerator(64, 64)
        black = bytes([0, 0, 0, 255]) * (64 * 64)
        white = bytes([255, 255, 255, 255]) * (64 * 64)
        generator.process(black)
        motion, metrics = generator.process(white)
        self.assertEqual(metrics["reset_reason"], "scene_cut")
        self.assertEqual(set(motion), {0})
        _, metrics = generator.process(white, force_reset=True)
        self.assertEqual(metrics["reset_reason"], "explicit")

    def test_rejects_bad_plane(self):
        with self.assertRaises(ValueError):
            TemporalGuideGenerator(64, 64).process(b"bad")
