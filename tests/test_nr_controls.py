"""No Comfy/GPU dependency: validate Look -> exact D5V2 wire fields."""
from dataclasses import asdict
import math
import unittest

from comfy_dlss_experimental.direct_nr import DirectNRSettings, VIDEO_HEADER
from comfy_dlss_experimental.video_pipeline import profile_settings


class NRControlsTests(unittest.TestCase):
    def settings(self, **overrides):
        return profile_settings({"schema_version": 2, **overrides}, 64, 96, 12, 120)

    def test_legacy_look_is_byte_identical(self):
        self.assertEqual(self.settings().encode(), DirectNRSettings(64, 96, 12, intensity=.25).encode())

    def test_all_controls_reach_exact_wire_slots(self):
        settings = self.settings(worker_profile=2, nr_preset=3, nr_style=0,
                                 automatic_mask=True, ui_correction=True,
                                 intensity=.5, local_tone_strength=.75,
                                 local_structure_strength=1.25, skin_structure_strength=2)
        packet = VIDEO_HEADER.unpack(settings.encode())
        self.assertEqual(packet[5:10], (2, 3, 0, 1, 1))
        self.assertEqual(packet[10:14], (.5, .75, 1.25, 2))

    def test_every_control_changes_only_its_field(self):
        base = asdict(self.settings())
        fields = {"worker_profile": ("profile", 0), "nr_preset": ("preset", 2),
                  "nr_style": ("style", 2), "automatic_mask": ("auto_mask", True),
                  "ui_correction": ("ui_correction", True), "intensity": ("intensity", 2),
                  "local_tone_strength": ("local_tone", 0),
                  "local_structure_strength": ("local_structure", 0),
                  "skin_structure_strength": ("skin_structure", 2)}
        for public, (native, value) in fields.items():
            with self.subTest(public=public):
                changed = asdict(self.settings(**{public: value}))
                self.assertEqual([k for k in base if base[k] != changed[k]], [native])
                self.assertEqual(changed[native], value)

    def test_flags_must_be_booleans(self):
        for field in ("nr_enabled", "automatic_mask", "ui_correction"):
            for value in (0, 1, "false", None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.settings(**{field: value})

    def test_native_ids_are_not_coerced(self):
        for field, maximum in (("worker_profile", 3), ("nr_preset", 3), ("nr_style", 2)):
            for value in (-1, maximum + 1, True, "1", 1.0, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.settings(**{field: value})

    def test_strengths_are_finite_and_bounded(self):
        for field in ("intensity", "local_tone_strength", "local_structure_strength", "skin_structure_strength"):
            for value in (math.nan, math.inf, -math.inf, -2, 3.01, True, "1", None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.settings(**{field: value})
        self.assertEqual(self.settings(skin_structure_strength=-1).skin_structure, -1)
        for field in ("intensity", "local_tone_strength", "local_structure_strength"):
            with self.assertRaises(ValueError):
                self.settings(**{field: -.01})

    def test_mix_and_enable_do_not_alter_native_controls(self):
        self.assertEqual(self.settings(mix=0, nr_enabled=False).encode(), self.settings().encode())


if __name__ == "__main__":
    unittest.main()
