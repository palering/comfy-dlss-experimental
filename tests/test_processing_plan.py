import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from comfy_dlss_experimental.backend_contracts import EXTERNAL_D5V2_NR, backend_contract
from comfy_dlss_experimental.nr_effect import build_pass_stack, expand_effect
from comfy_dlss_experimental.processing_plan import FeatureStage, compile_legacy_nr


def look(intensity=0.25):
    return {"schema_version": 2, "nr_enabled": True, "mix": 1.0, "intensity": intensity}


class ProcessingPlanTests(unittest.TestCase):
    def test_legacy_single_stack_and_inheritance_are_exactly_preserved(self):
        effects = [look(), look() | {"nr_enabled": False}, look() | {"mix": 0.0}]
        for count in (1, 2, 3):
            effects.append(build_pass_stack(count, look()))
            effects.append(build_pass_stack(count, look(0.1), look(0.2), look(0.3)))
        for effect in effects:
            with self.subTest(effect=effect):
                plan = compile_legacy_nr(effect)
                self.assertEqual(plan.legacy_parts(), expand_effect(effect))
                self.assertEqual(len(plan.stages), expand_effect(effect)[1]["pass_count"])
                for stage in plan.stages:
                    self.assertEqual(stage.backend_id, EXTERNAL_D5V2_NR.backend_id)
                    self.assertEqual(stage.feature, "nr")

    def test_plan_snapshots_do_not_alias_inputs_exports_or_sibling_stages(self):
        source = look() | {"extra": {"values": [1, 2]}}
        stack = build_pass_stack(2, source)
        plan = compile_legacy_nr(stack)
        expected = plan.legacy_parts()
        stack["passes"][0]["extra"]["values"].append(3)
        profiles, metadata = plan.legacy_parts()
        profiles[0]["extra"]["values"].append(4)
        metadata["inherited"][0] = True
        exported = plan.stages[0].parameters_copy()
        exported["extra"]["values"].append(5)
        self.assertEqual(plan.legacy_parts(), expected)
        self.assertEqual(plan.stages[1].parameters_copy()["extra"]["values"], [1, 2])

    def test_legacy_invalid_payloads_still_fail_before_execution(self):
        stack = build_pass_stack(2, look())
        for effect in (None, [], {}, look() | {"schema_version": 3},
                       stack | {"guide_policy": "invented"},
                       stack | {"intermediate_format": "mp4"},
                       stack | {"passes": [stack, look()]},
                       stack | {"pass_count": 4}):
            with self.subTest(effect=effect), self.assertRaises(ValueError):
                compile_legacy_nr(effect)

    def test_contract_only_advertises_implemented_wire_behavior(self):
        contract = backend_contract("external_d5v2_nr")
        self.assertIs(contract, EXTERNAL_D5V2_NR)
        self.assertEqual(contract.protocol, "D5V2")
        self.assertEqual(contract.features, {"nr"})
        self.assertEqual([plane.role for plane in contract.required_planes], ["color", "motion"])
        self.assertEqual([plane.format for plane in contract.required_planes], ["rgba8", "rg16f_le"])
        self.assertEqual(contract.spatial_relation, "same_extent")
        self.assertEqual(contract.temporal_relation, "one_to_one_preserve_pts")
        self.assertEqual(contract.output_format, "rgba8")
        with self.assertRaises(FrozenInstanceError):
            contract.protocol = "invented"

    def test_unknown_backend_or_function_never_falls_back(self):
        for backend_id in ("native_nr", "streamline", "", None, []):
            with self.subTest(backend_id=backend_id), self.assertRaises(ValueError):
                backend_contract(backend_id)
        for feature in ("sr", "fg", "rr", "NR", None, []):
            with self.subTest(feature=feature), self.assertRaises(ValueError):
                FeatureStage(EXTERNAL_D5V2_NR.backend_id, feature, look())

    def test_owned_contract_does_not_change_legacy_lowering(self):
        contract = backend_contract('owned_cnr1_nr')
        self.assertEqual(contract.protocol,'CNR1')
        self.assertEqual(contract.output_format,'rgba16f_le')
        self.assertEqual(contract.features,{'nr'})
        self.assertEqual(compile_legacy_nr(look()).stages[0].backend_id,'external_d5v2_nr')

    def test_disabled_stage_is_preserved_not_optimized_away(self):
        bypass = look() | {"nr_enabled": False, "mix": 0.0}
        plan = compile_legacy_nr(build_pass_stack(3, look(), bypass, look()))
        self.assertEqual(len(plan.stages), 3)
        self.assertEqual(plan.stages[1].parameters_copy(), bypass)

    def test_configuration_imports_do_not_load_native_or_media_implementations(self):
        script = """
import sys
from comfy_dlss_experimental.processing_plan import compile_legacy_nr
from comfy_dlss_experimental.guide_providers import create_flow_estimator
compile_legacy_nr({'schema_version': 2})
assert create_flow_estimator('zero', 64, 64) is None
for name in ('cv2', 'numpy', 'comfy_dlss_experimental.nvidia_flow',
             'comfy_dlss_experimental.native_relay', 'comfy_api',
             'comfy_dlss_experimental.platform_runtime'):
    assert name not in sys.modules, name
"""
        result = subprocess.run([sys.executable, "-c", script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
