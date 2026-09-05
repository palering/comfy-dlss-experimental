import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.execution_log import current_trace, execution_status, gpu_snapshot, measured, phase, tracked, request_worker_release, cached_gpu_snapshot


class ExecutionLogTests(unittest.TestCase):
    def test_release_targets_exact_live_execution_and_uses_cancellation(self):
        with tempfile.TemporaryDirectory() as directory, patch("comfy_dlss_experimental.config.data_root", return_value=Path(directory)):
            @tracked("process")
            def run(**kwargs):
                trace = current_trace()
                self.assertFalse(request_worker_release(trace.id)["accepted"])
                trace.set_worker_state("running")
                self.assertFalse(request_worker_release("stale-execution-id")["accepted"])
                self.assertFalse(trace.release_requested.is_set())
                self.assertTrue(request_worker_release(trace.id)["accepted"])
                self.assertTrue(request_worker_release(trace.id)["accepted"])
                self.assertTrue(trace.release_requested.is_set())
                trace.set_worker_state("releasing")
                trace.set_worker_state("released")
                self.assertFalse(request_worker_release(trace.id)["accepted"])
                raise InterruptedError("manual release")
            with self.assertRaises(InterruptedError):
                run()
            record = execution_status()["history"][0]
            self.assertEqual(record["worker_state"], "released")
            self.assertTrue(record["release_requested"])
            self.assertEqual(record["state"], "cancelled")
            self.assertFalse(request_worker_release(record["execution_id"])["accepted"])

    def test_cleanup_failure_is_not_labelled_released(self):
        with tempfile.TemporaryDirectory() as directory, patch("comfy_dlss_experimental.config.data_root", return_value=Path(directory)):
            @tracked("process")
            def run(**kwargs):
                current_trace().set_worker_state("cleanup_failed")
                raise RuntimeError("cleanup failed")
            with self.assertRaises(RuntimeError):
                run()
            record = execution_status()["history"][0]
            self.assertEqual(record["worker_state"], "cleanup_failed")
            self.assertEqual(record["state"], "failed")

    def test_telemetry_queries_are_shared_and_copies_are_independent(self):
        with patch("comfy_dlss_experimental.execution_log._gpu_cache", None), \
                patch("comfy_dlss_experimental.execution_log._gpu_checked_at", 0), \
                patch("comfy_dlss_experimental.execution_log.time.monotonic", return_value=10), \
                patch("comfy_dlss_experimental.execution_log.gpu_snapshot", return_value={"gpus": [{"name": "test"}]}) as query:
            first = cached_gpu_snapshot()
            first["gpus"][0]["name"] = "edited"
            second = cached_gpu_snapshot()
            self.assertEqual(second["gpus"][0]["name"], "test")
            self.assertEqual(query.call_count, 1)
            self.assertIn("sampled_at", second)

    def test_success_phases_identity_and_no_tensor_serialization(self):
        with tempfile.TemporaryDirectory() as directory, patch("comfy_dlss_experimental.config.data_root", return_value=Path(directory)):
            @tracked("process")
            def run(**kwargs):
                with phase("input_preparation"):
                    self.assertIsNotNone(current_trace())
                current_trace().record["guide_cache_hit"] = True
                return object(), {"passed": True}
            result = run(sequence={"video": object(), "public": {"width": 64}}, profile={"intensity": .4},
                         runtime={"backend": "direct_nr", "preset_path": "/example.json", "component_hashes": {"worker": "abc"}},
                         retain_prepared_cache=False)
            self.assertTrue(result[1]["passed"])
            self.assertEqual(result[1]["execution"]["state"], "success")
            self.assertIsNone(current_trace())
            status = execution_status()
            self.assertEqual(status["active"], [])
            record = status["history"][0]
            self.assertEqual(record["state"], "success")
            self.assertEqual(record["parameters"]["profile"], {"intensity": .4})
            self.assertIs(record["parameters"]["retain_prepared_cache"], False)
            self.assertEqual(record["runtime"]["component_hashes"]["worker"], "abc")
            self.assertTrue(record["guide_cache_hit"])
            self.assertGreaterEqual(record["stage_seconds"]["input_preparation"], 0)
            self.assertNotIn("result", record)

    def test_failures_and_cancellation_are_logged_and_propagate(self):
        for error, state in [(ValueError("bad input"), "failed"), (InterruptedError("cancel"), "cancelled")]:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory, patch("comfy_dlss_experimental.config.data_root", return_value=Path(directory)):
                @tracked("process")
                def fail(**kwargs):
                    with phase("validation"):
                        raise error
                with self.assertRaises(type(error)):
                    fail()
                record = execution_status()["history"][0]
                self.assertEqual(record["state"], state)
                self.assertEqual(record["error"], str(error))
                self.assertIsNone(current_trace())

    def test_nvidia_absent_is_unknown_not_zero(self):
        with patch("comfy_dlss_experimental.execution_log.subprocess.run", side_effect=FileNotFoundError()):
            value = gpu_snapshot()
        self.assertFalse(value["available"])
        self.assertEqual(value["gpus"], [])

    def test_gpu_xml_graphics_pid_and_na(self):
        xml = """<nvidia_smi_log><driver_version>test</driver_version><gpu><product_name>RTX</product_name>
        <fb_memory_usage><total>16000 MiB</total><used>N/A</used></fb_memory_usage>
        <utilization><gpu_util>6 %</gpu_util></utilization><processes><process_info><pid>123</pid>
        <type>G</type><used_memory>701 MiB</used_memory></process_info></processes></gpu></nvidia_smi_log>"""
        with patch("comfy_dlss_experimental.execution_log.subprocess.run") as run:
            run.return_value.stdout = xml
            gpu = gpu_snapshot()["gpus"][0]
        self.assertEqual(gpu["processes"], [{"pid": 123, "memory_mib": 701.0}])
        self.assertIsNone(gpu["memory_used_mib"])
        self.assertEqual(gpu["utilization_percent"], 6)

    def test_failure_to_write_diagnostics_does_not_mask_result(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "file"
            bad.write_text("not a directory")
            with patch("comfy_dlss_experimental.config.data_root", return_value=bad):
                @tracked("process")
                def run(**kwargs):
                    return None, {"passed": True}
                with self.assertWarns(UserWarning):
                    self.assertTrue(run()[1]["passed"])
            self.assertIsNone(current_trace())

    def test_phase_without_trace_is_safe(self):
        @measured("test")
        def run():
            return 3
        self.assertEqual(run(), 3)
