from pathlib import Path
from types import SimpleNamespace
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.setup_check import inspect_setup


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def media_report():
    return {
        "provider": "host_executables", "ready": True, "pyav_version": "18",
        "ffmpeg": {"available": True, "version": "ffmpeg version test", "source": "PATH", "path": "/bin/ffmpeg"},
        "ffprobe": {"available": True, "version": "ffprobe version test", "source": "PATH", "path": "/bin/ffprobe"},
    }


class SetupCheckTests(unittest.TestCase):
    def runtime(self, root):
        components = {}
        hashes = {}
        for role, name in (("relay", "dlss-native-relay.exe"), ("worker", "nvngx.dll"),
                           ("nvngx_dlssnr", "nvngx_dlssnr.dll")):
            path = root / name
            path.write_bytes(b"MZ" + role.encode())
            components[role] = str(path)
            hashes[role] = digest(path)
        return {"ready": True, "errors": [], "platform": "windows", "execution_mode": "windows_native",
                "backend": "direct_nr", "preset_id": "test", "components": components,
                "component_hashes": hashes}

    def patches(self):
        return (
            patch("comfy_dlss_experimental.setup_check.platform.system", return_value="Windows"),
            patch("comfy_dlss_experimental.setup_check.resolve_media_tools", return_value=media_report()),
            patch("comfy_dlss_experimental.setup_check._package", return_value={"available": True, "version": "test"}),
            patch("comfy_dlss_experimental.setup_check.probe_environment",
                  return_value=SimpleNamespace(to_dict=lambda: {
                      "platform": "windows", "tools": {}, "warnings": [],
                      "gpu": {"nvidia_smi": {"available": True, "returncode": 0, "output": "Test GPU"}},
                  })),
            # Keep this last: unittest.mock itself uses importlib to resolve the
            # targets above while entering their context managers.
            patch("comfy_dlss_experimental.setup_check.importlib.import_module",
                  return_value=SimpleNamespace(DISOpticalFlow_create=lambda *_: None)),
        )

    def test_ready_direct_runtime_and_connected_dis_sequence(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary))
            sequence = {"settings": {"motion_provider": "dis"}, "media_tools_config": {},
                        "input_report": {"ready": True}}
            patches = self.patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                report = inspect_setup(runtime, sequence=sequence)
            self.assertTrue(report["ready"])
            self.assertEqual(report["summary"]["fail"], 0)
            self.assertEqual(report["flow"]["kind"], "dis")
            self.assertFalse(report["worker_started"])
            self.assertTrue(all(report["components"][role]["sha256_match"] for role in runtime["components"]))

    def test_changed_component_and_unready_input_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary))
            Path(runtime["components"]["worker"]).write_bytes(b"MZchanged")
            sequence = {"settings": {"motion_provider": "dis"}, "media_tools_config": {},
                        "input_report": {"ready": False}}
            patches = self.patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                report = inspect_setup(runtime, sequence=sequence)
            self.assertFalse(report["ready"])
            failures = {item["id"] for item in report["checks"] if item["status"] == "fail"}
            self.assertIn("component_worker", failures)
            self.assertIn("sequence", failures)

    def test_no_sequence_is_an_explicit_warning_not_a_false_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary))
            patches = self.patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                report = inspect_setup(runtime)
            self.assertTrue(report["ready"])
            self.assertEqual(next(c for c in report["checks"] if c["id"] == "sequence")["status"], "warn")

    def test_selected_nvidia_flow_requires_our_host_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary))
            sequence = {"settings": {"flow_provider": {"schema_version": 1, "kind": "nvidia",
                        "preset": "balanced", "output_grid": 4, "device": 0, "temporal_hints": True}},
                        "media_tools_config": {}, "input_report": {"ready": True}}
            patches = self.patches()
            with patches[0], patches[1], patches[2], patches[3], \
                 patch("comfy_dlss_experimental.helper_artifacts.find_helper", side_effect=RuntimeError("NVOF helper missing")), \
                 patches[4]:
                report = inspect_setup(runtime, sequence=sequence)
            self.assertFalse(report["ready"])
            failure = next(c for c in report["checks"] if c["id"] == "flow_helper")
            self.assertIn("NVOF helper missing", failure["detail"])


if __name__ == "__main__":
    unittest.main()
