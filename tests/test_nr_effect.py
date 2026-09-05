import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.nr_effect import build_pass_stack, expand_effect
from comfy_dlss_experimental.video_pipeline import _render_variant_frames, render_effect


def look(intensity=0.25):
    return {"schema_version": 2, "nr_enabled": True, "mix": 1.0, "intensity": intensity}


class NREffectTests(unittest.TestCase):
    def test_missing_later_looks_inherit_previous_without_aliasing(self):
        first, third = look(0.2), look(0.8)
        stack = build_pass_stack(3, first, None, third)
        profiles, plan = expand_effect(stack)
        self.assertEqual([p["intensity"] for p in profiles], [0.2, 0.2, 0.8])
        self.assertEqual(plan["inherited"], [False, True, False])
        first["intensity"] = 3
        profiles[0]["intensity"] = 2
        self.assertEqual(stack["passes"][0]["intensity"], 0.2)
        self.assertEqual(stack["passes"][1]["intensity"], 0.2)

    def test_stack_is_bounded_and_nested_stacks_are_rejected(self):
        for count in (True, 0, 4, 2.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                build_pass_stack(count, look())
        with self.assertRaisesRegex(ValueError, "Nested"):
            build_pass_stack(2, build_pass_stack(1, look()))

    def test_tampered_stack_contract_is_rejected(self):
        stack = build_pass_stack(2, look())
        for changed in (
            stack | {"guide_policy": "recompute"},
            stack | {"intermediate_format": "h264"},
            stack | {"passes": stack["passes"][:1]},
            stack | {"inherited": [False, 1]},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                expand_effect(changed)

    def test_render_effect_chains_raw_history_and_encodes_only_final(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            prepared.mkdir()
            plane, frame_count, visible_count = 16, 3, 2
            (prepared / "color.rgba").write_bytes(b"a" * plane * frame_count)
            calls = []

            def render_frames(_prepared, manifest, _runtime, profile, _root, job, _warmup,
                              _cancelled, _progress, *, color_source, include_history, verify_original):
                job.mkdir(parents=True)
                calls.append((profile["intensity"], color_source, include_history, verify_original))
                count = len(manifest["frames"]) if include_history else manifest["visible_count"]
                raw = job / "output.rgba"
                raw.write_bytes(bytes([len(calls)]) * plane * count)
                digest = hashlib.sha256(raw.read_bytes()).hexdigest()
                return raw, {"raw_output_sha256": digest, "resident": None}

            def encode(raw, destination, manifest, **_kwargs):
                self.assertEqual(raw.stat().st_size, plane * manifest["visible_count"])
                destination.write_bytes(b"video")

            manifest = {"width": 2, "height": 2, "frames": [{}, {}, {}],
                        "visible_count": visible_count, "color_pipeline": {}}
            stack = build_pass_stack(3, look(0.1), look(0.2), look(0.3))
            with patch("comfy_dlss_experimental.video_pipeline._render_variant_frames", side_effect=render_frames), \
                    patch("comfy_dlss_experimental.video_pipeline.export_video", side_effect=encode), \
                    patch("comfy_dlss_experimental.storage_budget.require_disk"):
                output, report = render_effect(prepared, manifest, {}, stack, root, root / "job", 0,
                                               lambda: False, lambda *_: None)
            self.assertEqual(output.read_bytes(), b"video")
            self.assertEqual([call[0] for call in calls], [0.1, 0.2, 0.3])
            self.assertEqual([call[2] for call in calls], [True, True, False])
            self.assertEqual([call[3] for call in calls], [True, False, False])
            self.assertEqual(calls[1][1], root / "job/pass-1/output.rgba")
            self.assertEqual(calls[2][1], root / "job/pass-2/output.rgba")
            self.assertEqual(report["pass_count"], 3)
            self.assertFalse(report["intermediate_video_encoding"])
            self.assertTrue(report["passed"])
            self.assertFalse((root / "job/pass-1/output.rgba").exists())
            self.assertFalse((root / "job/pass-2/output.rgba").exists())
            self.assertFalse((root / "job/pass-3/output.rgba").exists())

    def test_intermediate_spool_keeps_history_but_final_spool_drops_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            prepared.mkdir()
            plane = 64 * 64 * 4
            colors = [bytes([value]) * plane for value in (10, 20, 30)]
            motions = [bytes([value]) * plane for value in (1, 2, 3)]
            (prepared / "color.rgba").write_bytes(b"".join(colors))
            (prepared / "motion.rg16f").write_bytes(b"".join(motions))
            frames = [{"pts_ns": index, "reset": index == 0, "visible": index > 0,
                       "color_sha256": hashlib.sha256(colors[index]).hexdigest(),
                       "motion_sha256": hashlib.sha256(motions[index]).hexdigest()}
                      for index in range(3)]
            manifest = {"width": 64, "height": 64, "frames": frames,
                        "visible_count": 2, "color_pipeline": {}}
            bypass = look() | {"nr_enabled": False}
            with patch("comfy_dlss_experimental.storage_budget.require_disk"):
                history, history_report = _render_variant_frames(
                    prepared, manifest, {"worker_policy": "isolated"}, bypass, root,
                    root / "history", 1, lambda: False, lambda *_: None,
                    color_source=prepared / "color.rgba", include_history=True,
                    verify_original=True,
                )
                final, final_report = _render_variant_frames(
                    prepared, manifest, {"worker_policy": "isolated"}, bypass, root,
                    root / "final", 1, lambda: False, lambda *_: None,
                    color_source=prepared / "color.rgba", include_history=False,
                    verify_original=True,
                )
            self.assertEqual(history.read_bytes(), b"".join(colors))
            self.assertEqual(final.read_bytes(), b"".join(colors[1:]))
            self.assertTrue(history_report["output_includes_history"])
            self.assertFalse(final_report["output_includes_history"])
            self.assertTrue(history_report["passed"])
            self.assertTrue(final_report["passed"])


if __name__ == "__main__":
    unittest.main()
