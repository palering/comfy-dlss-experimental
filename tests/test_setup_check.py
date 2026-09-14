from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
import hashlib
import importlib
import json
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
    def sr_runtime(self, root):
        components, hashes = {}, {}
        for role, name in (("worker", "comfy-dlss-worker.exe"), ("nvngx_dlss", "nvngx_dlss.dll")):
            path = root / name
            path.write_bytes(b"MZ" + role.encode())
            components[role], hashes[role] = str(path), digest(path)
        return {"ready": True, "errors": [], "platform": "windows", "execution_mode": "windows_native",
                "backend": "owned_sr", "preset_id": "sr-test", "components": components,
                "component_hashes": hashes, "runtime_key": "a" * 64, "worker_policy": "isolated",
                "compatibility": {"project_id": "7d5f45c3-147b-4d18-a8e8-ea2f51dc8ddd"}}

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

    def test_ready_sr_files_do_not_claim_compiled_or_gpu_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.sr_runtime(Path(temporary))
            with ExitStack() as stack:
                launch = stack.enter_context(patch("comfy_dlss_experimental.owned_process.OwnedWorkerProcess"))
                for item in self.patches():
                    stack.enter_context(item)
                report = inspect_setup(runtime, sequence={"settings": {"motion_provider": "dis"},
                    "media_tools_config": {}, "input_report": {"ready": True}})
            self.assertTrue(report["ready"])
            self.assertFalse(report["worker_started"])
            self.assertEqual(report["readiness_scope"], "files_and_host_dependencies_only")
            self.assertEqual(report["sr_validation"]["sdk_compiled"], "not_checked")
            self.assertEqual(report["sr_validation"]["gpu"], "not_executed")
            self.assertEqual(report["sr_validation"]["execution_readiness"], "not_established")
            self.assertEqual(set(report["components"]), {"worker", "nvngx_dlss"})
            warning = next(item for item in report["checks"] if item["id"] == "sr_capabilities")
            self.assertEqual(warning["status"], "warn")
            self.assertIn("CSR1 handshake", warning["detail"])
            self.assertEqual(report["runtime_pair"], "unrecognized")
            launch.assert_not_called()

    def test_sr_exact_roles_reject_nr_caller_or_model_even_if_files_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for role in ("caller", "nvngx_dlssnr", "relay"):
                runtime = self.sr_runtime(root)
                extra = root / (role + ".dll")
                extra.write_bytes(b"MZextra")
                runtime["components"][role] = str(extra)
                runtime["component_hashes"][role] = digest(extra)
                with ExitStack() as stack:
                    for item in self.patches():
                        stack.enter_context(item)
                    report = inspect_setup(runtime)
                self.assertFalse(report["ready"])
                failure = next(item for item in report["checks"] if item["id"] == "component_roles")
                self.assertEqual(failure["status"], "fail")

    def test_sr_project_policy_fingerprint_and_missing_model_block_preflight(self):
        invalid = [({"compatibility": {}}, "sr_project_id"),
                   ({"compatibility": None}, "sr_project_id"),
                   ({"compatibility": {"project_id": "00000000-0000-0000-0000-000000000000"}}, "sr_project_id"),
                   ({"compatibility": {"project_id": "7D5F45C3-147B-4D18-A8E8-EA2F51DC8DDD"}}, "sr_project_id"),
                   ({"worker_policy": "persistent"}, "sr_worker_policy"),
                   ({"runtime_key": "not-a-hash"}, "sr_runtime_contract")]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for replacement, check_id in invalid:
                runtime = self.sr_runtime(root)
                runtime.update(replacement)
                with ExitStack() as stack:
                    for item in self.patches():
                        stack.enter_context(item)
                    report = inspect_setup(runtime)
                with self.subTest(replacement=replacement):
                    self.assertFalse(report["ready"])
                    self.assertEqual(next(item for item in report["checks"] if item["id"] == check_id)["status"], "fail")
            runtime = self.sr_runtime(root)
            runtime["components"]["nvngx_dlss"] = str(root / "not-installed.dll")
            with ExitStack() as stack:
                for item in self.patches():
                    stack.enter_context(item)
                report = inspect_setup(runtime)
            self.assertFalse(report["ready"])
            self.assertEqual(next(item for item in report["checks"] if item["id"] == "component_nvngx_dlss")["status"], "fail")

    def test_owned_nr_roles_are_preserved_without_sr_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary))
            runtime["backend"] = "owned_nr"
            runtime["components"]["caller"] = runtime["components"].pop("relay")
            runtime["component_hashes"]["caller"] = runtime["component_hashes"].pop("relay")
            with ExitStack() as stack:
                for item in self.patches():
                    stack.enter_context(item)
                report = inspect_setup(runtime)
            self.assertTrue(report["ready"])
            self.assertEqual(set(report["components"]), {"worker", "caller", "nvngx_dlssnr"})
            self.assertNotIn("sr_validation", report)
            self.assertFalse(any(item["id"].startswith("sr_") for item in report["checks"]))

    def test_setup_node_explains_sr_scope_and_keeps_runtime_unchanged(self):
        from test_media_pipeline import node_modules
        with node_modules():
            module = importlib.import_module("comfy_dlss_experimental.nodes.setup_helper")
            runtime = {"backend": "owned_sr"}
            report = {"ready": True, "sr_validation": {"sdk_compiled": "not_checked"}}
            with patch.object(module, "inspect_setup", return_value=report):
                result = module.DLSSExperimentalSetupHelper.execute(runtime)
            self.assertIs(result.result[0], runtime)
            self.assertTrue(result.result[1])
            self.assertEqual(json.loads(result.ui["dlss_setup_report"][0]), report)
            description = module.DLSSExperimentalSetupHelper.define_schema().description
            self.assertIn("CSR1", description)
            self.assertIn("not compiled", description)

    def test_owned_sl_preflight_uses_renderer_motion_and_does_not_start_gpu(self):
        from test_sl_runtime import make_runtime
        parent = Path(__file__).resolve().parents[1] / "tmp" / "sl-setup-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            runtime = make_runtime(Path(temporary)) | {"platform": "windows", "execution_mode": "windows_native"}
            with ExitStack() as stack:
                launch = stack.enter_context(patch("comfy_dlss_experimental.owned_process.OwnedWorkerProcess"))
                for item in self.patches():
                    stack.enter_context(item)
                report = inspect_setup(runtime, flow_provider={"bad": "ignored_for_renderer_bundle"}, verify_nvidia_flow=True)
            self.assertTrue(report["ready"])
            self.assertEqual(report["sl_validation"]["protocol"], "CXR1")
            self.assertEqual(report["sl_validation"]["gpu"], "not_executed")
            self.assertEqual(set(report["python_packages"]), {"numpy"})
            self.assertEqual(report["flow"]["kind"], "renderer_bundle")
            self.assertFalse(report["sl_validation"]["foreground_required"])
            launch.assert_not_called()

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
