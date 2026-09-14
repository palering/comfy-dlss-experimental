from dataclasses import replace
import socket
import struct
import threading
import unittest

from comfy_dlss_experimental import sr_worker as wire
from comfy_dlss_experimental import owned_worker as nr_wire
from comfy_dlss_experimental.sr_contract import SRFrameMetadata, SRSettings, plan_sr


TOKEN = b't' * 32
COLOR = b'\x00\x38' * 64 * 64 * 4
MOTION = b'\0' * 64 * 64 * 4
DEPTH = struct.pack('<f', .5) * 64 * 64


def message(kind, request=0, session=0, payload=b''):
    return wire.HEADER.pack(wire.MAGIC, wire.VERSION, kind, len(payload), request, session) + payload


def capabilities(compiled=1):
    return wire.CAPABILITIES.pack(64, 64, 1920, 1080, 3840, 2160,
                                 1, 1, 1, 1, wire.MAX_PAYLOAD, 1, 1, 3, compiled, 1_000_000)


def settings():
    return wire.OwnedSRSettings(64, 64, 64, 64, SRSettings(mode='dlaa'))


class Connection:
    def __init__(self, responses=b'', *, token=TOKEN, caps=None, protocol=wire.MAGIC):
        caps = capabilities() if caps is None else caps
        self.data = bytearray(token + wire.HEADER.pack(protocol, 1, wire.CAPS, len(caps), 0, 0) + caps + responses)
        self.sent = bytearray()
        self.closed = False

    def recv(self, size):
        block = bytes(self.data[:min(size, 257)])
        del self.data[:len(block)]
        return block

    def sendall(self, data):
        self.sent.extend(data)

    def settimeout(self, value):
        assert value > 0

    def shutdown(self, *_):
        pass

    def close(self):
        self.closed = True


