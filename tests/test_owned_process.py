from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock, MagicMock
from comfy_dlss_experimental.owned_process import launch_platform, OwnedWorkerProcess


class OwnedProcessTests(unittest.TestCase):
    def _files(self, root):
        worker, caller = root / 'worker.exe', root / 'nvngx.dll'
        nr, sr = root / 'nvngx_dlssnr.dll', root / 'nvngx_dlss.dll'
        for path in (worker, caller, nr, sr):
            path.write_bytes(b'test fixture only')
        return worker, caller, nr, sr

    def test_sr_nr_and_sl_launch_use_separate_native_commands_and_clients(self):
        fixture_root = Path(__file__).resolve().parents[1] / 'tmp'
        fixture_root.mkdir(exist_ok=True)
        project = '7d5f45c3-147b-4d18-a8e8-ea2f51dc8ddd'
        with tempfile.TemporaryDirectory(dir=fixture_root) as temporary:
            root = Path(temporary)
            worker, caller, nr, _ = self._files(root)
            for name in ('sl.interposer.dll', 'sl.common.dll', 'NvLowLatencyVk.dll',
                         'sl.dlss.dll', 'sl.dlss_d.dll', 'nvngx_dlssd.dll'):
                (root / name).write_bytes(b'test fixture only')
            for feature in ('nr', 'sr', 'sl'):
                listener = MagicMock()
                listener.__enter__.return_value = listener
                listener.getsockname.return_value = ('127.0.0.1', 12345)
                listener.accept.return_value = (Mock(), ('127.0.0.1', 34567))
                thread = Mock()
                thread.is_alive.return_value = False
                with patch('comfy_dlss_experimental.owned_process.sys.platform', 'win32'), \
                     patch('comfy_dlss_experimental.owned_process.socket.socket', return_value=listener), \
                     patch('comfy_dlss_experimental.owned_process.subprocess.Popen') as popen, \
                     patch('comfy_dlss_experimental.owned_process.threading.Thread', return_value=thread), \
                     patch('comfy_dlss_experimental.owned_process.OwnedNRClient') as nr_client, \
                     patch('comfy_dlss_experimental.sr_worker.OwnedSRClient') as sr_client, \
                     patch('comfy_dlss_experimental.sl_worker.ReconstructionClient') as sl_client:
                    owner = OwnedWorkerProcess(worker, nr if feature == 'nr' else root,
                        caller if feature == 'nr' else None, root / f'{feature}-job',
                        feature=feature, project_id=project if feature in ('sr', 'sl') else None)
                    command = popen.call_args.args[0]
                    self.assertEqual(command[0], str(worker.resolve()))
                    self.assertEqual(command[1], f'--serve-{feature}')
                    self.assertEqual(len(command), 7)
                    self.assertEqual(command[-2], '12345')
                    self.assertEqual(len(command[-1]), 64)
                    if feature in ('sr', 'sl'):
                        self.assertEqual(command[2], str(root.resolve()))
                        self.assertEqual(command[4], project)
                        self.assertNotIn(str(caller.resolve()), command)
                        (sr_client if feature == 'sr' else sl_client).assert_called_once()
                        (sl_client if feature == 'sr' else sr_client).assert_not_called()
                        nr_client.assert_not_called()
                    else:
                        self.assertEqual(command[2:4], [str(nr.resolve()), str(caller.resolve())])
                        nr_client.assert_called_once()
                        sr_client.assert_not_called()
                        sl_client.assert_not_called()
                    self.assertFalse(popen.call_args.kwargs['start_new_session'])
                    owner.close()

    def test_invalid_sr_configuration_is_rejected_before_process_start(self):
        with patch('comfy_dlss_experimental.owned_process.subprocess.Popen') as popen:
            for options in ({'feature': 'fg'}, {'feature': 'sr'},
                            {'feature': 'sr', 'project_id': 'not-a-project-id'},
                            {'feature': 'sr', 'project_id': '00000000-0000-0000-0000-000000000000'},
                            {'feature': 'sr', 'project_id': '7D5F45C3-147B-4D18-A8E8-EA2F51DC8DDD'},
                            {'feature': 'sr', 'project_id': '7d5f45c3147b4d18a8e8ea2f51dc8ddd'},
                            {'feature': 'nr', 'project_id': '7d5f45c3-147b-4d18-a8e8-ea2f51dc8ddd'}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    OwnedWorkerProcess(Path('worker'), Path('model'), None, Path('unused'), **options)
            with self.assertRaisesRegex(ValueError, 'not the NR caller shim'):
                OwnedWorkerProcess(Path('worker'), Path('model'), Path('caller'), Path('unused'),
                    feature='sr', project_id='7d5f45c3-147b-4d18-a8e8-ea2f51dc8ddd')
            popen.assert_not_called()

    def test_sr_model_directory_requires_the_actual_dlss_runtime_file(self):
        fixture_root = Path(__file__).resolve().parents[1] / 'tmp'
        fixture_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixture_root) as temporary:
            root = Path(temporary)
            worker = root / 'worker.exe'
            worker.write_bytes(b'test fixture only')
            with patch('comfy_dlss_experimental.owned_process.sys.platform', 'win32'), \
                 patch('comfy_dlss_experimental.owned_process.subprocess.Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'nvngx_dlss.dll'):
                    OwnedWorkerProcess(worker, root, None, root / 'job', feature='sr',
                        project_id='7d5f45c3-147b-4d18-a8e8-ea2f51dc8ddd')
                popen.assert_not_called()
                self.assertFalse((root / 'job').exists())

    def test_windows_never_accepts_linux_settings(self):
        with patch('comfy_dlss_experimental.owned_process.sys.platform','win32'):
            self.assertEqual(launch_platform(None,None),'windows')
            for proton,prefix in [(Path('proton'),None),(None,Path('prefix'))]:
                with self.assertRaises(ValueError):launch_platform(proton,prefix)

    def test_mac_is_edit_only_and_linux_requires_proton(self):
        with patch('comfy_dlss_experimental.owned_process.sys.platform','darwin'):
            with self.assertRaises(ValueError):launch_platform(None,None)
        with patch('comfy_dlss_experimental.owned_process.sys.platform','linux'):
            with self.assertRaises(ValueError):launch_platform(None,None)

    def test_import_has_no_linux_or_model_side_effects(self):
        script="""
import sys, subprocess
before = set(sys.modules)  # The platform's stdlib subprocess may itself load fcntl.
import comfy_dlss_experimental.owned_process
for name in ('fcntl', 'comfy_dlss_experimental.native_relay', 'cv2', 'numpy', 'torch',
             'comfy_dlss_experimental.sr_worker'):
    assert name not in set(sys.modules) - before, name
"""
        result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_failed_cleanup_keeps_prefix_lock_and_can_retry(self):
        owner=OwnedWorkerProcess.__new__(OwnedWorkerProcess)
        owner.platform='linux';owner.run_marker='test'
        owner._closed=False;owner._cleanup_complete=False;owner.client=None;owner._socket=None
        owner._process=Mock();owner._drain_thread=None;owner._log=Mock();owner._lock_file=Mock()
        owner.cleanup={}
        with patch('comfy_dlss_experimental.native_relay._reap_marked_processes',side_effect=[RuntimeError('still alive'),{'remaining_owned_processes':0}]):
            with self.assertRaisesRegex(RuntimeError,'still alive'):owner.close()
            owner._lock_file.close.assert_not_called()
            self.assertFalse(owner._cleanup_complete)
            owner.close()
            owner._lock_file.close.assert_called_once()
            self.assertTrue(owner._cleanup_complete)
