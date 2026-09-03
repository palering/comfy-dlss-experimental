import sys
import unittest
from comfy_dlss_experimental.windows_pipe import WindowsPipeProcess


class WindowsPipeTests(unittest.TestCase):
    def test_binary_backpressure_and_log_flood(self):
        data = bytes(range(256)) * 1024
        code = "import sys; sys.stderr.buffer.write(b'e'*200000); sys.stderr.flush(); d=sys.stdin.buffer.read(262144); sys.stdout.buffer.write(d); sys.stdout.flush()"
        client = WindowsPipeProcess([sys.executable, "-c", code], timeout=5)
        try:
            self.assertEqual(client.exchange(data, len(data)), data)
            self.assertLessEqual(len(client.stderr), 8192)
        finally:
            client.close()
        self.assertTrue(all(not thread.is_alive() for thread in client.readers))

    def test_cancel_timeout_blocked_writer_and_no_orphan_threads(self):
        for cancelled, error in ((lambda: False, TimeoutError), (lambda: True, InterruptedError)):
            client = WindowsPipeProcess([sys.executable, "-c", "import time; time.sleep(10)"], cancelled, timeout=.1)
            try:
                with self.assertRaises(error):
                    client.exchange(b"a" * 1000000, 1)
            finally:
                client.close()
                client.close()
            self.assertIsNotNone(client.process.poll())
            self.assertFalse(client.writer.is_alive())

    def test_split_responses_remain_buffered(self):
        code = "import sys; sys.stdin.buffer.read(1); sys.stdout.buffer.write(b'abcd'); sys.stdout.flush(); sys.stdin.buffer.read(1)"
        client = WindowsPipeProcess([sys.executable, "-c", code])
        try:
            self.assertEqual(client.exchange(b"x", 2), b"ab")
            self.assertEqual(client.exchange(b"y", 2), b"cd")
        finally:
            client.close(graceful=True)

    def test_error_and_oversized_handshake(self):
        for code, error, message in (
            ("import sys; sys.stderr.write('native failure')", RuntimeError, "native failure"),
            ("import sys; sys.stdout.write('x'*4096); sys.stdout.flush()", ValueError, "Oversized"),
        ):
            client = WindowsPipeProcess([sys.executable, "-c", code])
            try:
                with self.assertRaisesRegex(error, message):
                    client.exchange(b"", None)
            finally:
                client.close()
