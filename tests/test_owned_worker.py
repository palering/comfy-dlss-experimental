import struct
import unittest
from comfy_dlss_experimental import owned_worker as wire


def message(kind,request=0,session=0,payload=b''):
    return wire.HEADER.pack(wire.MAGIC,wire.VERSION,kind,len(payload),request,session)+payload


class Connection:
    def __init__(self, responses=b'', token=b't'*32):
        self.data=bytearray(token+message(wire.CAPS,payload=struct.pack('<8I',64,64,1920,1080,1,1,wire.MAX_PAYLOAD,1))+responses)
        self.sent=bytearray();self.closed=False
    def recv(self,size):
        block=bytes(self.data[:min(size,257)]);del self.data[:len(block)];return block
    def sendall(self,data): self.sent.extend(data)
    def settimeout(self,value): assert value>0
    def shutdown(self,*_): pass
    def close(self): self.closed=True


class OwnedWorkerTests(unittest.TestCase):
    def test_structured_native_failure_exposes_stage_code_and_progress(self):
        text=b'NGX operation failed'
        payload=wire.ERROR_HEADER.pack(wire.ERROR_MAGIC,1,8,1,0xBAD00002,len(text),3,5)+text
        connection=Connection(message(wire.ERROR,1,1,payload))
        client=wire.OwnedNRClient(connection,b't'*32)
        with self.assertRaises(wire.OwnedWorkerError) as caught:
            client.create(wire.OwnedNRSettings(64,64),session_id=1)
        error=caught.exception
        self.assertEqual(error.stage,'evaluate')
        self.assertEqual(error.code_domain,'ngx')
        self.assertEqual(error.code,0xBAD00002)
        self.assertEqual((error.frame_index,error.evaluation_index),(3,5))
        self.assertEqual(error.as_dict()['code_hex'],'0xBAD00002')
        self.assertIn('frame=3, evaluation=5',str(error))
        self.assertTrue(client.failed);self.assertTrue(connection.closed)

    def test_legacy_four_byte_errors_remain_readable_without_invented_progress(self):
        error=wire.decode_worker_error(struct.pack('<I',1))
        self.assertIsInstance(error,RuntimeError)
        self.assertEqual(error.code,1)
        self.assertEqual(error.stage,'unknown')
        self.assertIsNone(error.frame_index)
        self.assertIsNone(error.evaluation_index)

    def test_malformed_diagnostics_are_bounded_and_rejected(self):
        text=b'GPU completion unverified'
        fields=[wire.ERROR_MAGIC,1,9,5,258,len(text),4,7]
        payload=wire.ERROR_HEADER.pack(*fields)+text
        self.assertEqual(wire.decode_worker_error(payload).code_domain,'wait')
        cases=[b'',b'123',b'12345',payload[:-1],payload+b'x',b'x'*(wire.MAX_ERROR_PAYLOAD+1)]
        for index,value in [(0,0),(1,2),(2,len(wire.ERROR_STAGES)),(3,len(wire.ERROR_DOMAINS)),(5,0)]:
            invalid=fields.copy();invalid[index]=value
            cases.append(wire.ERROR_HEADER.pack(*invalid)+text)
        for text in (b'',b'escape\x1b[2J',b'new\nline',b'\xff',b'\x7f'):
            invalid=fields.copy();invalid[5]=len(text)
            cases.append(wire.ERROR_HEADER.pack(*invalid)+text)
        for value in cases:
            with self.subTest(value=value),self.assertRaises(RuntimeError):wire.decode_worker_error(value)

    def test_oversized_error_closes_without_reading_payload(self):
        header=wire.HEADER.pack(wire.MAGIC,wire.VERSION,wire.ERROR,wire.MAX_ERROR_PAYLOAD+1,1,1)
        connection=Connection(header+b'not-read')
        client=wire.OwnedNRClient(connection,b't'*32)
        with self.assertRaisesRegex(RuntimeError,'diagnostic size'):
            client.create(wire.OwnedNRSettings(64,64),session_id=1)
        self.assertEqual(connection.data,b'not-read')
        self.assertTrue(connection.closed)

    def test_settings_validate_before_encoding(self):
        config=wire.OwnedNRSettings(640,360)
        self.assertEqual(len(config.encode()),48)
        from dataclasses import replace
        for key,value in [('width',True),('height',0),('warmup',241),('preset',4),('style',-1),
            ('intensity',float('nan')),('skin_structure',float('inf')),('local_tone',-1),('ui_correction',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError): replace(config,**{key:value}).encode()

    def test_fragmented_session_task_end_reuse_release_shutdown(self):
        color=b'\x00\x38'*64*64*4;motion=b'\0'*64*64*4
        response=struct.pack('<Q',0)+color
        connection=Connection(message(wire.ACK,1,7)+message(wire.RESULT,2,7,response)+message(wire.ACK,3,7)+
            message(wire.RESULT,4,7,response)+message(wire.ACK,5,0)+message(wire.ACK,6,7)+message(wire.ACK,7,0))
        client=wire.OwnedNRClient(connection,b't'*32)
        client.create(wire.OwnedNRSettings(64,64),session_id=7)
        self.assertEqual(client.process(color,motion,0),color)
        client.end()
        self.assertEqual(client.process(color,motion,0),color)
        client.ping();client.release();client.shutdown()
        self.assertEqual(client.total_frames,2);self.assertTrue(connection.closed)
        sent=bytes(connection.sent[1:]); frames=[]
        while sent:
            _,_,kind,size,_,_=wire.HEADER.unpack_from(sent)
            if kind==wire.FRAME_MESSAGE: frames.append(wire.FRAME.unpack_from(sent,wire.HEADER.size))
            sent=sent[wire.HEADER.size+size:]
        self.assertEqual(frames,[(0,1,0),(0,1,0)])

    def test_invalid_input_does_not_send_or_poison_session(self):
        connection=Connection(message(wire.ACK,1,1))
        client=wire.OwnedNRClient(connection,b't'*32)
        client.create(wire.OwnedNRSettings(64,64),session_id=1)
        count=len(connection.sent)
        with self.assertRaises(ValueError): client.process(b'',b'',0)
        with self.assertRaises(RuntimeError): client.end()
        with self.assertRaises(RuntimeError): client.create(wire.OwnedNRSettings(64,64),session_id=2)
        self.assertEqual(len(connection.sent),count);self.assertFalse(client.failed)

    def test_bad_response_closes_connection(self):
        cases=[message(wire.ACK,2,1),message(wire.ACK,1,2),message(wire.RESULT,1,1),
            message(wire.ACK,1,1,b'bad'),message(wire.ERROR,1,1,struct.pack('<I',1)),b'']
        for response in cases:
            with self.subTest(response=response):
                connection=Connection(response);client=wire.OwnedNRClient(connection,b't'*32)
                with self.assertRaises(RuntimeError): client.create(wire.OwnedNRSettings(64,64),session_id=1)
                self.assertTrue(client.failed);self.assertTrue(connection.closed)

    def test_wrong_timestamp_is_not_accepted(self):
        color=b'\x00\x38'*64*64*4;motion=b'\0'*64*64*4
        connection=Connection(message(wire.ACK,1,1)+message(wire.RESULT,2,1,struct.pack('<Q',1)+color))
        client=wire.OwnedNRClient(connection,b't'*32);client.create(wire.OwnedNRSettings(64,64),session_id=1)
        with self.assertRaisesRegex(RuntimeError,'PTS'):client.process(color,motion,0)
        self.assertTrue(client.failed)

    def test_authentication_and_caps_fail_closed(self):
        connection=Connection(token=b'x'*32)
        with self.assertRaises(ValueError):wire.OwnedNRClient(connection,b't'*32)
        self.assertTrue(connection.closed)
        connection=Connection();connection.data[32+4]=2
        with self.assertRaises(ValueError):wire.OwnedNRClient(connection,b't'*32)
        self.assertTrue(connection.closed)
