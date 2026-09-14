import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("owned_worker_build", ROOT / "sidecar/build_owned_worker.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class OwnedWorkerBuildTests(unittest.TestCase):
    def test_diagnostics_preserve_fence_policy_and_keep_writes_bounded(self):
        probe=(ROOT/'sidecar/src/nr_probe.cpp').read_text()
        observer=(ROOT/'sidecar/src/probe_report.hpp').read_text()
        self.assertIn('report_.observe(&observed,this)',probe)
        self.assertIn('report_.observe(nullptr,nullptr)',probe)
        self.assertIn('request_.request,request_.session}),250)',probe)
        self.assertIn('channel_.write(std::span(payload).first(size),250)',probe)
        self.assertIn('watchdog.refresh(990000)',probe)
        self.assertIn('channel.read(header,960000)',probe)
        self.assertIn('if (observer_) observer_(observer_context_, stage, state, code)',observer)
        engine=(ROOT/'sidecar/src/nr_engine.cpp').read_text()
        self.assertIn('WaitForSingleObject(event_.get(), 30000)',engine)
        self.assertIn('std::_Exit(70)',engine)

    def test_zig_json_and_zon_are_parsed_without_evaluation(self):
        for value in ('{\n "lib_dir": "/opt/zig/lib"\n}', '.{\n .lib_dir = "/opt/zig/lib",\n}'):
            self.assertEqual(builder.zig_lib_from_env(value), Path("/opt/zig/lib"))
        self.assertEqual(str(builder.zig_lib_from_env('{\n"lib_dir": "C:\\\\Zig\\\\lib"\n}')), r"C:\Zig\lib")
        with self.assertRaises(ValueError): builder.zig_lib_from_env("no library path")

    def test_microsoft_objects_and_legacy_routes_remain_separate(self):
        source = (ROOT / "sidecar/build_owned_worker.py").read_text()
        self.assertIn('"x86_64-windows-msvc"', source)
        self.assertIn('"x86_64-windows-gnu"', source)
        self.assertIn('"-lntdllcrt"', source)
        self.assertNotIn("nvsdk_ngx_d.lib", source)
        parameters = (ROOT / "sidecar/src/nr_parameters.cpp").read_text()
        self.assertIn("!defined(_MSC_VER)", parameters)
        self.assertNotIn("virtual ~", parameters)
        self.assertNotIn("_fltused", parameters)
        probe = (ROOT / "sidecar/src/nr_probe.cpp").read_text()
        self.assertIn("--self-test-parameter-abi", probe)
        self.assertIn("visual_acceptance", probe)
        engine = (ROOT / "sidecar/src/nr_engine.cpp").read_text()
        self.assertIn("LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR", engine)
        self.assertIn("std::_Exit(70)", engine)
        self.assertNotIn("D5V2", probe)

    def test_sequence_is_bounded_and_keeps_delivered_reset(self):
        probe = (ROOT / 'sidecar/src/nr_probe.cpp').read_text()
        self.assertIn('evaluation++, frame.reset, result', probe)
        self.assertNotIn('frame.reset && index', probe)
        io = (ROOT / 'sidecar/src/nr_sequence_io.hpp').read_text()
        self.assertIn('CREATE_NEW', io)
        self.assertIn('Preflight all records', io)
        self.assertIn('finite_half_plane', io)
        contract = (ROOT / 'sidecar/src/nr_sequence_contract.hpp').read_text()
        self.assertIn('64ULL * 1024 * 1024', contract)
        self.assertIn('count > 16', contract)
