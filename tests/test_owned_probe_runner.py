import importlib.util
import io
import json
from pathlib import Path
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('owned_probe_runner', ROOT / 'sidecar/run_owned_probe.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def event(sequence, stage, state, code=0):
    return dict(protocol=1, sequence=sequence, stage=stage, state=state, code=code)


class FragmentedConnection:
    def __init__(self, data):
        self.data = bytearray(data)
        self.sent = bytearray()

    def recv(self, count):
        result = bytes(self.data[:min(count, 3)])
        del self.data[:len(result)]
        return result

    def sendall(self, data):
        self.sent.extend(data)

    def settimeout(self, value):
        if value <= 0:
            raise AssertionError('Unbounded/expired socket operation')


class OwnedProbeRunnerTests(unittest.TestCase):
    def collect(self, messages, token=b't' * 32):
        data = token + b''.join(json.dumps(message).encode() + b'\n' for message in messages)
        connection = FragmentedConnection(data)
        received = []
        code = runner.collect_reports(connection, b't' * 32, time.monotonic()+5, received, io.StringIO())
        return code, received, connection

    def test_fragmented_authenticated_abi_completion(self):
        messages = [event(0, 'process', 'ready'), event(1, 'parameter_abi', 'begin'),
                    event(2, 'parameter_abi', 'returned'), event(3, 'process', 'complete')]
        code, received, connection = self.collect(messages)
        self.assertEqual(received, messages)
        self.assertTrue(runner.abi_passed(received, code))
        self.assertEqual(connection.sent, b'\x01\x01')

    def test_reported_failure_is_not_success(self):
        code, received, connection = self.collect([event(0, 'process', 'ready'),
            event(1, 'process', 'failed', 1), event(2, 'process', 'complete', 1)])
        self.assertEqual(code, 1)
        self.assertFalse(runner.abi_passed(received, code))
        self.assertEqual(connection.sent, b'\x01\x01')

    def test_zero_completion_without_abi_is_not_acceptance(self):
        code, received, _ = self.collect([event(0, 'process', 'ready'), event(1, 'process', 'complete')])
        self.assertFalse(runner.abi_passed(received, code))

    def test_rejects_bad_token_and_early_disconnect(self):
        with self.assertRaisesRegex(ValueError, 'authentication'):
            self.collect([], token=b'x'*32)
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            self.collect([event(0, 'process', 'ready')])

    def test_message_and_deadline_bounds(self):
        connection = FragmentedConnection(b't'*32 + b'x'*512)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            runner.collect_reports(connection, b't'*32, time.monotonic()+5, [], io.StringIO())
        with self.assertRaises(TimeoutError):
            runner.receive_exact(connection, 1, time.monotonic()-1)

    def test_schema_order_and_ready_are_strict(self):
        invalid = [event(1, 'process', 'ready'), event(0, 'process', 'complete'),
                   event(0, 'process', 'ready', -1), event(0, 'unknown', 'ready'),
                   dict(event(0, 'process', 'ready'), protocol=True),
                   dict(event(0, 'process', 'ready'), stage=[]),
                   dict(event(0, 'process', 'ready'), extra='unexpected')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.validate_event(value, 0)
        with self.assertRaises(ValueError):
            runner.validate_event(event(1, 'process', 'ready'), 1)

    def test_runner_keeps_proton_run_and_cpu_only_modes(self):
        source = (ROOT / 'sidecar/run_owned_probe.py').read_text()
        self.assertIn("[str(proton), 'run', str(worker)", source)
        self.assertNotIn("'runinprefix'", source)
        self.assertIn("choices=('abi', 'report-failure')", source)
        self.assertNotIn('--probe-nr-init-parameters', source)
        self.assertNotIn('process.poll()', source)

    def test_probe_keeps_separate_fence_and_lifecycle_diagnostics(self):
        source = (ROOT / 'sidecar/src/nr_probe.cpp').read_text()
        engine = (ROOT / 'sidecar/src/nr_engine.cpp').read_text()
        for stage in ('init', 'create', 'evaluate', 'release', 'shutdown'):
            self.assertIn(f'event("{stage}", "begin")' if stage != 'evaluate' else 'event("evaluate", "begin", frame)', engine)
            self.assertIn(f'event("{stage}", "returned",', engine)
        self.assertIn('submit_wait("create_fence")', engine)
        self.assertIn('submit_wait("evaluate_fence")', engine)
        self.assertIn('report.complete(', source)
        self.assertIn('--probe-device', source)
        self.assertIn('--probe-init-parameters', source)
        self.assertIn('--probe-create-parameters', source)
        self.assertIn('if (!report.connected()) std::printf', source)


if __name__ == '__main__':
    unittest.main()
