from __future__ import annotations

import io
import struct
import unittest

from comfy_dlss_experimental.direct_nr import (
    DirectNRClient, DirectNRSettings, FRAME_HEADER, FRAME_MAGIC, OUT_MAGIC, RESULT_HEADER, VIDEO_HEADER, VIDEO_MAGIC,
)


class MemoryWorker:
    def __init__(self, output=b"", exit_code=0):
        self.input = bytearray()
        self.output = io.BytesIO(output)
        self.exit_code = exit_code

    def write(self, data):
        self.input.extend(data)

    def read_exact(self, size):
        result = self.output.read(size)
        if len(result) != size:
            raise EOFError("truncated worker frame")
        return result

    def close_input(self):
        pass

    def wait(self):
        return {"exit_code": self.exit_code, "reason": 0}


class DirectNRTests(unittest.TestCase):
    def test_exact_reference_layout_and_roundtrip(self):
        settings = DirectNRSettings(64, 64, 1)
        color = bytes(range(256)) * 64
        motion = struct.pack("<ee", 0.5, -1.0) * (64 * 64)
        pts = 123456789
        stream = MemoryWorker(RESULT_HEADER.pack(OUT_MAGIC, 0, 1, len(color), 1, pts) + color)
        client = DirectNRClient(stream, settings)
        self.assertEqual(client.process(color, motion, pts), color)
        self.assertEqual(client.finish()["exit_code"], 0)
        self.assertEqual(VIDEO_HEADER.size, 56)
        self.assertEqual(FRAME_HEADER.size, 24)
        self.assertEqual(RESULT_HEADER.size, 28)
        self.assertEqual(VIDEO_HEADER.unpack(stream.input[:56])[0], VIDEO_MAGIC)
        self.assertEqual(FRAME_HEADER.unpack(stream.input[56:80]), (FRAME_MAGIC, 0, 1, 0, pts))
        self.assertEqual(stream.input[80:], color + motion)

    def test_invalid_settings(self):
        for patch in ({"width": 63}, {"height": 4321}, {"frame_count": 0}, {"warmup": 0},
                      {"intensity": float("nan")}, {"local_tone": -1}, {"width": True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                DirectNRSettings(**({"width": 64, "height": 64, "frame_count": 1} | patch)).encode()

    def test_rejects_invalid_output_and_poisoned_stream(self):
        size = 64 * 64 * 4
        for packet in (
            RESULT_HEADER.pack(0, 0, 1, size, 1, 0),
            RESULT_HEADER.pack(OUT_MAGIC, 2, 1, size, 1, 0),
            RESULT_HEADER.pack(OUT_MAGIC, 0, 1, size + 1, 1, 0),
            RESULT_HEADER.pack(OUT_MAGIC, 0, 1, size, 0xBAD00002, 0),
            RESULT_HEADER.pack(OUT_MAGIC, 0, 1, size, 1, 1),
            RESULT_HEADER.pack(OUT_MAGIC, 0, 1, size, 1, 0) + b"short",
        ):
            client = DirectNRClient(MemoryWorker(packet), DirectNRSettings(64, 64, 1))
            with self.assertRaises((RuntimeError, EOFError)):
                client.process(bytes(size), bytes(size), 0)
            with self.assertRaises(RuntimeError):
                client.process(bytes(size), bytes(size), 0)

    def test_incomplete_and_failed_exit(self):
        client = DirectNRClient(MemoryWorker(), DirectNRSettings(64, 64, 1))
        with self.assertRaises(RuntimeError):
            client.finish()
        size = 64 * 64 * 4
        stream = MemoryWorker(RESULT_HEADER.pack(OUT_MAGIC, 0, 1, size, 1, 0) + bytes(size), 23)
        client = DirectNRClient(stream, DirectNRSettings(64, 64, 1))
        client.process(bytes(size), bytes(size), 0)
        with self.assertRaises(RuntimeError):
            client.finish()


if __name__ == "__main__":
    unittest.main()
