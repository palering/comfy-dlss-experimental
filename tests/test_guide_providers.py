import builtins
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comfy_dlss_experimental.flow_provider import FlowProvider
from comfy_dlss_experimental.guide_providers import create_flow_estimator


class GuideProviderFactoryTests(unittest.TestCase):
    def test_zero_does_not_construct_an_estimator(self):
        with patch("comfy_dlss_experimental.guide_providers.DISFlow") as dis:
            self.assertIsNone(create_flow_estimator("zero", 64, 64))
            dis.assert_not_called()

    def test_dis_never_imports_native_backend(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if "nvidia_flow" in name:
                raise AssertionError("Unselected NVIDIA provider was imported")
            return original(name, *args, **kwargs)

        config = FlowProvider(preset="quality")
        with patch("comfy_dlss_experimental.guide_providers.DISFlow") as dis, \
                patch("builtins.__import__", side_effect=guarded):
            self.assertIs(create_flow_estimator("dis", 128, 64, config), dis.return_value)
            dis.assert_called_once_with(config)

    def test_nvidia_receives_exact_dimensions_config_and_cancellation(self):
        factory = Mock()
        config = FlowProvider(kind="nvidia", output_grid=1, device=2, temporal_hints=False)
        cancelled = lambda: True
        with patch.dict("sys.modules", {"comfy_dlss_experimental.nvidia_flow": SimpleNamespace(NvidiaFlow=factory)}), \
                patch("comfy_dlss_experimental.guide_providers.DISFlow") as dis:
            result = create_flow_estimator("nvidia", 256, 128, config, cancelled=cancelled)
            self.assertIs(result, factory.return_value)
            factory.assert_called_once_with(256, 128, config, cancelled=cancelled)
            dis.assert_not_called()

    def test_native_failure_is_not_silently_replaced_with_dis(self):
        factory = Mock(side_effect=RuntimeError("helper unavailable"))
        with patch.dict("sys.modules", {"comfy_dlss_experimental.nvidia_flow": SimpleNamespace(NvidiaFlow=factory)}), \
                patch("comfy_dlss_experimental.guide_providers.DISFlow") as dis:
            with self.assertRaisesRegex(RuntimeError, "helper unavailable"):
                create_flow_estimator("nvidia", 64, 64)
            factory.assert_called_once()
            dis.assert_not_called()

    def test_legacy_defaults_are_unchanged(self):
        with patch("comfy_dlss_experimental.guide_providers.DISFlow") as dis:
            create_flow_estimator("dis", 64, 64)
            dis.assert_called_once_with(FlowProvider())
        factory = Mock()
        with patch.dict("sys.modules", {"comfy_dlss_experimental.nvidia_flow": SimpleNamespace(NvidiaFlow=factory)}):
            create_flow_estimator("nvidia", 64, 64)
            self.assertEqual(factory.call_args.args, (64, 64, FlowProvider(kind="nvidia")))

    def test_invalid_or_mismatched_configuration_fails_before_construction(self):
        for kind, config in (("unknown", None), ("zero", FlowProvider()),
                             ("dis", FlowProvider(kind="nvidia")), ("nvidia", FlowProvider()),
                             ("dis", {}), ("dis", FlowProvider(preset="unknown"))):
            with self.subTest(kind=kind, config=config), \
                    patch("comfy_dlss_experimental.guide_providers.DISFlow") as dis:
                with self.assertRaises(ValueError):
                    create_flow_estimator(kind, 64, 64, config)
                dis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
