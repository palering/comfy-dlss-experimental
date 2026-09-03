import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.platform_runtime import resolve_execution, runtime_proton_choices
from comfy_dlss_experimental.video_pipeline import relay_environment
from comfy_dlss_experimental.discovery import custom_proton
from comfy_dlss_experimental.jobs import augment_graphical_session


class PlatformRuntimeTests(unittest.TestCase):
    def test_windows_does_not_discover_or_validate_linux_fields(self):
        with patch("platform.system", return_value="Windows"), \
             patch("comfy_dlss_experimental.discovery.discover_proton_installations", side_effect=AssertionError("Linux discovery called")), \
             patch("comfy_dlss_experimental.discovery.custom_proton", side_effect=AssertionError("Linux path read")):
            result = resolve_execution("windows", "unavailable", "stale-Linux-Proton", "/invalid", "invalid")
            self.assertFalse(result["errors"])
            self.assertEqual(result["execution_mode"], "windows_native")
            self.assertIsNone(result["proton"])
            self.assertIsNone(result["linux_display_backend"])
            self.assertEqual(runtime_proton_choices(), ["auto"])

    def test_platform_mismatch_stops_before_discovery(self):
        with patch("platform.system", return_value="Windows"), \
             patch("comfy_dlss_experimental.discovery.discover_proton_installations", side_effect=AssertionError):
            result = resolve_execution("linux")
            self.assertIn("不一致", result["errors"][0])
            self.assertIsNone(result["execution_mode"])

    def test_native_reserved_does_not_fall_back_to_proton(self):
        with patch("platform.system", return_value="Linux"), \
             patch("comfy_dlss_experimental.discovery.discover_proton_installations", side_effect=AssertionError):
            result = resolve_execution("auto", "native_reserved")
            self.assertEqual(result["execution_mode"], "linux_native_reserved")
            self.assertIn("尚未实现", result["errors"][0])
            with self.assertRaisesRegex(ValueError, "尚未实现"):
                relay_environment(result, Path("unused"))

    def test_windows_environment_never_enters_graphics_detection(self):
        with tempfile.TemporaryDirectory() as temporary, patch("platform.system", return_value="Windows"), \
             patch("comfy_dlss_experimental.jobs.augment_graphical_session", side_effect=AssertionError):
            proton, env = relay_environment({"platform": "windows"}, Path(temporary))
            self.assertIsNone(proton)
            self.assertEqual(env["TEMP"], str(Path(temporary) / "tmp"))
            self.assertEqual(env["TEMP"], env["TMP"])

    def test_display_detector_is_guarded_even_when_called_directly(self):
        with patch("platform.system", return_value="Windows"), patch("pathlib.Path.glob", side_effect=AssertionError):
            self.assertEqual(augment_graphical_session({"A": "b"}), ({"A": "b"}, {}))

    @unittest.skipIf(os.name == "nt", "POSIX launcher permissions")
    def test_custom_path_file_directory_spaces_and_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "GE 自定义 Proton"
            root.mkdir()
            launcher = root / "proton"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(0o755)
            self.assertEqual(custom_proton(str(root)).executable, launcher)
            self.assertEqual(custom_proton(str(launcher)).executable, launcher)
            with patch("platform.system", return_value="Linux"), \
                 patch("comfy_dlss_experimental.discovery.discover_proton_installations", side_effect=AssertionError):
                result = resolve_execution(proton="stale", proton_path=str(root))
                self.assertFalse(result["errors"])
                self.assertEqual(result["proton"]["executable"], str(launcher))
                result = resolve_execution(proton_path=str(root / "missing"))
                self.assertTrue(result["errors"])
            launcher.chmod(0o644)
            with self.assertRaises(ValueError):
                custom_proton(str(launcher))
            for value in ("relative/proton", "", "x\nfoo"):
                with self.assertRaises(ValueError):
                    custom_proton(value)
