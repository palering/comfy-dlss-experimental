import unittest
from fractions import Fraction
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from comfy_dlss_experimental.video_range import resolve_video_range
from comfy_dlss_experimental.storage_budget import frame_budget, require_disk, preparation_storage_plan, DISK_RESERVE


class VideoRangeTests(unittest.TestCase):
    def test_full_range_ignores_manual_duration_and_preserves_start(self):
        self.assertEqual(resolve_video_range(96, 0, 2, True), 96)
        self.assertEqual(resolve_video_range(96, 10, 2, True), 86)
        self.assertEqual(resolve_video_range(96, 10, 2, False), 2)
        self.assertEqual(resolve_video_range(96, 90, 10, False), 6)
        self.assertEqual(resolve_video_range(96, 10, 0, False, legacy_zero=True), 86)

    def test_invalid_or_empty_ranges_are_not_guessed(self):
        for args in [(float("nan"), 0, 2), (0, 0, 2), (8, 8, 2), (8, -1, 2), (8, 0, 0), (8, 0, 2, "true")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                resolve_video_range(*args)

    def test_no_thirty_second_or_two_gib_cutoff_but_protocol_is_bounded(self):
        self.assertEqual(frame_budget(60, .5, Fraction(24)), 1456)
        self.assertEqual(frame_budget(3600, .5, Fraction(24), True), 16)
        with self.assertRaisesRegex(ValueError, "1,000,000"):
            frame_budget(86400, 0, Fraction(60))
        with tempfile.TemporaryDirectory() as temp, patch("comfy_dlss_experimental.storage_budget.shutil.disk_usage",
                                                        return_value=SimpleNamespace(free=10 * 1024**3)):
            result = require_disk(Path(temp) / "new", 3 * 1024**3, stage="test")
            self.assertEqual(result["estimated_additional_bytes"], 3 * 1024**3)

    def test_insufficient_disk_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as temp, patch("comfy_dlss_experimental.storage_budget.shutil.disk_usage",
                                                        return_value=SimpleNamespace(free=DISK_RESERVE + 10)):
            target = Path(temp) / "not-created"
            with self.assertRaisesRegex(ValueError, "磁盘空间不足"):
                require_disk(target, 20, stage="test")
            self.assertFalse(target.exists())

    def test_cache_and_scratch_sharing_disk_are_added_before_admission(self):
        with tempfile.TemporaryDirectory() as temp, patch("comfy_dlss_experimental.storage_budget.shutil.disk_usage",
                                                        return_value=SimpleNamespace(free=10 * 1024**3)):
            report = preparation_storage_plan(Path(temp) / "cache", Path(temp) / "scratch", frame_count=100, plane_bytes=400)
            self.assertEqual(report["combined"]["estimated_additional_bytes"], 100 * (400 * 7 + 2048))
