from __future__ import annotations

import io
import os
import socket
import struct
import threading
import unittest
from collections import deque
from unittest.mock import MagicMock, patch

from comfy_dlss_experimental.native_relay import (
    DIAGNOSTIC, EXIT, HEADER, HELLO, MAGIC, MAX_PAYLOAD, OUTPUT, NativeRelay, _marked_pidfds,
)


class OwnedProcessTests(unittest.TestCase):
    def test_cleanup_failure_keeps_prefix_locked_and_allows_retry(self):
        client = NativeRelay.__new__(NativeRelay)
        client._closed = False
        client._socket = None
        client._reader_thread = None
        client._worker_log = io.BytesIO()
        client._launcher_log = io.BytesIO()
        client._prefix_lock = io.BytesIO()
        with patch.object(client, "_stop_process", side_effect=[RuntimeError("cleanup failed"), None]) as stop:
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                client.close()
            self.assertTrue(client._worker_log.closed)
            self.assertTrue(client._launcher_log.closed)
            self.assertFalse(client._prefix_lock.closed)
            self.assertFalse(client._closed)
            client.close()
            self.assertTrue(client._closed)
            self.assertTrue(client._prefix_lock.closed)
            client.close()
            self.assertEqual(stop.call_count, 2)

    @patch("comfy_dlss_experimental.native_relay.os.getuid", return_value=1234, create=True)
    def test_marker_is_exact_and_process_identity_is_pinned(self, _getuid):
        for value, expected in (("job-123", [(2222, 42)]), ("job-123-other", []), ("another-job", [])):
            with self.subTest(value=value):
                entry = MagicMock()
                entry.name = "2222"
                entry.stat.return_value.st_uid = os.getuid()
                entry.__truediv__.return_value.read_bytes.return_value = (
                    b"OTHER=x\0" + f"COMFY_DLSS_RELAY_RUN_ID={value}".encode() + b"\0"
                )
                proc = MagicMock()
                proc.iterdir.return_value = [entry]
                with (patch("comfy_dlss_experimental.native_relay.sys.platform", "linux"),
                      patch("comfy_dlss_experimental.native_relay.Path", return_value=proc),
                      patch("comfy_dlss_experimental.native_relay.os.pidfd_open", return_value=42, create=True) as pin,
                      patch("comfy_dlss_experimental.native_relay.os.close") as close):
                    self.assertEqual(_marked_pidfds("job-123"), expected)
                    pin.assert_called_once_with(2222)
                    self.assertEqual(close.call_count, 0 if expected else 1)


class NativeRelayReaderTests(unittest.TestCase):
    def setUp(self):
        self.receiver, self.sender = socket.socketpair()
        self.receiver.settimeout(2)
        self.client = NativeRelay.__new__(NativeRelay)
        self.client.timeout = 2
        self.client._cv = threading.Condition()
        self.client._socket = self.receiver
        self.client._chunks = deque()
        self.client._buffered = 0
        self.client._hello = None
        self.client._exit = None
        self.client._error = None
        self.client._discard = False
        self.client._worker_log = io.BytesIO()
        self.client._log_bytes = 0
        self.client._log_truncated = False
        self.thread = threading.Thread(target=self.client._reader)
        self.thread.start()

    def tearDown(self):
        self.sender.close()
        self.receiver.close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())

    def packet(self, kind, payload):
        encoded = HEADER.pack(MAGIC, kind, len(payload)) + payload
        # Exercise fragmented headers/payloads and interleaved channels.
        for offset in range(0, len(encoded), 3):
            self.sender.sendall(encoded[offset:offset + 3])

    def test_demultiplexes_output_and_logs(self):
        self.packet(HELLO, struct.pack("<II", 10, 20))
        self.packet(OUTPUT, b"abcd")
        self.packet(DIAGNOSTIC, b"not binary output\n")
        self.packet(OUTPUT, b"efghi")
        self.packet(EXIT, struct.pack("<II", 0, 0))
        self.assertEqual(self.client.read_exact(6), b"abcdef")
        self.assertEqual(self.client.read_exact(3), b"ghi")
        self.assertEqual(self.client.wait(), {"exit_code": 0, "reason": 0})
        self.assertEqual(self.client.hello, {"relay_pid": 10, "worker_pid": 20})
        self.assertEqual(self.client._worker_log.getvalue(), b"not binary output\n")

    def test_oversize_rejected_without_payload_read(self):
        self.sender.sendall(HEADER.pack(MAGIC, OUTPUT, MAX_PAYLOAD + 1))
        with self.assertRaisesRegex(RuntimeError, "invalid relay response"):
            self.client.read_exact(1)

    def test_logs_are_capped_but_output_keeps_flowing(self):
        with patch("comfy_dlss_experimental.native_relay.MAX_LOG", 16):
            self.packet(DIAGNOSTIC, b"x" * 32)
            self.packet(DIAGNOSTIC, b"y" * 32)
            self.packet(OUTPUT, b"result")
            self.packet(EXIT, struct.pack("<II", 0, 0))
            self.assertEqual(self.client.read_exact(6), b"result")
            self.assertEqual(self.client.wait()["exit_code"], 0)
        log = self.client._worker_log.getvalue()
        self.assertTrue(log.startswith(b"x" * 16 + b"\n[relay:"))
        self.assertNotIn(b"y", log.split(b"\n", 1)[0])
        self.assertEqual(log.count(b"log truncated"), 1)
        self.assertEqual(self.client._log_bytes, 16)

    def test_short_child_output_is_not_success(self):
        self.packet(OUTPUT, b"a")
        self.packet(EXIT, struct.pack("<II", 25, 0))
        with self.assertRaisesRegex(EOFError, "worker exited"):
            self.client.read_exact(2)

    def test_trailing_output_rejected(self):
        self.packet(OUTPUT, b"a")
        self.packet(EXIT, struct.pack("<II", 0, 0))
        with self.assertRaisesRegex(RuntimeError, "trailing output"):
            self.client.wait()


if __name__ == "__main__":
    unittest.main()
