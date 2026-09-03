from __future__ import annotations

import unittest
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from comfy_dlss_experimental.preview import PreviewSessionRegistry, _source_video_view


class _FakeVideo:
    def __init__(self, source: Path) -> None:
        self.source = source

    def get_stream_source(self) -> str:
        return str(self.source)


class PreviewSessionRegistryTests(unittest.TestCase):
    def test_new_session_cancels_only_same_node_owner(self):
        registry = PreviewSessionRegistry()
        kwargs = dict(sequence={"public": {}}, runtime={"ready": True}, contract={}, profile_a=None,
                      profile_b={}, start_time=1, duration=2, preview_scale=100)
        first = registry.create(**kwargs, node_id="5", client_id="a", workflow_id="one")
        other = registry.create(**kwargs, node_id="6", client_id="a", workflow_id="one")
        current = registry.create(**kwargs, node_id="5", client_id="a", workflow_id="one")
        self.assertTrue(first.cancelled.is_set())
        self.assertFalse(other.cancelled.is_set())
        payload = registry.update(current, state="ready")
        self.assertEqual(payload["revision"], 1)
        payload["transport"]["original_url"] = "changed"
        self.assertIsNone(current.public["transport"]["original_url"])
        self.assertTrue(registry.cancel(current.session_id))
        self.assertTrue(current.cancelled.is_set())

    def test_source_video_view_only_exposes_comfy_media_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            temp_root = root / "temp"
            output_root = root / "output"
            input_root.mkdir()
            temp_root.mkdir()
            output_root.mkdir()
            fake_folder_paths = types.SimpleNamespace(
                get_input_directory=lambda: str(input_root),
                get_temp_directory=lambda: str(temp_root),
                get_output_directory=lambda: str(output_root),
            )
            with patch.dict(sys.modules, {"folder_paths": fake_folder_paths}):
                self.assertEqual(
                    _source_video_view(_FakeVideo(input_root / "clips" / "source.mp4")),
                    {"filename": "source.mp4", "subfolder": "clips", "type": "input"},
                )
                self.assertIsNone(_source_video_view(_FakeVideo(root / "private.mp4")))

    def test_registry_evicts_oldest_session(self) -> None:
        registry = PreviewSessionRegistry(max_sessions=2)
        ids = []
        for index in range(3):
            record = registry.create(
                sequence={"public": {"sequence_id": str(index)}},
                runtime={"ready": True, "backend": "test"},
                contract={"mode": "dlaa"},
                profile_a=None,
                profile_b={"intensity": 1.0},
                start_time=0.0,
                duration=3.0,
                preview_scale=50,
            )
            ids.append(record.session_id)
        self.assertIsNone(registry.get(ids[0]))
        self.assertEqual(len(registry.list_public()), 2)

    def test_unready_runtime_marks_session_blocked(self) -> None:
        registry = PreviewSessionRegistry()
        record = registry.create(
            sequence={"public": {}},
            runtime={"ready": False, "errors": ["missing"]},
            contract={},
            profile_a=None,
            profile_b={},
            start_time=1.0,
            duration=2.0,
            preview_scale=75,
        )
        self.assertEqual(record.public["state"], "blocked")
