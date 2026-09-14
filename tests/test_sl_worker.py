from dataclasses import replace
import struct
import unittest

from comfy_dlss_experimental import sl_worker as wire
from comfy_dlss_experimental.sl_contract import FRAME_WIRE, SLFrameMetadata
from test_sl_contract import camera, config, rr_guides

TOKEN = b's' * 32
COLOR = struct.pack('<4e', .5, .5, .5, 1) * 4096
MOTION = bytes(4096 * 4)
DEPTH = struct.pack('<f', .5) * 4096


def message(kind, request=0, session=0, payload=b'', magic=wire.MAGIC):
    return wire.HEADER.pack(magic, 1, kind, len(payload), request, session) + payload


class Connection:
    def __init__(self, replies=b'', magic=wire.MAGIC, token=TOKEN, bits=7, portrait=False, byte_budget=False):
        extent = (64,64,1920,1920,16384,16384) if byte_budget else (64,64,1920,1920 if portrait else 1080,3840,3840 if portrait else 2160)
        caps = wire.CAPABILITIES.pack(*extent, 1, 1, 1, 1, wire.MAX_PAYLOAD, 1, 1, bits, 1, 1_000_000)
        self.data = bytearray(token + message(wire.CAPS, payload=caps, magic=magic) + replies)
        self.sent = bytearray()
        self.closed = False

    def recv(self, n):
        data = bytes(self.data[:min(n, 271)])
        del self.data[:len(data)]
        return data

    def sendall(self, data): self.sent.extend(data)
    def settimeout(self, n): assert n > 0
    def shutdown(self, *_): pass
    def close(self): self.closed = True


def process(client, pts=0, **kwargs):
    return client.process(COLOR, MOTION, DEPTH, pts, camera=camera(), metadata=SLFrameMetadata(), **kwargs)


