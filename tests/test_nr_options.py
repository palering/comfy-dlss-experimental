import unittest

from comfy_dlss_experimental.nr_options import LOOK_CHOICES, choice_id, legacy_labels
from comfy_dlss_experimental.video_pipeline import profile_settings


class NRChoiceTests(unittest.TestCase):
    def test_labels_and_legacy_ids_have_identical_transport(self):
        for name, choices in LOOK_CHOICES.items():
            for index, label in enumerate(choices):
                with self.subTest(name=name, label=label):
                    self.assertEqual(choice_id(name, label), index)
                    self.assertEqual(choice_id(name, index), index)
                    a = profile_settings({"schema_version": 2, name: index}, 64, 96, 12, 120)
                    b = profile_settings({"schema_version": 2, name: choice_id(name, label)}, 64, 96, 12, 120)
                    self.assertEqual(a.encode(), b.encode())

    def test_unknown_values_are_not_coerced(self):
        for name, choices in LOOK_CHOICES.items():
            for value in (None, True, False, -1, len(choices), 1.0, "1", "unknown", [], {}):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    choice_id(name, value)

    def test_frontend_metadata_is_derived_from_choices(self):
        for name, choices in LOOK_CHOICES.items():
            self.assertEqual(legacy_labels(name), {str(i): value for i, value in enumerate(choices)})
            self.assertEqual(len(choices), len(set(choices)))