class SRWorkerTests(unittest.TestCase):
    def test_portrait_is_not_sent_to_legacy_worker(self):
        connection=Connection();client=wire.OwnedSRClient(connection,TOKEN)
        before=bytes(connection.sent)
        with self.assertRaisesRegex(ValueError,'legacy CSR1'):
            client.create(wire.OwnedSRSettings(736,1280,1472,2560),session_id=1)
        self.assertEqual(bytes(connection.sent),before);client.close()
    def test_settings_use_existing_contract_and_official_enum_mapping(self):
        config = wire.OwnedSRSettings(640, 360, 1280, 720,
            SRSettings(mode='quality', preset='m', hdr=True, depth_inverted=True,
                       motion_jittered=True, auto_exposure=False, jitter_policy='external_render_metadata'))
        self.assertEqual(wire.SETTINGS.unpack(config.encode()), (640, 360, 1280, 720, 2, 13, 7, 0))
        self.assertEqual(len(config.encode()), 32)
        self.assertEqual(wire.SETTINGS.unpack(settings().encode())[4:], (5, 0, 8, 0))
        for key, value in [('input_width', True), ('output_width', 5000), ('output_height', 721)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(config, **{key: value}).encode()

    def test_maximum_output_contract_is_four_k(self):
        config = wire.OwnedSRSettings(1920, 1080, 3840, 2160)
        self.assertEqual(len(config.encode()), 32)
        self.assertEqual(wire.MAX_PAYLOAD, 8 + 3840 * 2160 * 8)
        with self.assertRaises(ValueError):
            wire.OwnedSRSettings(1920, 1080, 7680, 4320).encode()

    def test_plan_mapping_ignores_forged_native_cache_and_freezes_settings(self):
        report = plan_sr(SRSettings(mode='dlaa'), 64, 64)
        report['native_settings']['mode'] = 999
        config = wire.OwnedSRSettings.from_plan(report)
        report['settings']['mode'] = 'quality'
        self.assertEqual(config.settings.mode, 'dlaa')
        self.assertEqual(wire.SETTINGS.unpack(config.encode())[4], 5)
        for invalid in ({}, {'kind': 'sr_input_plan'}, {'kind': 'sr_input_plan', 'input': None}):
            with self.assertRaises(ValueError):
                wire.OwnedSRSettings.from_plan(invalid)

    def test_fragmented_end_reuse_release_and_new_session(self):
        result = struct.pack('<Q', 0) + COLOR
        conn = Connection(message(wire.ACK, 1, 7) + message(wire.RESULT, 2, 7, result)
            + message(wire.ACK, 3, 7) + message(wire.RESULT, 4, 7, result)
            + message(wire.ACK, 5, 0) + message(wire.ACK, 6, 7)
            + message(wire.ACK, 7, 8) + message(wire.ACK, 8, 0))
        client = wire.OwnedSRClient(conn, TOKEN)
        client.create(settings(), session_id=7)
        self.assertEqual(client.process(COLOR, MOTION, DEPTH, 0), COLOR)
        client.end()
        self.assertEqual(client.session_frames, 1)
        self.assertEqual(client.process(COLOR, MOTION, DEPTH, 0), COLOR)
        client.ping()
        client.release()
        self.assertEqual(client.session_frames, 0)
        client.create(settings(), session_id=8)
        client.shutdown()
        self.assertEqual(client.total_frames, 2)
        self.assertTrue(conn.closed)
        frames = []
        sent = bytes(conn.sent[1:])
        while sent:
            magic, _, kind, size, _, _ = wire.HEADER.unpack_from(sent)
            self.assertEqual(magic, wire.MAGIC)
            if kind == wire.FRAME_MESSAGE:
                frames.append(wire.FRAME.unpack_from(sent, wire.HEADER.size))
            sent = sent[wire.HEADER.size + size:]
        self.assertEqual([frame[:3] for frame in frames], [(0, 1, 0), (0, 1, 0)])

    def test_invalid_local_frames_do_not_send_or_poison_session(self):
        conn = Connection(message(wire.ACK, 1, 1))
        client = wire.OwnedSRClient(conn, TOKEN)
        client.create(settings(), session_id=1)
        count = len(conn.sent)
        for color, motion, depth, pts in [(b'', MOTION, DEPTH, 0), (COLOR, b'', DEPTH, 0),
                (COLOR, MOTION, b'', 0), (bytearray(COLOR), MOTION, DEPTH, 0),
                (COLOR, MOTION, DEPTH, True), (COLOR, MOTION, DEPTH, -1),
                (COLOR, MOTION, DEPTH, 1 << 63)]:
            with self.subTest(pts=pts), self.assertRaises(ValueError):
                client.process(color, motion, depth, pts)
        with self.assertRaises(ValueError):
            client.process(COLOR, MOTION, DEPTH, 0, metadata={'reset': True})
        with self.assertRaises(ValueError):
            client.process(COLOR, MOTION, DEPTH, 0, metadata=SRFrameMetadata(jitter_x=.25))
        with self.assertRaises(ValueError):
            client.process(COLOR, MOTION, DEPTH, 0, metadata=SRFrameMetadata(exposure=1e-300))
        with self.assertRaises(RuntimeError):
            client.end()
        self.assertEqual(len(conn.sent), count)
        self.assertFalse(client.failed)

    def test_manual_exposure_requires_explicit_metadata(self):
        conn = Connection(message(wire.ACK, 1, 1))
        client = wire.OwnedSRClient(conn, TOKEN)
        client.create(replace(settings(), settings=SRSettings(mode='dlaa', auto_exposure=False)), session_id=1)
        count = len(conn.sent)
        with self.assertRaisesRegex(ValueError, 'explicit'):
            client.process(COLOR, MOTION, DEPTH, 0)
        self.assertEqual(len(conn.sent), count)

    def test_pts_monotonic_and_evaluation_limit_checked_before_wire(self):
        conn = Connection(message(wire.ACK, 1, 1) + message(wire.RESULT, 2, 1, struct.pack('<Q', 1) + COLOR))
        client = wire.OwnedSRClient(conn, TOKEN)
        client.create(settings(), session_id=1)
        client.process(COLOR, MOTION, DEPTH, 1)
        count = len(conn.sent)
        for pts in (0, 1):
            with self.assertRaises(ValueError):
                client.process(COLOR, MOTION, DEPTH, pts)
        client.session_frames = wire.MAX_EVALUATIONS
        with self.assertRaisesRegex(RuntimeError, 'evaluation limit'):
            client.process(COLOR, MOTION, DEPTH, 2)
        self.assertEqual(len(conn.sent), count)

    def test_request_counter_is_bounded_without_wraparound(self):
        conn = Connection()
        client = wire.OwnedSRClient(conn, TOKEN)
        client.request_id = (1 << 64) - 2
        count = len(conn.sent)
        with self.assertRaisesRegex(RuntimeError, 'counter exhausted'):
            client.ping()
        self.assertEqual(len(conn.sent), count)

    def test_authentication_old_nr_binary_and_uncompiled_caps_fail_closed(self):
        cases = [(Connection(token=b'x' * 32), 'authentication'),
                 (Connection(protocol=nr_wire.MAGIC), 'CSR1'),
                 (Connection(caps=capabilities(compiled=0)), 'no linked SR SDK'),
                 (Connection(caps=capabilities(compiled=2)), 'capability contract')]
        for conn, text in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                wire.OwnedSRClient(conn, TOKEN)
            self.assertTrue(conn.closed)

    def test_capability_dimensions_and_flags_are_exact(self):
        for index, value in ((0, 1), (4, 7680), (6, 2), (10, wire.MAX_PAYLOAD + 1),
                             (11, 2), (13, 1), (15, wire.MAX_EVALUATIONS + 1)):
            caps = list(wire.CAPABILITIES.unpack(capabilities()))
            caps[index] = value
            conn = Connection(caps=wire.CAPABILITIES.pack(*caps))
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'capability contract'):
                wire.OwnedSRClient(conn, TOKEN)
            self.assertTrue(conn.closed)

    def test_response_size_identity_and_wrong_pts_fail_closed(self):
        for response in (message(wire.ACK, 2, 1), message(wire.ACK, 1, 2),
                         message(wire.RESULT, 1, 1), message(wire.ACK, 1, 1, b'x'), b''):
            conn = Connection(response)
            client = wire.OwnedSRClient(conn, TOKEN)
            with self.assertRaises(RuntimeError):
                client.create(settings(), session_id=1)
            self.assertTrue(client.failed)
            self.assertTrue(conn.closed)
        conn = Connection(message(wire.ACK, 1, 1) + message(wire.RESULT, 2, 1, struct.pack('<Q', 99) + COLOR))
        client = wire.OwnedSRClient(conn, TOKEN)
        client.create(settings(), session_id=1)
        with self.assertRaisesRegex(RuntimeError, 'PTS'):
            client.process(COLOR, MOTION, DEPTH, 0)
        self.assertTrue(client.failed)

    def test_error_is_bounded_and_preserves_native_diagnostics(self):
        detail = b'SR create failed'
        payload = nr_wire.ERROR_HEADER.pack(nr_wire.ERROR_MAGIC, 1, 6, 1, 0xBAD00002, len(detail), 0, 0) + detail
        conn = Connection(message(wire.ERROR, 1, 1, payload))
        client = wire.OwnedSRClient(conn, TOKEN)
        with self.assertRaises(nr_wire.OwnedWorkerError) as caught:
            client.create(settings(), session_id=1)
        self.assertEqual(caught.exception.stage, 'create')
        self.assertEqual(caught.exception.code, 0xBAD00002)
        header = wire.HEADER.pack(wire.MAGIC, 1, wire.ERROR, wire.MAX_ERROR_PAYLOAD + 1, 1, 1)
        conn = Connection(header + b'not-read')
        client = wire.OwnedSRClient(conn, TOKEN)
        with self.assertRaisesRegex(RuntimeError, 'diagnostic size'):
            client.create(settings(), session_id=1)
        self.assertEqual(conn.data, b'not-read')

    def test_real_socketpair_auth_frame_exchange_and_shutdown(self):
        host, worker = socket.socketpair()
        failures = []

        def exact(size):
            chunks = bytearray()
            while len(chunks) < size:
                data = worker.recv(size - len(chunks))
                if not data:
                    raise RuntimeError('test peer disconnected')
                chunks.extend(data)
            return bytes(chunks)

        def serve():
            try:
                worker.settimeout(3)
                worker.sendall(TOKEN)
                self.assertEqual(exact(1), b'\x01')
                worker.sendall(message(wire.CAPS, payload=capabilities()))
                for expected in (wire.CREATE, wire.FRAME_MESSAGE, wire.END, wire.SHUTDOWN):
                    magic, version, kind, size, request, session = wire.HEADER.unpack(exact(wire.HEADER.size))
                    self.assertEqual((magic, version, kind), (wire.MAGIC, 1, expected))
                    payload = exact(size)
                    if kind == wire.FRAME_MESSAGE:
                        self.assertEqual(payload[wire.FRAME.size:], COLOR + MOTION + DEPTH)
                        worker.sendall(message(wire.RESULT, request, session, payload[:8] + COLOR))
                    else:
                        worker.sendall(message(wire.ACK, request, session))
            except BaseException as exc:
                failures.append(exc)
            finally:
                worker.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            client = wire.OwnedSRClient(host, TOKEN, timeout=3)
            client.create(settings(), session_id=1)
            self.assertEqual(client.process(COLOR, MOTION, DEPTH, 123_456_789), COLOR)
            client.end()
            client.shutdown()
        finally:
            host.close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