class ReconstructionWorkerTests(unittest.TestCase):
    def test_3x_requires_byte_budget_worker_not_old_portrait_binary(self):
        settings=replace(config(),input_width=736,input_height=1280,output_width=2208,output_height=3840,mode='ultra_performance')
        old=wire.ReconstructionClient(Connection(portrait=True),TOKEN)
        before=bytes(old.connection.sent)
        with self.assertRaisesRegex(ValueError,'size.*Worker'):old.create(settings,session_id=1)
        self.assertEqual(bytes(old.connection.sent),before)
        old.close()
        new=wire.ReconstructionClient(Connection(message(wire.ACK,1,1),byte_budget=True),TOKEN)
        new.create(settings,session_id=1)
        new.close()
    def test_new_portrait_capabilities_and_legacy_worker_guard(self):
        settings=replace(config(),input_width=736,input_height=1280,output_width=736,output_height=1280)
        old=wire.ReconstructionClient(Connection(),TOKEN)
        with self.assertRaisesRegex(ValueError,'portrait|extent|size|worker'):
            old.create(settings,session_id=1)
        old.close()
        new=wire.ReconstructionClient(Connection(message(wire.ACK,1,1),portrait=True),TOKEN)
        new.create(settings,session_id=1)
        new.close()
    def test_structured_worker_error_is_bounded_and_preserved(self):
        from comfy_dlss_experimental.owned_worker import ERROR_HEADER, ERROR_MAGIC, OwnedWorkerError
        text = b'Worker rejected reconstruction state or input contract'
        error = ERROR_HEADER.pack(ERROR_MAGIC, 1, 1, 0, 1, len(text), 0, 0) + text
        conn = Connection(message(wire.ACK, 1, 1) + message(wire.ERROR, 2, 1, error))
        client = wire.ReconstructionClient(conn, TOKEN)
        client.create(config(), session_id=1)
        with self.assertRaises(OwnedWorkerError) as raised:
            process(client)
        self.assertEqual(raised.exception.stage, 'protocol')
        self.assertEqual(raised.exception.frame_index, 0)
        self.assertEqual(raised.exception.code_domain, 'worker')
        self.assertTrue(client.failed and conn.closed)

    def test_sr_end_reuse_and_release_preserve_pts_and_reset(self):
        result = struct.pack('<Q', 0) + COLOR
        connection = Connection(message(wire.ACK, 1, 7) + message(wire.RESULT, 2, 7, result) +
            message(wire.ACK, 3, 7) + message(wire.RESULT, 4, 7, result) + message(wire.ACK, 5, 7) + message(wire.ACK, 6))
        client = wire.ReconstructionClient(connection, TOKEN)
        self.assertEqual(client.capabilities['device_support'], 'checked_on_create')
        client.create(config(), session_id=7)
        self.assertEqual(process(client), COLOR)
        client.end()
        self.assertEqual(process(client), COLOR)
        self.assertEqual(client.total_frames, 2)
        client.release()
        client.shutdown()
        self.assertTrue(connection.closed)
        data = bytes(connection.sent)[1:]
        frames = []
        while data:
            header = wire.HEADER.unpack_from(data)
            payload = data[wire.HEADER.size:wire.HEADER.size + header[3]]
            if header[2] == wire.FRAME:
                frames.append(payload)
            data = data[wire.HEADER.size + header[3]:]
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(FRAME_WIRE.unpack_from(p)[1] == 1 for p in frames))
        self.assertTrue(all(len(p) == config().frame_bytes for p in frames))

    def test_rr_guides_are_required_and_carried_without_silent_sr_fallback(self):
        conn = Connection(message(wire.ACK, 1, 1) + message(wire.RESULT, 2, 1, struct.pack('<Q', 5) + COLOR))
        client = wire.ReconstructionClient(conn, TOKEN)
        client.create(config(True), session_id=1)
        before = bytes(conn.sent)
        with self.assertRaises(ValueError): process(client, pts=5)
        self.assertEqual(bytes(conn.sent), before)
        self.assertEqual(process(client, pts=5, rr=rr_guides()), COLOR)
        payload = bytes(conn.sent)[len(before) + wire.HEADER.size:]
        self.assertEqual(len(payload), config(True).frame_bytes)
        self.assertTrue(payload.endswith(rr_guides().encode(config(True))))
        client.close()

    def test_reject_bad_camera_jitter_planes_or_rr_before_sending(self):
        conn = Connection(message(wire.ACK, 1, 1))
        client = wire.ReconstructionClient(conn, TOKEN)
        client.create(config(), session_id=1)
        before = bytes(conn.sent)
        cases = [dict(camera=None), dict(camera=camera(2)), dict(metadata=None),
                 dict(metadata=SLFrameMetadata(jitter_x=.25)), dict(rr=rr_guides()), dict(pts_ns=-1), dict(color=b'')]
        for changes in cases:
            kwargs = dict(color=COLOR, motion=MOTION, depth=DEPTH, pts_ns=0, camera=camera(), metadata=SLFrameMetadata())
            kwargs.update(changes)
            with self.assertRaises(ValueError): client.process(**kwargs)
        self.assertEqual(bytes(conn.sent), before)
        client.close()

    def test_cross_protocol_auth_and_capability_mismatch_close(self):
        for options in (dict(magic=0x31525343), dict(magic=0x31524e43), dict(token=b'x'*32), dict(bits=3)):
            conn = Connection(**options)
            with self.assertRaises(ValueError): wire.ReconstructionClient(conn, TOKEN)
            self.assertTrue(conn.closed)

    def test_wrong_result_pts_identity_or_size_close(self):
        replies = [message(wire.RESULT, 2, 1, struct.pack('<Q', 9) + COLOR),
                   message(wire.RESULT, 99, 1, struct.pack('<Q', 0) + COLOR),
                   message(wire.RESULT, 2, 1, b'x'), message(wire.ACK, 2, 1)]
        for reply in replies:
            conn = Connection(message(wire.ACK, 1, 1) + reply)
            client = wire.ReconstructionClient(conn, TOKEN)
            client.create(config(), session_id=1)
            with self.assertRaises(RuntimeError): process(client)
            self.assertTrue(client.failed and conn.closed)

    def test_increasing_pts_and_session_limit(self):
        conn = Connection(message(wire.ACK, 1, 1) + message(wire.RESULT, 2, 1, struct.pack('<Q', 4) + COLOR))
        client = wire.ReconstructionClient(conn, TOKEN)
        client.create(config(), session_id=1)
        process(client, 4)
        before = bytes(conn.sent)
        for pts in (0, 4, True, 2**63):
            with self.assertRaises(ValueError): process(client, pts)
        client.session_frames = wire.MAX_EVALUATIONS
        with self.assertRaises(RuntimeError): process(client, 5)
        self.assertEqual(bytes(conn.sent), before)
        client.close()
