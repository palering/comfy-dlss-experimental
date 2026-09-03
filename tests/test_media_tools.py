from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.media_tools import (
    _version, media_executable, media_tool_identity, resolve_media_tools, with_media_tools, media_tools_scope,
)
from comfy_dlss_experimental.media_clip import inspect_media


class MediaToolTests(unittest.TestCase):
    def setUp(self):
        _version.cache_clear()

    def tools(self, root):
        suffix = ".exe" if os.name == "nt" else ""
        paths = {}
        for name in ("ffmpeg", "ffprobe"):
            path = root / (name + suffix)
            path.write_text("fixture", encoding="utf-8")
            path.chmod(0o755)
            paths[name] = path
        return paths

    def version_result(self, args, **kwargs):
        name = Path(args[0]).stem
        self.assertEqual(args[1:], ["-version"])
        self.assertNotIn("shell", kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=name + " version test\n")

    def test_explicit_directory_and_companion_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="media tools ") as temp:
            paths = self.tools(Path(temp))
            with patch("shutil.which", side_effect=AssertionError("Explicit path must not use PATH")), \
                    patch("subprocess.run", side_effect=self.version_result) as run:
                config = {"ffmpeg_path": temp}
                report = resolve_media_tools(config)
                self.assertTrue(report["ready"])
                self.assertEqual(report["ffmpeg"]["source"], "explicit")
                self.assertEqual(report["ffprobe"]["source"], "ffmpeg_directory")
                self.assertEqual(report["ffprobe"]["path"], str(paths["ffprobe"].resolve()))
                resolve_media_tools(config)
                self.assertEqual(run.call_count, 2, "version checks cached by file identity")
                paths["ffmpeg"].write_text("updated fixture", encoding="utf-8")
                changed = resolve_media_tools(config)
                self.assertEqual(run.call_count, 3)
                self.assertNotEqual(report["ffmpeg"]["size"], changed["ffmpeg"]["size"])

    def test_path_default_and_separate_ffprobe_override(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.tools(Path(temp))
            with patch("shutil.which", side_effect=lambda name: str(paths[name])), patch("subprocess.run", side_effect=self.version_result):
                report = resolve_media_tools({"ffprobe_path": str(paths["ffprobe"])})
                self.assertTrue(report["ready"])
                self.assertEqual(report["ffmpeg"]["source"], "PATH")
                self.assertEqual(report["ffprobe"]["source"], "explicit")

    def test_missing_explicit_companion_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.tools(Path(temp))
            paths["ffprobe"].unlink()
            with patch("shutil.which", side_effect=AssertionError("No fallback")), patch("subprocess.run", side_effect=self.version_result):
                report = resolve_media_tools({"ffmpeg_path": str(paths["ffmpeg"])})
                self.assertFalse(report["ready"])
                self.assertTrue(report["ffmpeg"]["available"])
                self.assertFalse(report["ffprobe"]["available"])

    def test_whitespace_paths_have_default_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = self.tools(Path(temp))
            with patch("shutil.which", side_effect=lambda name: str(paths[name])), patch("subprocess.run", side_effect=self.version_result):
                report = resolve_media_tools({"ffmpeg_path": "  ", "ffprobe_path": "\t"})
            self.assertTrue(report["ready"])
            self.assertEqual(report["ffmpeg"]["source"], "PATH")
            self.assertEqual(report["ffprobe"]["source"], "PATH")

    def test_invalid_paths_and_wrong_program_fail_closed(self):
        with patch("shutil.which", side_effect=AssertionError("No fallback")):
            for value in ("relative/ffmpeg", "bad\x00path", 42):
                self.assertFalse(resolve_media_tools({"ffmpeg_path": value})["ready"])
        with tempfile.TemporaryDirectory() as temp:
            self.tools(Path(temp))
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="other tool\n")):
                self.assertFalse(resolve_media_tools({"ffmpeg_path": temp})["ready"])
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 5)):
                self.assertFalse(resolve_media_tools({"ffmpeg_path": temp})["ready"])

    def test_contexts_are_concurrent_safe_and_do_not_change_path(self):
        original_path = os.environ.get("PATH")
        barrier = threading.Barrier(2)
        @with_media_tools
        def job(*, sequence):
            barrier.wait(timeout=3)
            return media_executable("ffmpeg"), media_tool_identity()
        def resolved(config):
            return {"ready": True, "ffmpeg": {"path": config["ffmpeg_path"]}}
        with patch("comfy_dlss_experimental.media_tools.resolve_media_tools", side_effect=resolved), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(job, sequence={"media_tools_config": {"ffmpeg_path": value}}) for value in ("first", "second")]
            self.assertEqual([f.result()[0] for f in futures], ["first", "second"])
        self.assertEqual(media_executable("ffmpeg"), "ffmpeg")
        self.assertEqual(os.environ.get("PATH"), original_path)

    def test_selected_probe_is_used_and_scope_restored_after_error(self):
        @with_media_tools
        def job(*, sequence):
            with tempfile.NamedTemporaryFile() as source:
                with patch("comfy_dlss_experimental.media_clip.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="{}")) as run:
                    inspect_media(Path(source.name))
                    self.assertEqual(run.call_args.args[0][0], "selected-probe")
            raise RuntimeError("test failure")
        report = {"ready": True, "ffmpeg": {"path": "selected-encoder"}, "ffprobe": {"path": "selected-probe"}}
        with patch("comfy_dlss_experimental.media_tools.resolve_media_tools", return_value=report):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                job(sequence={})
        self.assertIsNone(media_tool_identity())

    def test_bad_tools_publish_a_new_failed_preview_session(self):
        from comfy_dlss_experimental.node_execution import run_preview
        from comfy_dlss_experimental.preview import PreviewSessionRegistry
        registry = PreviewSessionRegistry()
        trace = SimpleNamespace(preview=None, release_requested=threading.Event(), record={})
        failed = {"ready": False, "ffmpeg": {"available": False, "error": "missing test encoder"},
                  "ffprobe": {"available": True}}
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(sys.modules, {"comfy_api.latest": SimpleNamespace(InputImpl=SimpleNamespace()),
                                         "folder_paths": SimpleNamespace(get_temp_directory=lambda: temporary)}), \
                patch("comfy_dlss_experimental.node_execution.preview_sessions", registry), \
                patch("comfy_dlss_experimental.node_execution.current_trace", return_value=trace), \
                patch("comfy_dlss_experimental.node_execution.interrupted", return_value=False), \
                patch("comfy_dlss_experimental.node_execution.send_preview_event") as send, \
                patch("comfy_dlss_experimental.media_tools.resolve_media_tools", return_value=failed):
            with self.assertRaisesRegex(ValueError, "missing test encoder"):
                run_preview.__wrapped__(sequence={}, runtime={"ready": True}, contract={}, profile_a=None,
                                        profile_b={}, start_time=0, duration=1, preview_scale=100, node_id="test")
            self.assertEqual(trace.preview.public["state"], "failed")
            self.assertIsNone(trace.preview.public["transport"]["processed_view"])
            self.assertEqual(send.call_args.args[0]["state"], "failed")
            self.assertTrue(trace.report_file.is_file())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "host media executables required")
    def test_real_export_uses_explicit_host_tools(self):
        from comfy_dlss_experimental import media_clip
        config = {name + "_path": str(Path(shutil.which(name)).absolute()) for name in ("ffmpeg", "ffprobe")}
        manifest = {"width": 64, "height": 64, "visible_count": 1, "visible_start_index": 0,
                    "fps": "24", "frames": [{"pts_ns": 0}],
                    "metadata": {"has_audio": False, "video": {"color_transfer": "bt709"}}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "frame.rgba"
            raw.write_bytes(bytes([128, 64, 32, 255]) * (64 * 64))
            output = root / "output.mp4"
            with patch("shutil.which", side_effect=AssertionError("Explicit tools must not use PATH")), \
                    patch.object(media_clip, "run_cancellable", wraps=media_clip.run_cancellable) as calls, \
                    media_tools_scope(config):
                media_clip.export_video(raw, output, manifest, with_audio=False)
                self.assertEqual([Path(call.args[0][0]).resolve() for call in calls.call_args_list],
                                 [Path(config[n + "_path"]).resolve() for n in ("ffmpeg", "ffprobe", "ffprobe")])
            self.assertTrue(output.is_file())
            self.assertFalse((root / "output.partial.mp4").exists())
