from __future__ import annotations

import os
import signal
import unittest
from unittest.mock import MagicMock, patch

from scripts.run_frame_worker_smoke import terminate_smoke_processes


@unittest.skipUnless(os.name == "posix", "POSIX process supervision test")
class SmokeCleanupTests(unittest.TestCase):
    def test_cleanup_only_signals_exact_marked_child(self):
        for marker_value, expected_signals in (("job-123", 1), ("job-123-other", 0), ("another-job", 0)):
            with self.subTest(marker=marker_value):
                process = MagicMock(pid=1111)
                proc = MagicMock()
                entry = MagicMock(name="entry")
                entry.name = "2222"
                entry.stat.return_value.st_uid = os.getuid()
                entry.__truediv__.return_value.read_bytes.return_value = (
                    b"PATH=/usr/bin" + bytes(1) +
                    f"COMFY_DLSS_SMOKE_ID={marker_value}".encode() + bytes(1) + b"OTHER=value" + bytes(1)
                )
                proc.iterdir.return_value = [entry]
                with (
                    patch("scripts.run_frame_worker_smoke.Path", return_value=proc),
                    patch("scripts.run_frame_worker_smoke.os.killpg") as group_signal,
                    patch("scripts.run_frame_worker_smoke.os.pidfd_open", return_value=42, create=True) as open_pid,
                    patch("scripts.run_frame_worker_smoke.signal.pidfd_send_signal", create=True) as child_signal,
                    patch("scripts.run_frame_worker_smoke.os.close") as close_fd,
                ):
                    terminate_smoke_processes(process, "job-123")
                group_signal.assert_called_once_with(1111, signal.SIGKILL)
                open_pid.assert_called_once_with(2222)
                self.assertEqual(child_signal.call_count, expected_signals)
                close_fd.assert_called_once_with(42)
                process.wait.assert_called_once_with(timeout=10)
